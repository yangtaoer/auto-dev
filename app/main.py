from __future__ import annotations

import json
import hmac
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
    add_artifact,
    add_event,
    create_session,
    delete_session,
    get_session_user,
    init_db,
    project_for_api,
    public_user,
    request_detail,
    row,
    rows,
    transaction,
    update_request,
    update_step,
    utc_now,
)
from .domain import DELIVERY_MODE_LABELS, DeliveryMode, STATUS_LABELS, RunStatus
from .orchestrator import worker
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
    version="0.2.9",
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
    email: str
    password: str = Field(min_length=8, max_length=200)
    role: str = Field(pattern=r"^(admin|pm)$")


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
    allowed_work_item_types: list[str] = ["用户情景"]
    allowed_states: list[str] = ["已评审"]
    repository_path: str = ""
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
    project_id: int
    work_item_id: int = Field(gt=0)
    delivery_mode: DeliveryMode | None = None


class RunnerIdentity(BaseModel):
    runner_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{2,80}$")


class RunnerHeartbeat(RunnerIdentity):
    hostname: str = Field(default="", max_length=200)
    version: str = Field(default="", max_length=80)
    state: str = Field(default="idle", max_length=40)
    current_request_id: str | None = None


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


def send_cloud_email(request_id: str, *, action_required: bool) -> None:
    detail = runner_request(request_id)
    project = detail["policy_snapshot"]
    recipients = [detail["requester_email"]]
    recipients.extend(email.strip() for email in project.get("notification_cc", "").split(",") if email.strip())
    subject_prefix = "[待审核]" if action_required else "[已交付]"
    subject = f"{subject_prefix} #{detail['work_item_id']} {detail.get('title', '')}"
    mailer = Mailer()
    html_body = mailer.delivery_html(detail, action_required=action_required)
    if not mailer.configured():
        artifacts = ArtifactService()
        name = "review-email-preview.html" if action_required else "delivery-email-preview.html"
        path = artifacts.request_dir(request_id) / name
        path.write_text(html_body, encoding="utf-8")
        add_artifact(request_id, "email_preview", name, str(path))
        add_event(request_id, "mail.preview", "SMTP 尚未配置，云端已生成邮件预览", level="warning")
        return
    attachments = [
        Path(item["local_path"])
        for item in detail["artifacts"]
        if item["kind"] == "merge_screenshot" and item["local_path"]
    ]
    mailer.send(to=recipients, subject=subject, html_body=html_body, attachments=attachments)
    if not action_required:
        update_request(request_id, email_sent_at=utc_now())


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
    return templates.TemplateResponse(request, "index.html", {"user": public_user(user)})


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
    active = rows(
        f"""SELECT r.*,p.name project_name,u.display_name requester_name
            FROM delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id
            WHERE {active_scope} ORDER BY r.updated_at DESC LIMIT 12""",
        params,
    )
    recent_scope = "" if not scope else "WHERE r.requester_id=?"
    recent = rows(
        f"""SELECT r.*,p.name project_name,u.display_name requester_name
            FROM delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id
            {recent_scope} ORDER BY r.created_at DESC LIMIT 40""",
        params,
    )
    runners = rows("SELECT * FROM runners ORDER BY runner_id")
    now = datetime.now(UTC)
    for item in runners:
        try:
            age = (now - datetime.fromisoformat(item["last_seen_at"])).total_seconds()
        except (TypeError, ValueError):
            age = 999999
        item["online"] = age <= 90 and item["state"] != "stopping"
    return {
        "counts": {item["status"]: item["count"] for item in counts},
        "active": active,
        "recent": recent,
        "runners": runners,
    }


@app.get("/api/projects")
def list_projects(user: Annotated[dict, Depends(current_user)]) -> dict:
    query = "SELECT * FROM projects ORDER BY enabled DESC,name" if user["role"] == "admin" else "SELECT * FROM projects WHERE enabled=1 ORDER BY name"
    return {"projects": [project_for_api(item) for item in rows(query)]}


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


