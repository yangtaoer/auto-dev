"""Queue, publish and notify a business logic diagram for a delivered request."""
from __future__ import annotations

import html
import json
import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import db
from ..config import settings
from . import model_settings
from .command_logs import bounded_log
from .delivery import Mailer
from .workshop_images import normalize_image


ARTIFACT_KIND = "business_logic_diagram"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_CID = "autodev-business-logic"


def _lookup(conn, request_id: str):
    return conn.execute("SELECT * FROM business_logic_diagrams WHERE request_id=?", (request_id,)).fetchone()


def _public(job) -> dict | None:
    if not job:
        return None
    return {key: job[key] for key in (
        "request_id", "status", "progress", "explanation", "artifact_id",
        "error_message", "mail_status", "mail_error", "updated_at",
    )}


def _stale_before() -> str:
    return (datetime.now(UTC) - timedelta(minutes=5)).isoformat()


def status(request_id: str) -> dict | None:
    job = db.row("SELECT * FROM business_logic_diagrams WHERE request_id=?", (request_id,))
    if (job and (job["status"] == "running" or job["mail_status"] in {"claimed", "sending"})
            and job["updated_at"] < _stale_before()):
        with db.transaction() as conn:
            _expire(conn, job["runner_id"])
            job = _lookup(conn, request_id)
    return _public(job)


def eligible(request: dict) -> bool:
    return (request.get("status") == "delivered" and request.get("task_type") == "development"
            and not request.get("routing_superseded_by"))


def _event(conn, request_id: str, event_type: str, message: str, *, warning: bool = False):
    conn.execute(
        "INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)",
        (request_id, "warning" if warning else "info", event_type, message, db.utc_now()),
    )


def _expire(conn, runner_id: str):
    cutoff = _stale_before()
    now = db.utc_now()
    conn.execute("""UPDATE business_logic_diagrams SET status='failed',error_message=?,updated_at=?
                    WHERE runner_id=? AND status='running' AND updated_at<?""",
                 ("执行器连接中断，可以重新生成。", now, runner_id, cutoff))
    conn.execute("""UPDATE business_logic_diagrams SET mail_status='queued',mail_token='',updated_at=?
                    WHERE runner_id=? AND mail_status='claimed' AND updated_at<?""", (now, runner_id, cutoff))
    conn.execute("""UPDATE business_logic_diagrams SET mail_status='uncertain',mail_error=?,updated_at=?
                    WHERE runner_id=? AND mail_status='sending' AND updated_at<?""",
                 ("邮件服务器未确认投递结果，请核对后重发。", now, runner_id, cutoff))


def queue(request_id: str, actor: dict, *, retry_mail=False) -> dict:
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        request = conn.execute("SELECT * FROM delivery_requests WHERE id=?", (request_id,)).fetchone()
        if not request:
            raise LookupError("任务不存在")
        if actor["role"] != "admin" and actor["id"] not in {request["requester_id"], request["acceptance_owner_id"]}:
            raise PermissionError("无权为该任务生成业务逻辑图")
        if not eligible(dict(request)):
            raise ValueError("仅已交付的研发需求支持生成业务逻辑图")
        _expire(conn, request["runner_id"])
        job = _lookup(conn, request_id)
        now = db.utc_now()
        if retry_mail:
            if not job or job["status"] != "completed":
                raise ValueError("业务逻辑图尚未生成")
            if job["mail_status"] not in {"failed", "uncertain"}:
                return _public(job)
            conn.execute("""UPDATE business_logic_diagrams SET mail_status='queued',mail_error='',mail_token='',updated_at=?
                            WHERE request_id=?""", (now, request_id))
            action = "business_logic.retry_mail"
        elif not job:
            conn.execute("""INSERT INTO business_logic_diagrams(request_id,actor_id,runner_id,created_at,updated_at)
                            VALUES(?,?,?,?,?)""", (request_id, actor["id"], request["runner_id"], now, now))
            action = "business_logic.queued"
        elif job["status"] == "failed":
            conn.execute("""UPDATE business_logic_diagrams SET status='queued',actor_id=?,runner_id=?,claim_token='',
                            progress='',error_message='',updated_at=? WHERE request_id=?""",
                         (actor["id"], request["runner_id"], now, request_id))
            action = "business_logic.queued"
        else:
            return _public(job)
        conn.execute("""INSERT INTO audit_logs(actor_id,action,target_type,target_id,created_at)
                        VALUES(?,?,'delivery_request',?,?)""", (actor["id"], action, request_id, now))
        return _public(_lookup(conn, request_id))


