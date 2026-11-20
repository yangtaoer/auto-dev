"""Map persisted execution and delivery receipts to the task diagram."""
from __future__ import annotations

from datetime import UTC, datetime


TERMINAL = {"delivered", "failed", "cancelled", "rejected"}
LOCAL_BUILD = {"local_package", "sichuan_review_local_package"}


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) < 16:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None


def _window(start: datetime, end: datetime | None, now: datetime, *, running=False, basis="") -> dict:
    if end is not None and end < start:
        end = None
    live = bool(running and end is None)
    measured_end = now if live else end
    return {
        "started_at": start.isoformat(),
        "duration_seconds": int((measured_end - start).total_seconds()) if measured_end and measured_end >= start else None,
        "running": live,
        "basis": basis,
    }


def intake_timing(intake: dict) -> dict:
    start = _timestamp(intake.get("claimed_at"))
    if not start:
        return {}
    end = _timestamp(intake.get("completed_at"))
    return {"intake": _window(start, end, datetime.now(UTC), running=intake.get("status") == "claimed")}


def stage_timings(detail: dict, *, now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(UTC)
    trace = detail.get("task_trace") or {}
    milestones, release = trace.get("milestones", {}), trace.get("release", {})
    steps = {step["step_code"]: step for step in detail.get("steps", [])}
    status = detail.get("status")
    active = status not in TERMINAL
    task_end = _timestamp(detail.get("completed_at"))
    timings = {}

    def event(kind):
        return _timestamp((milestones.get(kind) or {}).get("at"))

    def step_window(code):
        step = steps.get(code, {})
        state = step.get("status")
        if state in {None, "pending", "skipped"}:
            return None, None, False
        start, end = _timestamp(step.get("started_at")), _timestamp(step.get("finished_at"))
        # A direct terminal write can populate both timestamps from the same clock reading.
        if start == end:
            start = None
        if state == "running" and not active and detail.get("current_step") == code:
            end = end or task_end
        return start, end, state == "running" and active

    def record(phase, start, end, running=False, basis=""):
        if start:
            timings[phase] = _window(start, end, now, running=running, basis=basis)

    record("intake", *step_window("validate"))
    develop_start, develop_end, developing = step_window("develop")
    plan_start, plan_end = event("workspace.planning"), event("workspace.plan_recorded")
    if plan_start and (not develop_start or plan_start >= develop_start) and (not develop_end or plan_start <= develop_end):
        if plan_end and plan_end >= plan_start and (not develop_end or plan_end <= develop_end):
            record("requirement", plan_start, plan_end)
            develop_start = plan_end
        else:
            record("requirement", plan_start, None, developing and detail.get("planning_repository_scope"))
            develop_start = None
    else:
        record("requirement", *step_window("prepare"))
    record("development", develop_start, develop_end, developing)

    submit_start, submit_end, submitting = step_window("submit")
    record("submit", submit_start, submit_end, submitting)
    prs = [repo for repo in detail.get("repository_states") or [] if repo.get("pr_id") or repo.get("pr_url")]
    merged = all(repo.get("merge_commit") for repo in prs) if prs else bool(event("pr.merged") or detail.get("merge_commit"))
    merge_start = event("pr.waiting_merge") or event("pr.created")
    if not merge_start and (prs or detail.get("pr_id")):
        merge_start = submit_end
    merge_end = event("pr.merged") if merged else None
    record("merge", merge_start, merge_end, status == "waiting_merge", "从进入审核到平台确认合并")

    release_start, release_end, releasing = step_window("release")
    deliver_start, deliver_end, delivering = step_window("deliver")
    release_receipt = _timestamp(release.get("at"))
    deploy_start = _timestamp(release.get("deployment_started_at"))
    deploy_end = _timestamp(release.get("deployment_finished_at"))
    deploy_status = release.get("deployment_status")
    build_basis = ""
    if detail.get("delivery_mode") in LOCAL_BUILD and not release.get("build_id"):
        build_end = event("build.completed") or release_end
        build_start = event("build.started") or release_start
        if not build_start and detail.get("delivery_mode") == "local_package" and (build_end or status == "building"):
            build_start = deliver_start
        releasing = status == "building"
    elif release.get("shared"):
        build_start, build_end = release_start, release_receipt
        build_basis = "等待本次任务复用发版产物"
    else:
        pipeline_start = _timestamp(release.get("build_started_at"))
        build_start = pipeline_start or event("release.started") or release_start
        build_end = _timestamp(release.get("build_finished_at"))
        if build_start and build_end and build_end < build_start:
            build_end = None
        if not build_end:
            # A recorded deployment boundary excludes deployment from the build interval.
            if deploy_status in {None, "", "skipped-by-user", "not-applicable", "directory-absent"}:
                build_end = release_receipt or release_end
            else:
                build_end = deploy_start
        build_basis = "TFS 流水线起止记录" if pipeline_start else "从发版发起到流水线完成，含排队"
    record("release", build_start, build_end, releasing or status == "releasing", build_basis)

    deploy_basis = ""
    if not deploy_start and deploy_status == "deployed" and not release.get("shared"):
        runtime_start = _timestamp(release.get("runtime_started_at"))
        if runtime_start and build_start and release_receipt and build_start <= runtime_start <= release_receipt:
            deploy_start, deploy_end = runtime_start, release_receipt
            deploy_basis = "历史记录从服务启动到部署回执"
    record("deploy", deploy_start, deploy_end, basis=deploy_basis)

    delivery_entry = event("delivery.started")
    deliver_end = event("delivery.completed") or deliver_end
    delivery_reached = bool(delivery_entry or status in {"delivering", "waiting_analysis_sync", "delivered"})
    if delivery_reached:
        boundary = max((timestamp for timestamp in (merge_end, build_end, release_receipt, deploy_end) if timestamp), default=None)
        delivery_basis = ""
        if delivery_entry:
            deliver_start = delivery_entry
        elif status == "waiting_analysis_sync":
            deliver_start = _timestamp(steps.get("deliver", {}).get("started_at"))
        elif boundary and (not deliver_start or deliver_start < boundary):
            deliver_start = boundary
            delivery_basis = "历史记录从前置步骤完成回执起算"
        record("delivery", deliver_start, deliver_end, delivering or status in {"delivering", "waiting_analysis_sync"}, delivery_basis)

    if status == "delivered":
        feedback = _timestamp((detail.get("acceptance_progress") or {}).get("latest_feedback_at"))
        record("acceptance", deliver_end or task_end, feedback, True, "从交付完成到最新验收反馈")
    return timings
