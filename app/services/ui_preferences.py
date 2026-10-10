"""Account-owned appearance preferences; no business or model settings involved."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import ROOT
from app.db import row, transaction, utc_now

DEFAULTS = {"theme_id": "mint-garden", "font_size": "normal", "density": "compact", "motion": "full"}
CHOICES = {"font_size": {"normal", "large"}, "density": {"compact", "comfortable"},
           "motion": {"full", "reduced", "static"}}


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return json.loads((ROOT / "app/static/themes/catalog.json").read_text("utf-8"))


def theme(theme_id: str | None) -> dict[str, Any]:
    themes = catalog()["themes"]
    return next((item for item in themes if item["id"] == theme_id), themes[0])


def style(theme_id: str | None) -> str:
    # Values come exclusively from the shipped catalog, never from user CSS.
    return ";".join(f"--{key}:{value}" for key, value in theme(theme_id)["tokens"].items())


def get(user_id: int) -> dict[str, Any]:
    saved = row("SELECT theme_id,font_size,density,motion,updated_at FROM user_ui_preferences WHERE user_id=?", (user_id,))
    result = dict(DEFAULTS)
    if saved:
        result.update(saved)
    result["theme_id"] = theme(result["theme_id"])["id"]
    for key, options in CHOICES.items():
        if result[key] not in options:
            result[key] = DEFAULTS[key]
    return result


def save(user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    if set(values) != set(DEFAULTS):
        raise ValueError("外观设置字段不完整或包含未知字段")
    if values["theme_id"] not in {item["id"] for item in catalog()["themes"]}:
        raise ValueError("所选主题不可用")
    for key, options in CHOICES.items():
        if values[key] not in options:
            raise ValueError("外观设置选项无效")
    with transaction() as conn:
        conn.execute("""INSERT INTO user_ui_preferences(user_id,theme_id,font_size,density,motion,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET theme_id=excluded.theme_id,
            font_size=excluded.font_size,density=excluded.density,motion=excluded.motion,updated_at=excluded.updated_at""",
            (user_id, values["theme_id"], values["font_size"], values["density"], values["motion"], utc_now()))
    return get(user_id)
