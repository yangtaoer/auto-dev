from __future__ import annotations

import json
import html
import hmac
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, field_validator

from .config import ROOT, settings
from .db import (
    create_delivery_request,
    create_joint_delivery_requests,
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
    prior_request_history,
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
from .domain import (
    DEFAULT_DELIVERY_OPTIONS,
    DELIVERY_MODE_LABELS,
    DeliveryMode,
    TERMINAL_STATUSES,
    RunStatus,
    TaskType,
    TASK_TYPE_LABELS,
    status_label,
    visible_delivery_artifacts,
)
from .orchestrator import worker
from .live_stream import live_codex_streams
from .security import hash_password, verify_password
from .services.delivery import ArtifactService, Mailer
from .services.tfs import TfsClient


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
    title="AutoDev · 自主研发交付",
    version="1.0-Alpha.19",
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
    repository_tfs_paths: dict[str, str] = Field(default_factory=dict)
    base_branch: str = "dev"
    repository_base_branches: dict[str, str] = Field(default_factory=dict)
    verification_command: str = ""
    development_instructions: str = Field(default="", max_length=12000)
    repository_expectations: dict[str, str] = Field(default_factory=dict)
    quality_profile: dict[str, Any] = Field(default_factory=dict)
    artifact_policy: dict[str, Any] = Field(default_factory=dict)
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
    delivery_options: list[Literal["merge_screenshot", "license_request", "auto_release"]] = Field(
        default_factory=lambda: list(DEFAULT_DELIVERY_OPTIONS), max_length=3
    )
    task_type: Literal["development", "analysis"] = TaskType.DEVELOPMENT.value

    @field_validator("delivery_options")
    @classmethod
    def unique_delivery_options(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class SupplementAnswerInput(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    answer: str = Field(min_length=1, max_length=6000)


class SupplementInput(BaseModel):
    answers: list[SupplementAnswerInput] = Field(min_length=1, max_length=30)


class ContinueRequestInput(BaseModel):
    prompt: str = Field(default="", max_length=6000)


class RunnerProjectSync(BaseModel):
    runner_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,80}$")
    projects: list[ProjectInput] = Field(max_length=200)


class RunnerIntakeRoute(BaseModel):
    runner_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,80}$")
    project_key: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{2,60}$")
    project_keys: list[str] = Field(default_factory=list, min_length=0, max_length=5)
    classification: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    work_item_title: str = Field(default="", max_length=500)
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


def runner_record_online(runner: dict | None, *, now: datetime | None = None) -> bool:
    if not runner or runner.get("state") == "stopping":
        return False
    try:
        checked_at = now or datetime.now(UTC)
        last_seen = datetime.fromisoformat(str(runner.get("last_seen_at") or ""))
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        return (checked_at - last_seen).total_seconds() <= 90
    except (TypeError, ValueError):
        return False


def runner_is_online(runner_id: str) -> bool:
    return runner_record_online(row("SELECT * FROM runners WHERE runner_id=?", (runner_id,)))


def add_runner_display_state(item: dict[str, Any]) -> dict[str, Any]:
    """Expose an offline queue state without changing the persisted workflow status."""
    actual_status = str(item.get("status") or "")
    online = runner_is_online(str(item.get("runner_id") or ""))
    item["runner_online"] = online
    item["display_status"] = "waiting_runner" if actual_status == RunStatus.QUEUED.value and not online else actual_status
    if item["display_status"] == "waiting_runner":
        item["current_activity"] = "执行器当前离线，任务已安全排队，待执行器上线后自动继续"
        item["display_message"] = item["current_activity"]
    return item


def runner_request(request_id: str) -> dict[str, Any]:
    detail = request_detail(request_id)
    if not detail:
        raise HTTPException(status_code=404, detail="任务不存在")
    return detail


def public_engine_text(value: Any) -> Any:
    """Remove implementation-engine branding from every user-facing payload."""
    if not isinstance(value, str):
        return value
    return re.sub(
        r"codex",
        lambda match: "DEVCORE" if match.group(0).isupper() else ("DevCore" if match.group(0)[0].isupper() else "devcore"),
        value,
        flags=re.IGNORECASE,
    )


def public_request_payload(detail: dict[str, Any]) -> dict[str, Any]:
    result = dict(detail)
    result.pop("codex_thread_id", None)
    for key in ("title", "requirement_summary", "result_summary", "error_message"):
        result[key] = public_engine_text(result.get(key))
    result["supplement_requests"] = [
        {
            **item,
            "question": public_engine_text(item.get("question")),
            "reason": public_engine_text(item.get("reason")),
            "suggested_answer": public_engine_text(item.get("suggested_answer")),
        }
        for item in result.get("supplement_requests", [])
        if isinstance(item, dict)
    ]
    result["steps"] = [
        {**step, "name": public_engine_text(step.get("name")), "message": public_engine_text(step.get("message"))}
        for step in result.get("steps", [])
    ]
    result["events"] = [
        {
            **event,
            "event_type": public_engine_text(event.get("event_type")),
            "message": public_engine_text(event.get("message")),
        }
        for event in result.get("events", [])
    ]
    if isinstance(result.get("history_context"), list):
        result["history_context"] = [
            {
                key: item.get(key)
                for key in (
                    "id", "work_item_revision", "task_type", "status", "title",
                    "result_summary", "pr_id", "pr_url", "created_at", "completed_at",
                )
                if item.get(key) not in (None, "")
            }
            for item in result["history_context"]
            if isinstance(item, dict)
        ]
    for key in ("analysis_result", "history_context", "acceptance_ledger", "quality_gate_result"):
        if isinstance(result.get(key), (dict, list)):
            result[key] = public_engine_text_tree(result[key])
    return result


def public_engine_text_tree(value: Any) -> Any:
    if isinstance(value, str):
        return public_engine_text(value)
    if isinstance(value, list):
        return [public_engine_text_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: public_engine_text_tree(item) for key, item in value.items()}
    return value


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


def selectable_notification_users(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        public_user(item)
        for item in rows(
            """SELECT * FROM users
               WHERE active=1 AND (role='pm' OR id=?)
               ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,display_name""",
            (user["id"], user["id"]),
        )
    ]


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


def joint_group_children(joint_group_id: str) -> list[dict[str, Any]]:
    return [
        detail
        for item in rows(
            """SELECT id FROM delivery_requests
               WHERE joint_group_id=? ORDER BY joint_project_index,id""",
            (joint_group_id,),
        )
        if (detail := request_detail(item["id"])) is not None
    ]


def joint_child_summary(child: dict[str, Any]) -> dict[str, Any]:
    """Expose just enough sibling state for the combined task view."""
    public = public_request_payload(add_runner_display_state(dict(child)))
    display_status = public.get("display_status") or public["status"]
    latest_event = public.get("events", [{}])[0] if public.get("events") else {}
    return {
        "id": public["id"],
        "project_id": public["project_id"],
        "project_key": public["project_key"],
        "project_name": public["project_name"],
        "delivery_mode": public["delivery_mode"],
        "delivery_mode_label": DELIVERY_MODE_LABELS[DeliveryMode(public["delivery_mode"])],
        "task_type": public.get("task_type", TaskType.DEVELOPMENT.value),
        "task_type_label": TASK_TYPE_LABELS[TaskType(public.get("task_type", TaskType.DEVELOPMENT.value))],
        "status": public["status"],
        "display_status": display_status,
        "status_label": (
            "等待执行器上线"
            if display_status == "waiting_runner"
            else status_label(public.get("task_type", TaskType.DEVELOPMENT.value), public["status"])
        ),
        "current_step": public.get("current_step"),
        "current_activity": latest_event.get("message") or public.get("result_summary") or "等待执行",
        "result_summary": public.get("result_summary") or "",
        "error_message": public.get("error_message") or "",
        "repository_states": public.get("repository_states") or [],
        "pr_id": public.get("pr_id"),
        "pr_url": public.get("pr_url"),
        "created_at": public.get("created_at"),
        "started_at": public.get("started_at"),
        "completed_at": public.get("completed_at"),
        "updated_at": public.get("updated_at"),
        "joint_project_index": public.get("joint_project_index"),
        "joint_project_count": public.get("joint_project_count"),
    }


def joint_request_detail(joint_group_id: str) -> dict[str, Any]:
    intake = request_intake_detail(joint_group_id)
    children = joint_group_children(joint_group_id)
    if not intake or not children:
        raise HTTPException(status_code=404, detail="联合研发任务不存在")
    detail = dict(children[0])
    artifacts: list[dict[str, Any]] = []
    repository_states: list[dict[str, Any]] = []
    notification_cc: list[str] = []
    for child in children:
        project_name = str(child.get("project_name") or child.get("project_key") or "项目")
        visible = visible_delivery_artifacts(
            child["delivery_mode"], child.get("artifacts", []), child.get("delivery_options")
        )
        for artifact in visible:
            item = dict(artifact)
            item["name"] = f"【{project_name}】{item.get('name') or '交付产物'}"
            item["project_name"] = project_name
            artifacts.append(item)
        for state in child.get("repository_states", []):
            item = dict(state)
            item["name"] = f"{project_name} / {item.get('name') or 'repository'}"
            repository_states.append(item)
        notification_cc.extend(
            value.strip()
            for value in str(child.get("policy_snapshot", {}).get("notification_cc") or "").split(",")
            if value.strip()
        )
    started_values = [child.get("started_at") for child in children if child.get("started_at")]
    completed_values = [child.get("completed_at") for child in children if child.get("completed_at")]
    result_lines = [
        f"【{child['project_name']}】{child.get('result_summary') or child.get('error_message') or child['status']}"
        for child in children
    ]
    policy_snapshot = dict(detail.get("policy_snapshot") or {})
    policy_snapshot["notification_cc"] = ",".join(dict.fromkeys(notification_cc))
    detail.update(
        {
            "id": joint_group_id,
            "joint_group_id": joint_group_id,
            "joint_children": children,
            "joint_project_count": len(children),
            "title": intake.get("title") or detail.get("title"),
            "project_name": " + ".join(child["project_name"] for child in children),
            "status": intake.get("status") or "routed",
            "created_at": intake.get("created_at") or detail.get("created_at"),
            "started_at": min(started_values) if started_values else detail.get("started_at"),
            "completed_at": max(completed_values) if completed_values else intake.get("completed_at"),
            "notification_emails": intake.get("notification_emails") or detail.get("notification_emails"),
            "classification_summary": intake.get("classification_summary") or [],
            "matched_project_keys": intake.get("matched_project_keys") or [],
            "artifacts": artifacts,
            "repository_states": repository_states,
            "result_summary": "\n".join(result_lines),
            "error_message": intake.get("error_message") or "",
            "policy_snapshot": policy_snapshot,
            "task_type": intake.get("task_type") or detail.get("task_type") or TaskType.DEVELOPMENT.value,
        }
    )
    return detail


def send_joint_email(
    joint_group_id: str,
    *,
    action_required: bool = False,
    terminal_status: str | None = None,
) -> bool:
    detail = joint_request_detail(joint_group_id)
    intake = request_intake_detail(joint_group_id) or {}
    marker = "review_email_sent_at" if action_required else "email_sent_at"
    if intake.get(marker):
        return False
    mailer = Mailer()
    subject = mailer.delivery_subject(
        detail, action_required=action_required, terminal_status=terminal_status
    )
    html_body = mailer.delivery_html(
        detail, action_required=action_required, terminal_status=terminal_status
    )
    recipients = request_recipients(detail)
    if not mailer.configured():
        first_request_id = detail["joint_children"][0]["id"]
        preview_name = (
            "joint-review-email-preview.html" if action_required else "joint-delivery-email-preview.html"
        )
        path = ArtifactService().request_dir(first_request_id) / preview_name
        path.write_text(html_body, encoding="utf-8")
        add_artifact(first_request_id, "email_preview", path.name, str(path))
    else:
        attachments = [
            Path(item["local_path"])
            for item in detail["artifacts"]
            if item.get("kind") == "merge_screenshot" and item.get("local_path")
        ]
        mailer.send(to=recipients, subject=subject, html_body=html_body, attachments=attachments)
    with transaction() as conn:
        conn.execute(
            f"UPDATE request_intakes SET {marker}=?,updated_at=? WHERE id=?",
            (utc_now(), utc_now(), joint_group_id),
        )
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
    return templates.TemplateResponse(
        request,
        "login.html",
        {"demo_enabled": settings.seed_demo, "app_version": settings.runner_version},
    )


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
    active_sql = "status NOT IN ('delivered','failed','rejected','cancelled','waiting_input')"
    active_scope = active_sql if not scope else f"requester_id=? AND {active_sql}"
    activity_select = """(SELECT e.message FROM delivery_events e
        WHERE e.request_id=r.id ORDER BY e.id DESC LIMIT 1) current_activity"""
    active_requests = rows(
        f"""SELECT r.*,p.name project_name,p.tfs_collection_url,u.display_name requester_name,{activity_select}
            FROM delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id
            WHERE {active_scope} ORDER BY r.updated_at DESC LIMIT 12""",
        params,
    )
    recent_scope = "" if not scope else "WHERE r.requester_id=?"
    recent_requests = rows(
        f"""SELECT r.*,p.name project_name,p.tfs_collection_url,u.display_name requester_name,{activity_select}
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
        online = runner_is_online(str(intake["runner_id"]))
        display_status = "routing" if online else "waiting_runner"
        if not online:
            activity = "执行器当前离线，任务已安全排队，待执行器上线后自动继续"
        elif intake["status"] == "claimed":
            activity = "执行器正在识别所属项目与交付策略"
        else:
            activity = "任务已提交，等待执行器扫描"
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
                "task_type": intake.get("task_type", TaskType.DEVELOPMENT.value),
                "status": display_status,
                "runner_online": online,
                "delivery_options": intake.get("delivery_options") or list(DEFAULT_DELIVERY_OPTIONS),
                "current_activity": (
                    activity.replace("任务", "分析任务", 1)
                    if intake.get("task_type") == TaskType.ANALYSIS.value else activity
                ),
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
            "task_type": intake.get("task_type", TaskType.DEVELOPMENT.value),
            "status": "failed",
            "delivery_options": intake.get("delivery_options") or list(DEFAULT_DELIVERY_OPTIONS),
            "current_activity": public_engine_text(intake.get("error_message") or "项目识别失败，请查看详情"),
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
        add_runner_display_state(item)
        item.pop("codex_thread_id", None)
        item.pop("analysis_result", None)
        item["current_activity"] = public_engine_text(item.get("current_activity"))
        item["result_summary"] = public_engine_text(item.get("result_summary"))
        item["error_message"] = public_engine_text(item.get("error_message"))
        item["delivery_options"] = (
            None
            if item.get("delivery_options") is None
            else json_value(item.get("delivery_options"), DEFAULT_DELIVERY_OPTIONS)
        )
        item["artifacts"] = visible_delivery_artifacts(
            item["delivery_mode"], artifact_map.get(item["id"], []), item["delivery_options"]
        )
        item["repository_states"] = json_value(item.get("repository_states"), [])
        started_value = item.get("created_at") or item.get("started_at")
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
        item["online"] = runner_record_online(item, now=now)
        runner_detail = json_value(item.get("detail"), {})
        item["devcore_usage"] = runner_detail.get("codex_usage", {})
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
            SUM(CASE WHEN status NOT IN ('delivered','failed','rejected','cancelled','waiting_input') THEN 1 ELSE 0 END) running,
            SUM(CASE WHEN status='waiting_input' THEN 1 ELSE 0 END) waiting_input,
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
    busy_statuses = "'validating','developing','submitting','building','releasing','capturing','delivering'"
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
    stats = {key: int(summary.get(key) or 0) for key in ("total", "today_total", "success", "failed", "running", "waiting_input", "waiting_merge")}
    stats["total"] += pending_total + intake_failed_total
    stats["today_total"] += pending_today + intake_failed_today
    stats["failed"] += intake_failed_total
    stats["running"] += pending_total
    request_counts = {item["status"]: item["count"] for item in counts}
    waiting_runner_total = sum(1 for item in intake_items if item["status"] == "waiting_runner")
    queued_scope = "" if user["role"] == "admin" else "AND requester_id=?"
    queued_params: tuple[Any, ...] = () if user["role"] == "admin" else (user["id"],)
    offline_queued_total = sum(
        1
        for item in rows(
            f"SELECT runner_id FROM delivery_requests WHERE status='queued' {queued_scope}",
            queued_params,
        )
        if not runner_is_online(str(item["runner_id"]))
    )
    request_counts["routing"] = max(0, pending_total - waiting_runner_total)
    request_counts["queued"] = max(0, int(request_counts.get("queued") or 0) - offline_queued_total)
    request_counts["waiting_runner"] = waiting_runner_total + offline_queued_total
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


@app.get("/api/delivery-records")
def delivery_records(
    user: Annotated[dict, Depends(current_user)],
    page: int = 1,
    page_size: int = 10,
    status: str = "",
    task_type: str = "",
    project_key: str = "",
    requester_id: int | None = None,
    keyword: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Return a scoped, filterable delivery ledger without loading the entire history."""
    page = max(1, page)
    page_size = max(5, min(50, page_size))
    status = status.strip()
    task_type = task_type.strip()
    project_key = project_key.strip()
    keyword = keyword.strip()[:120]
    allowed_statuses = {item.value for item in RunStatus}
    if status and status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="交付状态筛选值无效")
    if task_type and task_type not in {item.value for item in TaskType}:
        raise HTTPException(status_code=400, detail="任务类型筛选值无效")

    conditions: list[str] = []
    params: list[Any] = []
    if user["role"] != "admin":
        conditions.append("r.requester_id=?")
        params.append(user["id"])
    elif requester_id is not None:
        conditions.append("r.requester_id=?")
        params.append(requester_id)
    if status:
        conditions.append("r.status=?")
        params.append(status)
    if task_type:
        conditions.append("r.task_type=?")
        params.append(task_type)
    if project_key:
        conditions.append("p.project_key=?")
        params.append(project_key)
    if keyword:
        conditions.append("(CAST(r.work_item_id AS TEXT) LIKE ? OR r.title LIKE ?)")
        search = f"%{keyword}%"
        params.extend((search, search))

    local_timezone = timezone(timedelta(hours=8))

    def day_boundary(value: str, *, following_day: bool = False) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=local_timezone)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期筛选必须使用 YYYY-MM-DD 格式") from exc
        if following_day:
            parsed += timedelta(days=1)
        return parsed.astimezone(UTC).isoformat()

    if date_from:
        conditions.append("r.created_at>=?")
        params.append(day_boundary(date_from))
    if date_to:
        conditions.append("r.created_at<?")
        params.append(day_boundary(date_to, following_day=True))
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    from_clause = "FROM delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id"
    total_row = row(f"SELECT COUNT(*) count {from_clause} {where_clause}", tuple(params)) or {"count": 0}
    total = int(total_row.get("count") or 0)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    activity_select = """(SELECT e.message FROM delivery_events e
        WHERE e.request_id=r.id ORDER BY e.id DESC LIMIT 1) current_activity"""
    items = rows(
        f"""SELECT r.*,p.name project_name,p.project_key,p.tfs_collection_url,
                   u.display_name requester_name,{activity_select}
            {from_clause} {where_clause}
            ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
        (*params, page_size, (page - 1) * page_size),
    )
    request_ids = [item["id"] for item in items]
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

    current_time = datetime.now(UTC)
    terminal_statuses = {item.value for item in TERMINAL_STATUSES}
    for item in items:
        add_runner_display_state(item)
        item.pop("codex_thread_id", None)
        item.pop("analysis_result", None)
        item["current_activity"] = public_engine_text(item.get("current_activity"))
        item["result_summary"] = public_engine_text(item.get("result_summary"))
        item["error_message"] = public_engine_text(item.get("error_message"))
        item["delivery_options"] = (
            None
            if item.get("delivery_options") is None
            else json_value(item.get("delivery_options"), DEFAULT_DELIVERY_OPTIONS)
        )
        item["artifacts"] = visible_delivery_artifacts(
            item["delivery_mode"], artifact_map.get(item["id"], []), item["delivery_options"]
        )
        item["repository_states"] = json_value(item.get("repository_states"), [])
        started_value = item.get("created_at") or item.get("started_at")
        ended_value = item.get("completed_at")
        if not ended_value and item.get("status") in terminal_statuses:
            ended_value = item.get("updated_at")
        try:
            started = datetime.fromisoformat(started_value) if started_value else None
            ended = datetime.fromisoformat(ended_value) if ended_value else current_time
            item["duration_seconds"] = max(0, int((ended - started).total_seconds())) if started else None
        except (TypeError, ValueError):
            item["duration_seconds"] = None
    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


@app.get("/api/admin/analytics")
def admin_analytics(_: Annotated[dict, Depends(admin_user)]) -> dict:
    overview = row(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) delivered,
                  SUM(CASE WHEN status IN ('failed','rejected') THEN 1 ELSE 0 END) failed,
                  SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) cancelled,
                  SUM(CASE WHEN status NOT IN ('delivered','failed','rejected','cancelled') THEN 1 ELSE 0 END) active,
                  AVG(CASE WHEN completed_at IS NOT NULL
                      THEN (julianday(completed_at)-julianday(COALESCE(started_at,created_at)))*86400 END) avg_duration_seconds
           FROM delivery_requests"""
    ) or {}
    total = int(overview.get("total") or 0)
    delivered = int(overview.get("delivered") or 0)
    overview["success_rate"] = round(delivered * 100 / total, 1) if total else 0
    for key in ("total", "delivered", "failed", "cancelled", "active"):
        overview[key] = int(overview.get(key) or 0)
    overview["avg_duration_seconds"] = int(overview.get("avg_duration_seconds") or 0)

    status_distribution = rows(
        "SELECT status name,COUNT(*) value FROM delivery_requests GROUP BY status ORDER BY value DESC"
    )
    mode_distribution = rows(
        "SELECT delivery_mode name,COUNT(*) value FROM delivery_requests GROUP BY delivery_mode ORDER BY value DESC"
    )
    project_distribution = rows(
        """SELECT p.name,p.project_key,COUNT(*) total,
                  SUM(CASE WHEN r.status='delivered' THEN 1 ELSE 0 END) delivered,
                  SUM(CASE WHEN r.status IN ('failed','rejected') THEN 1 ELSE 0 END) failed,
                  AVG(CASE WHEN r.completed_at IS NOT NULL
                      THEN (julianday(r.completed_at)-julianday(COALESCE(r.started_at,r.created_at)))*86400 END) avg_duration_seconds
           FROM delivery_requests r JOIN projects p ON p.id=r.project_id
           GROUP BY p.id,p.name,p.project_key ORDER BY total DESC,p.name LIMIT 20"""
    )
    for item in project_distribution:
        item["total"] = int(item.get("total") or 0)
        item["delivered"] = int(item.get("delivered") or 0)
        item["failed"] = int(item.get("failed") or 0)
        item["avg_duration_seconds"] = int(item.get("avg_duration_seconds") or 0)
    requester_distribution = rows(
        """SELECT u.display_name name,COUNT(*) value,
                  SUM(CASE WHEN r.status='delivered' THEN 1 ELSE 0 END) delivered
           FROM delivery_requests r JOIN users u ON u.id=r.requester_id
           GROUP BY u.id,u.display_name ORDER BY value DESC LIMIT 10"""
    )
    daily_rows = rows(
        """SELECT date(created_at,'+8 hours') day,COUNT(*) created,
                  SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) delivered,
                  SUM(CASE WHEN status IN ('failed','rejected') THEN 1 ELSE 0 END) failed
           FROM delivery_requests
           WHERE datetime(created_at)>=datetime('now','-13 days')
           GROUP BY date(created_at,'+8 hours') ORDER BY day"""
    )
    daily_lookup = {item["day"]: item for item in daily_rows}
    today = datetime.now(timezone(timedelta(hours=8))).date()
    daily_trend = []
    for offset in range(13, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        value = daily_lookup.get(day, {})
        daily_trend.append(
            {
                "day": day,
                "created": int(value.get("created") or 0),
                "delivered": int(value.get("delivered") or 0),
                "failed": int(value.get("failed") or 0),
            }
        )
    return {
        "overview": overview,
        "status_distribution": status_distribution,
        "mode_distribution": mode_distribution,
        "project_distribution": project_distribution,
        "requester_distribution": requester_distribution,
        "daily_trend": daily_trend,
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
        if isinstance(value, (list, dict)):
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
        if isinstance(value, (list, dict)):
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
                if isinstance(value, (list, dict)):
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


@app.get("/api/notification-recipients")
def notification_recipients(user: Annotated[dict, Depends(current_user)]) -> dict:
    """All active project-manager mailboxes selectable by any signed-in initiator."""
    return {"users": selectable_notification_users(user)}


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
    selectable_lookup = {
        email.lower()
        for target in selectable_notification_users(user)
        for email in target.get("emails", [])
    }
    if any(email.lower() not in selectable_lookup for email in selected_emails):
        raise HTTPException(status_code=422, detail="通知邮箱只能从当前账号或已启用项目经理的邮箱中选择")
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
        runner_id = str(runner_rows[0]["runner_id"])
        online = runner_is_online(runner_id)
        try:
            intake_id = create_request_intake(
                user["id"], payload.work_item_id, runner_id, selected_emails,
                payload.delivery_options if payload.task_type == TaskType.DEVELOPMENT.value else [],
                payload.task_type,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="该需求正在识别项目或等待执行") from exc
        return {
            "id": intake_id,
            "status": "routing" if online else "waiting_runner",
            "routing": True,
            "runner_online": online,
            "message": (
                ("分析任务已提交，执行器正在识别项目" if payload.task_type == TaskType.ANALYSIS.value else "任务已提交，执行器正在识别项目")
                if online
                else "执行器当前离线，任务已进入队列；执行器上线后将自动继续"
            ),
        }

    project = row("SELECT * FROM projects WHERE id=? AND enabled=1", (payload.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在或已停用")
    mode = project["delivery_mode"]
    if payload.delivery_mode and payload.delivery_mode.value != mode:
        if user["role"] != "admin" or not project["allow_requirement_override"]:
            raise HTTPException(status_code=403, detail="该项目不允许覆盖交付方式")
        mode = payload.delivery_mode.value
    try:
        request_id = create_delivery_request(
            project, user["id"], payload.work_item_id, mode, selected_emails,
            payload.delivery_options if payload.task_type == TaskType.DEVELOPMENT.value else [],
            task_type=payload.task_type,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该需求已有正在执行的研发任务") from exc
    online = runner_is_online(str(project["runner_id"]))
    return {
        "id": request_id,
        "status": RunStatus.QUEUED.value if online else "waiting_runner",
        "runner_online": online,
        "message": (
            ("问题分析任务已进入队列" if payload.task_type == TaskType.ANALYSIS.value else "研发任务已进入队列")
            if online
            else "执行器当前离线，任务已进入队列；执行器上线后将自动继续"
        ),
    }


@app.get("/api/intakes/{intake_id}")
def get_request_intake(intake_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    intake = request_intake_detail(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="自动识别任务不存在")
    if user["role"] != "admin" and intake["requester_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该自动识别任务")
    children = joint_group_children(intake_id) if intake.get("result_request_ids") else []
    online = runner_is_online(str(intake["runner_id"]))
    intake["runner_online"] = online
    intake["joint"] = len(children) > 1
    intake["children"] = [joint_child_summary(child) for child in children]
    analysis_task = intake.get("task_type") == TaskType.ANALYSIS.value
    if intake["status"] in {"queued", "claimed"}:
        intake["display_status"] = "routing" if online else "waiting_runner"
        intake["display_message"] = (
            f"执行器正在读取 TFS 内容并归类所属{'分析' if analysis_task else '研发'}项目"
            if online
            else "执行器当前离线，任务已安全排队，待执行器上线后自动继续"
        )
    elif intake["status"] == "routed":
        intake["display_status"] = "joint_running" if len(children) > 1 else (
            children[0].get("display_status") if children else "queued"
        )
        intake["display_message"] = (
            f"已识别 {len(children)} 个项目，正在并行完成{'问题分析与报告汇总' if analysis_task else '研发、审核与交付'}"
            if len(children) > 1
            else f"项目已识别，{'问题分析' if analysis_task else '研发'}任务正在执行"
        )
    elif intake["status"] == "finalizing":
        intake["display_status"] = "delivering"
        intake["display_message"] = f"全部项目已完成，正在统一更新 TFS 与发送{'分析' if analysis_task else '交付'}汇总邮件"
    else:
        intake["display_status"] = intake["status"]
        intake["display_message"] = intake.get("error_message") or (
            (
                "多项目联合问题分析已完成" if analysis_task else "多项目联合研发已全部交付"
            ) if intake["status"] == "delivered" else f"联合{'分析' if analysis_task else '研发'}任务已结束"
        )
    return {"intake": intake}


@app.get("/api/requests/{request_id}")
def get_request(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = public_request_payload(add_runner_display_state(can_access_request(user, request_id)))
    display_status = detail.get("display_status") or detail["status"]
    detail["status_label"] = (
        "等待执行器上线"
        if display_status == "waiting_runner"
        else status_label(detail.get("task_type", TaskType.DEVELOPMENT.value), detail["status"])
    )
    detail["task_type_label"] = TASK_TYPE_LABELS[TaskType(detail.get("task_type", TaskType.DEVELOPMENT.value))]
    detail["delivery_mode_label"] = DELIVERY_MODE_LABELS[DeliveryMode(detail["delivery_mode"])]
    joint_group_id = str(detail.get("joint_group_id") or "")
    if joint_group_id:
        intake = request_intake_detail(joint_group_id) or {}
        siblings = joint_group_children(joint_group_id)
        detail["joint_children"] = [joint_child_summary(child) for child in siblings]
        detail["joint_status"] = intake.get("status") or "routed"
        detail["joint_title"] = intake.get("title") or detail.get("title")
        detail["classification_summary"] = intake.get("classification_summary") or []
        if detail["status"] == RunStatus.DELIVERED.value and detail["joint_status"] not in {
            "delivered", "failed", "cancelled"
        }:
            detail["status_label"] = "本项目已完成，等待联合项目"
    return {"request": detail}


@app.post("/api/requests/{request_id}/codex-watch/start", include_in_schema=False)
@app.post("/api/requests/{request_id}/devcore-watch/start")
def start_devcore_watch(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = can_access_request(user, request_id)
    if detail["status"] != RunStatus.DEVELOPING.value:
        phase = "分析" if detail.get("task_type") == TaskType.ANALYSIS.value else "研发"
        raise HTTPException(status_code=409, detail=f"DevCore 当前未处于{phase}执行阶段")
    watcher_id, cursor = live_codex_streams.start(request_id)
    return {
        "watcher_id": watcher_id,
        "cursor": cursor,
        "ephemeral": True,
        "message": "仅传输打开窗口后的输出；关闭即停止采集且不保存",
    }


@app.get("/api/requests/{request_id}/codex-watch/{watcher_id}", include_in_schema=False)
@app.get("/api/requests/{request_id}/devcore-watch/{watcher_id}")
def poll_devcore_watch(
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


@app.post("/api/requests/{request_id}/codex-watch/{watcher_id}/stop", include_in_schema=False)
@app.post("/api/requests/{request_id}/devcore-watch/{watcher_id}/stop")
def stop_devcore_watch(
    request_id: str,
    watcher_id: str,
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    can_access_request(user, request_id)
    live_codex_streams.stop(request_id, watcher_id)
    return {"ok": True}


@app.post("/api/requests/{request_id}/retry")
def retry_request(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    original = can_access_request(user, request_id)
    if original.get("joint_group_id"):
        raise HTTPException(status_code=409, detail="联合研发任务需要按原 TFS 编号整体重新发起，不能只重试其中一个项目")
    if original["status"] != RunStatus.FAILED.value:
        raise HTTPException(status_code=409, detail="只有执行失败的任务可以重新发起")
    project = row("SELECT * FROM projects WHERE id=? AND enabled=1", (original["project_id"],))
    if not project:
        raise HTTPException(status_code=409, detail="原任务所属项目已停用，暂时无法重新发起")
    try:
        new_request_id = create_delivery_request(
            project,
            original["requester_id"],
            original["work_item_id"],
            original["delivery_mode"],
            original.get("notification_emails") or [original["requester_email"]],
            original.get("delivery_options"),
            task_type=original.get("task_type", TaskType.DEVELOPMENT.value),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该需求已有正在执行的研发任务") from exc
    add_event(request_id, "request.retried", f"{user['display_name']} 重新发起了任务，新任务 {new_request_id[:8].upper()}")
    add_event(new_request_id, "request.retry_created", f"由失败任务 {request_id[:8].upper()} 重新发起")
    with transaction() as conn:
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (
                user["id"], "request.retry", "delivery_request", new_request_id,
                json.dumps({"source_request_id": request_id}, ensure_ascii=False), utc_now(),
            ),
        )
    return {"id": new_request_id, "status": RunStatus.QUEUED.value, "work_item_id": original["work_item_id"]}


@app.post("/api/requests/{request_id}/supplement")
def supplement_request(
    request_id: str,
    payload: SupplementInput,
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    detail = can_access_request(user, request_id)
    if detail["status"] != RunStatus.WAITING_INPUT.value:
        raise HTTPException(status_code=409, detail="当前任务不处于待补充状态")
    expected = {
        str(item.get("id") or ""): item
        for item in detail.get("supplement_requests", [])
        if isinstance(item, dict) and item.get("id")
    }
    provided = {item.id: item.answer.strip() for item in payload.answers if item.answer.strip()}
    unknown = sorted(set(provided) - set(expected))
    if unknown:
        raise HTTPException(status_code=422, detail=f"包含未知补充项：{', '.join(unknown)}")
    missing = [
        item_id
        for item_id, item in expected.items()
        if item.get("required", True) and not provided.get(item_id)
    ]
    if missing:
        questions = "、".join(str(expected[item_id].get("question") or item_id) for item_id in missing)
        raise HTTPException(status_code=422, detail=f"请完成必填补充项：{questions}")
    answers = [{"id": item_id, "answer": answer} for item_id, answer in provided.items()]
    now = utc_now()
    update_request(
        request_id,
        supplement_answers=answers,
        supplemented_at=now,
        status=RunStatus.QUEUED.value,
        current_step="validate",
        progress=4,
        completed_at=None,
        next_poll_at=None,
        error_message="",
    )
    update_step(request_id, "clarify", "completed", f"{user['display_name']} 已补充 {len(answers)} 项信息，任务重新进入研发队列")
    add_event(
        request_id,
        "development.input_supplied",
        f"{user['display_name']} 已提交补充信息，任务将继续研发",
        metadata={"answer_ids": list(provided)},
    )
    with transaction() as conn:
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (
                user["id"], "request.supplement", "delivery_request", request_id,
                json.dumps({"answer_ids": list(provided)}, ensure_ascii=False), now,
            ),
        )
    return {"id": request_id, "status": RunStatus.QUEUED.value}


@app.post("/api/requests/{request_id}/continue")
def continue_waiting_approval_request(
    request_id: str,
    payload: ContinueRequestInput,
    user: Annotated[dict, Depends(admin_user)],
) -> dict:
    """Resume the same workspace and DevCore session after an administrator reviews a blocker."""
    detail = can_access_request(user, request_id)
    if detail["status"] != RunStatus.WAITING_APPROVAL.value:
        raise HTTPException(status_code=409, detail="只有等待人工确认的任务可以继续执行")
    if not detail.get("codex_thread_id") or not detail.get("repository_states"):
        raise HTTPException(status_code=409, detail="当前任务没有可恢复的 DevCore 会话或隔离工作区")

    prompt = payload.prompt.strip() or (
        "请基于现有代码和验证结果继续解决当前阻塞风险。对于需求未明确要求的验证形式，"
        "使用等价、可复核的自动化证据，不要仅因工具不可用再次阻断。"
    )
    continuation_id = f"admin-continue-{uuid.uuid4().hex[:12]}"
    continuation_request = {
        "id": continuation_id,
        "question": "管理员继续执行指示",
        "reason": str(detail.get("error_message") or "任务在研发风险门禁处暂停"),
        "suggested_answer": "结合当前阻塞原因继续修改、自检，并重新提交结构化研发结论。",
        "required": True,
    }
    supplement_requests = [
        item for item in detail.get("supplement_requests") or [] if isinstance(item, dict)
    ]
    supplement_answers = [
        item for item in detail.get("supplement_answers") or [] if isinstance(item, dict)
    ]
    supplement_requests.append(continuation_request)
    supplement_answers.append({"id": continuation_id, "answer": prompt})

    # An administrator-initiated continuation explicitly adopts the latest synced
    # project policy. This lets corrected machine gates apply to an already paused task.
    latest_project = row("SELECT * FROM projects WHERE id=? AND enabled=1", (detail["project_id"],))
    if not latest_project:
        raise HTTPException(status_code=409, detail="任务所属项目已停用，无法继续执行")
    latest_policy = project_for_api(latest_project)
    now = utc_now()
    update_request(
        request_id,
        policy_snapshot=latest_policy,
        supplement_requests=supplement_requests,
        supplement_answers=supplement_answers,
        supplemented_at=now,
        status=RunStatus.QUEUED.value,
        current_step="validate",
        progress=4,
        completed_at=None,
        next_poll_at=None,
        error_message="",
    )
    update_step(request_id, "develop", "pending", f"{user['display_name']} 已要求继续尝试解决阻塞风险")
    add_event(
        request_id,
        "development.admin_continued",
        f"{user['display_name']} 已确认继续执行，任务将复用原隔离工作区和 DevCore 会话",
        metadata={"continuation_id": continuation_id, "policy_refreshed": True},
    )
    with transaction() as conn:
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (
                user["id"], "request.continue", "delivery_request", request_id,
                json.dumps(
                    {
                        "continuation_id": continuation_id,
                        "prompt": prompt,
                        "previous_error": detail.get("error_message") or "",
                        "policy_refreshed": True,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
    return {"id": request_id, "status": RunStatus.QUEUED.value}


@app.post("/api/requests/{request_id}/cancel")
def cancel_request(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = can_access_request(user, request_id)
    joint_group_id = str(detail.get("joint_group_id") or "")
    if joint_group_id:
        intake = request_intake_detail(joint_group_id) or {}
        if intake.get("status") in {"delivered", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="联合研发任务已经结束")
        completed_at = utc_now()
        children = joint_group_children(joint_group_id)
        for child in children:
            if child["status"] in {"delivered", "failed", "rejected", "cancelled"}:
                continue
            update_request(child["id"], status=RunStatus.CANCELLED.value, completed_at=completed_at)
            add_event(
                child["id"],
                "joint.request_cancelled",
                f"{user['display_name']} 取消了整个联合研发任务",
                level="warning",
                metadata={"joint_group_id": joint_group_id},
            )
        with transaction() as conn:
            conn.execute(
                """UPDATE request_intakes SET status='cancelled',error_message=?,completed_at=?,updated_at=?
                   WHERE id=?""",
                (f"{user['display_name']} 取消了联合研发任务", completed_at, completed_at, joint_group_id),
            )
        try:
            send_joint_email(joint_group_id, terminal_status="cancelled")
        except Exception as exc:
            add_event(request_id, "mail.terminal_failed", f"联合任务取消通知邮件发送失败：{str(exc)[:1000]}", level="error")
        return {"ok": True, "joint": True}
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
        request_ids = intake.get("result_request_ids") or (
            [intake["result_request_id"]] if intake.get("result_request_id") else []
        )
        return {"ok": True, "request_id": intake.get("result_request_id"), "request_ids": request_ids}
    if intake["status"] != "claimed":
        raise HTTPException(status_code=409, detail="自动识别任务当前不可提交结果")

    project_keys = list(dict.fromkeys(payload.project_keys or ([payload.project_key] if payload.project_key else [])))
    if not project_keys:
        message = payload.error_message or "未找到符合准入范围的自动研发项目"
        with transaction() as conn:
            conn.execute(
                "UPDATE request_intakes SET status='failed',error_message=?,updated_at=? WHERE id=?",
                (message, utc_now(), intake_id),
            )
        return {"ok": True, "request_id": None, "request_ids": []}

    placeholders = ",".join("?" for _ in project_keys)
    project_rows = rows(
        f"SELECT * FROM projects WHERE project_key IN ({placeholders}) AND runner_id=? AND enabled=1",
        (*project_keys, payload.runner_id),
    )
    project_lookup = {item["project_key"]: item for item in project_rows}
    missing_keys = [key for key in project_keys if key not in project_lookup]
    if missing_keys:
        message = f"本机识别到项目 {'、'.join(missing_keys)}，但云端目录不存在或已停用"
        with transaction() as conn:
            conn.execute(
                "UPDATE request_intakes SET status='failed',error_message=?,updated_at=? WHERE id=?",
                (message, utc_now(), intake_id),
            )
        return {"ok": True, "request_id": None, "request_ids": []}
    selected_projects = [project_lookup[key] for key in project_keys]
    classification = [
        item
        for item in payload.classification
        if str(item.get("project_key") or "") in project_lookup
    ]
    try:
        if len(selected_projects) == 1:
            project = selected_projects[0]
            request_ids = [
                create_delivery_request(
                    project,
                    intake["requester_id"],
                    intake["work_item_id"],
                    project["delivery_mode"],
                    intake["notification_emails"],
                    intake.get("delivery_options"),
                    task_type=intake.get("task_type", TaskType.DEVELOPMENT.value),
                )
            ]
        else:
            request_ids = create_joint_delivery_requests(
                selected_projects,
                intake["requester_id"],
                intake["work_item_id"],
                intake["notification_emails"],
                intake.get("delivery_options"),
                intake_id,
                classification,
                intake.get("task_type", TaskType.DEVELOPMENT.value),
            )
    except sqlite3.IntegrityError:
        message = "该需求已有正在执行的研发任务"
        with transaction() as conn:
            conn.execute(
                "UPDATE request_intakes SET status='failed',error_message=?,updated_at=? WHERE id=?",
                (message, utc_now(), intake_id),
            )
        return {"ok": True, "request_id": None, "request_ids": []}
    with transaction() as conn:
        conn.execute(
            """UPDATE request_intakes SET status='routed',result_request_id=?,result_request_ids=?,
                   matched_project_keys=?,classification_summary=?,title=?,error_message='',updated_at=?
               WHERE id=?""",
            (
                request_ids[0],
                json.dumps(request_ids, ensure_ascii=False),
                json.dumps(project_keys, ensure_ascii=False),
                json.dumps(classification, ensure_ascii=False),
                payload.work_item_title,
                utc_now(),
                intake_id,
            ),
        )
    return {
        "ok": True,
        "request_id": request_ids[0],
        "request_ids": request_ids,
        "joint": len(request_ids) > 1,
    }


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
        """SELECT r.id,r.work_item_id,r.title,r.status,r.current_step,r.delivery_mode,r.task_type,
                  r.joint_group_id,r.joint_project_index,r.joint_project_count,
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


@app.get("/api/runner/request-history", dependencies=[Depends(runner_auth)])
def runner_request_history(
    project_key: str,
    work_item_id: int,
    request_id: str = "",
    limit: int = 8,
) -> dict:
    if not row("SELECT 1 FROM projects WHERE project_key=?", (project_key,)):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {
        "history": prior_request_history(
            project_key,
            work_item_id,
            exclude_request_id=request_id,
            limit=limit,
        )
    }


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
    "supplement_requests", "supplement_answers", "supplement_requested_at", "supplemented_at",
    "analysis_result", "history_context", "acceptance_ledger", "quality_gate_result",
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
                    if size >= max_bytes:
                        raise HTTPException(status_code=413, detail=f"单个产物必须小于 {settings.max_artifact_mb} MB")
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
    detail = runner_request(request_id)
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
            "已发送待补充信息邮件"
            if payload.action_required and detail["status"] == RunStatus.WAITING_INPUT.value
            else ("已发送 PR 待审核邮件" if payload.action_required else "已发送最终交付邮件")
        )
        add_event(request_id, event_type, message, level="warning" if payload.terminal else "info")
    return {"ok": True, "sent": sent}


@app.post("/api/runner/requests/{request_id}/joint-review-notify", dependencies=[Depends(runner_auth)])
def runner_notify_joint_review(request_id: str) -> dict:
    child = runner_request(request_id)
    joint_group_id = str(child.get("joint_group_id") or "")
    if not joint_group_id:
        return {"ok": True, "joint": False, "sent": False}
    children = joint_group_children(joint_group_id)
    product_review_children = [
        item
        for item in children
        if item.get("delivery_mode") == DeliveryMode.PRODUCT_MANUAL_REVIEW.value
    ]
    ready_statuses = {"waiting_merge", "delivered"}
    if not product_review_children or any(
        item["status"] not in ready_statuses for item in product_review_children
    ):
        return {
            "ok": True,
            "joint": True,
            "sent": False,
            "waiting_request_ids": [
                item["id"] for item in product_review_children if item["status"] not in ready_statuses
            ],
        }
    sent = send_joint_email(joint_group_id, action_required=True)
    if sent:
        for item in children:
            add_event(
                item["id"],
                "joint.mail.action_required",
                f"已统一发送 {len(product_review_children)} 个产品审核项目的 PR 待合并邮件",
                metadata={"joint_group_id": joint_group_id},
            )
    return {"ok": True, "joint": True, "sent": sent}


@app.post("/api/runner/requests/{request_id}/joint-finalize", dependencies=[Depends(runner_auth)])
def runner_finalize_joint(request_id: str) -> dict:
    child = runner_request(request_id)
    joint_group_id = str(child.get("joint_group_id") or "")
    if not joint_group_id:
        return {"ok": True, "joint": False, "finalized": False}
    children = joint_group_children(joint_group_id)
    terminal_statuses = {"delivered", "failed", "rejected", "cancelled"}
    pending = [item for item in children if item["status"] not in terminal_statuses]
    if pending:
        return {
            "ok": True,
            "joint": True,
            "finalized": False,
            "pending_request_ids": [item["id"] for item in pending],
        }
    with transaction() as conn:
        claimed = conn.execute(
            """UPDATE request_intakes SET status='finalizing',updated_at=?
               WHERE id=? AND status='routed'""",
            (utc_now(), joint_group_id),
        ).rowcount
    if not claimed:
        intake = request_intake_detail(joint_group_id) or {}
        return {
            "ok": True,
            "joint": True,
            "finalized": intake.get("status") in {"delivered", "failed"},
            "status": intake.get("status"),
        }

    failed_children = [item for item in children if item["status"] != "delivered"]
    completed_at = utc_now()
    if failed_children:
        reason = "；".join(
            f"{item['project_name']}：{item.get('error_message') or item['status']}"
            for item in failed_children
        )[:3000]
        with transaction() as conn:
            conn.execute(
                """UPDATE request_intakes SET status='failed',error_message=?,completed_at=?,updated_at=?
                   WHERE id=?""",
                (reason, completed_at, completed_at, joint_group_id),
            )
        for item in children:
            add_event(
                item["id"],
                "joint.delivery_failed",
                f"联合研发终止：{reason}",
                level="error",
                metadata={"joint_group_id": joint_group_id},
            )
        try:
            send_joint_email(joint_group_id, terminal_status="failed")
        except Exception as exc:
            add_event(request_id, "mail.terminal_failed", f"联合研发终态邮件发送失败：{str(exc)[:1000]}", level="error")
        return {"ok": True, "joint": True, "finalized": True, "status": "failed"}

    aggregate = joint_request_detail(joint_group_id)
    try:
        if aggregate.get("task_type") == TaskType.ANALYSIS.value:
            report_rows = []
            for item in aggregate.get("joint_children") or []:
                result = item.get("analysis_result") or {}
                report = next(
                    (artifact for artifact in item.get("artifacts", []) if artifact.get("kind") == "analysis_report"),
                    None,
                )
                report_url = ArtifactService().artifact_url(report) if report else settings.public_base_url
                report_rows.append(
                    f"<li><strong>{html.escape(str(item.get('project_name') or '项目'))}：</strong>"
                    f"{html.escape(str(result.get('summary') or item.get('result_summary') or '分析完成'))} "
                    f'<a href="{html.escape(str(report_url), quote=True)}">下载报告</a></li>'
                )
            manifest = "<div><p><strong>AutoDev 联合问题分析完成</strong></p><ul>" + "".join(report_rows) + "</ul></div>"
            TfsClient(aggregate["policy_snapshot"]["tfs_collection_url"]).update_delivery_artifacts(
                aggregate["work_item_id"], manifest
            )
            tfs_result = {"state": "保持不变", "task_type": TaskType.ANALYSIS.value}
        else:
            manifest = ArtifactService().delivery_manifest_html(aggregate)
            tfs_result = TfsClient(aggregate["policy_snapshot"]["tfs_collection_url"]).complete_delivery(
                aggregate["work_item_id"], manifest, actual_version="V1.0"
            )
    except Exception as exc:
        message = f"联合交付汇总写入 TFS 失败：{str(exc)[:2500]}"
        with transaction() as conn:
            conn.execute(
                """UPDATE request_intakes SET status='failed',error_message=?,completed_at=?,updated_at=?
                   WHERE id=?""",
                (message, completed_at, completed_at, joint_group_id),
            )
        add_event(request_id, "joint.tfs_failed", message, level="error")
        try:
            send_joint_email(joint_group_id, terminal_status="failed")
        except Exception:
            pass
        return {"ok": True, "joint": True, "finalized": True, "status": "failed", "error": message}

    with transaction() as conn:
        conn.execute(
            """UPDATE request_intakes SET status='delivered',error_message='',completed_at=?,updated_at=?
               WHERE id=?""",
            (completed_at, completed_at, joint_group_id),
        )
    for item in children:
        add_event(
            item["id"],
            "joint.delivery_completed",
            (
                "全部联合项目问题分析均已完成，分析报告已统一写入 TFS"
                if aggregate.get("task_type") == TaskType.ANALYSIS.value
                else "全部联合项目均已交付，TFS 状态和交付产物已统一更新"
            ),
            metadata={"joint_group_id": joint_group_id, "tfs": tfs_result},
        )
    try:
        send_joint_email(joint_group_id)
    except Exception as exc:
        add_event(request_id, "mail.delivery_failed", f"联合研发最终邮件发送失败：{str(exc)[:1000]}", level="error")
    return {"ok": True, "joint": True, "finalized": True, "status": "delivered"}


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
    return {"template": "compact-wide", "card_width": 860, "brand_mark": "autodev-email-mark.png"}


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
