"""Choose a restart point from durable delivery evidence."""
from __future__ import annotations

from ..db import json_value
from .analysis_sync import report_ready
from .deployment_links import _build_identity, registry


WAITING = "waiting_stage_recovery"
DEPLOY = "retry_deployment"
SYNC = "retry_delivery_sync"
CONTROL_ACTIONS = {DEPLOY, SYNC}
ACTIVE_CONTROLS = {"pending", "running", "waiting_merge"}


def recorded(detail: dict, kind: str) -> dict | None:
    return next((event for event in detail.get("events", []) if event.get("event_type") == kind), None)


def _metadata(event: dict | None) -> dict:
    value = (event or {}).get("metadata")
    parsed = value if isinstance(value, dict) else json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


def plan(detail: dict, *, include_queued=False) -> dict | None:
    if detail.get("joint_group_id") or detail.get("routing_superseded_by"):
        return None
    status = detail.get("status")
    if status not in {"failed", "waiting_analysis_sync"} and not (include_queued and status == WAITING):
        return None
    if report_ready(detail):
        return {"action": "retry-analysis-sync", "label": "重试报告同步", "stage": "delivery",
                "description": "使用已生成的报告，从 TFS 回写与通知继续。"}
    if detail.get("task_type") == "analysis":
        return None
    if detail.get("current_step") == "deliver" and (recorded(detail, "delivery.sync_started") or recorded(detail, "tfs.delivery_completed")):
        return {"action": SYNC, "label": "重试同步", "stage": "delivery",
                "description": "沿用本次交付物，从 TFS 回写与通知继续。"}
    if detail.get("current_step") != "release" or "auto_release" not in (detail.get("delivery_options") or []):
        return None
    policy = detail.get("policy_snapshot") or {}
    if policy.get("simulation_mode"):
        return None
    target = next((entry for entry in registry()["server14Projects"] if entry["projectKey"] == policy.get("project_key")), None)
    launch = _metadata(recorded(detail, "release.started"))
    latest = next((event for event in detail.get("events", [])
                   if event.get("event_type") in {"release.failed", "release.completed", "release.shared"}), {})
    if latest.get("event_type") != "release.failed":
        return None
    failure = _metadata(latest)
    deployment = failure.get("deployment14") or {}
    build_id = failure.get("buildId")
    definition_id = failure.get("definitionId") or launch.get("definitionId")
    if (not target or definition_id != target["definitionId"]
            or not isinstance(build_id, int) or isinstance(build_id, bool) or build_id <= 0
            or _build_identity(failure.get("resultsUrl")) != ("dcs", build_id)
            or failure.get("result") != "succeeded" or failure.get("expectedArtifactFound") is False
            or deployment.get("eligible") is not True
            or deployment.get("status") not in {"blocked", "failed", "failed-rolled-back"}):
        return None
    return {"action": DEPLOY, "label": "重试部署", "stage": "deploy", "build_id": build_id,
            "definition_id": definition_id, "description": f"使用 Build #{build_id}，从 14 环境部署验证继续。"}


def failed_release_metadata(result: dict) -> dict:
    fields = ("buildId", "result", "resultsUrl", "buildStartedAt", "buildFinishedAt",
              "definitionId", "expectedArtifactFound")
    deployment = result.get("deployment14") or {}
    return {**{key: result.get(key) for key in fields},
            "deployment14": {key: deployment.get(key) for key in ("eligible", "status", "startedAt", "finishedAt")}}
