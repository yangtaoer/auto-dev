"""验证本机 Codex SDK 登录态和结构化输出，不修改工作区。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox
from app.config import settings
from app.services.codex_runner import CodexRunner
from app.services.codex_runtime import resolve_codex_runtime
from app.services.process_env import sanitized_process_env


schema = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "project": {"type": "string"},
    },
    "required": ["ok", "project"],
    "additionalProperties": False,
}


runtime = resolve_codex_runtime()
with Codex(CodexConfig(codex_bin=runtime["path"], env=sanitized_process_env())) as codex:
    if settings.codex_api_key:
        codex.login_api_key(settings.codex_api_key)
    thread = codex.thread_start(
        cwd=str(Path(__file__).resolve().parent.parent),
        model=settings.codex_model,
        sandbox=Sandbox.read_only,
        approval_mode=ApprovalMode.deny_all,
        service_name="tellhow-autodev-smoke",
    )
    handle = thread.turn(
        '仅验证模型连接和结构化输出，不调用工具、不读取或修改文件。返回 ok=true、project="AutoDev"。',
        output_schema=schema,
    )
    text = CodexRunner()._collect_output(handle.stream(), lambda kind, message: print(kind, message, flush=True))
    payload = json.loads(text)
    if payload.get("ok") is not True:
        raise SystemExit("Codex SDK smoke test failed")
    print(json.dumps({"thread_id": thread.id, "model": settings.codex_model,
                      "runtime": runtime["version"], **payload}, ensure_ascii=False))
