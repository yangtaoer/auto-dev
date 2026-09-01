from __future__ import annotations

import logging
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .db import (
    add_artifact, add_event, prior_request_history, request_detail, row, transaction,
    update_request, update_step, utc_now,
)
from .services.oss_storage import OssArtifactStorage, cleanup_local_deliveries


logger = logging.getLogger("autodev.remote_store")


class LocalStore:
    remote = False

    def next_queued(self) -> str | None:
        with transaction() as conn:
            item = conn.execute(
                "SELECT id FROM delivery_requests WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not item:
                return None
            now = utc_now()
            changed = conn.execute(
                """UPDATE delivery_requests SET status='validating',started_at=COALESCE(started_at,?),updated_at=?
                   WHERE id=? AND status='queued'""",
                (now, now, item["id"]),
            ).rowcount
            return item["id"] if changed else None

    def next_waiting(self) -> str | None:
        now = utc_now()
        lease_until = (datetime.now(UTC) + timedelta(seconds=max(60, settings.poll_seconds * 3))).isoformat()
        with transaction() as conn:
            item = conn.execute(
                """SELECT id FROM delivery_requests
                   WHERE status='waiting_merge' AND (next_poll_at IS NULL OR next_poll_at<=?)
                   ORDER BY updated_at LIMIT 1""",
                (now,),
            ).fetchone()
            if not item:
                return None
            conn.execute(
                "UPDATE delivery_requests SET next_poll_at=?,updated_at=? WHERE id=? AND status='waiting_merge'",
                (lease_until, now, item["id"]),
            )
            return item["id"]

    @staticmethod
    def detail(request_id: str) -> dict[str, Any] | None:
        return request_detail(request_id)

    @staticmethod
    def update_request(request_id: str, **fields: Any) -> None:
        update_request(request_id, **fields)

    @staticmethod
    def update_step(request_id: str, step_code: str, status: str, message: str = "") -> None:
        update_step(request_id, step_code, status, message)

    @staticmethod
    def add_event(
        request_id: str,
        event_type: str,
        message: str,
        *,
        level: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        add_event(request_id, event_type, message, level=level, metadata=metadata)

    @staticmethod
    def add_artifact(request_id: str, kind: str, name: str, local_path: str = "", external_url: str = "") -> int:
        return add_artifact(request_id, kind, name, local_path, external_url)

    def get_status(self, request_id: str) -> str | None:
        item = row("SELECT status FROM delivery_requests WHERE id=?", (request_id,))
        return item["status"] if item else None

    @staticmethod
    def prior_requests(project_key: str, work_item_id: int, request_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return prior_request_history(project_key, work_item_id, exclude_request_id=request_id, limit=limit)

    def notify(self, request_id: str, *, action_required: bool, terminal: bool = False) -> None:
        raise RuntimeError("本地存储不使用远程通知接口")


class RemoteStore:
    """Cloud control-plane adapter used by the outbound-only Windows runner."""

    remote = True

    def __init__(self, cloud_url: str, token: str, runner_id: str) -> None:
        if not cloud_url:
            raise RuntimeError("本机执行器缺少 AUTODEV_CLOUD_URL")
        if not token:
            raise RuntimeError("本机执行器缺少 AUTODEV_RUNNER_TOKEN 或对应 _FILE")
        self.runner_id = runner_id
        self.client = httpx.Client(
            base_url=cloud_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=False,
        )
        self.oss_storage = None
        if settings.oss_enabled:
            self.oss_storage = OssArtifactStorage(
                access_key_id=settings.aliyun_access_key_id,
                access_key_secret=settings.aliyun_access_key_secret,
                region=settings.oss_region,
                endpoint=settings.oss_endpoint,
                bucket=settings.oss_bucket,
                prefix=settings.oss_prefix,
                url_expire_seconds=settings.oss_url_expire_seconds,
                retention_days=settings.oss_retention_days,
            )

    def close(self) -> None:
        self.client.close()

    def heartbeat(
        self,
        state: str = "idle",
        current_request_id: str | None = None,
        codex_usage: dict[str, Any] | None = None,
        current_request_ids: list[str] | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._json(
            self._request(
                "POST",
                "/api/runner/heartbeat",
                json={
                    "runner_id": self.runner_id,
                    "hostname": socket.gethostname(),
                    "version": settings.runner_version,
                    "state": state,
                    "current_request_id": current_request_id,
                    "current_request_ids": current_request_ids or ([current_request_id] if current_request_id else []),
                    "max_concurrency": max_concurrency or settings.runner_max_concurrency,
                    "codex_usage": codex_usage or {},
                },
            )
        )

    def sync_projects(self, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        data = self._json(
            self._request(
                "PUT",
                "/api/runner/projects",
                json={"runner_id": self.runner_id, "projects": projects},
            )
        )
        return data.get("projects", [])

    def claim_intake(self) -> dict[str, Any] | None:
        data = self._json(
            self._request("POST", "/api/runner/intakes/claim", json={"runner_id": self.runner_id})
        )
        return data.get("intake")

    def route_intake(
        self,
        intake_id: str,
        *,
        project_key: str | None = None,
        project_keys: list[str] | None = None,
        classification: list[dict[str, Any]] | None = None,
        work_item_title: str = "",
        error_message: str = "",
    ) -> list[str]:
        data = self._json(
            self._request(
                "POST",
                f"/api/runner/intakes/{intake_id}/route",
                json={
                    "runner_id": self.runner_id,
                    "project_key": project_key,
                    "project_keys": project_keys or ([project_key] if project_key else []),
                    "classification": classification or [],
                    "work_item_title": work_item_title,
                    "error_message": error_message,
                },
            )
        )
        return list(data.get("request_ids") or ([data["request_id"]] if data.get("request_id") else []))

    def finalize_joint(self, request_id: str) -> dict[str, Any]:
        return self._json(
            self._request("POST", f"/api/runner/requests/{request_id}/joint-finalize", json={})
        )

    def notify_joint_review(self, request_id: str) -> dict[str, Any]:
        return self._json(
            self._request("POST", f"/api/runner/requests/{request_id}/joint-review-notify", json={})
        )

    def next_queued(self) -> str | None:
        data = self._json(self._request("POST", "/api/runner/claim", json={"runner_id": self.runner_id}))
        item = data.get("request")
        return item["id"] if item else None

    def next_waiting(self) -> str | None:
        data = self._json(self._request("GET", "/api/runner/pollable", params={"runner_id": self.runner_id}))
        item = data.get("request")
        return item["id"] if item else None

    def detail(self, request_id: str) -> dict[str, Any] | None:
        response = self._request("GET", f"/api/runner/requests/{request_id}")
        if response.status_code == 404:
            return None
        return self._json(response)["request"]

    def list_tasks(self, limit: int = 80) -> list[dict[str, Any]]:
        data = self._json(
            self._request("GET", "/api/runner/tasks", params={"runner_id": self.runner_id, "limit": limit})
        )
        return data.get("tasks", [])

    def prior_requests(self, project_key: str, work_item_id: int, request_id: str, limit: int = 8) -> list[dict[str, Any]]:
        data = self._json(
            self._request(
                "GET",
                "/api/runner/request-history",
                params={
                    "project_key": project_key,
                    "work_item_id": work_item_id,
                    "request_id": request_id,
                    "limit": limit,
                },
            )
        )
        return list(data.get("history") or [])

    def codex_watch_active(self, request_id: str) -> bool:
        data = self._json(self._request("GET", f"/api/runner/requests/{request_id}/codex-watch/active"))
        return bool(data.get("active"))

    def publish_codex_events(self, request_id: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        self._json(
            self._request(
                "POST",
                f"/api/runner/requests/{request_id}/codex-watch/events",
                json={"events": events},
            )
        )

    def update_request(self, request_id: str, **fields: Any) -> None:
        self._json(self._request("PATCH", f"/api/runner/requests/{request_id}", json={"fields": fields}))

    def update_step(self, request_id: str, step_code: str, status: str, message: str = "") -> None:
        self._json(
            self._request(
                "PATCH",
                f"/api/runner/requests/{request_id}/steps/{step_code}",
                json={"status": status, "message": message},
            )
        )

    def add_event(
        self,
        request_id: str,
        event_type: str,
        message: str,
        *,
        level: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._json(
            self._request(
                "POST",
                f"/api/runner/requests/{request_id}/events",
                json={"event_type": event_type, "message": message, "level": level, "metadata": metadata or {}},
            )
        )

    def add_artifact(self, request_id: str, kind: str, name: str, local_path: str = "", external_url: str = "") -> int:
        data = {"kind": kind, "name": name, "external_url": external_url}
        if local_path:
            path = Path(local_path)
            if not path.is_file():
                raise RuntimeError(f"待上传交付物不存在：{path}")
            max_bytes = settings.max_artifact_mb * 1024 * 1024
            if path.stat().st_size >= max_bytes:
                raise RuntimeError(f"交付物 {path.name} 达到或超过上传限制，单个文件必须小于 {settings.max_artifact_mb} MB")
            if self.oss_storage:
                _, oss_url = self.oss_storage.upload(request_id, kind, name, path)
                data["external_url"] = oss_url
                response = self._request("POST", f"/api/runner/requests/{request_id}/artifacts", data=data)
            else:
                with path.open("rb") as stream:
                    response = self.client.post(
                        f"/api/runner/requests/{request_id}/artifacts",
                        data=data,
                        files={"file": (name, stream, "application/octet-stream")},
                        timeout=httpx.Timeout(900, connect=15),
                    )
        else:
            response = self._request("POST", f"/api/runner/requests/{request_id}/artifacts", data=data)
        return int(self._json(response)["artifact_id"])

    def cleanup_expired_artifacts(self) -> tuple[int, int]:
        remote_deleted = self.oss_storage.cleanup_expired_objects() if self.oss_storage else 0
        local_deleted = cleanup_local_deliveries(settings.delivery_dir, settings.oss_retention_days)
        return remote_deleted, local_deleted

    def get_status(self, request_id: str) -> str | None:
        detail = self.detail(request_id)
        return detail["status"] if detail else None

    def notify(self, request_id: str, *, action_required: bool, terminal: bool = False) -> None:
        self._json(
            self._request(
                "POST",
                f"/api/runner/requests/{request_id}/notify",
                json={"action_required": action_required, "terminal": terminal},
            )
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        transient_statuses = {429, 502, 503, 504}
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code not in transient_statuses or attempt == 3:
                    return response
                last_error = RuntimeError(f"HTTP {response.status_code}")
            except httpx.TransportError as exc:
                last_error = exc
                if attempt == 3:
                    raise RuntimeError(f"云端连接失败：{exc}") from exc
            delay = 0.4 * (2 ** attempt)
            logger.warning(
                "云端接口暂时不可用，%.1f 秒后重试 (%s/4) method=%s path=%s error=%s",
                delay,
                attempt + 2,
                method,
                url,
                last_error,
            )
            time.sleep(delay)
        raise RuntimeError(f"云端接口调用失败：{last_error}")

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        if response.is_redirect:
            raise RuntimeError("云端地址发生重定向，请把 AUTODEV_CLOUD_URL 配置为最终 HTTPS 地址")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"云端接口失败 ({response.status_code})：{detail}") from exc
        return response.json()
