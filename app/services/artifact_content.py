"""Read registered delivery files from disk or the runner's public OSS endpoint."""
from __future__ import annotations

import codecs
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx


PREVIEW_MAX_BYTES = 512 * 1024
TEXT_ARTIFACT_KINDS = {"sql", "joint_sql", "config", "rollback_note", "analysis_report"}
TEXT_SUFFIXES = {
    ".sql", ".txt", ".yaml", ".yml", ".md", ".markdown", ".json", ".xml",
    ".properties", ".conf", ".ini", ".cfg", ".toml", ".csv", ".tsv", ".log",
    ".js", ".sh", ".ps1", ".bat", ".cmd", ".template", ".example", ".env",
}
OSS_HOST = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]\.oss-[a-z0-9-]+\.aliyuncs\.com")
NON_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ArtifactContentError(ValueError):
    def __init__(self, message: str, status_code: int = 415) -> None:
        super().__init__(message)
        self.status_code = status_code


def trusted_oss_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https" and parts.username is None and parts.password is None
            and parts.port in (None, 443) and bool(OSS_HOST.fullmatch(parts.hostname or ""))
        )
    except ValueError:
        return False


def text_artifact_previewable(artifact: dict) -> bool:
    return (
        artifact.get("kind") in TEXT_ARTIFACT_KINDS
        and Path(str(artifact.get("name") or "")).suffix.casefold() in TEXT_SUFFIXES
        and (bool(artifact.get("local_path")) or not artifact.get("external_url")
             or trusted_oss_url(str(artifact["external_url"])))
    )


def read_oss_artifact(url: str, limit: int) -> bytes:
    if not trusted_oss_url(url):
        raise ArtifactContentError("此外部产物链接无法在线读取")
    try:
        with httpx.stream("GET", url, timeout=12, follow_redirects=False, trust_env=False,
                          headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code in {401, 403, 404, 410}:
                raise ArtifactContentError("产物链接已过期或文件不存在", 410)
            if response.status_code != 200:
                raise ArtifactContentError("暂时无法读取产物文件", 502)
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                content.extend(chunk[:limit - len(content)])
                if len(content) == limit:
                    break
            return bytes(content)
    except httpx.HTTPError:
        raise ArtifactContentError("暂时无法读取产物文件", 502) from None


def decode_artifact_text(content: bytes, *, truncated: bool = False) -> str:
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ("utf-16",)
    elif content.startswith(codecs.BOM_UTF8):
        encodings = ("utf-8-sig",)
    else:
        encodings = ("utf-8", "gb18030")
    for encoding in encodings:
        try:
            text = codecs.getincrementaldecoder(encoding)().decode(content, final=not truncated)
        except UnicodeError:
            continue
        if not NON_TEXT.search(text):
            return text
    raise ArtifactContentError("文件无法按文本读取")


def read_artifact_bytes(artifact: dict, delivery_root: Path, limit: int) -> bytes:
    if artifact.get("local_path"):
        path = Path(artifact["local_path"]).resolve()
        try:
            if path.is_relative_to(delivery_root.resolve()) and path.is_file():
                with path.open("rb") as file:
                    return file.read(limit)
        except OSError:
            pass
    if not artifact.get("external_url"):
        raise ArtifactContentError("产物文件不存在", 404)
    return read_oss_artifact(artifact["external_url"], limit)


def read_text_preview(artifact: dict, delivery_root: Path) -> tuple[str, bool]:
    data = read_artifact_bytes(artifact, delivery_root, PREVIEW_MAX_BYTES + 1)
    truncated = len(data) > PREVIEW_MAX_BYTES
    return decode_artifact_text(data[:PREVIEW_MAX_BYTES], truncated=truncated), truncated
