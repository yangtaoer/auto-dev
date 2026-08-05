"""验证本机 Codex SDK 登录态和结构化输出，不修改工作区。"""

from __future__ import annotations

import json
from pathlib import Path

from openai_codex import ApprovalMode, Codex, Sandbox


schema = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "project": {"type": "string"},
    },
    "required": ["ok", "project"],
    "additionalProperties": False,
}


with Codex() as codex:
    thread = codex.thread_start(
        cwd=str(Path(__file__).resolve().parent.parent),
        sandbox=Sandbox.read_only,
        approval_mode=ApprovalMode.deny_all,
        service_name="tellhow-autodev-smoke",
    )
    result = thread.run(
        "只读检查当前仓库的 README 标题。返回 ok=true，project 使用 README 的项目名称。不要修改文件。",
        output_schema=schema,
    )
    payload = json.loads(result.final_response or "{}")
    if payload.get("ok") is not True:
        raise SystemExit("Codex SDK smoke test failed")
    print(json.dumps({"thread_id": thread.id, **payload}, ensure_ascii=False))
