from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from .config import settings
from .services.tfs import TfsClient


def load_project_presets() -> list[dict[str, Any]]:
    preset_dir = settings.project_preset_dir
    preset_dir.mkdir(parents=True, exist_ok=True)
    projects: list[dict[str, Any]] = []
    for path in sorted(preset_dir.glob("*.json")):
        try:
            project = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"项目预设无法读取：{path.name}：{exc}") from exc
        if not isinstance(project, dict):
            raise RuntimeError(f"项目预设必须是 JSON 对象：{path.name}")
        project["runner_id"] = settings.runner_id
        projects.append(project)
    return projects


def update_project_routing_aliases(project_key: str, aliases: list[str]) -> dict[str, Any]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in aliases:
        alias = str(value).strip()
        folded = alias.casefold()
        if alias and folded not in seen:
            normalized.append(alias)
            seen.add(folded)
    if not normalized:
        raise ValueError("项目别名不能为空，请至少填写一个用于识别需求标题的关键词")

    preset_dir = settings.project_preset_dir
    preset_dir.mkdir(parents=True, exist_ok=True)
    target_path: Path | None = None
    target_project: dict[str, Any] | None = None
    for path in sorted(preset_dir.glob("*.json")):
        try:
            project = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"项目预设无法读取：{path.name}：{exc}") from exc
        if isinstance(project, dict) and str(project.get("project_key", "")) == project_key:
            target_path, target_project = path, project
            break
    if target_path is None or target_project is None:
        raise KeyError(f"未找到本机项目预设：{project_key}")

    target_project["routing_title_keywords"] = normalized
    temporary_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(target_project, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(target_path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"项目别名保存失败：{target_path.name}：{exc}") from exc
    return {**target_project, "runner_id": settings.runner_id}


def _plain_requirement_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def _requirement_sections(item: dict[str, Any]) -> list[str]:
    """Return compact requirement clauses that can be assigned to individual projects."""
    sections: list[str] = []
    for raw in (item.get("description"), item.get("acceptance_criteria")):
        value = html.unescape(str(raw or ""))
        value = re.sub(r"<(?:br|/p|/li|/div|/tr|/h[1-6])\b[^>]*>", "\n", value, flags=re.IGNORECASE)
        value = re.sub(r"<[^>]+>", " ", value)
        for part in re.split(r"[\r\n]+|(?<=[。；;])", value):
            section = re.sub(r"\s+", " ", part).strip(" \t-•，,；;")
            if section and section not in sections:
                sections.append(section[:600])
    return sections[:40]


def resolve_projects_for_work_item(
    work_item_id: int,
    projects: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Classify one TFS requirement into every strongly evidenced project."""
    catalog = projects if projects is not None else load_project_presets()
    fetched: dict[str, dict[str, Any]] = {}
    fetch_errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    area_fallbacks: list[dict[str, Any]] = []
    area_matched = False

    for project in catalog:
        if not project.get("enabled", True) or project.get("simulation_mode"):
            continue
        collection = str(project.get("tfs_collection_url", "")).rstrip("/")
        if not collection:
            continue
        if collection not in fetched:
            try:
                fetched[collection] = TfsClient(collection).get_work_item(work_item_id)
            except Exception as exc:
                fetch_errors.append(f"{collection}: {exc}")
                continue
        item = fetched[collection]
        area_path = str(item.get("area_path", "")).strip().rstrip("\\")
        configured_area = str(project.get("tfs_area_path", "")).strip().rstrip("\\")
        fallback_project = str(project.get("tfs_project", "")).strip().rstrip("\\")
        match_prefix = configured_area or fallback_project
        if match_prefix:
            normalized_area = area_path.casefold()
            normalized_prefix = match_prefix.casefold()
            if normalized_area != normalized_prefix and not normalized_area.startswith(f"{normalized_prefix}\\"):
                continue
        area_matched = True
        title_value = str(item.get("title", ""))
        title = title_value.casefold()
        bracket_values = [value.strip() for value in re.findall(r"【([^】]+)】", title_value) if value.strip()]
        standard_name = str(project.get("name") or "").strip()
        explicit_values = [value for value in bracket_values if value.casefold() == standard_name.casefold()]
        keywords = list(
            dict.fromkeys(
                [
                    standard_name,
                    *[
                        str(value).strip()
                        for value in project.get("routing_title_keywords", [])
                        if str(value).strip()
                    ],
                ]
            )
        )
        keywords = [value for value in keywords if value]
        matched_title_keywords = [value for value in keywords if value and value.casefold() in title]
        requirement_body = " ".join(
            (
                _plain_requirement_text(item.get("description")),
                _plain_requirement_text(item.get("acceptance_criteria")),
            )
        ).casefold()
        matched_content_keywords = [
            value
            for value in keywords
            if len(value) >= 4 and value.casefold() in requirement_body
        ]
        if not standard_name and not keywords:
            area_fallbacks.append(
                {
                    "score": len(match_prefix) * 1000,
                    "project": project,
                    "item": item,
                    "source": "area_path",
                    "matched_terms": [match_prefix] if match_prefix else [],
                    "explicit_order": 10_000,
                }
            )
        if not explicit_values and not matched_title_keywords and not matched_content_keywords:
            continue
        if explicit_values:
            source = "title_standard_name"
            matched = explicit_values
            source_score = 100_000
        elif matched_title_keywords:
            source = "title_alias"
            matched = matched_title_keywords
            source_score = 10_000
        else:
            source = "requirement_content"
            matched = matched_content_keywords
            source_score = 1_000
        matched = sorted(set(matched), key=lambda value: (-len(value), value))
        score = len(match_prefix) * 1000 + source_score + max((len(value) for value in matched), default=0)
        candidates.append(
            {
                "score": score,
                "project": project,
                "item": item,
                "source": source,
                "matched_terms": matched,
                "explicit_order": min(
                    (bracket_values.index(value) for value in explicit_values if value in bracket_values),
                    default=10_000,
                ),
            }
        )

    if not candidates and area_fallbacks:
        best_score = max(item["score"] for item in area_fallbacks)
        candidates = [item for item in area_fallbacks if item["score"] == best_score]

    if not candidates:
        if not fetched and fetch_errors:
            raise RuntimeError(f"无法读取 TFS #{work_item_id}：{fetch_errors[0]}")
        fetched_item = next(iter(fetched.values()), {})
        area = fetched_item.get("area_path", "未知")
        if area_matched:
            raise RuntimeError(
                f"TFS #{work_item_id} 的 Area Path“{area}”已进入自主范围，"
                f"但标题“{fetched_item.get('title', '')}”未命中任何项目路由关键字"
            )
        raise RuntimeError(f"TFS #{work_item_id} 的 Area Path“{area}”未匹配任何本机项目预设")

    source_order = {"title_standard_name": 0, "title_alias": 1, "requirement_content": 2, "area_path": 3}
    candidates.sort(
        key=lambda entry: (
            source_order.get(entry["source"], 9),
            entry["explicit_order"],
            -entry["score"],
            str(entry["project"].get("name") or ""),
        )
    )
    selected_projects: list[dict[str, Any]] = []
    classification: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    requirement_sections = _requirement_sections(candidates[0]["item"])
    all_catalog_terms = {
        str(value).strip().casefold()
        for entry in candidates
        for value in (
            entry["project"].get("name"),
            *(entry["project"].get("routing_title_keywords") or []),
        )
        if str(value or "").strip()
    }
    shared_sections = [
        section
        for section in requirement_sections
        if not any(term in section.casefold() for term in all_catalog_terms)
    ][:12]
    for candidate in candidates:
        project = candidate["project"]
        project_key = str(project.get("project_key") or "")
        if not project_key or project_key in seen_keys:
            continue
        seen_keys.add(project_key)
        selected_projects.append(project)
        classification.append(
            {
                "project_key": project_key,
                "project_name": project.get("name") or project_key,
                "source": candidate["source"],
                "matched_terms": candidate["matched_terms"],
                "scoped_sections": [
                    section
                    for section in requirement_sections
                    if any(
                        str(term).casefold() in section.casefold()
                        for term in (
                            project.get("name"),
                            *(project.get("routing_title_keywords") or []),
                        )
                        if str(term or "").strip()
                    )
                ][:12],
                "shared_sections": shared_sections,
            }
        )
    if len(selected_projects) > 5:
        names = "、".join(str(project.get("name") or project.get("project_key")) for project in selected_projects)
        raise RuntimeError(f"需求共匹配 {len(selected_projects)} 个项目（{names}），超过单次联合研发上限 5 个，请拆分需求")
    return selected_projects, candidates[0]["item"], classification


def resolve_project_for_work_item(
    work_item_id: int,
    projects: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Backward-compatible single-project resolver used by existing integrations."""
    matched, item, _ = resolve_projects_for_work_item(work_item_id, projects)
    if len(matched) > 1:
        keys = "、".join(str(project.get("project_key") or "未命名") for project in matched)
        raise RuntimeError(f"TFS #{work_item_id} 同时匹配多个项目预设：{keys}")
    return matched[0], item
