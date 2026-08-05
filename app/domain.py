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
    RunStatus.DEVELOPING: "Codex 研发中",
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
    ("develop", "Codex 自动研发"),
    ("submit", "提交代码"),
    ("deliver", "生成与发送交付物"),
]
