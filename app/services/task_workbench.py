"""Role-scoped work queues and a shared display status for task surfaces."""
from __future__ import annotations

from collections.abc import Callable

from ..db import rows
from ..domain import status_label
from .project_learning import acceptance_workbench_states


EXECUTING = {"validating", "developing", "submitting", "building", "releasing", "capturing", "delivering", "routing"}
RECOVERY_ACTIONS = {"retry_deployment", "retry_delivery_sync"}
STATUS_LABELS = {
    "waiting_runner": "等待执行器上线",
    "runner_disconnected": "执行器离线，待核对",
    "waiting_stage_recovery": "等待阶段恢复",
}


def display_state(item: dict, online: bool, last_seen: str | None) -> None:
    actual = item["status"]
    visible = actual
    if not online and actual == "queued":
        visible = "waiting_runner"
    elif not online and actual in EXECUTING:
        visible = "runner_disconnected"
    item["runner_online"] = online
    item["display_status"] = visible
    item["status_label"] = STATUS_LABELS.get(visible) or status_label(item.get("task_type", "development"), visible)
    item["last_confirmed_at"] = (last_seen if visible == "runner_disconnected" else None) or item.get("updated_at")
    if visible == "waiting_runner":
        item["display_message"] = item["current_activity"] = "执行器当前离线，任务已安全排队，待执行器上线后自动继续"
    elif visible == "runner_disconnected":
        item["display_message"] = item["current_activity"] = "最近一次心跳后尚未确认执行状态。执行器上线后会核对检查点。"


def handoff(item: dict, user: dict, *, acceptance: dict | None = None, recovery: dict | None = None) -> dict | None:
    """Describe the current actor and action on both the homepage and the task."""
    actual = item["status"]
    shown = item.get("display_status") or actual
    mine = item.get("requester_id") == user["id"]
    manager = mine or user["role"] == "admin"
    requester = item.get("requester_name") or "需求提出人"
    confirmed = item.get("last_confirmed_at") or item.get("updated_at")

    def assignment(lane, actor, action, stage=None, operation=None):
        return {"lane": lane, "actor": actor, "label": action, "stage": stage,
                "operation": operation, "confirmed_at": confirmed}

    if actual == "delivered":
        if not acceptance or acceptance["status"] == "accepted" or acceptance.get("repair_request_id"):
            return None
        owner = item.get("acceptance_owner_id") or item.get("requester_id")
        if item.get("joint_status") not in {None, "", "delivered", "failed", "cancelled"}:
            return assignment("waiting", "联合项目", "查看交付", "delivery")
        if owner == user["id"]:
            if acceptance["status"] == "changes_requested":
                return assignment("attention", "我", "查看返修", "acceptance")
            return assignment("acceptance", "我", "提交验收", "acceptance")
        if manager:
            return assignment("waiting", item.get("acceptance_owner_name") or "指定验收人", "查看验收", "acceptance")
        return None
    if actual == "waiting_input":
        return assignment("supplement" if mine else "waiting", "我" if mine else requester,
                          "补充信息" if manager else "查看待补充", "development")
    if actual == "waiting_merge":
        reviewer = "四川审核人" if item.get("delivery_mode") in {"sichuan_auto_review", "sichuan_review_local_package"} else "产品审核人"
        return assignment("waiting", reviewer, "查看 PR", "merge")
    if actual == "waiting_approval":
        return assignment("attention" if user["role"] == "admin" else "waiting", "管理员", "查看待确认", None)
    if shown == "runner_disconnected":
        return assignment("queue", "执行器", "查看检查点")
    if actual == "failed" or (actual == "waiting_analysis_sync" and recovery):
        if recovery and manager:
            return assignment("attention", "我", recovery["label"], recovery["stage"], recovery["action"])
        if actual == "failed":
            return assignment("attention", "需求提出人或管理员", "查看异常")
    if shown in EXECUTING:
        return assignment("executing", "执行器", "查看进度")
    if actual not in {"cancelled", "rejected"}:
        return assignment("queue", "执行器", "查看进度")
    return None


def load(user: dict, runners: dict, online_by_runner: dict, clean: Callable[[str], str]) -> dict:
    requests = rows(
        """SELECT r.id,r.work_item_id,r.title,r.status,r.task_type,r.delivery_mode,r.current_step,
                  r.requester_id,r.acceptance_owner_id,r.runner_id,r.updated_at,r.error_message,
                  p.name project_name,u.display_name requester_name,a.display_name acceptance_owner_name,
                  i.status joint_status
           FROM visible_delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id
           LEFT JOIN users a ON a.id=r.acceptance_owner_id LEFT JOIN request_intakes i ON i.id=r.joint_group_id
           WHERE (
               (r.status='delivered' AND (r.requester_id=? OR r.acceptance_owner_id=?)
                AND NOT EXISTS (SELECT 1 FROM request_controls c WHERE c.request_id=r.id AND c.action='rollback'
                                AND c.status IN ('pending','running','waiting_merge','completed'))
                AND NOT EXISTS (SELECT 1 FROM visible_delivery_requests n WHERE n.project_id=r.project_id
                    AND n.work_item_id=r.work_item_id AND n.task_type=r.task_type AND n.status='delivered'
                    AND n.created_at>r.created_at))
               OR (r.status NOT IN ('delivered','cancelled','rejected') AND (? OR r.requester_id=?)
                   AND (r.status!='failed' OR NOT EXISTS (SELECT 1 FROM visible_delivery_requests n
                       WHERE n.project_id=r.project_id AND n.work_item_id=r.work_item_id AND n.task_type=r.task_type
                       AND n.created_at>r.created_at)))
           ) ORDER BY r.updated_at DESC,r.id DESC""",
        (user["id"], user["id"], user["role"] == "admin", user["id"]),
    )
    verdicts = acceptance_workbench_states([item["id"] for item in requests if item["status"] == "delivered"])
    lanes = {name: [] for name in ("acceptance", "supplement", "waiting", "executing", "queue", "attention")}
    for item in requests:
        runner_id = item["runner_id"]
        display_state(item, online_by_runner.get(runner_id, False), runners.get(runner_id, {}).get("last_seen_at"))
        action = handoff(item, user, acceptance=verdicts.get(item["id"]))
        if not action:
            continue
        lanes[action["lane"]].append({
            "id": item["id"], "work_item_id": item["work_item_id"], "title": clean(item["title"] or "") or "",
            "project_name": item["project_name"], "display_status": item["display_status"], "status_label": item["status_label"],
            **action,
        })
    return lanes