def claim(runner_id: str) -> dict | None:
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire(conn, runner_id)
        job = conn.execute("""SELECT d.* FROM business_logic_diagrams d JOIN delivery_requests r ON r.id=d.request_id
            WHERE d.runner_id=? AND r.status='delivered' AND r.task_type='development'
              AND COALESCE(r.routing_superseded_by,'')='' AND (d.status='queued' OR d.mail_status='queued')
            ORDER BY (d.mail_status='queued') DESC,d.updated_at,d.request_id LIMIT 1""", (runner_id,)).fetchone()
        if not job:
            return None
        token, now = str(uuid.uuid4()), db.utc_now()
        result = {"request_id": job["request_id"], "claim_token": token}
        if job["mail_status"] == "queued":
            conn.execute("""UPDATE business_logic_diagrams SET mail_status='claimed',mail_token=?,updated_at=?
                            WHERE request_id=?""", (token, now, job["request_id"]))
            return {**result, "action": "mail"}
        conn.execute("""UPDATE business_logic_diagrams SET status='running',claim_token=?,progress=?,updated_at=?
                        WHERE request_id=?""", (token, "正在准备已交付的需求资料", now, job["request_id"]))
        return {**result, "action": "generate", "model_config": model_settings.current()}


def _claimed(conn, request_id: str, runner_id: str, token: str, *, mail=False):
    job = _lookup(conn, request_id)
    if not job:
        raise LookupError("业务逻辑图请求不存在")
    if not token or job["runner_id"] != runner_id or job["mail_token" if mail else "claim_token"] != token:
        raise PermissionError("请求不属于当前执行器领取的会话")
    return job


def generation_update(request_id: str, runner_id: str, token: str, *, progress="", error_message="") -> dict:
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = _claimed(conn, request_id, runner_id, token)
        if job["status"] != "running":
            return _public(job)
        conn.execute("""UPDATE business_logic_diagrams SET status=?,progress=?,error_message=?,updated_at=?
                        WHERE request_id=?""",
                     ("failed" if error_message else "running", bounded_log(progress, 500),
                      bounded_log(error_message, 2000), db.utc_now(), request_id))
        return _public(_lookup(conn, request_id))


def complete(request_id: str, runner_id: str, token: str, explanation: str, raw: bytes) -> dict:
    explanation = bounded_log(explanation.strip(), 1800)
    if not explanation or not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("业务说明与有效的图片均须提供")
    image, _, _, _ = normalize_image(raw, output_format="PNG")
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError("图片文件超过邮件与交付容量限制")
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = _claimed(conn, request_id, runner_id, token)
        if job["status"] == "completed":
            return _public(job)
        request = conn.execute("SELECT * FROM delivery_requests WHERE id=?", (request_id,)).fetchone()
        if job["status"] != "running" or not request or not eligible(dict(request)):
            raise RuntimeError("当前任务不再接收这份业务逻辑图")
        name = f"TFS-{request['work_item_id']}-业务逻辑图.png"
        target = (settings.delivery_dir / request_id / ARTIFACT_KIND / name).resolve()
        if not target.is_relative_to(settings.delivery_dir.resolve()):
            raise RuntimeError("业务逻辑图路径无效")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp")
        try:
            temp.write_bytes(image)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        now = db.utc_now()
        artifact_id = conn.execute("""INSERT INTO delivery_artifacts(request_id,kind,name,local_path,size_bytes,created_at)
            VALUES(?,?,?,?,?,?)""", (request_id, ARTIFACT_KIND, name, str(target), len(image), now)).lastrowid
        conn.execute("""UPDATE business_logic_diagrams SET status='completed',progress='',explanation=?,
                        artifact_id=?,mail_status='queued',updated_at=? WHERE request_id=?""",
                     (explanation, artifact_id, now, request_id))
        _event(conn, request_id, "business_logic.completed", "业务逻辑图已加入交付产物")
        return _public(_lookup(conn, request_id))


