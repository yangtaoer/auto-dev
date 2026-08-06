from __future__ import annotations

import json
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


def resolve_project_for_work_item(
    work_item_id: int,
    projects: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = projects if projects is not None else load_project_presets()
    fetched: dict[str, dict[str, Any]] = {}
    fetch_errors: list[str] = []
    candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []

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
        candidates.append((len(match_prefix), project, item))

    if not candidates:
        if not fetched and fetch_errors:
            raise RuntimeError(f"无法读取 TFS #{work_item_id}：{fetch_errors[0]}")
        area = next(iter(fetched.values()), {}).get("area_path", "未知")
        raise RuntimeError(f"TFS #{work_item_id} 的 Area Path“{area}”未匹配任何本机项目预设")

    candidates.sort(key=lambda entry: entry[0], reverse=True)
    best_score = candidates[0][0]
    best = [entry for entry in candidates if entry[0] == best_score]
    if len(best) > 1:
        keys = "、".join(entry[1].get("project_key", "未命名") for entry in best)
        raise RuntimeError(f"TFS #{work_item_id} 同时匹配多个项目预设：{keys}，请细化 Area Path")
    return best[0][1], best[0][2]
