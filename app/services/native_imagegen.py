"""Run and verify native Codex image generation for a background job."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .codex_runner import CodexRunner
from .codex_runtime import resolve_codex_runtime
from .process_env import sanitized_process_env
from .task_cancellation import interrupt_on_cancel
from .workshop_recovery import WorkshopGenerationError


class ImagegenCapabilities(BaseModel):
    # Only the image capability is needed. Older app servers omit unrelated
    # fields that the newer SDK's full response model requires.
    image_generation: bool = Field(default=False, alias="imageGeneration")


def generate_native_images(*, cwd, prompt, execution, schema, required, progress, cancelled,
                           state, service_name, developer_instructions, references=()):
    from openai_codex import ApprovalMode, Codex, CodexConfig, LocalImageInput, Sandbox, SkillInput, TextInput
    runtime = resolve_codex_runtime(model=execution["model"])
    outputs, calls = state["outputs"], state["calls"]
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    allowed = (cwd.resolve(), (codex_home / "generated_images").resolve())
    inputs = [TextInput(prompt), *(LocalImageInput(str(path)) for path in references)]
    skill = codex_home / "skills/.system/imagegen/SKILL.md"
    if skill.is_file():
        inputs.insert(0, SkillInput(name="imagegen", path=str(skill)))
    final = None
    success = False
    with Codex(CodexConfig(codex_bin=runtime["path"], cwd=str(cwd), env=sanitized_process_env(),
                           config_overrides=("mcp_servers={}", "features.image_generation=true"))) as codex:
        caps = codex._client.request("modelProvider/capabilities/read", {},
                                     response_model=ImagegenCapabilities)
        if not caps.image_generation:
            raise RuntimeError("当前执行器的原生 ImageGen 不可用，请核对账户和运行器能力。")
        thread = codex.thread_start(
            cwd=str(cwd), model=execution["model"], sandbox=Sandbox.workspace_write,
            service_tier=execution.get("service_tier"), approval_mode=ApprovalMode.deny_all,
            service_name=service_name, developer_instructions=developer_instructions,
        )
        state["thread_id"] = thread.id
        progress("正在调用原生 ImageGen", thread.id)
        handle = thread.turn(inputs, model=execution["model"], effort=execution["effort"],
                             service_tier=execution.get("service_tier"), output_schema=schema)
        with interrupt_on_cancel(handle, cancelled):
            for notification in handle.stream():
                method = notification.method
                params = CodexRunner._dump(notification.payload)
                item = params.get("item") or {}
                if method in {"item/started", "item/completed"} and item.get("type") == "imageGeneration":
                    state["image_requested"] = True
                if method == "item/completed":
                    if item.get("type") == "imageGeneration" and item.get("status") == "completed":
                        calls.append({"id": item.get("id"), "revised_prompt": str(item.get("revisedPrompt") or "")[:12000]})
                        path, raw = item.get("savedPath"), item.get("result")
                        if path:
                            candidate = Path(path).resolve()
                            if not any(root in candidate.parents for root in allowed):
                                raise RuntimeError("ImageGen 返回了生成目录之外的路径")
                            outputs.append(candidate.read_bytes())
                        elif raw:
                            outputs.append(base64.b64decode(raw.split(",", 1)[-1], validate=True))
                        progress(f"ImageGen 已生成 {len(outputs)} 张图片", thread.id)
                    if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                        final = item.get("text")
                if method == "error" and not params.get("willRetry"):
                    raise WorkshopGenerationError(params.get("error") or params, thread_id=thread.id,
                                                  images=outputs, calls=calls, image_requested=state["image_requested"])
                if method == "turn/completed":
                    turn = params.get("turn") or {}
                    success = turn.get("status") == "completed"
                    if not success:
                        raise WorkshopGenerationError(turn.get("error") or {"message": "图像生成被中断"},
                                                      thread_id=thread.id, images=outputs, calls=calls,
                                                      image_requested=state["image_requested"])
    if not success or len(outputs) < required or not final or cancelled():
        raise RuntimeError(f"未收到 {required} 张原生 ImageGen 图片及完整说明，可以重试。")
    return {**json.loads(final), "images": outputs[-required:], "thread_id": state["thread_id"],
            "imagegen_verified": True, "imagegen_calls": calls}
