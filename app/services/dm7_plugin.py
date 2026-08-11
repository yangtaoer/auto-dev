from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Dm7PluginCapability:
    available: bool
    skill_path: Path | None = None
    launcher_path: Path | None = None
    config_overrides: tuple[str, ...] = ()
    message: str = ""


def discover_dm7_plugin() -> Dm7PluginCapability:
    """Find the locally installed DM7 plugin and expose it to the SDK app-server.

    Codex Desktop can register plugins for interactive tasks without automatically
    forwarding their MCP tools to SDK-created threads. The runner therefore mounts
    the same local launcher explicitly for each development session.
    """

    codex_root = Path(os.getenv("CODEX_HOME") or (Path.home() / ".codex"))
    cache_root = codex_root / "plugins" / "cache" / "dm7-database-local" / "dm7-database"
    candidates = [
        path
        for path in cache_root.glob("*/skills/dm7-database/SKILL.md")
        if path.is_file() and (path.parents[2] / "scripts" / "launch-mcp.ps1").is_file()
    ]
    if not candidates:
        return Dm7PluginCapability(
            available=False,
            message="未找到已安装的 DM7 数据库插件；涉及数据库的关键任务将进入待补充而不是盲目实施。",
        )

    skill_path = max(candidates, key=lambda path: path.stat().st_mtime)
    plugin_root = skill_path.parents[2]
    launcher_path = plugin_root / "scripts" / "launch-mcp.ps1"
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        return Dm7PluginCapability(
            available=False,
            skill_path=skill_path,
            launcher_path=launcher_path,
            message="DM7 插件已安装，但没有找到 Windows PowerShell，无法启动数据库工具服务。",
        )

    command = json.dumps(str(powershell), ensure_ascii=False)
    args = json.dumps(
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher_path)],
        ensure_ascii=False,
    )
    overrides = (
        f"mcp_servers.dm7_autodev.command={command}",
        f"mcp_servers.dm7_autodev.args={args}",
        "mcp_servers.dm7_autodev.startup_timeout_sec=60",
        "mcp_servers.dm7_autodev.tool_timeout_sec=3600",
        "mcp_servers.dm7_autodev.required=true",
        'mcp_servers.dm7_autodev.default_tools_approval_mode="approve"',
    )
    return Dm7PluginCapability(
        available=True,
        skill_path=skill_path,
        launcher_path=launcher_path,
        config_overrides=overrides,
        message="DM7 数据库插件已挂载，本轮可自主检查本地开发库结构与数据。",
    )
