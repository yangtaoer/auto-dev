"""Use a separately pinned CLI: the Python SDK's bundled CLI can lag new models."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from ..config import ROOT, settings


MIN_ASTRA_VERSION = (0, 153, 4)


def managed_binary(runtime_dir: Path) -> Path | None:
    arm = platform.machine().lower() in {"arm64", "aarch64"}
    arch = "arm64" if arm else "x64"
    cpu = "aarch64" if arm else "x86_64"
    system = platform.system()
    os_tag, target = {
        "Windows": ("win32", f"{cpu}-pc-windows-msvc"),
        "Linux": ("linux", f"{cpu}-unknown-linux-musl"),
        "Darwin": ("darwin", f"{cpu}-apple-darwin"),
    }.get(system, ("", ""))
    package = (runtime_dir / "node_modules/@openai/codex").resolve()
    native = f"codex-{os_tag}-{arch}"
    # npm flat/nested installs and pnpm's symlinked package layout.
    roots = [package.parent / native, package / "node_modules/@openai" / native,
             runtime_dir / "node_modules/@openai" / native, package]
    for root in roots:
        binary = root / "vendor" / target / "bin" / ("codex.exe" if system == "Windows" else "codex")
        if binary.is_file():
            return binary.resolve()
    return None


def resolve_codex_runtime() -> dict[str, str]:
    explicit = os.getenv("CODEX_BIN", "").strip()
    binary = Path(explicit) if explicit else managed_binary(ROOT / "local-runner/codex-runtime")
    if binary is None:
        found = shutil.which("codex.exe" if os.name == "nt" else "codex")
        binary = Path(found) if found else None
    if binary is None or not binary.is_file():
        raise RuntimeError("未找到新版 Codex CLI；请运行 local-runner/install.ps1，或设置 CODEX_BIN 为新版原生运行器路径。")
    result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=15,
                            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout)
    if result.returncode or not match:
        raise RuntimeError("无法确认 Codex CLI 版本；请检查 CODEX_BIN 或重新安装平台运行器。")
    if settings.codex_model == "gpt-6-astra" and tuple(map(int, match.groups())) < MIN_ASTRA_VERSION:
        raise RuntimeError(f"Codex 运行器 {match.group()} 过旧；GPT-6 Astra 需使用平台已验证的 0.153.4 或更新版本，请运行 local-runner/install.ps1 后重试。")
    return {"path": str(binary), "version": match.group()}
