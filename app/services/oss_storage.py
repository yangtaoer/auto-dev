from __future__ import annotations

import logging
import mimetypes
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import quote


logger = logging.getLogger("autodev.oss")


class OssArtifactStorage:
    """Local-runner OSS storage for delivery artifacts and expiring download links."""

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        region: str,
        endpoint: str,
        bucket: str,
        prefix: str,
        url_expire_seconds: int,
        retention_days: int,
    ) -> None:
        import alibabacloud_oss_v2 as oss

        endpoint = endpoint.strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        credentials = oss.credentials.StaticCredentialsProvider(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
        )
        config = oss.Config(
            region=region,
            endpoint=endpoint,
            credentials_provider=credentials,
            connect_timeout=15,
            readwrite_timeout=900,
        )
        self.oss = oss
        self.client = oss.Client(config)
        self.bucket = bucket
        self.prefix = prefix.strip().strip("/") or "autodev"
        self.url_expire_seconds = url_expire_seconds
        self.retention_days = retention_days

    def upload(self, request_id: str, kind: str, name: str, local_path: Path) -> tuple[str, str]:
        if not local_path.is_file():
            raise RuntimeError(f"待上传 OSS 的交付物不存在：{local_path}")
        object_key = self._object_key(request_id, kind, name)
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        filename = quote(local_path.name, safe="")
        request = self.oss.PutObjectRequest(
            bucket=self.bucket,
            key=object_key,
            content_type=content_type,
            content_disposition=f"attachment; filename*=UTF-8''{filename}",
        )
        self.client.put_object_from_file(request, str(local_path))
        url = self.signed_download_url(object_key, local_path.name)
        logger.info("交付物已上传 OSS key=%s size=%s", object_key, local_path.stat().st_size)
        return object_key, url

    def signed_download_url(self, object_key: str, filename: str) -> str:
        disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
        request = self.oss.GetObjectRequest(
            bucket=self.bucket,
            key=object_key,
            response_content_disposition=disposition,
        )
        result = self.client.presign(
            request,
            expires=timedelta(seconds=self.url_expire_seconds),
        )
        return str(result.url)

    def cleanup_expired_objects(self, *, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        request = self.oss.ListObjectsV2Request(
            bucket=self.bucket,
            prefix=self.prefix + "/",
            max_keys=1000,
        )
        deleted = 0
        paginator = self.client.list_objects_v2_paginator()
        for page in paginator.iter_page(request):
            for item in page.contents or []:
                modified = item.last_modified
                if not modified:
                    continue
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=UTC)
                if modified > cutoff:
                    continue
                try:
                    self.client.delete_object(
                        self.oss.DeleteObjectRequest(bucket=self.bucket, key=item.key)
                    )
                    deleted += 1
                except Exception as exc:
                    logger.warning("OSS 过期对象删除失败 key=%s error=%s", item.key, exc)
        return deleted

    def _object_key(self, request_id: str, kind: str, name: str) -> str:
        normalized = str(name).replace("\\", "/")
        parts = []
        for part in PurePosixPath(normalized).parts:
            if part in {"", ".", "..", "/"}:
                continue
            cleaned = re.sub(r"[^\w.()\-\u4e00-\u9fff]+", "_", part, flags=re.UNICODE)
            parts.append(cleaned[:180] or "artifact")
        if not parts:
            parts = ["artifact.bin"]
        safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "_", kind)[:60] or "artifact"
        safe_request = re.sub(r"[^a-zA-Z0-9_.-]+", "_", request_id)[:80]
        return "/".join((self.prefix, safe_request, safe_kind, *parts))


def cleanup_local_deliveries(delivery_dir: Path, retention_days: int, *, now: datetime | None = None) -> int:
    """Delete only expired per-request folders under the configured runner delivery root."""
    if not delivery_dir.is_dir():
        return 0
    root = delivery_dir.resolve()
    cutoff = (now or datetime.now(UTC)).timestamp() - retention_days * 86400
    deleted = 0
    for candidate in root.iterdir():
        if not candidate.is_dir() or candidate.stat().st_mtime > cutoff:
            continue
        resolved = candidate.resolve()
        if resolved.parent != root:
            logger.warning("跳过不在交付根目录内的清理目标：%s", resolved)
            continue
        shutil.rmtree(resolved)
        deleted += 1
    return deleted
