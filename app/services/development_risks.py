from __future__ import annotations

import re


def critical_risk(value: str) -> bool:
    """Only substantive logic conflicts/security hazards warrant human judgment."""
    text = re.sub(r"(?:未发现|不存在|没有|无)(?:重大|严重|核心)?(?:安全隐患|安全风险|逻辑冲突)", "", value)
    return bool(re.search(
        r"(?:核心|重要|严重|重大|现有|代码|业务)(?:代码|业务)?(?:逻辑|规则|口径)?冲突|"
        r"(?:重大|严重|高危)安全|越权|权限绕过|密钥泄露|数据泄露|不可逆|数据丢失|"
        r"破坏.*(?:生产|数据)|受保护路径|critical.*(?:conflict|security)|data loss|security vulnerability",
        text, re.I,
    ))


def advisory_risk(value: str) -> bool:
    if re.search(r"(?:功能|接口|页面接入|验收项).*(?:未实现|未完成|缺失)|(?:构建|测试|断言)失败", value):
        return False
    return not critical_risk(value) and any(term in value.lower() for term in (
        "截图", "浏览器后端", "asset_manifest_checked", "directory_layout_checked",
        "cache_strategy_checked", "前端部署验证", "证据是否足够", "证据是否足以",
    ))


def development_risks(result: dict, *, legacy_review: bool = False) -> tuple[list[str], list[str]]:
    """Legacy advisory notes never require approval; real incomplete work stays blocked."""
    warnings = [str(value).strip() for value in result.get("risks", []) if str(value).strip()]
    if "blocking_risks" not in result:
        blockers = []
    else:
        raw = result["blocking_risks"]
        if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
            raise RuntimeError("研发结果 blocking_risks 格式无效，不能自动放行")
        blockers = [value.strip() for value in raw if value.strip() and not advisory_risk(value)]
        warnings.extend(value.strip() for value in raw if value.strip() and advisory_risk(value))
    blockers.extend(value for value in warnings if critical_risk(value) and value not in blockers)
    warnings = [value for value in warnings if value not in blockers]
    return warnings, blockers
