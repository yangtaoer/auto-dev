"""Read official TFS validation for one exact PR source and merge preview."""
from __future__ import annotations

import re


_SHA = re.compile(r"[0-9a-fA-F]{40}")


def current_build_policies(tfs, pr: dict) -> list[dict]:
    source, merge = pr.get("source_commit") or "", pr.get("merge_commit") or ""
    if not _SHA.fullmatch(source) or not _SHA.fullmatch(merge):
        return []
    result = []
    for item in tfs.pull_request_policies(pr):
        config = item.get("configuration") or {}
        context = item.get("context") or {}
        definition = str((config.get("settings") or {}).get("buildDefinitionId") or "")
        build = str(context.get("buildId") or "")
        if (config.get("isEnabled") and config.get("isBlocking") and definition.isdecimal() and int(definition) > 0
                and build.isdecimal() and int(build) > 0 and context.get("lastMergeSourceCommitId") == source
                and context.get("lastMergeCommitId") == merge
                and not context.get("buildIsNotCurrent") and not context.get("isExpired")):
            result.append({"definition_id": int(definition), "build_id": int(build),
                           "status": item.get("status") or "unknown",
                           "evaluation_id": str(item.get("evaluationId") or ""),
                           "name": str(context.get("buildDefinitionName") or "TFS 构建验证")})
    return result


def evaluate(tfs, pr: dict, definitions: tuple[int, ...]) -> dict:
    policies = current_build_policies(tfs, pr)
    receipt = {"pr_id": pr["id"], "source_commit": pr.get("source_commit"), "merge_commit": pr.get("merge_commit"),
               "target_commit": pr.get("target_commit"), "builds": policies, "status": "observed"}
    if not definitions:
        return receipt
    receipt["status"] = "pending"
    receipt["required_build_definitions"] = list(definitions)
    current = []
    for definition in definitions:
        found = [item for item in policies if item["definition_id"] == definition]
        if len(found) == 1:
            current.append(found[0])
    if any(build["status"] == "rejected" for build in current):
        receipt["status"] = "rejected"
    elif len(current) == len(definitions) and all(
        build["status"] == "approved"
        and tfs.successful_pull_request_build(pr, build["build_id"], build["definition_id"])
        for build in current
    ):
        receipt["status"] = "passed"
    return receipt


def waiting_message(receipt: dict) -> str:
    if receipt["status"] == "rejected":
        return f"PR #{receipt['pr_id']} 当前合并预览的正式构建扫描未通过"
    return f"PR #{receipt['pr_id']} 正在等待当前源提交和合并预览的正式扫描回执"
