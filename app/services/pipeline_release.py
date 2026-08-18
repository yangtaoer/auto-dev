from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..config import settings
from .process_env import sanitized_process_env


class TfsPipelineReleaseError(RuntimeError):
    pass


class TfsPipelineReleaseService:
    """Adapter for the locally installed tfs-run-pipeline skill."""

    PROJECT_NAME_OVERRIDES = {
        "资阳网络发令": "资阳调度运行指挥平台",
    }

    def __init__(self, script_path=None) -> None:
        self.script_path = script_path or settings.tfs_pipeline_skill_script

    def catalog_name(self, project_name: str) -> str:
        return self.PROJECT_NAME_OVERRIDES.get(project_name.strip(), project_name.strip())

    def resolve_plan(self, project_name: str) -> dict[str, Any]:
        return self._invoke(project_name, [])

    def run(self, project_name: str) -> dict[str, Any]:
        if not settings.tfs_pat:
            raise TfsPipelineReleaseError("未配置 TFS_PAT，无法执行自动发版")
        result = self._invoke(
            project_name,
            [
                "--confirm-run",
                "--timeout-seconds",
                str(settings.tfs_pipeline_timeout_seconds),
            ],
            require_pat=True,
            timeout=settings.tfs_pipeline_timeout_seconds + 90,
        )
        if result.get("result") != "succeeded":
            raise TfsPipelineReleaseError(
                f"自动发版未成功：build {result.get('buildId', '—')} / {result.get('result', 'unknown')}"
            )
        if not result.get("expectedArtifactFound") or not result.get("artifactsUrl"):
            raise TfsPipelineReleaseError("自动发版完成但未找到预期发布产物")
        return result

    def _invoke(
        self,
        project_name: str,
        arguments: list[str],
        *,
        require_pat: bool = False,
        timeout: int = 60,
    ) -> dict[str, Any]:
        if not self.script_path.is_file():
            raise TfsPipelineReleaseError(
                f"未安装 tfs-run-pipeline skill：{self.script_path}"
            )
        command = [
            sys.executable,
            str(self.script_path),
            "--project-name",
            self.catalog_name(project_name),
            *arguments,
        ]
        env = sanitized_process_env()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if require_pat:
            env["TFS_PAT"] = settings.tfs_pat
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            reason = (completed.stderr or completed.stdout or "未知错误").strip()[-2000:]
            raise TfsPipelineReleaseError(f"自动发版技能执行失败：{reason}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TfsPipelineReleaseError("自动发版技能没有返回有效 JSON") from exc
        if not isinstance(payload, dict):
            raise TfsPipelineReleaseError("自动发版技能返回格式不正确")
        return payload