@app.post("/api/users")
def create_user(payload: UserInput, user: Annotated[dict, Depends(admin_user)]) -> dict:
    with transaction() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users(username,display_name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                (payload.username, payload.display_name, payload.email, hash_password(payload.password), payload.role, utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
    created = row("SELECT * FROM users WHERE id=?", (cursor.lastrowid,))
    return {"user": public_user(created)}


@app.get("/api/users")
def list_users(_: Annotated[dict, Depends(admin_user)]) -> dict:
    return {"users": [public_user(item) for item in rows("SELECT * FROM users ORDER BY role,display_name")]}


@app.post("/api/requests")
def submit_request(payload: DeliveryRequestInput, user: Annotated[dict, Depends(current_user)]) -> dict:
    project = row("SELECT * FROM projects WHERE id=? AND enabled=1", (payload.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在或已停用")
    mode = project["delivery_mode"]
    if payload.delivery_mode and payload.delivery_mode.value != mode:
        if user["role"] != "admin" or not project["allow_requirement_override"]:
            raise HTTPException(status_code=403, detail="该项目不允许覆盖交付方式")
        mode = payload.delivery_mode.value
    try:
        request_id = create_delivery_request(project, user["id"], payload.work_item_id, mode)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该需求已有正在执行的研发任务") from exc
    return {"id": request_id, "status": RunStatus.QUEUED.value}


@app.get("/api/requests/{request_id}")
def get_request(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = can_access_request(user, request_id)
    detail["status_label"] = STATUS_LABELS.get(RunStatus(detail["status"]), detail["status"])
    detail["delivery_mode_label"] = DELIVERY_MODE_LABELS[DeliveryMode(detail["delivery_mode"])]
    return {"request": detail}


@app.post("/api/requests/{request_id}/cancel")
def cancel_request(request_id: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    detail = can_access_request(user, request_id)
    if detail["status"] in {"delivered", "failed", "rejected", "cancelled"}:
        raise HTTPException(status_code=409, detail="任务已经结束")
    update_request(request_id, status=RunStatus.CANCELLED.value, completed_at=utc_now())
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
               state=excluded.state,current_request_id=excluded.current_request_id,last_seen_at=excluded.last_seen_at""",
            (
                payload.runner_id,
                payload.hostname,
                payload.version,
                payload.state,
                payload.current_request_id,
                now,
                "{}",
            ),
        )
    return {"ok": True, "server_time": now}


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
    item = row(
        """SELECT id FROM delivery_requests WHERE runner_id=? AND status='waiting_merge'
           AND (next_poll_at IS NULL OR next_poll_at<=?) ORDER BY updated_at LIMIT 1""",
        (runner_id, utc_now()),
    )
    return {"request": request_detail(item["id"]) if item else None}


@app.get("/api/runner/requests/{request_id}", dependencies=[Depends(runner_auth)])
def runner_get_request(request_id: str) -> dict:
    return {"request": runner_request(request_id)}


RUNNER_MUTABLE_FIELDS = {
    "work_item_revision", "title", "requirement_summary", "status", "current_step", "progress",
    "branch_name", "base_commit", "commit_hash", "pr_id", "pr_url", "merge_commit", "codex_thread_id",
    "result_summary", "error_message", "started_at", "completed_at", "next_poll_at", "email_sent_at",
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
    send_cloud_email(request_id, action_required=payload.action_required)
    add_event(
        request_id,
        "mail.action_required" if payload.action_required else "mail.delivery_sent",
        "已发送 PR 待审核邮件" if payload.action_required else "已发送最终交付邮件",
    )
    return {"ok": True}


@app.get("/api/artifacts/{artifact_id}")
def download_artifact(artifact_id: int, user: Annotated[dict, Depends(current_user)]):
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
    return FileResponse(path, filename=artifact["name"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
