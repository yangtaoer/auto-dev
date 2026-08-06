from __future__ import annotations

import json
import hmac
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, field_validator

from .config import ROOT, settings
from .db import (
    create_delivery_request,
    create_request_intake,
    claim_request_intake,
    add_artifact,
    add_event,
    create_session,
    delete_session,
    get_session_user,
    init_db,
    json_value,
    project_for_api,
    public_user,
    replace_user_emails,
    request_detail,
    request_intake_detail,
    row,
    rows,
    transaction,
    update_request,
    update_step,
    utc_now,
)
from .domain import DELIVERY_MODE_LABELS, DeliveryMode, STATUS_LABELS, RunStatus
from .orchestrator import worker
from .live_stream import live_codex_streams
from .security import hash_password, verify_password
from .services.delivery import ArtifactService, Mailer


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.environment == "production" and not settings.runner_token:
        raise RuntimeError("生产环境必须通过 AUTODEV_RUNNER_TOKEN_FILE 配置本机执行器令牌")
    if settings.worker_enabled:
        worker.start()
    yield
    worker.stop()


app = FastAPI(
    title="全自助需求研发交付",
    version="0.4.5",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "app" / "templates")


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class UserInput(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,40}$")
    display_name: str = Field(min_length=1, max_length=80)
    email: str | None = None
    emails: list[str] = Field(default_factory=list)
    password: str = Field(min_length=8, max_length=200)
    role: str = Field(pattern=r"^(admin|pm)$")
    active: bool = True


class UserUpdateInput(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,40}$")
    display_name: str = Field(min_length=1, max_length=80)
    email: str | None = None
    emails: list[str] = Field(default_factory=list)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    role: str = Field(pattern=r"^(admin|pm)$")
    active: bool = True


class ProjectInput(BaseModel):
    project_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{2,60}$")
    name: str = Field(min_length=2, max_length=100)
    enabled: bool = True
    simulation_mode: bool = True
    delivery_mode: DeliveryMode
    allow_requirement_override: bool = False
    tfs_collection_url: str
    tfs_project: str
    tfs_area_path: str = ""
    reviewer_name: str = ""
    routing_title_keywords: list[str] = Field(default_factory=list, max_length=30)
    allowed_work_item_types: list[str] = ["用户情景"]
    allowed_states: list[str] = ["已评审"]
    repository_path: str = ""
    repository_paths: list[str] = Field(default_factory=list, max_length=30)
    base_branch: str = "dev"
    build_command: str = ""
    package_patterns: list[str] = []
    sql_patterns: list[str] = ["**/*.sql"]
    config_patterns: list[str] = ["**/*.yml", "**/*.yaml", "**/*.properties", "**/*.xml"]
    protected_patterns: list[str] = ["**/common/**", "**/shared/**", "**/production/**"]
    notification_cc: str = ""
    runner_id: str = Field(default="yangtao-pc", pattern=r"^[a-zA-Z0-9_.-]{2,80}$")

    @field_validator("tfs_collection_url")
    @classmethod
    def validate_tfs_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("TFS 地址必须以 http:// 或 https:// 开头")
        return value.rstrip("/")


class DeliveryRequestInput(BaseModel):
    project_id: int | None = None
    work_item_id: int = Field(gt=0)
    delivery_mode: DeliveryMode | None = None
    notification_emails: list[str] = Field(default_factory=list)


class RunnerProjectSync(BaseModel):
    runner_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,80}$")
    projects: list[ProjectInput] = Field(max_length=200)


class RunnerIntakeRoute(BaseModel):
    runner_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,80}$")
    project_key: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{2,60}$")
    error_message: str = Field(default="", max_length=2000)


class RunnerIdentity(BaseModel):
    runner_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,80}$")


class RunnerHeartbeat(RunnerIdentity):
    hostname: str = Field(default="", max_length=200)
    version: str = Field(default="", max_length=80)
    state: str = Field(default="idle", max_length=40)
    current_request_id: str | None = None
    current_request_ids: list[str] = Field(default_factory=list, max_length=5)
    max_concurrency: int = Field(default=1, ge=1, le=5)
    codex_usage: dict[str, Any] = Field(default_factory=dict)


class RunnerRequestUpdate(BaseModel):
    fields: dict[str, Any]


class RunnerStepUpdate(BaseModel):
    status: str = Field(pattern=r"^(pending|running|completed|failed|skipped)$")
    message: str = Field(default="", max_length=2000)


