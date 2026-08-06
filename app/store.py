from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .db import add_artifact, add_event, request_detail, row, update_request, update_step, utc_now
from .services.oss_storage import OssArtifactStorage, cleanup_local_deliveries


class LocalStore:
    remote = False

    def next_queued(self) -> str | None:
        item = row("SELECT id FROM delivery_requests WHERE status='queued' ORDER BY created_at LIMIT 1")
        return item["id"] if item else None

    def next_waiting(self) -> str | None:
        item = row(
            """SELECT id FROM delivery_requests
               WHERE status='waiting_merge' AND (next_poll_at IS NULL OR next_poll_at<=?)
               ORDER BY updated_at LIMIT 1""",
            (utc_now(),),
        )
        return item["id"] if item else None

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

    def notify(self, request_id: str, *, action_required: bool) -> None:
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

    def heartbeat(self, state: str = "idle", current_request_id: str | None = None) -> None:
        self._json(
            self.client.post(
                "/api/runner/heartbeat",
                json={
                    "runner_id": self.runner_id,
                    "hostname": socket.gethostname(),
                    "version": settings.runner_version,
                    "state": state,
                    "current_request_id": current_request_id,
                },
            )
        )

    def sync_projects(self, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        data = self._json(
            self.client.put(
                "/api/runner/projects",
                json={"runner_id": self.runner_id, "projects": projects},
            )
        )
        return data.get("projects", [])

    def next_queued(self) -> str | None:
        data = self._json(self.client.post("/api/runner/claim", json={"runner_id": self.runner_id}))
        item = data.get("request")
        return item["id"] if item else None

    def next_waiting(self) -> str | None:
        data = self._json(self.client.get("/api/runner/pollable", params={"runner_id": self.runner_id}))
        item = data.get("request")
        return item["id"] if item else None

    def detail(self, request_id: str) -> dict[str, Any] | None:
        response = self.client.get(f"/api/runner/requests/{request_id}")
        if response.status_code == 404:
            return None
        return self._json(response)["request"]

    def update_request(self, request_id: str, **fields: Any) -> None:
        self._json(self.client.patch(f"/api/runner/requests/{request_id}", json={"fields": fields}))

    def update_step(self, request_id: str, step_code: str, status: str, message: str = "") -> None:
        self._json(
            self.client.patch(
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
            self.client.post(
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
            if path.stat().st_size > max_bytes:
                raise RuntimeError(f"交付物 {path.name} 超过 {settings.max_artifact_mb} MB 上传限制")
            if self.oss_storage:
                _, oss_url = self.oss_storage.upload(request_id, kind, name, path)
                data["external_url"] = oss_url
                response = self.client.post(f"/api/runner/requests/{request_id}/artifacts", data=data)
            else:
                with path.open("rb") as stream:
                    response = self.client.post(
                        f"/api/runner/requests/{request_id}/artifacts",
                        data=data,
                        files={"file": (name, stream, "application/octet-stream")},
                        timeout=httpx.Timeout(900, connect=15),
                    )
        else:
            response = self.client.post(f"/api/runner/requests/{request_id}/artifacts", data=data)
        return int(self._json(response)["artifact_id"])

    def cleanup_expired_artifacts(self) -> tuple[int, int]:
        remote_deleted = self.oss_storage.cleanup_expired_objects() if self.oss_storage else 0
        local_deleted = cleanup_local_deliveries(settings.delivery_dir, settings.oss_retention_days)
        return remote_deleted, local_deleted

    def get_status(self, request_id: str) -> str | None:
        detail = self.detail(request_id)
        return detail["status"] if detail else None

    def notify(self, request_id: str, *, action_required: bool) -> None:
        self._json(
            self.client.post(
                f"/api/runner/requests/{request_id}/notify",
                json={"action_required": action_required},
            )
        )

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
