"""Readable, bounded summaries; full build diagnostics stay on the runner."""
from __future__ import annotations

import os
import re
import uuid

from ..config import settings


def clean_log(value: str) -> str:
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", str(value))
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return value.replace("\r\n", "\n").strip()


def bounded_log(value: str, limit: int = 2000) -> str:
    text = clean_log(value)
    if len(text) <= limit:
        return text
    marker = "\n…（省略中间日志）…\n"
    head = min(300, limit // 3)
    return text[:head] + marker + text[-(limit - head - len(marker)):]


def decode_output(value: bytes | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    # Java on Chinese Windows emits the ANSI code page; PowerShell emits UTF-8.
    # Decode each line independently so one stream can contain both encodings.
    fallback = "utf-8"
    if os.name == "nt":
        import ctypes
        fallback = f"cp{ctypes.windll.kernel32.GetACP()}"
    lines = []
    for line in value.splitlines(keepends=True):
        try:
            lines.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            lines.append(line.decode(fallback, errors="replace"))
    return "".join(lines)


def command_failure(code: int, stdout: bytes | str, stderr: bytes | str, request_id: str = "") -> RuntimeError:
    output = clean_log(decode_output(stdout) + "\n" + decode_output(stderr))
    key = re.sub(r"[^a-zA-Z0-9_-]", "_", request_id)[:80] or "unassigned"
    folder = settings.data_dir / "build-logs" / key
    log_path = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        log_path = folder / f"{uuid.uuid4().hex}.log"
        log_path.write_text(output, encoding="utf-8")
    except OSError:
        # Logging must never hide the actual build failure.
        pass
    tail = output[-1100:]
    message = f"本地构建命令失败（退出码 {code}）。\n{tail}"
    if log_path:
        message += f"\n完整构建日志：{log_path}"
    return RuntimeError(bounded_log(message, 1800))