class RunnerEventInput(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    message: str = Field(max_length=2000)
    level: str = Field(default="info", pattern=r"^(info|warning|error)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunnerNotifyInput(BaseModel):
    action_required: bool = False
    terminal: bool = False


class RunnerTestEmailInput(BaseModel):
    recipient: str = Field(min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LiveCodexEvent(BaseModel):
    kind: str = Field(default="status", pattern=r"^(assistant|reasoning|command|file|plan|status)$")
    content: str = Field(max_length=12000)
    group: str = Field(default="", max_length=160)
    delta: bool = False
    format: str = Field(default="plain", pattern=r"^(plain|markdown)$")


class RunnerLiveCodexEvents(BaseModel):
    events: list[LiveCodexEvent] = Field(max_length=100)


def current_user(autodev_session: Annotated[str | None, Cookie()] = None) -> dict[str, Any]:
    user = get_session_user(autodev_session)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def admin_user(user: Annotated[dict, Depends(current_user)]) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def runner_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = f"Bearer {settings.runner_token}"
    if not settings.runner_token or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="本机执行器令牌无效")


def runner_request(request_id: str) -> dict[str, Any]:
    detail = request_detail(request_id)
    if not detail:
        raise HTTPException(status_code=404, detail="任务不存在")
    return detail


def normalize_emails(emails: list[str], legacy_email: str | None = None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in emails or ([legacy_email] if legacy_email else []):
        email = raw.strip().lower()
        if not email or email in seen:
            continue
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(status_code=422, detail=f"邮箱格式不正确：{raw}")
        seen.add(email)
        normalized.append(email)
    if not normalized:
        raise HTTPException(status_code=422, detail="至少配置一个通知邮箱")
    if len(normalized) > 10:
        raise HTTPException(status_code=422, detail="每个账号最多配置 10 个邮箱")
    return normalized


def request_recipients(detail: dict[str, Any]) -> list[str]:
    selected = list(detail.get("notification_emails") or [detail["requester_email"]])
    selected.extend(
        email.strip()
        for email in detail["policy_snapshot"].get("notification_cc", "").split(",")
        if email.strip()
    )
    result: list[str] = []
    seen: set[str] = set()
    for email in selected:
        key = email.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(email.strip())
    return result


def send_cloud_email(request_id: str, *, action_required: bool, terminal: bool = False) -> bool:
    detail = runner_request(request_id)
    if terminal and action_required:
        raise HTTPException(status_code=422, detail="终态通知不能同时标记为待审核")
    if terminal and detail["status"] not in {"failed", "rejected", "cancelled"}:
        raise HTTPException(status_code=409, detail="当前任务不是失败、驳回或取消状态")
    if terminal and detail.get("email_sent_at"):
        return False
    recipients = request_recipients(detail)
    mailer = Mailer()
    terminal_status = detail["status"] if terminal else None
    subject = mailer.delivery_subject(
        detail,
        action_required=action_required,
        terminal_status=terminal_status,
    )
    html_body = mailer.delivery_html(
        detail,
        action_required=action_required,
        terminal_status=terminal_status,
    )
    if not mailer.configured():
        artifacts = ArtifactService()
        name = (
            "terminal-email-preview.html"
            if terminal
            else ("review-email-preview.html" if action_required else "delivery-email-preview.html")
        )
        path = artifacts.request_dir(request_id) / name
        path.write_text(html_body, encoding="utf-8")
        add_artifact(request_id, "email_preview", name, str(path))
        add_event(request_id, "mail.preview", "SMTP 尚未配置，云端已生成邮件预览", level="warning")
        if terminal:
            update_request(request_id, email_sent_at=utc_now())
        return True
    attachments = [
        Path(item["local_path"])
        for item in detail["artifacts"]
        if item["kind"] == "merge_screenshot" and item["local_path"]
    ]
    mailer.send(to=recipients, subject=subject, html_body=html_body, attachments=attachments)
    if terminal or not action_required:
        update_request(request_id, email_sent_at=utc_now())
    return True


def can_access_request(user: dict, request_id: str) -> dict:
    detail = request_detail(request_id)
    if not detail:
        raise HTTPException(status_code=404, detail="任务不存在")
    if user["role"] != "admin" and detail["requester_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    return detail


@app.get("/healthz")
def health() -> dict:
    return {"status": "ok", "mode": "standalone" if settings.worker_enabled else "cloud-control-plane"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_session_user(request.cookies.get("autodev_session")):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"demo_enabled": settings.seed_demo})


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = get_session_user(request.cookies.get("autodev_session"))
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"user": public_user(user), "app_version": settings.runner_version},
    )


@app.post("/api/auth/login")
def login(payload: LoginInput, response: Response) -> dict:
    user = row("SELECT * FROM users WHERE username=? AND active=1", (payload.username,))
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, expires = create_session(user["id"])
    response.set_cookie(
        "autodev_session", token, httponly=True, samesite="strict", secure=settings.secure_cookies,
        max_age=12 * 60 * 60, path="/",
    )
    return {"user": public_user(user), "expires_at": expires}


@app.post("/api/auth/logout")
def logout(response: Response, autodev_session: Annotated[str | None, Cookie()] = None) -> dict:
    delete_session(autodev_session)
    response.delete_cookie("autodev_session", path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user: Annotated[dict, Depends(current_user)]) -> dict:
    return {"user": public_user(user)}


@app.get("/api/dashboard")
def dashboard(user: Annotated[dict, Depends(current_user)]) -> dict:
    scope = "" if user["role"] == "admin" else "WHERE requester_id=?"
    params = () if user["role"] == "admin" else (user["id"],)
    counts = rows(f"SELECT status,COUNT(*) count FROM delivery_requests {scope} GROUP BY status", params)
    active_sql = "status NOT IN ('delivered','failed','rejected','cancelled')"
    active_scope = active_sql if not scope else f"requester_id=? AND {active_sql}"
    activity_select = """(SELECT e.message FROM delivery_events e
        WHERE e.request_id=r.id ORDER BY e.id DESC LIMIT 1) current_activity"""
    active_requests = rows(
        f"""SELECT r.*,p.name project_name,u.display_name requester_name,{activity_select}
            FROM delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id
            WHERE {active_scope} ORDER BY r.updated_at DESC LIMIT 12""",
        params,
    )
    recent_scope = "" if not scope else "WHERE r.requester_id=?"
    recent_requests = rows(
        f"""SELECT r.*,p.name project_name,u.display_name requester_name,{activity_select}
            FROM delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id
            {recent_scope} ORDER BY r.created_at DESC LIMIT 40""",
        params,
    )
    intake_scope = "" if user["role"] == "admin" else "AND i.requester_id=?"
    pending_intakes = rows(
        f"""SELECT i.*,u.display_name requester_name
            FROM request_intakes i JOIN users u ON u.id=i.requester_id
            WHERE i.status IN ('queued','claimed') {intake_scope}
            ORDER BY i.created_at DESC LIMIT 12""",
        params,
    )
    failed_intakes = rows(
        f"""SELECT i.*,u.display_name requester_name
            FROM request_intakes i JOIN users u ON u.id=i.requester_id
            WHERE i.status='failed' {intake_scope}
            ORDER BY i.created_at DESC LIMIT 40""",
        params,
    )
    intake_items: list[dict[str, Any]] = []
    for intake in pending_intakes:
        intake_items.append(
            {
                "id": intake["id"],
                "intake_id": intake["id"],
                "record_type": "intake",
                "work_item_id": intake["work_item_id"],
                "requester_id": intake["requester_id"],
                "requester_name": intake["requester_name"],
                "title": "正在读取 TFS 需求并识别项目…",
                "project_name": "项目识别中",
                "delivery_mode": "routing",
                "status": "routing",
                "current_activity": "执行器正在识别所属项目与交付策略" if intake["status"] == "claimed" else "任务已提交，等待执行器扫描",
                "created_at": intake["created_at"],
                "updated_at": intake["updated_at"],
                "completed_at": None,
                "duration_seconds": None,
                "artifacts": [],
            }
        )
    failed_intake_items = [
        {
            "id": intake["id"],
            "intake_id": intake["id"],
            "record_type": "intake",
            "work_item_id": intake["work_item_id"],
            "requester_id": intake["requester_id"],
            "requester_name": intake["requester_name"],
            "title": "未能识别需求所属项目",
            "project_name": "项目识别失败",
            "delivery_mode": "routing",
            "status": "failed",
            "current_activity": intake.get("error_message") or "项目识别失败，请查看详情",
            "created_at": intake["created_at"],
            "updated_at": intake["updated_at"],
            "completed_at": intake["updated_at"],
            "duration_seconds": None,
            "artifacts": [],
        }
        for intake in failed_intakes
    ]
    active = sorted(
        [*active_requests, *intake_items], key=lambda item: item.get("updated_at") or "", reverse=True
    )[:12]
    recent = sorted(
        [*recent_requests, *intake_items, *failed_intake_items],
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )[:40]
    request_ids = [item["id"] for item in (*active_requests, *recent_requests)]
    artifact_map: dict[str, list[dict[str, Any]]] = {request_id: [] for request_id in request_ids}
    if request_ids:
        placeholders = ",".join("?" for _ in request_ids)
        for artifact in rows(
            f"""SELECT id,request_id,kind,name,external_url,size_bytes
                FROM delivery_artifacts
                WHERE request_id IN ({placeholders})
                  AND kind NOT IN ('report','pull_request','merge_evidence')
                  AND NOT (kind='merge_screenshot' AND name LIKE '%凭证%')
                ORDER BY id""",
            tuple(request_ids),
        ):
            artifact_map.setdefault(artifact["request_id"], []).append(artifact)
    terminal_statuses = {"delivered", "failed", "rejected", "cancelled"}
    current_time = datetime.now(UTC)
    for item in (*active_requests, *recent_requests):
        item["artifacts"] = artifact_map.get(item["id"], [])
        started_value = item.get("started_at") or item.get("created_at")
        ended_value = item.get("completed_at")
        if not ended_value and item.get("status") in terminal_statuses:
            ended_value = item.get("updated_at")
        try:
            started = datetime.fromisoformat(started_value) if started_value else None
            ended = datetime.fromisoformat(ended_value) if ended_value else current_time
            item["duration_seconds"] = max(0, int((ended - started).total_seconds())) if started else None
        except (TypeError, ValueError):
            item["duration_seconds"] = None
    runners = rows("SELECT * FROM runners ORDER BY runner_id")
    now = datetime.now(UTC)
    for item in runners:
        try:
            age = (now - datetime.fromisoformat(item["last_seen_at"])).total_seconds()
        except (TypeError, ValueError):
            age = 999999
        item["online"] = age <= 90 and item["state"] != "stopping"
        runner_detail = json_value(item.get("detail"), {})
        item["codex_usage"] = runner_detail.get("codex_usage", {})
        item["current_request_ids"] = runner_detail.get(
            "current_request_ids", [item["current_request_id"]] if item.get("current_request_id") else []
        )
        item["active_count"] = len(item["current_request_ids"])
        item["max_concurrency"] = min(5, max(1, int(runner_detail.get("max_concurrency") or 1)))
        item.pop("detail", None)
    local_now = datetime.now(timezone(timedelta(hours=8)))
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()
    stats_scope = "" if user["role"] == "admin" else "WHERE requester_id=?"
    stats_params: tuple[Any, ...] = () if user["role"] == "admin" else (user["id"],)
    today_join = "AND" if stats_scope else "WHERE"
    summary = row(
        f"""SELECT COUNT(*) total,
            SUM(CASE WHEN created_at>=? THEN 1 ELSE 0 END) today_total,
            SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) success,
            SUM(CASE WHEN status IN ('failed','rejected') THEN 1 ELSE 0 END) failed,
            SUM(CASE WHEN status NOT IN ('delivered','failed','rejected','cancelled') THEN 1 ELSE 0 END) running,
            SUM(CASE WHEN status='waiting_merge' THEN 1 ELSE 0 END) waiting_merge
            FROM delivery_requests {stats_scope} {today_join} created_at IS NOT NULL""",
        (today_start, *stats_params),
    ) or {}
    intake_summary = row(
        f"""SELECT COUNT(*) total,
            SUM(CASE WHEN i.created_at>=? THEN 1 ELSE 0 END) today_total,
            SUM(CASE WHEN i.status='claimed' THEN 1 ELSE 0 END) claimed
            FROM request_intakes i WHERE i.status IN ('queued','claimed') {intake_scope}""",
        (today_start, *params),
    ) or {}
    pending_total = int(intake_summary.get("total") or 0)
    pending_today = int(intake_summary.get("today_total") or 0)
    failed_intake_summary = row(
        f"""SELECT COUNT(*) total,
            SUM(CASE WHEN i.created_at>=? THEN 1 ELSE 0 END) today_total
            FROM request_intakes i WHERE i.status='failed' {intake_scope}""",
        (today_start, *params),
    ) or {}
    intake_failed_total = int(failed_intake_summary.get("total") or 0)
    intake_failed_today = int(failed_intake_summary.get("today_total") or 0)
    queued_requests = row(
        "SELECT COUNT(*) count FROM delivery_requests WHERE status='queued'",
    ) or {"count": 0}
    busy_statuses = "'validating','developing','submitting','building','capturing','delivering'"
    busy_requests = row(
        f"SELECT COUNT(*) count FROM delivery_requests WHERE status IN ({busy_statuses})",
    ) or {"count": 0}
    intake_capacity = row(
        """SELECT
            SUM(CASE WHEN status='claimed' THEN 1 ELSE 0 END) claimed,
            SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) queued
            FROM request_intakes WHERE status IN ('queued','claimed')"""
    ) or {}
    capacity_active = min(
        settings.runner_max_concurrency,
        int(busy_requests.get("count") or 0) + int(intake_capacity.get("claimed") or 0),
    )
    capacity_queued = int(queued_requests.get("count") or 0) + int(intake_capacity.get("queued") or 0)
    stats = {key: int(summary.get(key) or 0) for key in ("total", "today_total", "success", "failed", "running", "waiting_merge")}
    stats["total"] += pending_total + intake_failed_total
    stats["today_total"] += pending_today + intake_failed_today
    stats["failed"] += intake_failed_total
    stats["running"] += pending_total
    request_counts = {item["status"]: item["count"] for item in counts}
    request_counts["routing"] = pending_total
    request_counts["failed"] = int(request_counts.get("failed") or 0) + intake_failed_total
    return {
        "counts": request_counts,
        "active": active,
        "recent": recent,
        "runners": runners,
        "stats": stats,
        "capacity": {
            "limit": settings.runner_max_concurrency,
            "active": capacity_active,
            "queued": capacity_queued,
            "available": max(0, settings.runner_max_concurrency - capacity_active),
        },
    }


@app.get("/api/projects")
def list_projects(_: Annotated[dict, Depends(current_user)]) -> dict:
    return {"projects": [project_for_api(item) for item in rows("SELECT * FROM projects WHERE enabled=1 ORDER BY name")]}


@app.post("/api/projects")
def create_project(payload: ProjectInput, user: Annotated[dict, Depends(admin_user)]) -> dict:
    data = payload.model_dump(mode="json")
    now = utc_now()
    columns = list(data)
    values = []
    for key in columns:
        value = data[key]
        if isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            value = int(value)
        values.append(value)
    with transaction() as conn:
        try:
            cursor = conn.execute(
                f"INSERT INTO projects({','.join(columns)},created_at,updated_at) VALUES({','.join('?' for _ in columns)},?,?)",
                (*values, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="项目标识已存在") from exc
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], "project.create", "project", str(cursor.lastrowid), json.dumps(data, ensure_ascii=False), now),
        )
    return {"project": project_for_api(row("SELECT * FROM projects WHERE id=?", (cursor.lastrowid,)))}


