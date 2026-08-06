from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from datetime import UTC, datetime
from typing import Any, Callable

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
        on_live_event: Callable[[dict[str, Any]], None] | None = None,
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
            on_event("codex.thread", "Codex 研发会话已启动")
            handle = thread.turn(prompt, output_schema=RESULT_SCHEMA)
            final_text: str | None = None
            for notification in handle.stream():
                method = notification.method
                if method in {"item/started", "item/completed", "turn/completed"}:
                    on_event("codex.event", self._event_summary(method, notification.payload))
                if on_live_event:
                    live_event = self._live_event(method, notification.payload)
                    if live_event:
                        on_live_event(live_event)
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

    @classmethod
    def read_account_usage(cls) -> dict[str, Any]:
        """Read the local Codex account meter for the cloud admin dashboard."""
        from openai_codex import Codex, CodexConfig
        from openai_codex.generated.v2_all import GetAccountRateLimitsResponse

        codex_config = CodexConfig(env=sanitized_process_env())
        with Codex(codex_config) as codex:
            if settings.codex_api_key:
                codex.login_api_key(settings.codex_api_key)
            account_response = codex.account()
            limits_response = codex._client.request(  # SDK does not expose a public convenience wrapper yet.
                "account/rateLimits/read", None, response_model=GetAccountRateLimitsResponse
            )
        account = cls._dump(account_response).get("account") or {}
        if "root" in account:
            account = account["root"] or {}
        limits = cls._dump(limits_response)
        snapshot = limits.get("rateLimits") or {}
        credits = snapshot.get("credits") or {}
        by_id = limits.get("rateLimitsByLimitId") or {}

        def window(value: dict[str, Any] | None) -> dict[str, Any] | None:
            if not value:
                return None
            used = max(0, min(100, int(value.get("usedPercent") or 0)))
            return {
                "used_percent": used,
                "remaining_percent": 100 - used,
                "resets_at": value.get("resetsAt"),
                "window_minutes": value.get("windowDurationMins"),
            }

        meters = []
        for limit_id, value in by_id.items():
            if not isinstance(value, dict):
                continue
            meters.append(
                {
                    "id": limit_id,
                    "name": value.get("limitName") or limit_id,
                    "primary": window(value.get("primary")),
                    "secondary": window(value.get("secondary")),
                }
            )
        reset_credits = limits.get("rateLimitResetCredits") or {}
        return {
            "available": True,
            "account_type": account.get("type") or "unknown",
            "plan_type": account.get("planType") or snapshot.get("planType") or "unknown",
            "primary": window(snapshot.get("primary")),
            "secondary": window(snapshot.get("secondary")),
            "credits": {
                "balance": credits.get("balance"),
                "has_credits": bool(credits.get("hasCredits")),
                "unlimited": bool(credits.get("unlimited")),
            },
            "meters": meters,
            "reset_credits_available": int(reset_credits.get("availableCount") or 0),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _dump(payload) -> dict:
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json", by_alias=True)
        if hasattr(payload, "__dict__"):
            return dict(payload.__dict__)
        return {}

    def _event_summary(self, method: str, payload) -> str:
        data = self._dump(payload)
        item = data.get("item", {})
        item_type = item.get("type")
        if item_type == "commandExecution":
            command = str(item.get("command") or "").replace("\n", " ")[:180]
            if method == "item/started":
                return f"正在执行命令：{command}" if command else "正在执行项目命令"
            exit_code = item.get("exitCode")
            return f"命令执行完成（退出码 {exit_code}）：{command}" if exit_code is not None else "命令执行完成"
        if item_type == "fileChange":
            changes = item.get("changes") or []
            paths = [str(change.get("path") or "") for change in changes if isinstance(change, dict)]
            suffix = "、".join(filter(None, paths[:4]))
            return f"正在修改文件：{suffix}" if suffix else "Codex 正在修改代码文件"
        if item_type == "reasoning":
            return "Codex 已完成一段分析"
        if item_type == "agentMessage":
            return "Codex 正在整理研发结果" if method == "item/started" else "Codex 已输出本轮研发结论"
        if item_type == "plan":
            return "Codex 已更新研发计划"
        if item_type:
            return f"Codex 正在处理：{item_type}" if method == "item/started" else f"Codex 已完成：{item_type}"
        if method == "turn/completed":
            turn = data.get("turn", {})
            return f"Codex 本轮执行结束：{turn.get('status', 'completed')}"
        return method

    def _live_event(self, method: str, payload) -> dict[str, Any] | None:
        data = self._dump(payload)
        delta_map = {
            "item/agentMessage/delta": "assistant",
            "item/commandExecution/outputDelta": "command",
            "item/fileChange/outputDelta": "file",
            "item/plan/delta": "plan",
            # Expose Codex's public reasoning summaries, never private reasoning text deltas.
            "item/reasoning/summaryTextDelta": "reasoning",
        }
        if method in delta_map:
            content = data.get("delta")
            if not content:
                return None
            group = data.get("itemId") or data.get("item_id") or ""
            if method == "item/reasoning/summaryTextDelta":
                group = f"{group}:{data.get('summaryIndex', data.get('summary_index', 0))}"
            return {"kind": delta_map[method], "content": content, "group": group, "delta": True}

        if method in {"item/started", "item/completed"}:
            item = data.get("item") or {}
            item_type = item.get("type") or "status"
            group = item.get("id") or ""
            phase = "开始" if method == "item/started" else "完成"
            if item_type == "commandExecution":
                command = str(item.get("command") or "").strip()
                if method == "item/started" and command:
                    return {"kind": "command", "content": f"$ {command}\n", "group": group, "delta": False}
                exit_code = item.get("exitCode")
                return {
                    "kind": "status",
                    "content": f"命令执行{phase}" + (f" · 退出码 {exit_code}" if exit_code is not None else ""),
                    "group": f"{group}:status",
                    "delta": False,
                }
            if item_type == "fileChange":
                changes = item.get("changes") or []
                paths = [str(change.get("path") or "") for change in changes if isinstance(change, dict)]
                content = "代码文件变更" + ("：\n" + "\n".join(f"• {path}" for path in paths[:20]) if paths else "")
                return {"kind": "file", "content": content, "group": group, "delta": False}
            if item_type in {"agentMessage", "reasoning", "plan"}:
                return None
            return {"kind": "status", "content": f"{item_type} · {phase}", "group": group, "delta": False}
        if method == "turn/completed":
            turn = data.get("turn") or {}
            return {
                "kind": "status",
                "content": f"本轮研发执行结束 · {turn.get('status', 'completed')}",
                "group": "turn-completed",
                "delta": False,
            }
        return None
