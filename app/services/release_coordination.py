"""Durable, atomic project release coalescing shared by local/cloud workers."""
from __future__ import annotations

import hashlib
import json
import uuid

from ..db import transaction, utc_now


def _scope(request: dict) -> str:
    policy = json.loads(request["policy_snapshot"])
    # Do not reuse a release from another branch, repository set or pipeline policy.
    contract = {key: policy.get(key) for key in (
        "tfs_collection_url", "project_key", "base_branch", "repository_base_branches",
        "repository_tfs_paths", "simulation_mode",
    )}
    return f"{request['project_id']}:" + hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def claim(request_id: str, token: str | None = None) -> dict:
    token = token or str(uuid.uuid4())
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        request = conn.execute("SELECT * FROM delivery_requests WHERE id=?", (request_id,)).fetchone()
        if not request or request["status"] in {"cancelled", "failed", "rejected", "delivered"}:
            return {"action": "stop"}
        if request["status"] not in {"capturing", "delivering", "waiting_release", "releasing"}:
            return {"action": "stop"}
        scope, now = _scope(dict(request)), utc_now()
        # A terminal owner must not strand every following request forever.
        # Reconcile already-recorded success; never silently queue a replacement build.
        stopped = conn.execute("""SELECT b.id,b.owner_id FROM release_batches b JOIN delivery_requests r ON r.id=b.owner_id
            WHERE b.project_id=? AND b.status='running' AND r.status IN ('failed','cancelled','delivered','rejected')""", (request['project_id'],)).fetchall()
        for abandoned in stopped:
            artifact = conn.execute("SELECT name,external_url FROM delivery_artifacts WHERE request_id=? AND kind='release_artifact' AND external_url<>'' ORDER BY id DESC LIMIT 1", (abandoned['owner_id'],)).fetchone()
            result = ({'artifactsUrl':artifact['external_url'], 'pipelineName':artifact['name']} if artifact else
                      {'error':'合并发版执行任务已结束但未记录成功产物；已保留批次，不自动重复发版'})
            conn.execute("UPDATE release_batches SET status=?,result=?,updated_at=? WHERE id=?", ('completed' if artifact else 'failed', json.dumps(result, ensure_ascii=False), now, abandoned['id']))
        conn.execute("INSERT OR IGNORE INTO release_members(request_id,scope,created_at) VALUES (?,?,?)", (request_id, scope, now))
        batch = conn.execute("SELECT b.* FROM release_batches b JOIN release_members m ON m.batch_id=b.id WHERE m.request_id=?", (request_id,)).fetchone()
        if batch:
            if batch["status"] == "completed":
                return {"action": "reuse", "batch_id": batch["id"], "result": json.loads(batch["result"])}
            if batch["status"] == "failed":
                return {"action": "failed", "message": json.loads(batch["result"]).get("error", "合并发版失败")}
            if batch["owner_id"] == request_id and batch["claim_token"] == token:
                return {"action": "run", "batch_id": batch["id"]}
            return {"action": "wait", "message": "同项目合并发版正在执行，完成后共享产物"}
        conn.execute("UPDATE delivery_requests SET status='waiting_release',current_step='release',updated_at=? WHERE id=?", (now, request_id))
        active = conn.execute("""SELECT id,work_item_id FROM delivery_requests WHERE project_id=? AND id<>?
            AND task_type='development' AND status IN
            ('queued','validating','developing','submitting','building','waiting_merge','capturing','releasing','delivering')
            ORDER BY created_at""", (request["project_id"], request_id)).fetchall()
        if active:
            return {"action": "wait", "message": "暂缓本次独立发版，等待同项目需求 " + "、".join(f"#{item['work_item_id']}" for item in active) + " 完成后统一发版", "pending": [item["id"] for item in active]}
        if conn.execute("SELECT 1 FROM release_batches WHERE project_id=? AND status='running'", (request["project_id"],)).fetchone():
            return {"action": "wait", "message": "同项目已有发版执行中，本轮稍后重新核验"}
        batch_id = str(uuid.uuid4())
        conn.execute("INSERT INTO release_batches(id,scope,project_id,owner_id,status,claim_token,created_at,updated_at) VALUES (?,?,?,?,'running',?,?,?)", (batch_id, scope, request["project_id"], request_id, token, now, now))
        conn.execute("""UPDATE release_members SET batch_id=? WHERE scope=? AND batch_id IS NULL
            AND request_id IN (SELECT id FROM delivery_requests WHERE status='waiting_release')""", (batch_id, scope))
        members = [item[0] for item in conn.execute("SELECT request_id FROM release_members WHERE batch_id=?", (batch_id,))]
        return {"action": "run", "batch_id": batch_id, "members": members}


def finish(request_id: str, batch_id: str, result: dict, *, failed: bool = False) -> dict:
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute("SELECT * FROM release_batches WHERE id=? AND owner_id=?", (batch_id, request_id)).fetchone()
        if not batch:
            raise ValueError("发版批次不属于当前任务")
        if batch["status"] == "running":
            conn.execute("UPDATE release_batches SET status=?,result=?,updated_at=? WHERE id=?", ("failed" if failed else "completed", json.dumps(result, ensure_ascii=False), utc_now(), batch_id))
            conn.execute("UPDATE delivery_requests SET next_poll_at=NULL WHERE id IN (SELECT request_id FROM release_members WHERE batch_id=?) AND status='waiting_release'", (batch_id,))
        return {"ok": True}