def _mail(request: dict, explanation: str, image: Path) -> dict:
    mailer = Mailer()
    addresses = db.rows("SELECT email FROM user_emails WHERE user_id=? ORDER BY id", (request["requester_id"],))
    recipients = mailer._deduplicate_emails([request["requester_email"], *(row["email"] for row in addresses)])
    if not recipients:
        raise ValueError("需求发起人没有可用邮件地址")
    if not any(part.strip() for part in settings.task_admin_email.replace(";", ",").split(",")):
        admins = db.rows("SELECT email FROM users WHERE role='admin' AND active=1 ORDER BY id")
        if not admins:
            raise ValueError("管理员没有可用邮件地址")
        recipients = mailer._deduplicate_emails([*recipients, *(row["email"] for row in admins)])
    title = " ".join(str(request["title"]).split())
    link = f"{settings.public_base_url}/?request={request['id']}"
    subject = f"【AutoDev · 业务逻辑图】TFS #{request['work_item_id']}｜{title[:80]}"
    body = f"""<div style="max-width:760px;margin:auto;font-family:'Microsoft YaHei',sans-serif;color:#082c30">
    <h2>{html.escape(title)}</h2>
    <p style="white-space:pre-wrap">{html.escape(explanation)}</p>
    <p><img src="cid:{IMAGE_CID}" alt="业务逻辑图" style="display:block;max-width:100%;height:auto"></p>
    <p><a href="{html.escape(link, quote=True)}">打开任务查看交付产物</a></p></div>"""
    return {"to": recipients, "subject": subject, "html_body": body,
            "text_body": f"{title}\n\n{explanation}\n\n打开任务查看业务逻辑图\n{link}",
            "inline_images": {IMAGE_CID: image}}


def send_claimed(request_id: str, runner_id: str, token: str) -> dict:
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = _claimed(conn, request_id, runner_id, token, mail=True)
        if job["mail_status"] != "claimed":
            return _public(job)
        artifact = conn.execute("SELECT local_path FROM delivery_artifacts WHERE id=? AND request_id=? AND kind=?",
                                (job["artifact_id"], request_id, ARTIFACT_KIND)).fetchone()
        image = Path(artifact["local_path"]).resolve() if artifact else None
        explanation = job["explanation"]
        conn.execute("UPDATE business_logic_diagrams SET mail_status='sending',updated_at=? WHERE request_id=?",
                     (db.utc_now(), request_id))
    try:
        request = db.request_detail(request_id)
        if not request or not image or not image.is_relative_to(settings.delivery_dir.resolve()) or not image.is_file():
            raise ValueError("业务逻辑图文件不可读取")
        mailer = Mailer()
        if not mailer.configured():
            raise ValueError("平台邮件配置尚未就绪")
        mailer.send(**_mail(request, explanation, image))
        mail_status, error = "sent", ""
    except (ValueError, smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused,
            smtplib.SMTPConnectError, smtplib.SMTPDataError) as exc:
        mail_status, error = "failed", bounded_log(str(exc), 600)
    except Exception:
        mail_status, error = "uncertain", "邮件服务器未确认投递结果，请核对后重发。"
    with db.transaction() as conn:
        changed = conn.execute("""UPDATE business_logic_diagrams SET mail_status=?,mail_error=?,updated_at=?
            WHERE request_id=? AND mail_token=? AND mail_status IN ('sending','uncertain')""",
            (mail_status, error, db.utc_now(), request_id, token)).rowcount
        if changed:
            _event(conn, request_id, f"business_logic.mail_{mail_status}",
                   "已发送业务逻辑图通知" if mail_status == "sent" else "业务逻辑图邮件投递未确认",
                   warning=mail_status != "sent")
        return _public(_lookup(conn, request_id))
