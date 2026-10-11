"""Publish allowlisted public UI assets, never delivery files or credentials.

Run from the repository with AUTODEV_ENV_FILE pointing to the local runner env.
Requires the runner OSS SDK and Pillow (pip install -r scripts/requirements-media.txt).
No bucket ACL, CORS, lifecycle, or delivery objects are changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.services.oss_storage import OssArtifactStorage
from app.services.ui_media import is_media_path

PREFIX = "autodev-static/v1"
CACHE_CONTROL = "public, max-age=31536000, immutable"


def asset_files() -> list[Path]:
    static = ROOT / "app/static"
    return sorted(path for folder in ["brand", "media", "themes/backgrounds", "themes/previews"]
                  for path in (static / folder).rglob("*") if path.is_file()
                  and not path.is_symlink() and is_media_path("/static/" + path.relative_to(static).as_posix()))


def prepared_asset(source: Path, temporary: Path) -> Path:
    # Mechanical size/encoding optimization only; the artwork is unchanged.
    if source.suffix.lower() != ".png" or "themes" not in source.parts:
        return source
    from PIL import Image
    target = temporary / (source.stem + ".webp")
    with Image.open(source) as image:
        image = image.convert("RGB") if image.mode not in {"RGB", "RGBA"} else image.copy()
        maximum = (840, 480) if "previews" in source.parts else (1280, 1920)
        image.thumbnail(maximum, Image.Resampling.LANCZOS)
        image.save(target, "WEBP", quality=90, method=6)
    return target if target.stat().st_size < source.stat().st_size else source


def verify_asset(url: str, size: int, content_type: str, *, video: bool) -> None:
    # Anonymous, unsigned access proves the URL will work in the user's browser.
    with urlopen(Request(url, method="HEAD"), timeout=30) as response:
        if (response.status != 200 or int(response.headers.get("Content-Length", "0")) != size
                or response.headers.get("Content-Type", "").split(";")[0] != content_type
                or response.headers.get("Cache-Control") != CACHE_CONTROL):
            raise RuntimeError("OSS media verification failed: " + url)
    if video:
        with urlopen(Request(url, headers={"Range": "bytes=0-1023"}), timeout=30) as response:
            if response.status != 206 or len(response.read(1025)) != 1024:
                raise RuntimeError("OSS video does not support verified range playback: " + url)


def publish(*, dry_run: bool = False) -> dict:
    if not settings.oss_enabled:
        raise RuntimeError("OSS is not configured. Point AUTODEV_ENV_FILE to local-runner/.env.runner.")
    artifact_prefix = settings.oss_prefix.strip("/") or "autodev"
    if PREFIX.startswith(artifact_prefix + "/") or artifact_prefix.startswith(PREFIX + "/") or PREFIX == artifact_prefix:
        raise RuntimeError("UI media prefix overlaps the expiring delivery prefix; refusing to publish.")
    endpoint = urlsplit(settings.oss_endpoint if "://" in settings.oss_endpoint else "https://" + settings.oss_endpoint)
    if not endpoint.hostname or not endpoint.hostname.startswith("oss-") or not endpoint.hostname.endswith(".aliyuncs.com"):
        raise RuntimeError("Configure the public Alibaba OSS regional endpoint for UI publishing.")
    base_url = f"https://{settings.oss_bucket}.{endpoint.hostname}"
    storage = None if dry_run else OssArtifactStorage(
        access_key_id=settings.aliyun_access_key_id, access_key_secret=settings.aliyun_access_key_secret,
        region=settings.oss_region, endpoint=settings.oss_endpoint, bucket=settings.oss_bucket,
        prefix=settings.oss_prefix, url_expire_seconds=settings.oss_url_expire_seconds,
        retention_days=settings.oss_retention_days)
    entries = {}
    with tempfile.TemporaryDirectory(prefix="autodev-ui-media-") as directory:
        for source in asset_files():
            file = prepared_asset(source, Path(directory))
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            relative = source.relative_to(ROOT / "app/static")
            key = f"{PREFIX}/{relative.parent.as_posix()}/{source.stem}.{digest[:20]}{file.suffix.lower()}"
            content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if storage:
                storage.client.put_object_from_file(storage.oss.PutObjectRequest(
                    bucket=storage.bucket, key=key, object_acl="public-read", storage_class="Standard",
                    content_type=content_type, cache_control=CACHE_CONTROL, content_disposition="inline",
                    metadata={"source-sha256": source_hash, "sha256": digest}), str(file))
                verify_asset(base_url + "/" + key, file.stat().st_size, content_type,
                             video=content_type.startswith("video/"))
            entries["/static/" + relative.as_posix()] = {
                "key": key, "source_sha256": source_hash, "sha256": digest,
                "source_bytes": source.stat().st_size, "bytes": file.stat().st_size, "content_type": content_type}
            print(f"{'PLAN' if dry_run else 'OK'} {relative.as_posix()} {source.stat().st_size} -> {file.stat().st_size}", flush=True)
    return {"version": 1, "base_url": base_url, "assets": entries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Estimate size; no upload or manifest changes")
    args = parser.parse_args()
    manifest = publish(dry_run=args.dry_run)
    if not args.dry_run:
        # Generated public URL inventory, safe to version; no keys or signatures.
        destination = ROOT / "app/static/media-manifest.json"
        destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(entry["bytes"] for entry in manifest["assets"].values())
    original = sum(entry["source_bytes"] for entry in manifest["assets"].values())
    print(f"Assets: {len(manifest['assets'])}; bytes: {original} -> {total}; saved: {(1-total/original)*100:.1f}%")
