"""Durable, read-only post-delivery conversations executed by the local runner."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from .. import db
from . import model_settings


def _public(item: dict) -> dict:
    result = {key: value for key, value in item.items() if key not in {
        "claim_token", "idempotency_key", "runner_id", "model_config", "thread_id",
    }}
    result["model"] = db.json_value(item.get("model_config"), {})
    start, end = result.get("started_at"), result.get("completed_at")
    result["elapsed_seconds"] = max(0, (datetime.fromisoformat(end or db.utc_now()) - datetime.fromisoformat(start)).total_seconds()) if start else 0
    return result


def _expire(conn) -> None:
    cutoff = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    now = db.utc_now()
    conn.execute("""UPDATE request_followups SET status='failed',error_message=?,completed_at=?,updated_at=?
                    WHERE status='running' AND updated_at<?""",
                 ("执行器连接中断，追问记录已保留，可重新提问。", now, now, cutoff))


def list_for_request(request_id: str) -> list[dict]:
    with db.transaction() as conn:
        _expire(conn)
        return [_public(dict(row)) for row in conn.execute("""SELECT f.*,u.display_name actor_name
            FROM request_followups f JOIN users u ON u.id=f.actor_id
            WHERE f.request_id=? ORDER BY f.created_at,f.id""", (request_id,))]


def queue(request_id: str, question: str, actor: dict, idempotency_key: str) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("请输入追问内容")
    with db.transaction() as conn:
        _expire(conn)
        request = conn.execute("SELECT * FROM delivery_requests WHERE id=?", (request_id,)).fetchone()
        if not request:
            raise LookupError("任务不存在")
        if actor["role"] != "admin" and actor["id"] not in {request["requester_id"], request["acceptance_owner_id"]}:
            raise PermissionError("无权追问该需求")
        if request["status"] != "delivered":
            raise ValueError("仅已交付需求支持追问")
        if request["routing_superseded_by"]:
            raise ValueError("请在已更正的 APP 需求中追问")
        existing = conn.execute("SELECT * FROM request_followups WHERE request_id=? AND actor_id=? AND idempotency_key=?",
                                (request_id, actor["id"], idempotency_key)).fetchone()
        if existing:
            if existing["question"] != question:
                raise RuntimeError("重复提交标识与原问题不一致")
            return _public(dict(existing))
        if conn.execute("SELECT 1 FROM request_followups WHERE request_id=? AND status IN ('queued','running')", (request_id,)).fetchone():
            raise RuntimeError("当前问题正在回答，请等待完成后继续追问")
        identifier, now = str(uuid.uuid4()), db.utc_now()
        config = model_settings.current()
        conn.execute("""INSERT INTO request_followups(id,request_id,actor_id,runner_id,question,idempotency_key,model_config,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (identifier, request_id, actor["id"], request["runner_id"], question,
                                          idempotency_key, json.dumps(config), now, now))
        conn.execute("INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES (?,'request.followup','delivery_request',?,?,?)",
                     (actor["id"], request_id, json.dumps({"followup_id": identifier}), now))
        return _public(dict(conn.execute("SELECT * FROM request_followups WHERE id=?", (identifier,)).fetchone()))


def claim(runner_id: str) -> dict | None:
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire(conn)
        item = conn.execute("SELECT * FROM request_followups WHERE runner_id=? AND status='queued' ORDER BY created_at,id LIMIT 1", (runner_id,)).fetchone()
        if not item:
            return None
        now, token = db.utc_now(), str(uuid.uuid4())
        conn.execute("UPDATE request_followups SET status='running',started_at=?,updated_at=?,claim_token=? WHERE id=?",
                     (now, now, token, item["id"]))
        result = dict(item)
        result.update(status="running", started_at=now, claim_token=token)
        result["model_config"] = db.json_value(result["model_config"], {})
        result["history"] = [dict(row) for row in conn.execute("""SELECT question,answer FROM request_followups
            WHERE request_id=? AND status='completed' ORDER BY created_at DESC LIMIT 20""", (item["request_id"],))][::-1]
        return result


def update(identifier: str, runner_id: str, claim_token: str, *, status: str = "running", answer: str = "",
           progress: str = "", error_message: str = "", thread_id: str = "") -> dict:
    if status not in {"running", "completed", "failed"}:
        raise ValueError("无效追问状态")
    if status == "completed" and not answer.strip():
        raise ValueError("回答为空，不能标记完成")
    with db.transaction() as conn:
        item = conn.execute("SELECT * FROM request_followups WHERE id=?", (identifier,)).fetchone()
        if not item:
            raise LookupError("追问不存在")
        if item["runner_id"] != runner_id or item["claim_token"] != claim_token:
            raise PermissionError("追问不属于当前执行器领取的会话")
        if item["status"] != "running":
            if item["status"] == status and item["answer"] == answer:
                return _public(dict(item))  # Safe replay after a lost HTTP response.
            raise RuntimeError("追问已结束，拒绝迟到的执行器结果")
        now = db.utc_now()
        conn.execute("""UPDATE request_followups SET status=?,answer=?,progress=?,error_message=?,thread_id=?,
            completed_at=?,updated_at=? WHERE id=?""", (status, answer[:100000], progress[:1000], error_message[:3000],
                                                       thread_id[:100], now if status != "running" else None, now, identifier))
        return _public(dict(conn.execute("SELECT * FROM request_followups WHERE id=?", (identifier,)).fetchone()))
