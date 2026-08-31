from __future__ import annotations


def development_risks(result: dict, *, legacy_review: bool = False) -> tuple[list[str], list[str]]:
    """Separate advisory risks from blockers; never reinterpret old unclassified output."""
    warnings = [str(value).strip() for value in result.get("risks", []) if str(value).strip()]
    if "blocking_risks" not in result:
        blockers = ["旧版研发结果尚未区分风险等级，请人工确认：" + "；".join(warnings)] if legacy_review and warnings else []
    else:
        raw = result["blocking_risks"]
        if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
            raise RuntimeError("研发结果 blocking_risks 格式无效，不能自动放行")
        blockers = [value.strip() for value in raw if value.strip()]
    return warnings, blockers
