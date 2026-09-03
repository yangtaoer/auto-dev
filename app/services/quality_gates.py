from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from .delivery import added_files, menu_link_from_view_path


_MIGRATION_VERSION = re.compile(r"(?i)(?:^|[/\\])V(?P<version>\d+(?:[._]\d+)*)[^/\\]*\.sql$")
_UNQUALIFIED_BUSINESS_TABLE = re.compile(
    r"(?im)\b(?:from|join|update|into|delete\s+from|merge\s+into|table)\s+(TH_[A-Z0-9_]+)\b"
)


def _matches(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) or Path(normalized).match(pattern) for pattern in patterns)


def _normalized_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./").casefold()


def _path_is_mapped(path: str, mapped: set[str]) -> bool:
    normalized = _normalized_path(path)
    return any(
        normalized == candidate
        or normalized.endswith("/" + candidate)
        or candidate.endswith("/" + normalized)
        for candidate in mapped
        if candidate
    )


def _requirement_requests_visual_evidence(profile: dict[str, Any], requirement_text: str) -> bool:
    """Keep screenshots non-blocking for every project, including stale task snapshots."""
    del profile, requirement_text
    return False


def normalize_acceptance_ledger(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert legacy string mappings while preserving the richer new contract."""
    value = result.get("acceptance_ledger")
    if isinstance(value, list) and value:
        normalized = []
        for index, item in enumerate(value, 1):
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "id": str(item.get("id") or f"AC-{index}"),
                    "criterion": str(item.get("criterion") or "").strip(),
                    "status": str(item.get("status") or "completed"),
                    "repositories": [str(entry) for entry in item.get("repositories") or []],
                    "files": [str(entry) for entry in item.get("files") or []],
                    "tests": [str(entry) for entry in item.get("tests") or []],
                    "evidence": [str(entry) for entry in item.get("evidence") or []],
                }
            )
        return normalized
    legacy = result.get("acceptance_mapping")
    if not isinstance(legacy, list):
        return []
    return [
        {
            "id": f"AC-{index}",
            "criterion": str(item),
            "status": "completed",
            "repositories": [],
            "files": [],
            "tests": [],
            "evidence": ["历史兼容映射，未提供结构化证据"],
        }
        for index, item in enumerate(legacy, 1)
        if str(item).strip()
    ]


def _check(checks: list[dict[str, Any]], check_id: str, name: str, status: str, detail: str, evidence: list[str] | None = None) -> None:
    checks.append(
        {
            "id": check_id,
            "name": name,
            "status": status,
            "detail": detail,
            "evidence": evidence or [],
        }
    )


def evaluate_development_quality(
    project: dict[str, Any],
    repository_states: list[dict[str, Any]],
    result: dict[str, Any],
    *,
    requirement_text: str = "",
) -> dict[str, Any]:
    profile = project.get("quality_profile") or {}
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    changed_paths = [
        f"{state.get('name')}/{relative}"
        for state in repository_states
        for relative in state.get("changed_files") or []
    ]
    ledger = normalize_acceptance_ledger(result)

    if profile.get("require_acceptance_ledger"):
        if not result.get("acceptance_ledger"):
            blockers.append("缺少结构化验收项账本，不能建立验收项与文件、测试之间的映射")
        incomplete = [item["id"] for item in ledger if item.get("status") not in {"completed", "not_applicable"}]
        if incomplete:
            blockers.append("存在未完成验收项：" + "、".join(incomplete))
        mapped_files = {_normalized_path(path) for item in ledger for path in item.get("files") or []}
        unmapped = [path for path in changed_paths if not _path_is_mapped(path, mapped_files)]
        if changed_paths and unmapped:
            blockers.append("存在未归属验收项的变更文件：" + "、".join(unmapped[:12]))
        missing_evidence = [
            item["id"] for item in ledger
            if item.get("status") == "completed" and not (item.get("tests") or item.get("evidence"))
        ]
        if missing_evidence:
            blockers.append("已完成验收项缺少测试或证据：" + "、".join(missing_evidence))
        _check(
            checks,
            "acceptance-ledger",
            "验收项变更账本",
            "blocked" if any("验收项" in item or "变更文件" in item for item in blockers) else "passed",
            f"已登记 {len(ledger)} 个验收项、{len(changed_paths)} 个实际变更文件",
        )

    invariant_profile = profile.get("business_invariants") or {}
    semantic_patterns = invariant_profile.get("change_patterns") or []
    semantic_change = bool(invariant_profile.get("required")) or any(
        _matches(path, semantic_patterns) for path in changed_paths
    )
    invariants = [item for item in result.get("business_invariants") or [] if isinstance(item, dict)]
    if semantic_change:
        if not invariants:
            blockers.append("业务语义变更未提供数据不变量校验")
        unverified = [str(item.get("name") or "未命名校验") for item in invariants if item.get("status") == "unverified"]
        if unverified:
            blockers.append("业务数据不变量尚未验证：" + "、".join(unverified))
        _check(
            checks,
            "business-invariants",
            "业务数据不变量",
            "blocked" if not invariants or unverified else "passed",
            f"已声明 {len(invariants)} 项业务公式、口径或数据一致性检查",
            [str(item.get("evidence") or item.get("source") or "") for item in invariants],
        )

    sql_profile = profile.get("sql") or {}
    sql_changed: list[tuple[dict[str, Any], str, Path]] = []
    for state in repository_states:
        root = Path(str(state.get("worktree_path") or ""))
        for relative in state.get("changed_files") or []:
            if str(relative).lower().endswith(".sql"):
                sql_changed.append((state, str(relative), root / str(relative)))
    if sql_changed and sql_profile:
        sql_blockers: list[str] = []
        if sql_profile.get("migration_version_guard", True):
            for state, relative, _ in sql_changed:
                match = _MIGRATION_VERSION.search(relative.replace("\\", "/"))
                if not match:
                    continue
                version = match.group("version").replace("_", ".")
                root = Path(str(state.get("worktree_path") or ""))
                collisions = []
                for candidate in root.rglob("*.sql"):
                    candidate_relative = candidate.relative_to(root).as_posix()
                    candidate_match = _MIGRATION_VERSION.search(candidate_relative)
                    if candidate_match and candidate_match.group("version").replace("_", ".") == version:
                        collisions.append(candidate_relative)
                if len(set(collisions)) > 1:
                    sql_blockers.append(f"迁移版本 V{version} 冲突：{'、'.join(sorted(set(collisions)))}")
        if sql_profile.get("require_schema_placeholder"):
            for _, relative, path in sql_changed:
                if not path.is_file():
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                tables = sorted(set(_UNQUALIFIED_BUSINESS_TABLE.findall(content)))
                if tables:
                    sql_blockers.append(f"{relative} 存在未使用 ${{BIZ_SCHEMA}} 的业务表：{'、'.join(tables)}")
        for _, relative, path in sql_changed:
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?is)alter\s+table.+add.+\b(?:clob|blob)\b", content):
                sql_blockers.append(f"{relative} 直接向既有表增加 LOB 字段，需按 DM7 聚集键限制复核或改用独立数据表")
            if re.search(r"(?is)cluster\s+primary\s+key", content) and re.search(r"(?i)\b(?:clob|blob)\b", content):
                sql_blockers.append(f"{relative} 同时包含聚集主键与 LOB 字段，DM7 兼容性未通过")
        changelog_path = str(sql_profile.get("changelog_path") or "docs/sql-changelog.md")
        if sql_profile.get("require_changelog") and any(_MIGRATION_VERSION.search(relative) for _, relative, _ in sql_changed):
            if not any(_normalized_path(path).endswith(_normalized_path(changelog_path)) for path in changed_paths):
                sql_blockers.append(f"版本化 SQL 未同步更新 {changelog_path}")
        database_validation = result.get("database_validation") or {}
        if sql_profile.get("require_database_verification") and database_validation.get("status") != "verified":
            sql_blockers.append("SQL 发生变更，但未通过已标注环境的 DM7 开发库连接完成验证")
        blockers.extend(sql_blockers)
        _check(
            checks,
            "sql-dm7",
            "SQL / DM7 门禁",
            "blocked" if sql_blockers else "passed",
            f"已扫描 {len(sql_changed)} 个变更 SQL",
            [relative for _, relative, _ in sql_changed],
        )

    visual_profile = profile.get("visual") or {}
    frontend_patterns = visual_profile.get("frontend_patterns") or []
    frontend_changed = any(_matches(path, frontend_patterns) for path in changed_paths)
    visual_required = frontend_changed and _requirement_requests_visual_evidence(
        visual_profile, requirement_text
    )
    if visual_required:
        visual = result.get("visual_validation") or {}
        expected_viewports = {str(item) for item in visual_profile.get("viewports") or []}
        actual_viewports = {str(item) for item in visual.get("viewports") or []}
        visual_blocked = visual.get("status") != "passed" or not expected_viewports.issubset(actual_viewports)
        if visual_blocked:
            blockers.append("前端变更未完成项目规定的页面、视口和截图验收")
        _check(
            checks,
            "visual-acceptance",
            "页面视觉验收",
            "blocked" if visual_blocked else "passed",
            "要求视口：" + ("、".join(sorted(expected_viewports)) or "按项目默认"),
            [str(item) for item in visual.get("screenshots") or []],
        )
    elif frontend_changed:
        visual = result.get("visual_validation") or {}
        _check(
            checks,
            "visual-acceptance",
            "页面视觉验收",
            "passed",
            "全平台不以真实页面截图作为交付依据；采用生产构建、真实路由与自动断言证据",
            [
                *[str(item) for item in visual.get("screenshots") or []],
                *[str(item) for item in visual.get("notes") or []],
            ],
        )

    if frontend_changed:
        deployment = result.get("deployment_validation") or {}
        required_checks = visual_profile.get("deployment_checks") or []
        missed = [name for name in required_checks if not deployment.get(name)]
        if missed:
            blockers.append("前端部署验证未完成：" + "、".join(missed))
        _check(
            checks,
            "deployment-assets",
            "前端部署与缓存验证",
            "blocked" if missed else "passed",
            "已检查资源哈希、目录层级和缓存策略" if not missed else "缺少：" + "、".join(missed),
        )

    menu_profile = profile.get("menu") or {}
    if menu_profile.get("require_binding_manifest"):
        required_links: list[str] = []
        for state in repository_states:
            root = Path(str(state.get("worktree_path") or ""))
            base_commit = str(state.get("base_commit") or "")
            if not root.is_dir() or not base_commit:
                continue
            for relative in added_files(root, base_commit):
                link = menu_link_from_view_path(relative)
                if link:
                    required_links.append(link)
        declared = {
            str(item.get("menu_link") or "")
            for item in result.get("menu_changes") or []
            if isinstance(item, dict)
        }
        missing_links = [link for link in required_links if link not in declared]
        if missing_links:
            blockers.append("新增视图缺少菜单绑定清单：" + "、".join(missing_links))
        if required_links:
            _check(
                checks,
                "menu-binding",
                "视图与菜单权限闭环",
                "blocked" if missing_links else "passed",
                f"检测到 {len(required_links)} 个新增视图菜单入口",
                required_links,
            )

    if result.get("decision") == "already_satisfied":
        existing = result.get("existing_implementation") or {}
        if not existing.get("verified") or not existing.get("evidence"):
            blockers.append("声明需求已实现，但没有提供最新目标分支中的提交、PR或代码证据")
        _check(
            checks,
            "existing-implementation",
            "历史实现复用",
            "blocked" if blockers else "passed",
            "已在最新目标分支验证既有实现" if existing.get("verified") else "既有实现证据不足",
            [str(item) for item in existing.get("evidence") or []],
        )

    status = "blocked" if blockers else ("warning" if warnings else "passed")
    return {
        "status": status,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "business_invariants": invariants,
        "database_validation": result.get("database_validation") or {},
        "visual_validation": result.get("visual_validation") or {},
        "deployment_validation": result.get("deployment_validation") or {},
        "menu_changes": result.get("menu_changes") or [],
        "existing_implementation": result.get("existing_implementation") or {},
    }


def evaluate_analysis_quality(
    project: dict[str, Any],
    result: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    profile = (project.get("quality_profile") or {}).get("analysis") or {}
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    environment = result.get("environment") or {}
    if profile.get("require_environment_label") and not str(environment.get("label") or "").strip():
        blockers.append("问题分析未标注代码、数据库或现场数据所属环境")
    _check(
        checks,
        "analysis-environment",
        "分析环境标识",
        "blocked" if blockers else "passed",
        str(environment.get("label") or "未标注环境"),
        [str(environment.get("data_source") or ""), str(environment.get("observed_at") or "")],
    )
    classification = str(result.get("issue_classification") or "unknown")
    if classification == "unknown" and result.get("decision") == "completed":
        blockers.append("问题已结束分析，但未区分代码、数据或环境问题")
    _check(checks, "issue-classification", "问题类型判定", "blocked" if classification == "unknown" else "passed", classification)

    prior_roots = [
        str((item.get("analysis_result") or {}).get("root_cause") or "").strip()
        for item in history
        if item.get("task_type") == "analysis" and item.get("status") == "delivered"
    ]
    current_root = str(result.get("root_cause") or "").strip()
    differs = any(root and current_root and root != current_root for root in prior_roots)
    conflicts = [item for item in result.get("historical_conflicts") or [] if isinstance(item, dict)]
    if differs and not conflicts:
        warnings.append("本次根因与历史分析不同，但未记录矛盾点及其解决依据")
    _check(
        checks,
        "history-comparison",
        "历史分析对照",
        "warning" if differs and not conflicts else "passed",
        f"已对照 {len(prior_roots)} 次历史分析，记录 {len(conflicts)} 个差异",
    )
    evidence = [item for item in result.get("evidence") or [] if isinstance(item, dict)]
    unverifiable = [item for item in evidence if not str(item.get("source") or "").strip()]
    if not evidence or unverifiable:
        blockers.append("分析证据链为空或包含无法复核的来源")
    _check(checks, "evidence-ledger", "可复核证据账本", "blocked" if not evidence or unverifiable else "passed", f"已登记 {len(evidence)} 条证据")
    status = "blocked" if blockers else ("warning" if warnings else "passed")
    return {"status": status, "checks": checks, "blockers": blockers, "warnings": warnings}
