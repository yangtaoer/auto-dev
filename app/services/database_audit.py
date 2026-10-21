"""Task-scoped write receipts. Never export global DM logs or persist raw SQL/secrets."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def sql_fingerprint(sql: str) -> str:
    # Whitespace-only normalization: do not lowercase literals or strip SQL comments/guards.
    return hashlib.sha256(" ".join(sql.strip().rstrip(";").split()).encode("utf-8")).hexdigest()


def database_write_receipt(item: dict[str, Any]) -> dict[str, str] | None:
    if item.get("type") != "mcpToolCall" or str(item.get("tool") or "").split("__")[-1] != "dm7_execute":
        return None
    arguments = item.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return None
    if not isinstance(arguments, dict):
        return None
    sql = str(arguments.get("sql") or "")
    without_comments = re.sub(r"(?s)/\*.*?\*/|(?m:--[^\n]*)", " ", sql)
    without_literals = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", "?", without_comments)
    if not re.search(r"(?im)(?:^|[;\n])\s*(create|alter|drop|truncate|insert|update|delete|merge|grant|revoke|comment|call|execute|exec|begin|declare)\b", without_literals) and not (
        re.match(r"(?i)^\s*with\b", without_literals) and re.search(r"(?i)\b(insert|update|delete|merge)\b", without_literals)
    ):
        return None
    return {
        "call_id": str(item.get("id") or sql_fingerprint(sql)),
        "sql_sha256": sql_fingerprint(sql),
        "purpose": str(arguments.get("purpose") or "test"),
        "status": str(item.get("status") or "inProgress"),
    }


class DatabaseWriteJournal:
    """Persist receipts at tool start as well as completion, surviving failed/resumed turns."""
    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict[str, str]] = {}
        if path.is_file():
            # A corrupt audit must not silently turn into 'no database writes'.
            self.entries = {item["call_id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}

    def observe(self, receipt: dict[str, str]) -> None:
        if self.entries.get(receipt["call_id"]) == receipt:
            return
        self.entries[receipt["call_id"]] = receipt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(list(self.entries.values()), ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)
