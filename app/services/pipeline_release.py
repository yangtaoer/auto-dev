from __future__ import annotations

import json
import subprocess
import sys
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ..config import settings
from .process_env import sanitized_process_env


class TfsPipelineReleaseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        result: dict[str, Any] | None = None,
        diagnostics: str = "",
    ) -> None:
        super().__init__(message)
        self.transient = transient
        self.result = result or {}
        self.diagnostics = diagnostics


class TfsPipelineReleaseService:
    """Adapter for the locally installed tfs-run-pipeline skill."""

    PROJECT_NAME_OVERRIDES = {
        "资阳网络发令": "资阳调度运行指挥平台",
    }
    TRANSIENT_FAILURE_MARKERS = (
        "malformed \\uxxxx encoding",
    )

    def __init__(self, script_path=None) -> None:
        self.script_path = script_path or settings.tfs_pipeline_skill_script

    def catalog_name(self, project_name: str) -> str:
        return self.PROJECT_NAME_OVERRIDES.get(project_name.strip(), project_name.strip())

    def resolve_plan(self, project_name: str) -> dict[str, Any]:
        return self._invoke(project_name, [])

    def run(self, project_name: str) -> dict[str, Any]:
        if not settings.tfs_pat:
            raise TfsPipelineReleaseError("未配置 TFS_PAT，无法执行自动发版")
        retry_history: list[dict[str, Any]] = []
        payload: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                payload = self._invoke(
                    project_name,
                    [
                        "--confirm-run",
                        "--timeout-seconds",
                        str(settings.tfs_pipeline_timeout_seconds),
                    ],
                    require_pat=True,
                    timeout=settings.tfs_pipeline_timeout_seconds + 90,
                )
                break
            except TfsPipelineReleaseError as exc:
                if attempt or not exc.transient:
                    raise
                retry_history.append(
                    {
                        "buildId": exc.result.get("buildId"),
                        "resultsUrl": exc.result.get("resultsUrl"),
                        "reason": self._diagnostic_summary(exc.diagnostics or str(exc)),
                    }
                )
        if payload is None:
            raise TfsPipelineReleaseError("自动发版未返回执行结果")
        result = self._delivery_result(payload)
        if result.get("result") != "succeeded":
            raise TfsPipelineReleaseError(
                f"自动发版未成功：build {result.get('buildId', '—')} / {result.get('result', 'unknown')}"
            )
        if not result.get("expectedArtifactFound") or not result.get("artifactsUrl"):
            raise TfsPipelineReleaseError("自动发版完成但未找到预期发布产物")
        if retry_history:
            result = {
                **result,
                "retryCount": len(retry_history),
                "retryHistory": retry_history,
            }
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
        payload = self._load_payload(completed.stdout)
        if completed.returncode:
            result = self._delivery_result(payload) if payload else {}
            diagnostics = (completed.stderr or "").strip()
            if result and not self._is_transient_failure(diagnostics):
                diagnostics = "\n".join(
                    item for item in (diagnostics, self._build_failure_diagnostics(result)) if item
                )
            reason = self._diagnostic_summary(
                diagnostics or completed.stdout or "未知错误"
            )
            build_label = f"Build #{result['buildId']}" if result.get("buildId") else "流水线"
            raise TfsPipelineReleaseError(
                f"自动发版失败：{build_label}；{reason}",
                transient=self._is_transient_failure(diagnostics),
                result=result,
                diagnostics=diagnostics,
            )
        if payload is None:
            raise TfsPipelineReleaseError("自动发版技能没有返回有效 JSON")
        if not isinstance(payload, dict):
            raise TfsPipelineReleaseError("自动发版技能返回格式不正确")
        return payload

    @staticmethod
    def _load_payload(output: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _delivery_result(payload: dict[str, Any]) -> dict[str, Any]:
        deliveries = payload.get("deliveries")
        if isinstance(deliveries, list) and deliveries:
            delivery = deliveries[-1]
            if isinstance(delivery, dict):
                return dict(delivery)
        return dict(payload)

    @classmethod
    def _is_transient_failure(cls, diagnostics: str) -> bool:
        normalized = diagnostics.casefold()
        return any(marker in normalized for marker in cls.TRANSIENT_FAILURE_MARKERS)

    @staticmethod
    def _diagnostic_summary(diagnostics: str) -> str:
        lines = [line.strip() for line in diagnostics.splitlines() if line.strip()]
        malformed = [line for line in lines if "malformed \\uxxxx encoding" in line.casefold()]
        selected = malformed[-1:] or lines[-8:]
        summary = "；".join(selected)
        return summary[-1800:] or "未知错误"

    def _build_failure_diagnostics(self, result: dict[str, Any]) -> str:
        build_id = result.get("buildId")
        tfs_project = str(result.get("tfsProject") or "").strip()
        results_url = str(result.get("resultsUrl") or result.get("artifactsUrl") or "").strip()
        if not build_id or not tfs_project or not results_url or not settings.tfs_pat:
            return ""
        try:
            parsed = urlsplit(results_url)
            prefix = parsed.path.split("/_build/", 1)[0].rstrip("/")
            encoded_project = "/" + quote(tfs_project, safe="")
            if prefix.casefold().endswith(encoded_project.casefold()):
                prefix = prefix[: -len(encoded_project)]
            base_url = f"{parsed.scheme}://{parsed.netloc}{prefix}"
            timeline_url = (
                f"{base_url}/{quote(tfs_project, safe='')}/_apis/build/builds/{int(build_id)}"
                "/timeline?api-version=4.1"
            )
            with httpx.Client(
                auth=httpx.BasicAuth("", settings.tfs_pat),
                timeout=30,
                trust_env=False,
            ) as client:
                timeline_response = client.get(timeline_url)
                timeline_response.raise_for_status()
                records = timeline_response.json().get("records", [])
                failed_logs = [
                    int((record.get("log") or {}).get("id"))
                    for record in records
                    if str(record.get("result") or "").casefold() == "failed"
                    and (record.get("log") or {}).get("id")
                ]
                outputs: list[str] = []
                for log_id in failed_logs[-4:]:
                    log_url = (
                        f"{base_url}/{quote(tfs_project, safe='')}/_apis/build/builds/{int(build_id)}"
                        f"/logs/{log_id}?api-version=4.1"
                    )
                    log_response = client.get(log_url)
                    log_response.raise_for_status()
                    outputs.append(log_response.text[-6000:])
                return "\n".join(outputs)[-12000:]
        except (ValueError, TypeError, httpx.HTTPError, json.JSONDecodeError):
            return ""
