from __future__ import annotations

from enum import StrEnum


class DeliveryMode(StrEnum):
    LOCAL_PACKAGE = "local_package"
    SICHUAN_AUTO_REVIEW = "sichuan_auto_review"
    PRODUCT_MANUAL_REVIEW = "product_manual_review"


DELIVERY_MODE_LABELS = {
    DeliveryMode.LOCAL_PACKAGE: "本地打包交付",
    DeliveryMode.SICHUAN_AUTO_REVIEW: "四川审核后交付",
    DeliveryMode.PRODUCT_MANUAL_REVIEW: "产品审核后交付",
}


REVIEW_DELIVERY_MODES = {
    DeliveryMode.SICHUAN_AUTO_REVIEW.value,
    DeliveryMode.PRODUCT_MANUAL_REVIEW.value,
}

REVIEW_DELIVERABLE_KINDS = {"merge_screenshot", "menu_link"}


def visible_delivery_artifacts(delivery_mode: str, artifacts: list[dict]) -> list[dict]:
    """Return only user-facing deliverables allowed by the configured delivery mode."""
    visible = [
        item
        for item in artifacts
        if item.get("kind") not in {"report", "pull_request", "merge_evidence", "email_preview"}
        and not (item.get("kind") == "merge_screenshot" and "凭证" in str(item.get("name") or ""))
    ]
    if delivery_mode in REVIEW_DELIVERY_MODES:
        return [item for item in visible if item.get("kind") in REVIEW_DELIVERABLE_KINDS]
    return visible


class RunStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    DEVELOPING = "developing"
    SUBMITTING = "submitting"
    BUILDING = "building"
    WAITING_MERGE = "waiting_merge"
    CAPTURING = "capturing"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
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
    RunStatus.WAITING_MERGE: "等待 PR 合并",
    RunStatus.CAPTURING: "生成合并凭证",
    RunStatus.DELIVERING: "发送交付邮件",
    RunStatus.DELIVERED: "已交付",
    RunStatus.WAITING_APPROVAL: "等待人工确认",
    RunStatus.REJECTED: "准入驳回",
    RunStatus.FAILED: "执行失败",
    RunStatus.CANCELLED: "已取消",
}


PIPELINE_STEPS = [
    ("validate", "需求准入校验"),
    ("prepare", "准备隔离工作区"),
    ("develop", "DevCore 自动研发"),
    ("submit", "提交代码"),
    ("deliver", "生成与发送交付物"),
]
