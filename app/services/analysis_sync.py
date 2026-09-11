"""Resume report delivery, never repeat an already completed model analysis."""
from __future__ import annotations

import json

from .. import db


def report_ready(detail: dict) -> bool:
    return (
        detail.get("task_type") == "analysis"
        and detail.get("current_step") == "deliver"
        and bool(detail.get("analysis_result"))
        and any(a.get("kind") == "analysis_report" for a in detail.get("artifacts", []))
    )


def retry(request_id: str, user: dict) -> dict:
    detail = db.request_detail(request_id)
    if not detail:
        raise LookupError("任务不存在")
    if user["role"] != "admin" and detail["requester_id"] != user["id"]:
        raise PermissionError("无权操作该任务")
    if detail.get("joint_group_id"):
        raise RuntimeError("联合分析需要由联合交付流程统一同步")
    if not report_ready(detail) or detail["status"] not in {"failed", "waiting_analysis_sync"}:
        raise RuntimeError("只有报告已生成但交付未完成的分析任务可以重试同步")
    response = {"id": request_id, "status": "waiting_analysis_sync", "work_item_id": detail["work_item_id"], "reuse_report": True}
    if detail["status"] == "waiting_analysis_sync":
        # Do not reset an existing poll lease when the button is double-clicked.
        return response
    with db.transaction() as conn:
        changed = conn.execute(
            """UPDATE delivery_requests SET status='waiting_analysis_sync', next_poll_at=?,
               completed_at=NULL, email_sent_at=NULL, error_message='', updated_at=?
               WHERE id=? AND status=?
               AND NOT EXISTS (SELECT 1 FROM request_controls c WHERE c.request_id=delivery_requests.id
                 AND c.status IN ('pending','running','waiting_merge'))""",
            (db.utc_now(), db.utc_now(), request_id, detail["status"]),
        ).rowcount
        if not changed:
            raise RuntimeError("任务状态已经变化，请刷新后再试")
        conn.execute(
            "INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], "analysis.retry_sync", "delivery_request", request_id,
             json.dumps({"reuse_report": True}), db.utc_now()),
        )
    db.add_event(request_id, "analysis.sync_queued", "复用已有报告重试交付，不重新执行问题分析")
    return response
