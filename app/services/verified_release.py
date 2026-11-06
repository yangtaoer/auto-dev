"""Atomically record a verified package and retain superseded artifacts for audit."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..db import transaction, utc_now
from .release_policy import requires_integrity, verified


def record(request_id: str, result: dict) -> dict:
    if not verified(result) or result.get("result") != "succeeded" or result.get("expectedArtifactFound") is not True:
        raise ValueError("产物缺少完整校验证据，不能更新交付记录")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 250000:
        raise ValueError("发布校验证据超过记录大小限制")
    workflow = json.loads(Path(__file__).with_name("release_workflows.json").read_text("utf-8"))["projects"]["广安网络发令"]
    expected = {p["repository"]: p["definitionId"] for p in workflow["pipelines"] if p["purpose"] == "dependency"}
    modules = result["releaseIntegrity"]["modules"]
    actual = {m.get("repository"): m.get("definitionId") for m in modules}
    if actual != expected or len(modules) != len(expected) or result.get("definitionId") != 5481:
        raise ValueError("产物校验的仓库或流水线范围不完整")
    if result["releaseIntegrity"].get("version") != 1 or any(
        not re.fullmatch(r"[a-f0-9]{64}", str(m.get("moduleSha256", "")))
        or not re.fullmatch(r"[a-f0-9]{40}", str(m.get("sourceVersion", "")))
        or not isinstance(m.get("buildId"), int) or m["buildId"] <= 0 for m in modules
    ):
        raise ValueError("依赖校验回执缺少可核对的构建、提交或哈希")
    url = str(result.get("artifactsUrl") or "")
    parsed = urlsplit(url)
    build_id = int(result.get("buildId") or 0)
    if build_id <= 0 or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("发布产物地址不合法")
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM delivery_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在")
        policy = json.loads(row["policy_snapshot"])
        base = urlsplit(policy.get("tfs_collection_url", ""))
        if (not requires_integrity(policy) or policy.get("project_key") != "guangan-network-command"
                or row["task_type"] != "development" or parsed.scheme != base.scheme or parsed.netloc.casefold() != base.netloc.casefold()
                or parsed.path.casefold() != (base.path.rstrip("/") + "/DCS/_build/results").casefold()
                or parse_qs(parsed.query).get("buildId") != [str(build_id)]):
            raise ValueError("发布产物不属于当前任务的项目范围")
        current = conn.execute("SELECT id FROM delivery_artifacts WHERE request_id=? AND kind='release_artifact' AND external_url=?",
                               (request_id, url)).fetchone()
        evidence = conn.execute("SELECT 1 FROM delivery_events WHERE request_id=? AND event_type='release.completed' AND metadata=?",
                                (request_id, encoded)).fetchone()
        if current and evidence:
            return {"artifact_id": current["id"], "idempotent": True}
        if row["status"] != "releasing":
            raise ValueError("任务不在发版阶段，不能替换交付物")
        old = [r["id"] for r in conn.execute("SELECT id FROM delivery_artifacts WHERE request_id=? AND kind='release_artifact'", (request_id,))]
        conn.execute("UPDATE delivery_artifacts SET kind='superseded_release_artifact' WHERE request_id=? AND kind='release_artifact'", (request_id,))
        now = utc_now()
        cursor = conn.execute("""INSERT INTO delivery_artifacts(request_id,kind,name,external_url,created_at)
            VALUES(?,'release_artifact',?,?,?)""", (request_id, f"自动发版 · {result.get('pipelineName', '广安')} · Build #{build_id}", url, now))
        conn.execute("""INSERT INTO delivery_events(request_id,event_type,level,message,metadata,created_at)
            VALUES(?,'release.completed','info',?,?,?)""", (request_id, f"自动发版成功：Build #{build_id}；依赖模块与包内文件校验通过", encoded, now))
        conn.execute("""INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at)
            VALUES(NULL,'release.verified_recorded','request',?,?,?)""", (request_id,
            json.dumps({"build_id": build_id, "artifact_id": cursor.lastrowid, "superseded_artifact_ids": old}), now))
        return {"artifact_id": cursor.lastrowid, "superseded_artifact_ids": old, "idempotent": False}
