from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from datetime import UTC, datetime
from typing import Any, Callable

from ..config import settings
from .dm7_plugin import discover_dm7_plugin
from .process_env import sanitized_process_env
from .tfs import TfsClient


RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["completed", "needs_input"]},
        "summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "acceptance_mapping": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "blocking_risks": {"type": "array", "items": {"type": "string"}},
        "sql_changes": {"type": "array", "items": {"type": "string"}},
        "config_changes": {"type": "array", "items": {"type": "string"}},
        "database_operations": {"type": "array", "items": {"type": "string"}},
        "supplement_requests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "reason": {"type": "string"},
                    "suggested_answer": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["id", "question", "reason", "suggested_answer", "required"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "decision", "summary", "changed_files", "acceptance_mapping", "risks", "sql_changes",
        "config_changes", "database_operations", "supplement_requests", "blocking_risks",
    ],
    "additionalProperties": False,
}


ANALYSIS_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["completed", "needs_input"]},
        "summary": {"type": "string"},
        "root_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "is_data_issue": {"type": "boolean"},
        "code_change_needed": {"type": "boolean"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["code", "database", "log", "tfs", "configuration", "inference"]},
                    "source": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["kind", "source", "detail"],
                "additionalProperties": False,
            },
        },
        "affected_scope": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "database_operations": {"type": "array", "items": {"type": "string"}},
        "supplement_requests": RESULT_SCHEMA["properties"]["supplement_requests"],
    },
    "required": [
        "decision", "summary", "root_cause", "confidence", "is_data_issue", "code_change_needed",
        "changed_files", "evidence", "affected_scope", "recommended_actions", "risks",
        "database_operations", "supplement_requests",
    ],
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
        resume_thread_id: str | None = None,
        supplement_requests: list[dict[str, Any]] | None = None,
        supplement_answers: list[dict[str, Any]] | None = None,
        task_type: str = "development",
    ) -> CodexRunResult:
        from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, SkillInput, TextInput

        protected = ", ".join(project.get("protected_patterns", [])) or "无"
        repository_paths = project.get("repository_paths") or [project.get("repository_path", "")]
        repository_names = [Path(path).name for path in repository_paths if str(path).strip()]
        repository_scope = "、".join(repository_names) or "当前仓库"
        repository_rule = (
            "当前工作区是多仓库根目录。先判断需求涉及哪些仓库，只修改必要仓库；需求需要时允许跨仓修改。"
            if len(repository_names) > 1
            else "当前工作区是单仓库，只在该仓库内完成需求。"
        )
        dm7 = discover_dm7_plugin()
        project_context = str(project.get("development_instructions") or "").strip()
        if project_context:
            project_context = "项目专属开发约定（以本次隔离仓库和已验证代码为准）：\n" + project_context
        supplement_context = ""
        if supplement_answers:
            request_by_id = {
                str(item.get("id") or ""): item for item in (supplement_requests or []) if isinstance(item, dict)
            }
            answer_lines = []
            for item in supplement_answers:
                if not isinstance(item, dict):
                    continue
                request = request_by_id.get(str(item.get("id") or ""), {})
                question = str(request.get("question") or item.get("id") or "补充项")
                answer_lines.append(f"- {question}\n  用户补充：{item.get('answer', '')}")
            supplement_context = (
                "\n这是待补充任务的继续处理。以下内容由用户在平台补充，须作为本轮依据：\n"
                + "\n".join(answer_lines)
                + "\n不要重复询问已明确回答的内容。"
            )

        joint_context = ""
        joint_classification = project.get("joint_classification") or {}
        if joint_classification:
            scoped_sections = [str(value) for value in joint_classification.get("scoped_sections", []) if str(value).strip()]
            shared_sections = [str(value) for value in joint_classification.get("shared_sections", []) if str(value).strip()]
            matched_terms = "、".join(str(value) for value in joint_classification.get("matched_terms", [])) or project.get("name", "当前项目")
            scoped_text = "\n".join(f"- {value}" for value in scoped_sections) or "- 需求未拆出独立条目，请结合当前项目仓库判断相关改动。"
            shared_text = "\n".join(f"- {value}" for value in shared_sections) or "- 无"
            joint_context = f"""
这是一个多项目联合研发需求中的独立子任务。
当前负责项目：{project.get('name', project.get('project_key', '当前项目'))}
同组项目：{'、'.join(project.get('joint_project_keys') or [])}
归类依据：{matched_terms}
当前项目专属需求：
{scoped_text}
跨项目共同要求：
{shared_text}
只实现属于当前项目仓库的内容；不得把其他子项目的专属功能重复实现到本项目。共同要求中与当前项目相关的部分仍需落实。
""".strip()

        dm7_instructions = (
            "DM7 数据库插件已可用。涉及数据库时，应主动使用 DM7 工具检查本机开发库的连接、元数据、表结构和必要样例数据，"
            "不要仅因仓库内缺少表结构说明而停止。这里是本机开发环境：允许为保证需求完整性自主执行必要的受控数据库操作；"
            "读取优先使用查询和结构描述工具。确需修改时必须按插件要求提供真实用途：测试数据使用 purpose=\"test\"，版本迁移验证使用 purpose=\"migration\"。"
            "不得向项目经理索要数据库密码，优先使用插件中已保存的本地连接。"
            if dm7.available
            else dm7.message
        )
        analysis_dm7_instructions = (
            "DM7 数据库插件已可用。应按问题需要主动执行只读查询、描述表结构并核验必要样例数据；"
            "禁止 DDL、DML、事务写入和测试数据构造，不得向项目经理索要数据库密码。"
            if dm7.available
            else dm7.message
        )
        requirement_image_context = self._requirement_image_context(work_item, project, on_event)

        if task_type == "analysis":
            tfs_relations = json.dumps(work_item.get("relations") or [], ensure_ascii=False)[:12000]
            prompt = f"""
你正在执行一个经过准入的 TFS 问题分析任务。用户要求结合代码、TFS 信息和本机开发环境定位原因，不进行代码改造。

需求编号：#{work_item['id']}
需求标题：{work_item['title']}
问题描述：{work_item.get('description', '')}
期望结果：{work_item.get('acceptance_criteria', '')}
区域：{work_item.get('area_path', '')}
TFS 附件与关联元数据：{tfs_relations or '无'}
{requirement_image_context}
仓库范围：{repository_scope}
{project_context}
{joint_context}
{supplement_context}

约束：
1. 这是只读问题分析。不得修改、创建或删除任何仓库文件，不执行 git commit、git push、创建 PR、构建、发版或发送通知。
2. {repository_rule.replace('只修改必要仓库；需求需要时允许跨仓修改', '只分析相关仓库；问题跨仓时允许交叉检索').replace('只在该仓库内完成需求', '只在该仓库内完成分析')}。
3. 主动检索相关代码、配置、Git 历史和 TFS 上下文，用文件路径、行号、条件分支或调用链支撑结论。
4. {analysis_dm7_instructions}
5. 区分已验证事实与推断。evidence 中的 source 必须可复核；无法直接验证的内容使用 kind=inference，并在 detail 说明推断链路。
6. 只有缺少的信息会阻止可靠定位，且无法从代码、TFS、配置、日志或本机 DM7 开发库查明时，才返回 decision=needs_input。
7. 找到高可信或中可信原因时返回 decision=completed。没有代码变更是本任务的正常成功条件，changed_files 必须为空数组。
8. code_change_needed 只表示后续是否建议转为自主研发任务；本轮无论其值如何都不得改代码。
9. 所有面向用户的结论必须使用简体中文，明确给出根因、证据、影响范围、可信度和建议动作。
""".strip()
        else:
            prompt = f"""
你正在执行一个经过准入的 TFS 自动研发任务。

需求编号：#{work_item['id']}
需求标题：{work_item['title']}
需求描述：{work_item.get('description', '')}
验收标准：{work_item.get('acceptance_criteria', '')}
区域：{work_item.get('area_path', '')}
{requirement_image_context}
仓库范围：{repository_scope}
{project_context}
{joint_context}
{supplement_context}

约束：
1. 只修改当前工作区，不执行 git commit、git push、创建 PR 或发送通知。
2. {repository_rule}
3. 不修改受保护路径：{protected}。
4. 优先使用项目/区域专属扩展点，不改变其他区域的现有行为。
5. SQL 与配置变更必须独立、明确，并在最终结果中列出。
6. 不接触密钥、生产配置和真实数据。
7. 实现需求并进行必要的低风险自检；项目构建命令由外层系统统一执行。
8. 所有面向用户的分析摘要和最终结果必须使用简体中文（文件路径、代码和命令除外）。
9. 不要根据改动大小或是否改变接口行为决定是否交付；只要产生有效代码变更，外层系统都会继续提交代码并执行既定构建。
10. 最终结果只描述研发修改与自检，不要把“未提交代码”或“未打包”列为未完成事项，这两步由外层交付流程强制执行。
11. {dm7_instructions}
12. 不要把一般性风险、可通过代码默认值处理的细节或可由仓库/数据库工具查明的信息升级为阻塞项。先检索代码、文档和数据库，再作判断。
13. 只有缺少的信息会直接决定错误业务口径、越权或不可逆数据设计，且无法从代码、TFS、配置和 DM7 本机开发库查明时，才返回 decision=needs_input。此时不要提交半成品，supplement_requests 必须逐项给出明确问题、原因和建议答案。
14. 可以可靠实现时必须返回 decision=completed 并完成代码、自检及必要 SQL/配置修改；普通提醒（如缺少截图中指定样例、已覆盖的测试限制）写入 risks，不得因此跳过研发。
15. 风险必须分级：需要用户补充明确业务信息时返回 needs_input 与 supplement_requests；需要管理员授权的高风险改动或无法保证完整交付的技术阻塞写入 blocking_risks（无则 []）。缺少必要客户端/服务端仓库、只增加接口却没有页面接入、验收关键项未实现不能宣称 completed 且无阻塞，必须说明缺失范围。不得用普通 risks 掩盖未实现功能。
""".strip()

        developer_instructions = (
            "你是只读问题分析执行器。必须保持工作区零改动，以可复核证据定位根因。"
            "所有可展示给用户的分析摘要、计划与最终回复必须使用简体中文。"
            "优先自主查明问题；仅在关键事实无法获得且无法形成可靠结论时进入待补充。"
            if task_type == "analysis"
            else (
                "你是全自主研发执行器。修改应最小、可审计、可回滚。"
                "所有可展示给用户的分析摘要、计划与最终回复必须使用简体中文。"
                "优先自主查明并解决问题；仅在关键事实无法获得且继续开发必然不可靠时进入待补充。"
            )
        )
        codex_config = CodexConfig(
            cwd=str(cwd),
            env=sanitized_process_env(),
            config_overrides=dm7.config_overrides,
        )
        with Codex(codex_config) as codex:
            if settings.codex_api_key:
                # 通过 app-server 控制通道登录，避免把 API key 暴露给仓库构建命令。
                codex.login_api_key(settings.codex_api_key)
            thread_options = {
                "cwd": str(cwd),
                "model": settings.codex_model,
                "sandbox": Sandbox.read_only if task_type == "analysis" else Sandbox.full_access,
                "approval_mode": ApprovalMode.deny_all,
                "developer_instructions": developer_instructions,
            }
            if resume_thread_id:
                thread = codex.thread_resume(resume_thread_id, **thread_options)
                on_event("devcore.thread_resumed", f"DevCore 已载入补充信息并继续原{'分析' if task_type == 'analysis' else '研发'}会话")
            else:
                thread = codex.thread_start(service_name="tellhow-autodev", **thread_options)
                on_event("devcore.thread", f"DevCore {'问题分析' if task_type == 'analysis' else '研发'}会话已启动")
            on_event(
                "dm7.capability_ready" if dm7.available else "dm7.capability_unavailable",
                dm7.message,
            )
            run_input = [TextInput(prompt)]
            if dm7.available and dm7.skill_path:
                run_input.insert(0, SkillInput(name="dm7-database:dm7-database", path=str(dm7.skill_path)))
            handle = thread.turn(
                run_input,
                output_schema=ANALYSIS_RESULT_SCHEMA if task_type == "analysis" else RESULT_SCHEMA,
            )
            final_text: str | None = None
            for notification in handle.stream():
                method = notification.method
                if method in {"item/started", "item/completed", "turn/completed"}:
                    on_event(
                        "devcore.event",
                        self._event_summary(method, notification.payload, task_type=task_type),
                    )
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
            raise RuntimeError("DevCore 未返回结构化研发结果")
        try:
            parsed = json.loads(final_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"DevCore 结果不是有效 JSON: {final_text[:500]}") from exc
        return CodexRunResult(thread.id, parsed)

    @staticmethod
    def _requirement_image_context(
        work_item: dict,
        project: dict,
        on_event: Callable[[str, str], None],
    ) -> str:
        collection = str(project.get("tfs_collection_url") or "").strip()
        sources = TfsClient._requirement_image_sources(work_item)
        if not collection or not sources:
            return "TFS 需求图片：未发现"
        work_item_id = int(work_item.get("id") or 0)
        revision = int(work_item.get("revision") or 0)
        destination = settings.data_dir / "runner" / "requirement-images" / f"{work_item_id}-r{revision}"
        result = TfsClient(collection).download_requirement_images(work_item, destination)
        paths = result["paths"]
        errors = result["errors"]
        if paths:
            on_event("tfs.images_downloaded", f"已通过 TFS 认证下载 {len(paths)} 张需求图片")
        if errors:
            on_event("tfs.images_partial", "；".join(errors))
        path_lines = "\n".join(f"- {path}" for path in paths) or "- 无可读取图片"
        error_lines = "\n".join(f"- {value}" for value in errors)
        return (
            "TFS 需求图片（已由外层使用 TFS 凭据下载，不要再次访问原始认证 URL）：\n"
            f"{path_lines}\n"
            "必须逐张使用本机图片查看能力读取，并将图片内容与文字需求共同作为实现/分析依据。"
            + (f"\n未能下载的图片：\n{error_lines}" if error_lines else "")
        )

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

    def _event_summary(self, method: str, payload, *, task_type: str = "development") -> str:
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
            return f"正在修改文件：{suffix}" if suffix else "DevCore 正在修改代码文件"
        if item_type == "reasoning":
            return "DevCore 已完成一段分析"
        if item_type == "agentMessage":
            if task_type == "analysis":
                return "DevCore 正在整理分析结果" if method == "item/started" else "DevCore 已输出本轮问题分析结论"
            return "DevCore 正在整理研发结果" if method == "item/started" else "DevCore 已输出本轮研发结论"
        if item_type == "plan":
            return f"DevCore 已更新{'分析' if task_type == 'analysis' else '研发'}计划"
        if item_type:
            return f"DevCore 正在处理：{item_type}" if method == "item/started" else f"DevCore 已完成：{item_type}"
        if method == "turn/completed":
            turn = data.get("turn", {})
            return f"DevCore 本轮执行结束：{turn.get('status', 'completed')}"
        return method

    def _live_event(self, method: str, payload) -> dict[str, Any] | None:
        data = self._dump(payload)
        delta_map = {
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
                    "kind": "command",
                    "content": f"\n[命令执行{phase}" + (f"，退出码 {exit_code}" if exit_code is not None else "") + "]\n",
                    "group": group,
                    "delta": True,
                }
            if item_type == "fileChange":
                changes = item.get("changes") or []
                paths = [str(change.get("path") or "") for change in changes if isinstance(change, dict)]
                content = "代码文件变更" + ("：\n" + "\n".join(f"• {path}" for path in paths[:20]) if paths else "")
                return {"kind": "file", "content": content, "group": group, "delta": False}
            if item_type == "agentMessage":
                if method != "item/completed" or not item.get("text"):
                    return None
                return {
                    "kind": "assistant",
                    "content": self._format_live_result(str(item["text"])),
                    "group": group,
                    "delta": False,
                    "format": "markdown",
                }
            if item_type in {"reasoning", "plan"}:
                return None
            return None
        return None

    @staticmethod
    def _format_live_result(raw: str) -> str:
        """Turn the structured SDK result into safe, readable Chinese Markdown."""
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            if raw.lstrip().startswith(("{", "[")):
                return "### 研发结论\n\nDevCore 已返回结果，系统正在校验结构化内容。"
            return f"### DevCore 回复\n\n{raw.strip()}"
        if not isinstance(result, dict):
            return "### 研发结论\n\nDevCore 已完成本轮研发，系统正在校验结果。"

        def section(title: str, value: Any, *, empty: str = "无") -> list[str]:
            lines = [f"### {title}", ""]
            if isinstance(value, list):
                items = [str(item).strip() for item in value if str(item).strip()]
                lines.extend([f"- {item}" for item in items] or [empty])
            else:
                text = str(value or "").strip()
                lines.append(text or empty)
            return lines

        blocks: list[str] = []
        if "root_cause" in result:
            confidence_labels = {"high": "高", "medium": "中", "low": "低"}
            blocks.extend(section("分析结论", result.get("summary"), empty="DevCore 已完成问题分析。"))
            blocks.extend(["", "### 根本原因", "", str(result.get("root_cause") or "尚未形成唯一根因")])
            blocks.extend([
                "", "### 结论可信度", "",
                confidence_labels.get(str(result.get("confidence")), str(result.get("confidence") or "—")),
                "", "### 证据链", "",
            ])
            evidence = result.get("evidence") or []
            for item in evidence:
                if not isinstance(item, dict):
                    continue
                blocks.append(
                    f"- [{item.get('kind') or 'evidence'}] {item.get('source') or '未标注来源'}：{item.get('detail') or '—'}"
                )
            if not evidence:
                blocks.append("暂无可复核证据")
            for title, field in (
                ("影响范围", "affected_scope"),
                ("建议动作", "recommended_actions"),
                ("风险提示", "risks"),
                ("数据库核验", "database_operations"),
            ):
                blocks.append("")
                blocks.extend(section(title, result.get(field), empty="无"))
            blocks.extend([
                "", "### 后续判断", "",
                f"- 是否属于数据问题：{'是' if result.get('is_data_issue') else '否'}",
                f"- 是否建议转为自主研发：{'是' if result.get('code_change_needed') else '否'}",
            ])
            requests = result.get("supplement_requests") or []
            if result.get("decision") == "needs_input" or requests:
                blocks.extend(["", "### 待补充信息", ""])
                for index, item in enumerate(requests, 1):
                    if not isinstance(item, dict):
                        continue
                    blocks.append(f"- {index}. {item.get('question') or '请补充关键信息'}")
                    if item.get("reason"):
                        blocks.append(f"  原因：{item['reason']}")
                    if item.get("suggested_answer"):
                        blocks.append(f"  建议：{item['suggested_answer']}")
            return "\n".join(blocks).strip()
        for title, field, empty in (
            ("研发结论", "summary", "DevCore 已完成本轮研发。"),
            ("变更文件", "changed_files", "无代码文件变更"),
            ("验收覆盖", "acceptance_mapping", "未提供验收映射"),
            ("风险提示", "risks", "无"),
            ("阻塞风险（需确认）", "blocking_risks", "无"),
            ("SQL 变更", "sql_changes", "无"),
            ("配置变更", "config_changes", "无"),
            ("数据库操作", "database_operations", "无"),
        ):
            blocks.extend(section(title, result.get(field), empty=empty))
            blocks.append("")
        requests = result.get("supplement_requests") or []
        if result.get("decision") == "needs_input" or requests:
            blocks.extend(["### 待补充信息", ""])
            for index, item in enumerate(requests, 1):
                if not isinstance(item, dict):
                    continue
                blocks.append(f"- {index}. {item.get('question') or '请补充关键信息'}")
                if item.get("reason"):
                    blocks.append(f"  原因：{item['reason']}")
                if item.get("suggested_answer"):
                    blocks.append(f"  建议：{item['suggested_answer']}")
        return "\n".join(blocks).strip()
