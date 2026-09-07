"""Control-plane settings: no local SDK dependency on the cloud server."""
from __future__ import annotations

import json

from ..config import settings
from ..db import row, rows, transaction, utc_now


def current() -> dict:
    saved = row("SELECT value FROM platform_settings WHERE key='codex'")
    return json.loads(saved["value"]) if saved else {"model": settings.codex_model, "effort": "high"}


def catalog() -> list[dict]:
    models = {}
    for runner in rows("SELECT detail FROM runners ORDER BY last_seen_at"):
        detail = json.loads(runner["detail"] or "{}")
        for model in (detail.get("codex_usage") or {}).get("models") or []:
            if model.get("model") and model.get("efforts"):
                models[model["model"]] = model
    return list(models.values())


def save(model: str, effort: str, actor_id: int) -> dict:
    supported = next((entry for entry in catalog() if entry["model"] == model), None)
    if not supported or effort not in supported["efforts"]:
        raise ValueError("请选择执行器已确认支持的模型和思考深度；执行器离线时请先恢复连接")
    value = {"model": model, "effort": effort}
    with transaction() as conn:
        conn.execute("INSERT INTO platform_settings(key,value,updated_at) VALUES ('codex',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (json.dumps(value), utc_now()))
        conn.execute("INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES (?,'model.settings_updated','platform','codex',?,?)", (actor_id, json.dumps(value), utc_now()))
    return value


def for_request(request_id: str) -> dict:
    value = current()
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO run_model_configs(request_id,value) VALUES (?,?)", (request_id, json.dumps(value)))
        saved = conn.execute("SELECT value FROM run_model_configs WHERE request_id=?", (request_id,)).fetchone()
    return json.loads(saved["value"])
