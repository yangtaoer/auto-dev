from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    DEVELOPMENT = "development"
    ANALYSIS = "analysis"


TASK_TYPE_LABELS = {
    TaskType.DEVELOPMENT: "自主研发",
    TaskType.ANALYSIS: "问题分析",
}


class DeliveryMode(StrEnum):
    LOCAL_PACKAGE = "local_package"
    SICHUAN_AUTO_REVIEW = "sichuan_auto_review"
    SICHUAN_REVIEW_LOCAL_PACKAGE = "sichuan_review_local_package"
    PRODUCT_MANUAL_REVIEW = "product_manual_review"


DELIVERY_MODE_LABELS = {
    DeliveryMode.LOCAL_PACKAGE: "本地打包交付",
    DeliveryMode.SICHUAN_AUTO_REVIEW: "四川审核后交付",
    DeliveryMode.SICHUAN_REVIEW_LOCAL_PACKAGE: "四川审核后本地打包交付",
    DeliveryMode.PRODUCT_MANUAL_REVIEW: "产品审核后交付",
}


REVIEW_DELIVERY_MODES = {
    DeliveryMode.SICHUAN_AUTO_REVIEW.value,
    DeliveryMode.PRODUCT_MANUAL_REVIEW.value,
}

SICHUAN_APPROVAL_DELIVERY_MODES = {
    DeliveryMode.SICHUAN_AUTO_REVIEW.value,
    DeliveryMode.SICHUAN_REVIEW_LOCAL_PACKAGE.value,
}

DELIVERY_OPTION_MERGE_SCREENSHOT = "merge_screenshot"
DELIVERY_OPTION_LICENSE_REQUEST = "license_request"
DELIVERY_OPTION_AUTO_RELEASE = "auto_release"
DEFAULT_DELIVERY_OPTIONS = [DELIVERY_OPTION_AUTO_RELEASE]
DELIVERY_OPTIONS = {
    DELIVERY_OPTION_MERGE_SCREENSHOT,
    DELIVERY_OPTION_LICENSE_REQUEST,
    DELIVERY_OPTION_AUTO_RELEASE,
}
REVIEW_DELIVERABLE_KINDS = {"merge_screenshot", "menu_link", "license_request", "release_artifact"}


def visible_delivery_artifacts(
    delivery_mode: str,
    artifacts: list[dict],
    delivery_options: list[str] | None = None,
) -> list[dict]:
    """Return only user-facing deliverables allowed by the configured delivery mode."""
    visible = [
        item
        for item in artifacts
        if item.get("kind") not in {"report", "pull_request", "merge_evidence", "email_preview"}
        and not (item.get("kind") == "merge_screenshot" and "凭证" in str(item.get("name") or ""))
    ]
    analysis_reports = [item for item in visible if item.get("kind") == "analysis_report"]
    if delivery_mode in REVIEW_DELIVERY_MODES:
        allowed = {"menu_link"}
        # None marks pre-option legacy rows and keeps their original screenshot/License behavior.
        if delivery_options is None:
            allowed.update({"merge_screenshot", "license_request"})
        else:
            if DELIVERY_OPTION_MERGE_SCREENSHOT in delivery_options:
                allowed.add("merge_screenshot")
            if DELIVERY_OPTION_LICENSE_REQUEST in delivery_options:
                allowed.add("license_request")
            if DELIVERY_OPTION_AUTO_RELEASE in delivery_options:
                allowed.add("release_artifact")
        return analysis_reports + [
            item for item in visible if item.get("kind") in allowed and item.get("kind") != "analysis_report"
        ]
    return visible


class RunStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    DEVELOPING = "developing"
    SUBMITTING = "submitting"
    BUILDING = "building"
    RELEASING = "releasing"
    WAITING_MERGE = "waiting_merge"
    CAPTURING = "capturing"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    RunStatus.DELIVERED,
    RunStatus.REJECTED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


STATUS_LABELS = {
    RunStatus.QUEUED: "等待执行",
    RunStatus.VALIDATING: "准入校验",
    RunStatus.DEVELOPING: "DevCore 研发中",
    RunStatus.SUBMITTING: "提交代码",
    RunStatus.BUILDING: "本地构建",
    RunStatus.RELEASING: "自动发版",
    RunStatus.WAITING_MERGE: "等待 PR 合并",
    RunStatus.CAPTURING: "生成合并凭证",
    RunStatus.DELIVERING: "发送交付邮件",
    RunStatus.DELIVERED: "已交付",
    RunStatus.WAITING_INPUT: "待补充信息",
    RunStatus.WAITING_APPROVAL: "等待人工确认",
    RunStatus.REJECTED: "准入驳回",
    RunStatus.FAILED: "执行失败",
    RunStatus.CANCELLED: "已取消",
}


ANALYSIS_STATUS_LABELS = {
    RunStatus.QUEUED: "等待分析",
    RunStatus.VALIDATING: "问题准入校验",
    RunStatus.DEVELOPING: "DevCore 分析中",
    RunStatus.DELIVERING: "发送分析报告",
    RunStatus.DELIVERED: "分析完成",
    RunStatus.WAITING_INPUT: "待补充分析信息",
    RunStatus.WAITING_APPROVAL: "等待人工确认",
    RunStatus.REJECTED: "准入驳回",
    RunStatus.FAILED: "分析失败",
    RunStatus.CANCELLED: "已取消",
}


def status_label(task_type: str, status: str) -> str:
    try:
        run_status = RunStatus(status)
    except ValueError:
        return status
    labels = ANALYSIS_STATUS_LABELS if task_type == TaskType.ANALYSIS.value else STATUS_LABELS
    return labels.get(run_status, status)


PIPELINE_STEPS = [
    ("validate", "需求准入校验"),
    ("prepare", "准备隔离工作区"),
    ("develop", "DevCore 自动研发"),
    ("clarify", "补充研发信息"),
    ("submit", "提交代码"),
    ("release", "自动发版"),
    ("deliver", "生成与发送交付物"),
]


ANALYSIS_PIPELINE_STEP_CODES = {"validate", "prepare", "develop", "clarify", "deliver"}
ANALYSIS_PIPELINE_NAMES = {
    "validate": "问题准入校验",
    "prepare": "准备只读分析工作区",
    "develop": "DevCore 问题分析",
    "clarify": "补充分析信息",
    "deliver": "生成与发送分析报告",
}