@app.put("/api/projects/{project_id}")
def update_project(project_id: int, payload: ProjectInput, user: Annotated[dict, Depends(admin_user)]) -> dict:
    if not row("SELECT 1 FROM projects WHERE id=?", (project_id,)):
        raise HTTPException(status_code=404, detail="项目不存在")
    data = payload.model_dump(mode="json")
    values = []
    for key, value in data.items():
        if isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            value = int(value)
        values.append(value)
    with transaction() as conn:
        conn.execute(
            f"UPDATE projects SET {','.join(f'{key}=?' for key in data)},updated_at=? WHERE id=?",
            (*values, utc_now(), project_id),
        )
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], "project.update", "project", str(project_id), json.dumps(data, ensure_ascii=False), utc_now()),
        )
    return {"project": project_for_api(row("SELECT * FROM projects WHERE id=?", (project_id,)))}


@app.put("/api/runner/projects", dependencies=[Depends(runner_auth)])
def sync_runner_projects(payload: RunnerProjectSync) -> dict:
    """Mirror the runner's local project preset catalog into the cloud read-only registry."""
    now = utc_now()
    project_keys = [item.project_key for item in payload.projects]
    if len(project_keys) != len(set(project_keys)):
        raise HTTPException(status_code=422, detail="本机项目预设中存在重复的项目标识")
    with transaction() as conn:
        if project_keys:
            placeholders = ",".join("?" for _ in project_keys)
            conn.execute(
                f"UPDATE projects SET enabled=0,updated_at=? WHERE runner_id=? AND project_key NOT IN ({placeholders})",
                (now, payload.runner_id, *project_keys),
            )
        else:
            conn.execute(
                "UPDATE projects SET enabled=0,updated_at=? WHERE runner_id=?",
                (now, payload.runner_id),
            )
        for project in payload.projects:
            data = project.model_dump(mode="json")
            data["runner_id"] = payload.runner_id
            columns = list(data)
            values: list[Any] = []
            for value in data.values():
                if isinstance(value, list):
                    value = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, bool):
                    value = int(value)
                values.append(value)
            updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "project_key")
            conn.execute(
                f"""INSERT INTO projects({','.join(columns)},created_at,updated_at)
                    VALUES({','.join('?' for _ in columns)},?,?)
                    ON CONFLICT(project_key) DO UPDATE SET {updates},updated_at=excluded.updated_at""",
                (*values, now, now),
            )
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(NULL,?,?,?,?,?)",
            (
                "project.runner_sync", "runner", payload.runner_id,
                json.dumps({"project_keys": project_keys}, ensure_ascii=False), now,
            ),
        )
    return {
        "ok": True,
        "projects": [
            project_for_api(item)
            for item in rows("SELECT * FROM projects WHERE runner_id=? AND enabled=1 ORDER BY name", (payload.runner_id,))
        ],
    }


