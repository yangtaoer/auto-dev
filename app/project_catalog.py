from __future__ import annotations

import json
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


def resolve_project_for_work_item(
    work_item_id: int,
    projects: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = projects if projects is not None else load_project_presets()
    fetched: dict[str, dict[str, Any]] = {}
    fetch_errors: list[str] = []
    candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
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
        title = str(item.get("title", "")).casefold()
        keywords = [str(value).strip() for value in project.get("routing_title_keywords", []) if str(value).strip()]
        matched_keywords = [value for value in keywords if value.casefold() in title]
        if keywords and not matched_keywords:
            continue
        keyword_score = max((len(value) for value in matched_keywords), default=0)
        score = len(match_prefix) * 1000 + (500 + keyword_score if matched_keywords else 0)
        candidates.append((score, project, item))

    if not candidates:
        if not fetched and fetch_errors:
            raise RuntimeError(f"无法读取 TFS #{work_item_id}：{fetch_errors[0]}")
        fetched_item = next(iter(fetched.values()), {})
        area = fetched_item.get("area_path", "未知")
        if area_matched:
            raise RuntimeError(
                f"TFS #{work_item_id} 的 Area Path“{area}”已进入自助范围，"
                f"但标题“{fetched_item.get('title', '')}”未命中任何项目路由关键字"
            )
        raise RuntimeError(f"TFS #{work_item_id} 的 Area Path“{area}”未匹配任何本机项目预设")

    candidates.sort(key=lambda entry: entry[0], reverse=True)
    best_score = candidates[0][0]
    best = [entry for entry in candidates if entry[0] == best_score]
    if len(best) > 1:
        keys = "、".join(entry[1].get("project_key", "未命名") for entry in best)
        raise RuntimeError(f"TFS #{work_item_id} 同时匹配多个项目预设：{keys}，请细化 Area Path")
    return best[0][1], best[0][2]
