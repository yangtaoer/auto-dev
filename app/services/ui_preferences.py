"""Account-owned appearance preferences; no business or model settings involved."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import ROOT
from app.db import row, transaction, utc_now
from app.services import ui_media

DEFAULTS = {"theme_id": "mint-garden", "font_size": "normal", "density": "compact", "motion": "full"}
CHOICES = {"font_size": {"normal", "large"}, "density": {"compact", "comfortable"},
           "motion": {"full", "reduced", "static"}}


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return json.loads((ROOT / "app/static/themes/catalog.json").read_text("utf-8"))


def theme(theme_id: str | None, user_id: int | None = None) -> dict[str, Any]:
    if str(theme_id or '').startswith('workshop-'):
        from .theme_workshop import resolve_theme
        custom=resolve_theme(theme_id,user_id)
        if custom:return custom
    themes = catalog()["themes"]
    return next((item for item in themes if item["id"] == theme_id), themes[0])


def style(theme_id: str | None, user_id: int | None = None) -> str:
    # Values come exclusively from the shipped catalog, never from user CSS.
    return ";".join(f"--{key}:{value}" for key, value in ui_media.theme_media(theme(theme_id,user_id))["tokens"].items())


def get(user_id: int) -> dict[str, Any]:
    saved = row("SELECT theme_id,font_size,density,motion,updated_at FROM user_ui_preferences WHERE user_id=?", (user_id,))
    result = dict(DEFAULTS)
    if saved:
        result.update(saved)
    result["theme_id"] = theme(result["theme_id"],user_id)["id"]
    for key, options in CHOICES.items():
        if result[key] not in options:
            result[key] = DEFAULTS[key]
    return result


def save(user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    if set(values) != set(DEFAULTS):
        raise ValueError("外观设置字段不完整或包含未知字段")
    from .theme_workshop import themes_for_user
    if values["theme_id"] not in {item["id"] for item in catalog()["themes"]+themes_for_user(user_id)}:
        raise ValueError("所选主题不可用")
    for key, options in CHOICES.items():
        if values[key] not in options:
            raise ValueError("外观设置选项无效")
    with transaction() as conn:
        previous=conn.execute('SELECT theme_id FROM user_ui_preferences WHERE user_id=?',(user_id,)).fetchone()
        conn.execute("""INSERT INTO user_ui_preferences(user_id,theme_id,font_size,density,motion,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET theme_id=excluded.theme_id,
            font_size=excluded.font_size,density=excluded.density,motion=excluded.motion,updated_at=excluded.updated_at""",
            (user_id, values["theme_id"], values["font_size"], values["density"], values["motion"], utc_now()))
        if not previous or previous['theme_id']!=values['theme_id']:
            import uuid
            conn.execute("INSERT INTO theme_usage_events(user_id,theme_id,kind,dedupe_key,created_at) VALUES (?,?,'activate',?,?)",
                         (user_id,values['theme_id'],str(uuid.uuid4()),utc_now()))
    return get(user_id)
