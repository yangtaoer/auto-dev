"""The control plane must not call an unverified package a completed release."""
from __future__ import annotations

import json
import re


def requires_integrity(project: dict) -> bool:
    return not project.get("simulation_mode") and (
        project.get("project_key") == "guangan-network-command"
        or bool(((project.get("quality_profile") or {}).get("release_integrity") or {}).get("required")))


def verified(result: dict) -> bool:
    proof = result.get("releaseIntegrity") or {}
    modules = proof.get("modules") if isinstance(proof, dict) else None
    return (isinstance(proof, dict) and proof.get("status") == "verified" and proof.get("preEncryptionModulesVerified") is True
            and proof.get("postEncryptionResourcesVerified") is True and bool(proof.get("modules"))
            and isinstance(modules, list) and all(isinstance(p, dict) and p.get("verified") is True for p in modules))


def commits_for_release(details: list[dict]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for detail in details:
        for state in detail.get("repository_states") or []:
            for key in ("commit_hash", "merge_commit"):
                commit = state.get(key)
                if not commit:
                    continue
                if not re.fullmatch(r"[a-f0-9]{40}", commit):
                    raise RuntimeError("待发版需求存在无法核对的提交，未执行构建")
                result.setdefault(state["name"], set()).add(commit)
    return {name: sorted(commits) for name, commits in sorted(result.items())}


def existing_proof(detail: dict, artifact_url: str) -> dict:
    for event in detail.get("events") or []:
        if event.get("event_type") not in {"release.completed", "release.shared"}:
            continue
        result = event.get("metadata") or {}
        if isinstance(result, str):
            result = json.loads(result)
        if event["event_type"] == "release.shared":
            result = result.get("result") or {}
        if result.get("artifactsUrl") == artifact_url and verified(result):
            return result
    raise RuntimeError("已有交付包缺少目标提交和依赖校验证据，不能继续将旧产物标记为已交付")
