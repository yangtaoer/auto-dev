from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import settings
from .process_env import sanitized_process_env


RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "acceptance_mapping": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "sql_changes": {"type": "array", "items": {"type": "string"}},
        "config_changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "changed_files", "acceptance_mapping", "risks", "sql_changes", "config_changes"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CodexRunResult:
    thread_id: str
    result: dict


class CodexRunner:
    def run(
        self,
        *,
        cwd: Path,
        work_item: dict,
        project: dict,
        on_event: Callable[[str, str], None],
    ) -> CodexRunResult:
        from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

        protected = ", ".join(project.get("protected_patterns", [])) or "无"
        prompt = f"""
你正在执行一个经过准入的 TFS 自动研发任务。

需求编号：#{work_item['id']}
需求标题：{work_item['title']}
需求描述：{work_item.get('description', '')}
验收标准：{work_item.get('acceptance_criteria', '')}
区域：{work_item.get('area_path', '')}

约束：
1. 只修改当前工作区，不执行 git commit、git push、创建 PR 或发送通知。
2. 不修改受保护路径：{protected}。
3. 优先使用项目/区域专属扩展点，不改变其他区域的现有行为。
4. SQL 与配置变更必须独立、明确，并在最终结果中列出。
5. 不接触密钥、生产配置和真实数据。
6. 实现需求并进行必要的低风险自检；项目构建命令由外层系统统一执行。
""".strip()

        developer_instructions = (
            "你是全自助研发执行器。修改应最小、可审计、可回滚。"
            "发现需求不完整、共享代码影响或高风险操作时停止修改，并在 risks 中说明。"
        )
        codex_config = CodexConfig(cwd=str(cwd), env=sanitized_process_env())
        with Codex(codex_config) as codex:
            if settings.codex_api_key:
                # 通过 app-server 控制通道登录，避免把 API key 暴露给仓库构建命令。
                codex.login_api_key(settings.codex_api_key)
            thread = codex.thread_start(
                cwd=str(cwd),
                model=settings.codex_model,
                sandbox=Sandbox.workspace_write,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions=developer_instructions,
                service_name="tellhow-autodev",
            )
            on_event("codex.thread", f"Codex 线程已创建：{thread.id}")
            handle = thread.turn(prompt, output_schema=RESULT_SCHEMA)
            final_text: str | None = None
            for notification in handle.stream():
                method = notification.method
                if method in {"item/started", "item/completed", "turn/completed"}:
                    on_event("codex.event", self._event_summary(method, notification.payload))
                if method == "item/completed":
                    payload = self._dump(notification.payload)
                    item = payload.get("item", {})
                    if item.get("type") in {"agentMessage", "agent_message"} and item.get("text"):
                        final_text = item["text"]

        if not final_text:
            raise RuntimeError("Codex 未返回结构化研发结果")
        try:
            parsed = json.loads(final_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Codex 结果不是有效 JSON: {final_text[:500]}") from exc
        return CodexRunResult(thread.id, parsed)

    @staticmethod
    def _dump(payload) -> dict:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json", by_alias=True)
        if hasattr(payload, "__dict__"):
            return dict(payload.__dict__)
        return {}

    def _event_summary(self, method: str, payload) -> str:
        data = self._dump(payload)
        item = data.get("item", {})
        item_type = item.get("type")
        if item_type:
            status = item.get("status") or ""
            return f"{item_type} {status}".strip()
        if method == "turn/completed":
            turn = data.get("turn", {})
            return f"Codex 本轮执行结束：{turn.get('status', 'completed')}"
        return method