@app.post("/api/users")
def create_user(payload: UserInput, user: Annotated[dict, Depends(admin_user)]) -> dict:
    emails = normalize_emails(payload.emails, payload.email)
    now = utc_now()
    with transaction() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users(username,display_name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    payload.username, payload.display_name, emails[0], hash_password(payload.password),
                    payload.role, int(payload.active), now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        replace_user_emails(conn, int(cursor.lastrowid), emails)
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], "user.create", "user", str(cursor.lastrowid), json.dumps({"emails": emails, "role": payload.role}, ensure_ascii=False), now),
        )
    created = row("SELECT * FROM users WHERE id=?", (cursor.lastrowid,))
    return {"user": public_user(created)}


@app.get("/api/users")
def list_users(_: Annotated[dict, Depends(admin_user)]) -> dict:
    return {"users": [public_user(item) for item in rows("SELECT * FROM users ORDER BY role,display_name")]}


@app.put("/api/users/{user_id}")
def update_user(user_id: int, payload: UserUpdateInput, actor: Annotated[dict, Depends(admin_user)]) -> dict:
    existing = row("SELECT * FROM users WHERE id=?", (user_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="账号不存在")
    if user_id == actor["id"] and (not payload.active or payload.role != actor["role"]):
        raise HTTPException(status_code=409, detail="当前登录管理员不能停用自己或修改自己的角色")
    emails = normalize_emails(payload.emails, payload.email)
    fields: dict[str, Any] = {
        "username": payload.username,
        "display_name": payload.display_name,
        "email": emails[0],
        "role": payload.role,
        "active": int(payload.active),
    }
    if payload.password:
        fields["password_hash"] = hash_password(payload.password)
    now = utc_now()
    with transaction() as conn:
        try:
            conn.execute(
                f"UPDATE users SET {','.join(f'{key}=?' for key in fields)} WHERE id=?",
                (*fields.values(), user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        replace_user_emails(conn, user_id, emails)
        if not payload.active:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (
                actor["id"], "user.update", "user", str(user_id),
                json.dumps({"emails": emails, "role": payload.role, "active": payload.active, "password_changed": bool(payload.password)}, ensure_ascii=False),
                now,
            ),
        )
    updated = row("SELECT * FROM users WHERE id=?", (user_id,))
    return {"user": public_user(updated)}


@app.post("/api/requests")
def submit_request(payload: DeliveryRequestInput, user: Annotated[dict, Depends(current_user)]) -> dict:
    configured_emails = public_user(user)["emails"]
    selected_emails = normalize_emails(payload.notification_emails or configured_emails)
    configured_lookup = {email.lower() for email in configured_emails}
    if any(email.lower() not in configured_lookup for email in selected_emails):
        raise HTTPException(status_code=422, detail="通知邮箱只能从当前账号已配置的邮箱中选择")
    if payload.project_id is None:
        runner_rows = rows("SELECT DISTINCT runner_id FROM projects WHERE enabled=1 ORDER BY runner_id")
        if not runner_rows:
            raise HTTPException(status_code=409, detail="当前没有可用的自动研发项目")
        if len(runner_rows) > 1:
            raise HTTPException(status_code=409, detail="当前项目分布在多个执行器，暂时无法自动识别，请联系管理员")
        if row(
            "SELECT 1 FROM delivery_requests WHERE work_item_id=? AND status NOT IN ('delivered','rejected','failed','cancelled')",
            (payload.work_item_id,),
        ):
            raise HTTPException(status_code=409, detail="该需求已有正在执行的研发任务")
        try:
            intake_id = create_request_intake(
                user["id"], payload.work_item_id, runner_rows[0]["runner_id"], selected_emails
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="该需求正在识别项目或等待执行") from exc
        return {"id": intake_id, "status": "routing", "routing": True}

    project = row("SELECT * FROM projects WHERE id=? AND enabled=1", (payload.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在或已停用")
    mode = project["delivery_mode"]
    if payload.delivery_mode and payload.delivery_mode.value != mode:
        if user["role"] != "admin" or not project["allow_requirement_override"]:
            raise HTTPException(status_code=403, detail="该项目不允许覆盖交付方式")
        mode = payload.delivery_mode.value
    try:
        request_id = create_delivery_request(project, user["id"], payload.work_item_id, mode, selected_emails)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该需求已有正在执行的研发任务") from exc
    return {"id": request_id, "status": RunStatus.QUEUED.value}


@app.get("/api/intakes/{intake_id}")
def get_request_intake(intake_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    intake = request_intake_detail(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="自动识别任务不存在")
    if user["role"] != "admin" and intake["requester_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该自动识别任务")
    return {"intake": intake}


@app.get("/api/requests/{request_id}")
def get_request(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = can_access_request(user, request_id)
    detail["status_label"] = STATUS_LABELS.get(RunStatus(detail["status"]), detail["status"])
    detail["delivery_mode_label"] = DELIVERY_MODE_LABELS[DeliveryMode(detail["delivery_mode"])]
    return {"request": detail}


@app.post("/api/requests/{request_id}/codex-watch/start")
def start_codex_watch(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = can_access_request(user, request_id)
    if detail["status"] != RunStatus.DEVELOPING.value:
        raise HTTPException(status_code=409, detail="Codex 当前未处于研发执行阶段")
    watcher_id, cursor = live_codex_streams.start(request_id)
    return {
        "watcher_id": watcher_id,
        "cursor": cursor,
        "ephemeral": True,
        "message": "仅传输打开窗口后的输出；关闭即停止采集且不保存",
    }


@app.get("/api/requests/{request_id}/codex-watch/{watcher_id}")
def poll_codex_watch(
    request_id: str,
    watcher_id: str,
    user: Annotated[dict, Depends(current_user)],
    after: int = 0,
) -> dict:
    can_access_request(user, request_id)
    result = live_codex_streams.poll(request_id, watcher_id, max(0, after))
    if result is None:
        raise HTTPException(status_code=410, detail="查看会话已结束，请重新打开")
    return result


@app.post("/api/requests/{request_id}/codex-watch/{watcher_id}/stop")
def stop_codex_watch(
    request_id: str,
    watcher_id: str,
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    can_access_request(user, request_id)
    live_codex_streams.stop(request_id, watcher_id)
    return {"ok": True}


@app.post("/api/requests/{request_id}/cancel")
def cancel_request(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = can_access_request(user, request_id)
    if detail["status"] in {"delivered", "failed", "rejected", "cancelled"}:
        raise HTTPException(status_code=409, detail="任务已经结束")
    update_request(request_id, status=RunStatus.CANCELLED.value, completed_at=utc_now())
    add_event(request_id, "request.cancelled", f"{user['display_name']} 取消了任务", level="warning")
    try:
        if send_cloud_email(request_id, action_required=False, terminal=True):
            add_event(request_id, "mail.terminal_sent", "已发送任务取消通知邮件", level="warning")
    except Exception as exc:
        add_event(request_id, "mail.terminal_failed", f"任务取消通知邮件发送失败：{str(exc)[:1000]}", level="error")
    return {"ok": True}


@app.post("/api/requests/{request_id}/simulate-merge")
def simulate_merge(request_id: str, user: Annotated[dict, Depends(admin_user)]) -> dict:
    detail = can_access_request(user, request_id)
    if not detail["policy_snapshot"].get("simulation_mode") or detail["status"] != "waiting_merge":
        raise HTTPException(status_code=409, detail="只有演示模式下等待合并的任务可使用此操作")
    update_request(request_id, merge_commit="demo-merge-" + request_id.replace("-", "")[:12], next_poll_at=utc_now())
    worker.process_once()
    return {"ok": True}


@app.post("/api/runner/heartbeat", dependencies=[Depends(runner_auth)])
def runner_heartbeat(payload: RunnerHeartbeat) -> dict:
    now = utc_now()
    with transaction() as conn:
        conn.execute(
            """INSERT INTO runners(runner_id,hostname,version,state,current_request_id,last_seen_at,detail)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(runner_id) DO UPDATE SET hostname=excluded.hostname,version=excluded.version,
               state=excluded.state,current_request_id=excluded.current_request_id,last_seen_at=excluded.last_seen_at,
               detail=excluded.detail""",
            (
                payload.runner_id,
                payload.hostname,
                payload.version,
                payload.state,
                payload.current_request_id,
                now,
                json.dumps(
                    {
                        "codex_usage": payload.codex_usage,
                        "current_request_ids": list(dict.fromkeys(payload.current_request_ids))[:5],
                        "max_concurrency": payload.max_concurrency,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return {"ok": True, "server_time": now}


@app.post("/api/runner/intakes/claim", dependencies=[Depends(runner_auth)])
def runner_claim_intake(payload: RunnerIdentity) -> dict:
    return {"intake": claim_request_intake(payload.runner_id)}


@app.post("/api/runner/intakes/{intake_id}/route", dependencies=[Depends(runner_auth)])
def runner_route_intake(intake_id: str, payload: RunnerIntakeRoute) -> dict:
    intake = request_intake_detail(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="自动识别任务不存在")
    if intake["runner_id"] != payload.runner_id:
        raise HTTPException(status_code=403, detail="该自动识别任务不属于当前执行器")
    if intake["status"] == "routed":
        return {"ok": True, "request_id": intake["result_request_id"]}
    if intake["status"] != "claimed":
        raise HTTPException(status_code=409, detail="自动识别任务当前不可提交结果")

    if not payload.project_key:
        message = payload.error_message or "未找到符合准入范围的自动研发项目"
        with transaction() as conn:
            conn.execute(
                "UPDATE request_intakes SET status='failed',error_message=?,updated_at=? WHERE id=?",
                (message, utc_now(), intake_id),
            )
        return {"ok": True, "request_id": None}

    project = row(
        "SELECT * FROM projects WHERE project_key=? AND runner_id=? AND enabled=1",
        (payload.project_key, payload.runner_id),
    )
    if not project:
        message = f"本机识别到项目 {payload.project_key}，但云端目录不存在或已停用"
        with transaction() as conn:
            conn.execute(
                "UPDATE request_intakes SET status='failed',error_message=?,updated_at=? WHERE id=?",
                (message, utc_now(), intake_id),
            )
        return {"ok": True, "request_id": None}
    try:
        request_id = create_delivery_request(
            project,
            intake["requester_id"],
            intake["work_item_id"],
            project["delivery_mode"],
            intake["notification_emails"],
        )
    except sqlite3.IntegrityError:
        message = "该需求已有正在执行的研发任务"
        with transaction() as conn:
            conn.execute(
                "UPDATE request_intakes SET status='failed',error_message=?,updated_at=? WHERE id=?",
                (message, utc_now(), intake_id),
            )
        return {"ok": True, "request_id": None}
    with transaction() as conn:
        conn.execute(
            """UPDATE request_intakes SET status='routed',result_request_id=?,error_message='',updated_at=?
               WHERE id=?""",
            (request_id, utc_now(), intake_id),
        )
    return {"ok": True, "request_id": request_id}


@app.post("/api/runner/claim", dependencies=[Depends(runner_auth)])
def runner_claim(payload: RunnerIdentity) -> dict:
    """Atomically reserve one queued request for its configured local runner."""
    with transaction() as conn:
        item = conn.execute(
            "SELECT id FROM delivery_requests WHERE runner_id=? AND status='queued' ORDER BY created_at LIMIT 1",
            (payload.runner_id,),
        ).fetchone()
        if not item:
            return {"request": None}
        now = utc_now()
        changed = conn.execute(
            """UPDATE delivery_requests SET status='validating',started_at=COALESCE(started_at,?),updated_at=?
               WHERE id=? AND status='queued'""",
            (now, now, item["id"]),
        ).rowcount
        if not changed:
            return {"request": None}
        conn.execute(
            "INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)",
            (item["id"], "info", "runner.claimed", f"本机执行器 {payload.runner_id} 已领取任务", now),
        )
    return {"request": request_detail(item["id"])}


@app.get("/api/runner/pollable", dependencies=[Depends(runner_auth)])
def runner_pollable(runner_id: str) -> dict:
    now = utc_now()
    lease_until = (datetime.now(UTC) + timedelta(seconds=max(60, settings.poll_seconds * 3))).isoformat()
    with transaction() as conn:
        item = conn.execute(
            """SELECT id FROM delivery_requests WHERE runner_id=? AND status='waiting_merge'
               AND (next_poll_at IS NULL OR next_poll_at<=?) ORDER BY updated_at LIMIT 1""",
            (runner_id, now),
        ).fetchone()
        if item:
            conn.execute(
                "UPDATE delivery_requests SET next_poll_at=?,updated_at=? WHERE id=? AND status='waiting_merge'",
                (lease_until, now, item["id"]),
            )
    return {"request": request_detail(item["id"]) if item else None}


@app.get("/api/runner/tasks", dependencies=[Depends(runner_auth)])
def runner_tasks(runner_id: str, limit: int = 80) -> dict:
    limit = max(1, min(120, limit))
    tasks = rows(
        """SELECT r.id,r.work_item_id,r.title,r.status,r.current_step,r.delivery_mode,
                  r.created_at,r.updated_at,r.started_at,r.completed_at,r.error_message,
                  p.name project_name,u.display_name requester_name,
                  (SELECT e.message FROM delivery_events e WHERE e.request_id=r.id ORDER BY e.id DESC LIMIT 1) current_activity
           FROM delivery_requests r
           JOIN projects p ON p.id=r.project_id
           JOIN users u ON u.id=r.requester_id
           WHERE r.runner_id=? ORDER BY r.updated_at DESC LIMIT ?""",
        (runner_id, limit),
    )
    return {"tasks": tasks}


@app.get("/api/runner/requests/{request_id}", dependencies=[Depends(runner_auth)])
def runner_get_request(request_id: str) -> dict:
    return {"request": runner_request(request_id)}


@app.get("/api/runner/requests/{request_id}/codex-watch/active", dependencies=[Depends(runner_auth)])
def runner_codex_watch_active(request_id: str) -> dict:
    runner_request(request_id)
    return {"active": live_codex_streams.active(request_id)}


@app.post("/api/runner/requests/{request_id}/codex-watch/events", dependencies=[Depends(runner_auth)])
def runner_publish_codex_events(request_id: str, payload: RunnerLiveCodexEvents) -> dict:
    runner_request(request_id)
    accepted = live_codex_streams.publish_many(
        request_id,
        [event.model_dump(mode="json") for event in payload.events],
    )
    return {"ok": True, "accepted": accepted}


RUNNER_MUTABLE_FIELDS = {
    "work_item_revision", "title", "requirement_summary", "status", "current_step", "progress",
    "branch_name", "base_commit", "commit_hash", "pr_id", "pr_url", "merge_commit", "codex_thread_id",
    "result_summary", "error_message", "repository_states", "started_at", "completed_at", "next_poll_at", "email_sent_at",
}


@app.patch("/api/runner/requests/{request_id}", dependencies=[Depends(runner_auth)])
def runner_update_request(request_id: str, payload: RunnerRequestUpdate) -> dict:
    runner_request(request_id)
    unknown = set(payload.fields) - RUNNER_MUTABLE_FIELDS
    if unknown:
        raise HTTPException(status_code=400, detail=f"不允许更新字段：{', '.join(sorted(unknown))}")
    update_request(request_id, **payload.fields)
    return {"ok": True}


@app.patch("/api/runner/requests/{request_id}/steps/{step_code}", dependencies=[Depends(runner_auth)])
def runner_update_step(request_id: str, step_code: str, payload: RunnerStepUpdate) -> dict:
    runner_request(request_id)
    if not row("SELECT 1 FROM delivery_steps WHERE request_id=? AND step_code=?", (request_id, step_code)):
        raise HTTPException(status_code=404, detail="步骤不存在")
    update_step(request_id, step_code, payload.status, payload.message)
    return {"ok": True}


@app.post("/api/runner/requests/{request_id}/events", dependencies=[Depends(runner_auth)])
def runner_add_event(request_id: str, payload: RunnerEventInput) -> dict:
    runner_request(request_id)
    add_event(
        request_id,
        payload.event_type,
        payload.message,
        level=payload.level,
        metadata=payload.metadata,
    )
    return {"ok": True}


@app.post("/api/runner/requests/{request_id}/artifacts", dependencies=[Depends(runner_auth)])
async def runner_upload_artifact(
    request_id: str,
    kind: Annotated[str, Form(min_length=1, max_length=80)],
    name: Annotated[str, Form(min_length=1, max_length=300)],
    external_url: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> dict:
    runner_request(request_id)
    if file is None and not external_url:
        raise HTTPException(status_code=400, detail="必须上传文件或提供外部链接")
    local_path = ""
    if file is not None:
        safe_name = re.sub(r"[^\w.()\-\u4e00-\u9fff]+", "_", Path(name).name, flags=re.UNICODE)[:220] or "artifact.bin"
        target_dir = settings.delivery_dir / request_id / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex[:12]}-{safe_name}"
        size = 0
        max_bytes = settings.max_artifact_mb * 1024 * 1024
        try:
            with target.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(status_code=413, detail=f"单个产物不能超过 {settings.max_artifact_mb} MB")
                    stream.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        local_path = str(target)
    artifact_id = add_artifact(request_id, kind, name, local_path, external_url)
    return {"artifact_id": artifact_id}


@app.post("/api/runner/requests/{request_id}/notify", dependencies=[Depends(runner_auth)])
def runner_notify(request_id: str, payload: RunnerNotifyInput) -> dict:
    sent = send_cloud_email(
        request_id,
        action_required=payload.action_required,
        terminal=payload.terminal,
    )
    if sent:
        event_type = "mail.terminal_sent" if payload.terminal else (
            "mail.action_required" if payload.action_required else "mail.delivery_sent"
        )
        message = "已发送任务终态通知邮件" if payload.terminal else (
            "已发送 PR 待审核邮件" if payload.action_required else "已发送最终交付邮件"
        )
        add_event(request_id, event_type, message, level="warning" if payload.terminal else "info")
    return {"ok": True, "sent": sent}


@app.post("/api/runner/requests/{request_id}/test-email", dependencies=[Depends(runner_auth)])
def runner_test_email(request_id: str, payload: RunnerTestEmailInput) -> dict:
    detail = runner_request(request_id)
    mailer = Mailer()
    if not mailer.configured():
        raise HTTPException(status_code=503, detail="云端 SMTP 尚未配置")
    title = str(detail.get("title") or "研发任务").strip()[:80]
    subject = f"【AutoDev · 测试邮件】TFS #{detail['work_item_id']}｜{title}"
    mailer.send(to=[payload.recipient], subject=subject, html_body=mailer.delivery_html(detail))
    add_event(request_id, "mail.template_test", "已发送新版交付模板测试邮件")
    return {"ok": True, "recipient": payload.recipient}


@app.get("/api/runner/email-template", dependencies=[Depends(runner_auth)])
def runner_email_template() -> dict:
    return {"template": "compact-wide", "card_width": 860, "brand_mark": "autodev-mark.png"}


@app.get("/api/artifacts/{artifact_id}")
def download_artifact(
    artifact_id: int,
    user: Annotated[dict, Depends(current_user)],
    preview: bool = False,
):
    artifact = row(
        """SELECT a.*,r.requester_id FROM delivery_artifacts a
           JOIN delivery_requests r ON r.id=a.request_id WHERE a.id=?""",
        (artifact_id,),
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="产物不存在")
    if user["role"] != "admin" and artifact["requester_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权下载该产物")
    if artifact["external_url"]:
        return RedirectResponse(artifact["external_url"])
    path = Path(artifact["local_path"]).resolve()
    delivery_root = settings.delivery_dir.resolve()
    if delivery_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="产物文件不存在")
    if preview and artifact["kind"] == "merge_screenshot":
        return FileResponse(path)
    return FileResponse(path, filename=artifact["name"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
