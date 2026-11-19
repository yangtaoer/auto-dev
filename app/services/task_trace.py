"""Persisted task evidence for the workspace. Build and deployment are distinct."""
from __future__ import annotations

from ..db import json_value, rows
from .deployment_links import safe_url


EVENTS = (
    "request.created", "tfs.validated", "tfs.attachments_prepared", "tfs.attachments_partial",
    "workspace.prepared", "workspace.planning", "workspace.plan_recorded",
    "git.pushed", "pr.created", "pr.merged", "pr.validation_failed", "build.completed",
    "release.started", "release.completed", "release.shared", "release.failed",
    "tfs.delivery_completed", "mail.delivery_sent", "delivery.completed", "analysis.sync_pending",
)


def for_request(request_id: str) -> dict:
    placeholders = ",".join("?" for _ in EVENTS)
    observed = rows(
        f"""SELECT id,event_type,message,metadata,created_at FROM delivery_events
            WHERE id IN (
                SELECT MAX(id) FROM delivery_events WHERE request_id=? AND event_type IN ({placeholders})
                GROUP BY event_type
            ) ORDER BY id DESC""",
        (request_id, *EVENTS),
    )
    milestones, release = {}, {}
    for event in observed:
        kind = event["event_type"]
        milestones.setdefault(kind, {"at": event["created_at"], "message": event["message"]})
        if kind not in {"release.completed", "release.shared", "release.failed"} or release:
            continue
        result = json_value(event["metadata"], {})
        if not isinstance(result, dict):
            continue
        if kind == "release.shared":
            result = result.get("result") or {}
        if not isinstance(result, dict):
            continue
        deployment = result.get("deployment14") or {}
        release = {
            "at": event["created_at"],
            "build_id": result.get("buildId"),
            "result": result.get("result") or ("failed" if kind == "release.failed" else ""),
            "results_url": safe_url(result.get("resultsUrl")),
        }
        if isinstance(deployment, dict):
            release["deployment_status"] = deployment.get("status")
            release["deployment_reason"] = deployment.get("reason") if deployment.get("eligible") else None
    return {"milestones": milestones, "release": release}


def dashboard_previews(request_ids: list[str]) -> dict[str, dict]:
    if not request_ids:
        return {}
    placeholders = ",".join("?" for _ in request_ids)
    previews = {request_id: {"steps": []} for request_id in request_ids}
    for step in rows(
        f"SELECT request_id,step_code,status FROM delivery_steps WHERE request_id IN ({placeholders})",
        tuple(request_ids),
    ):
        previews[step.pop("request_id")]["steps"].append(step)
    for item in rows(
        f"""SELECT r.id,e.message,
                   (SELECT MAX(id) FROM delivery_events WHERE request_id=r.id AND event_type='workspace.planning') planning,
                   (SELECT MAX(id) FROM delivery_events WHERE request_id=r.id AND event_type='workspace.plan_recorded') planned
            FROM delivery_requests r LEFT JOIN delivery_events e
            ON e.id=(SELECT MAX(id) FROM delivery_events WHERE request_id=r.id
                AND event_type IN ('devcore.reply','devcore.summary','workspace.plan_recorded'))
            WHERE r.id IN ({placeholders})""",
        tuple(request_ids),
    ):
        if item["message"]:
            previews[item["id"]]["latest_reply"] = item["message"]
        previews[item["id"]]["planning_repository_scope"] = (item["planning"] or 0) > (item["planned"] or 0)
        previews[item["id"]]["repository_plan_recorded"] = bool(item["planned"])
    return previews
