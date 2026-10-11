"""Public UI media only; credentials and expiring delivery links never enter this manifest."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from app.config import ROOT, settings

MANIFEST = ROOT / "app/static/media-manifest.json"
MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".mp4", ".webm"}


def is_media_path(source: object) -> bool:
    if not isinstance(source, str) or not re.fullmatch(r"/static/[a-zA-Z0-9_./-]+", source):
        return False
    path = PurePosixPath(source)
    return (".." not in path.parts and path.suffix.lower() in MEDIA_EXTENSIONS
            and source.startswith(("/static/brand/", "/static/media/", "/static/themes/backgrounds/",
                                   "/static/themes/previews/")))


@lru_cache(maxsize=1)
def bundle() -> dict:
    empty = {"origin": "", "assets": {}}
    if not settings.ui_media_enabled:
        return empty
    try:
        manifest = json.loads(MANIFEST.read_text("utf-8"))
        base = manifest["base_url"].rstrip("/")
        parsed = urlsplit(base)
        if (manifest.get("version") != 1 or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path
                or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.netloc)):
            return empty
        assets = {}
        for source, entry in manifest["assets"].items():
            if not is_media_path(source) or not isinstance(entry, dict):
                continue
            key, digest = entry.get("key", ""), entry.get("source_sha256", "")
            if (not isinstance(key, str) or not re.fullmatch(r"autodev-static/v1/[a-zA-Z0-9_./-]+", key)
                    or ".." in PurePosixPath(key).parts or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                continue
            file = ROOT / "app" / source.lstrip("/")
            # A changed source must never keep using an obsolete cloud asset.
            if not file.is_file():
                continue
            with file.open("rb") as stream:
                actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual_digest == digest:
                assets[source] = base + "/" + key
        return {"origin": base if assets else "", "assets": assets}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return empty


def url(source: str) -> str:
    return bundle()["assets"].get(source, source)


def theme_media(item: dict) -> dict:
    result = dict(item, tokens=dict(item["tokens"]))
    result["preview"] = url(item["preview"])
    result["background"] = url(item["background"])
    result["tokens"]["skin-sidebar-art"] = f'url("{result["background"]}")'
    return result
