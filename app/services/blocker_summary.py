from __future__ import annotations

import re
from typing import Any


_BLOCKER_PREFIX = re.compile(
    r"^\s*(?:(?:发现)?阻塞风险(?:，需要人工确认)?|需要人工确认)\s*[：:]\s*",
    flags=re.IGNORECASE,
)

_TECHNICAL_CHECK_LABELS = {
    "asset_manifest_checked": "静态资源清单校验（asset_manifest_checked）",
    "directory_layout_checked": "部署目录结构校验（directory_layout_checked）",
    "cache_strategy_checked": "缓存策略校验（cache_strategy_checked）",
}


def _clean_reason(value: Any) -> str:
    reason = re.sub(r"\s+", " ", str(value or "").strip())
    while reason and _BLOCKER_PREFIX.match(reason):
        reason = _BLOCKER_PREFIX.sub("", reason, count=1).strip()
    for technical_name, display_name in _TECHNICAL_CHECK_LABELS.items():
        reason = re.sub(rf"\b{re.escape(technical_name)}\b", display_name, reason, flags=re.IGNORECASE)
    return reason or "研发门禁发现尚未获得可复核证据的阻塞风险。"


def _decision_required(reason: str) -> str:
    text = reason.casefold()
    if any(keyword in text for keyword in ("真实页面截图", "页面截图", "浏览器截图")):
        return (
            "真实页面截图不属于任何项目的交付硬门禁；请直接使用 production 构建、真实路由、"
            "自动断言、资源哈希和部署检查作为可复核证据继续，不需要补充截图环境。"
        )
    if any(keyword in text for keyword in _TECHNICAL_CHECK_LABELS) or "前端部署验证" in text:
        return (
            "请判断现有 production 构建、静态资源清单、部署目录与缓存策略的自动化证据"
            "是否足以证明版本可发布；认可则在下方写明依据并继续，不认可则指出必须补做的部署验证。"
        )
    if any(keyword in text for keyword in ("仓库", "repository", "repo", "tfs 路径", "tfs路径")):
        return (
            "请判断缺失仓库是否属于本次需求范围；属于范围则提供或修正 TFS 仓库路径，"
            "不在范围则说明排除依据后继续。"
        )
    if any(keyword in text for keyword in ("tfs 附件", "tfs附件", "附件图片", "图片因认证", "图片无法读取")):
        return (
            "请判断该附件图片是否包含开发必需信息；若必需，请补充可访问权限或图片内容，"
            "若不影响验收，请说明可忽略的依据后继续。"
        )
    if any(keyword in text for keyword in ("账号", "密码", "权限", "认证", "连接失败", "网络不可用")):
        return (
            "请判断目标环境是否为本次验收的必要条件；若是，请提供可用访问方式或完成授权，"
            "若可以用本地自动化证据代替，请写明可接受的验证边界。"
        )
    if any(keyword in text for keyword in ("需求不明确", "业务规则", "业务口径", "验收标准", "验收条件")):
        return (
            "请确认影响实现的业务规则或验收口径；给出明确选择后，任务将在原隔离工作区继续。"
        )
    return (
        "请判断该风险是否会阻止需求完整交付；若风险可接受或已有等价证据，请写明判断依据并继续，"
        "若不可接受，请指出必须补齐的条件。"
    )


def summarize_blocker(detail: dict[str, Any]) -> dict[str, str]:
    """Turn a technical waiting-approval error into a user-facing decision brief."""
    reason = _clean_reason(detail.get("error_message") or detail.get("current_activity"))
    return {
        "reason": reason,
        "decision_required": _decision_required(reason),
    }
