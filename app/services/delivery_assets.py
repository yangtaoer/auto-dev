"""Reconcile changed files, declared deployment files and observed database writes."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .artifact_contract import changed_asset_kind, safe_asset_path
from .database_audit import sql_fingerprint


def _has_changes(values: list) -> bool:
    return any(str(value).strip().casefold().rstrip("。.") not in {
        "", "无", "无变更", "无sql变更", "无 sql 变更", "无配置变更", "无数据库变更", "none", "not_applicable", "无需修改",
    } for value in values)


def reconcile_delivery_assets(project: dict, states: list[dict], result: dict) -> tuple[list[dict], list[str]]:
    """No project can opt out; gaps go through the ordinary same-thread repair loop."""
    if project.get("simulation_mode"):
        return [], []
    declared = [item for item in result.get("delivery_assets") or [] if isinstance(item, dict)]
    records = [item for item in result.get("database_changes") or [] if isinstance(item, dict)]
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    blockers: list[str] = []
    file_contents: dict[str, str] = {}
    temporary_paths = {
        str(path).replace("\\", "/")
        for record in records if record.get("classification") == "temporary_verification" and str(record.get("evidence") or "").strip()
        for path in record.get("script_paths") or []
    }
    states_by_name = {str(state.get("name") or ""): state for state in states}

    def add_asset(state: dict, relative: str, kind: str, purpose: str = "") -> None:
        name = str(state.get("name") or "")
        path_key = f"{name}/{relative}"
        if kind == "sql" and (path_key in temporary_paths or relative in temporary_paths):
            return
        root = Path(str(state.get("worktree_path") or "")).resolve()
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            blockers.append(f"已声明的交付脚本/配置不在仓库内或不存在：{path_key}")
            return
        content = source.read_text(encoding="utf-8-sig", errors="replace")
        executable = re.sub(r"(?s)/\*.*?\*/|(?m:--[^\n]*)", "", content).strip()
        if not executable:
            blockers.append(f"交付脚本/配置为空或只有注释：{path_key}")
            return
        file_contents[path_key] = content
        assets[(name, relative)] = {"repository": name, "path": relative, "kind": kind, "purpose": purpose}

    for state in states:
        for value in state.get("changed_files") or []:
            relative = str(value).replace("\\", "/")
            kind = changed_asset_kind(relative, project)
            # Deleted assets are not new files to deliver; any required replacement is declared below.
            if kind and (Path(str(state.get("worktree_path") or "")) / relative).is_file():
                add_asset(state, relative, kind)
    for item in declared:
        name, relative, kind = (str(item.get(key) or "") for key in ("repository", "path", "kind"))
        relative = relative.replace("\\", "/")
        state = states_by_name.get(name)
        if kind not in {"sql", "config"} or not state or not safe_asset_path(relative) or changed_asset_kind(relative, project, declared_kind=kind) != kind:
            blockers.append(f"交付文件声明不合法（仅 SQL/必要配置，不能是源码/测试文件）：{name}/{relative}")
            continue
        add_asset(state, relative, kind, str(item.get("purpose") or ""))

    sql_assets = [item for item in assets.values() if item["kind"] == "sql"]
    config_assets = [item for item in assets.values() if item["kind"] == "config"]
    if _has_changes(result.get("sql_changes") or []) and not sql_assets:
        blockers.append("结果声明存在业务 SQL 变更，但未保存可交付的非空 .sql 文件；请补齐脚本及 delivery_assets 路径")
    if _has_changes(result.get("config_changes") or []) and not config_assets:
        blockers.append("结果声明存在部署配置变更，但缺少配置文件/配置模板；请补齐并填写 delivery_assets")

    for field, kind in (("sql_changes", "sql"), ("config_changes", "config")):
        keys = {f"{item['repository']}/{item['path']}" for item in assets.values() if item["kind"] == kind}
        for value in result.get(field) or []:
            for mentioned in re.findall(r"[\w./\\${}-]+\.(?:sql|properties|ya?ml|conf|ini|cfg|template|example)\b", str(value), re.I):
                mentioned = mentioned.replace("\\", "/")
                if not any(key == mentioned or key.endswith("/" + mentioned) for key in keys):
                    blockers.append(f"结果中提及的 {kind} 部署文件未归集：{mentioned}；请核对 delivery_assets 及实际文件")

    if not sql_assets and any(re.search(
        r"(?i)(?:^\s*(?:CREATE\s+TABLE|ALTER\s+TABLE|INSERT\s+INTO|UPDATE\s+\w+\s+SET)|"
        r"(?:已|执行|完成)(?:业务)?(?:建表|改表|数据迁移|数据修复|新增表|新增字段))", str(value)
    ) for value in result.get("database_operations") or []):
        blockers.append("数据库操作记录包含业务写入/建表，但没有正式 SQL 交付文件；不能只在开发库执行")

    sql_keys = {f"{item['repository']}/{item['path']}" for item in sql_assets}
    def has_sql(path: object) -> bool:
        value = str(path).replace("\\", "/")
        return bool(value) and (value in sql_keys or sum(key.endswith("/" + value) for key in sql_keys) == 1)

    for record in records:
        if record.get("classification") == "business_change" and (
            not record.get("script_paths") or not all(has_sql(path) for path in record["script_paths"])
        ):
            blockers.append("业务数据库变更没有对应的正式 SQL 文件（包括建表、改表及迁移），请补齐 script_paths")
        if record.get("classification") == "temporary_verification" and not str(record.get("evidence") or "").strip():
            blockers.append("临时数据库验证缺少清理或事务回滚证据，不能据此排除业务脚本")

    fingerprints = {sql_fingerprint(file_contents[key]) for key in sql_keys}
    for call in result.get("database_write_calls") or []:
        call_id = str(call.get("call_id") or "")
        matched = [record for record in records if call_id in record.get("call_ids", [])]
        business = call.get("purpose") in {"production_change", "migration"}
        valid_record = any(
            (record.get("classification") == "business_change" and record.get("script_paths")
             and all(has_sql(path) for path in record["script_paths"]))
            or (not business and record.get("classification") == "temporary_verification" and record.get("evidence"))
            for record in matched
        )
        if not valid_record and call.get("sql_sha256") not in fingerprints:
            blockers.append(
                f"数据库写入 {call_id}（purpose={call.get('purpose')}, status={call.get('status')}）尚无交付脚本映射；"
                "请在 database_changes.call_ids 记录此 ID 并提供业务 SQL 路径，或为临时验证提供清理/回滚证据"
            )
    return list(assets.values()), list(dict.fromkeys(blockers))
