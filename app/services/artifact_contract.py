"""The platform-wide business delivery contract, independent of project snapshots."""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CHANGED_ASSET_KINDS = frozenset({"sql", "config", "rollback_note"})
BUSINESS_ARTIFACT_KINDS = CHANGED_ASSET_KINDS | {"package", "release_artifact", "business_logic_diagram"}
PACKAGE_EXTENSIONS = frozenset({".zip", ".jar", ".war", ".tar", ".gz", ".tgz", ".7z", ".exe", ".msi", ".apk", ".aab", ".deb", ".rpm"})
SQL_PATTERNS = ["*.sql", "**/*.sql"]
CONFIG_PATTERNS = [
    "**/*.yml", "**/*.yaml", "**/*.properties", "**/*.conf", "**/*.ini", "**/*.cfg",
    "**/config/*.js", "**/config/*.json", "**/*config*.xml", "**/application*.xml",
    "deploy/*.sh", "deploy/*.ps1", "**/*.template", "**/*.example",
]
DELIVERY_CONTRACT_INSTRUCTIONS = """
全平台研发交付只包含业务系统包（或成功流水线的业务包产物链接）、SQL 脚本、必要部署配置和适用的旧数据回退说明。截图、普通说明报告、菜单链接、校验 JSON、测试程序不作为独立研发交付物。
所有项目的正向和回退交付脚本一律禁止创建、修改、删除或调用存储过程，禁止用匿名过程块或动态执行包装部署 SQL。业务建表、改表、索引、初始化和可用直接 SQL 完成的存量数据迁移或修复，必须先保存为本需求仓库中的可执行 .sql 文件再执行。补齐顺序、适用 Schema、前置条件和重复执行保护。回退可用直接 SQL 时单独交付回退 SQL。临时测试造数与清理不得混入正式脚本。
按实际交付基线整理 SQL，尽量使用一个脚本。仓库中确有多份脚本时逐项声明用途和执行顺序，平台会合为一个交付文件并保留各段。需要选择安装、升级或回退路径时，写清每段适用条件。
旧数据无法安全使用直接 SQL 处理或回退时，将原因、影响范围、备份或恢复来源、人工回退步骤和验证方式写入独立 .txt 或 .md 文件，并按 rollback_note 交付。在 database_changes 使用 manual_rollback，script_paths 指向说明文件，evidence 说明无法使用直接 SQL 的原因。能够用 SQL 完成的正向变更仍交付 SQL；说明不能替代已经执行的业务写入脚本。
运行配置新增/变更必须交付对应配置文件或可合并配置模板，写明配置项、目标文件、用途和占位符；不得包含真实密码、令牌、私钥或本机连接凭据。不要将 pom.xml、Mapper XML、TBP view.xml 等源码伪装成配置产物。配置删除也要提供明确的可应用配置模板。
最终 delivery_assets 逐项填写 repository（当前仓库名）、path（仓库相对路径）、kind（sql/config/rollback_note）、purpose。交付文件必须保存在实际隔离仓库并纳入本次代码变更，不能只放 /tmp、被忽略的目录或测试目录；sql_changes/config_changes 同步列出对应路径，无变更则 []。database_changes 记录业务变更、临时验证和人工回退的交付路径及证据；业务脚本不得被标为临时验证以跳过交付。
""".strip()


def matches_patterns(path: str, patterns: Iterable[str]) -> bool:
    normalized = str(path).replace("\\", "/").casefold()
    for value in patterns:
        pattern = str(value).replace("\\", "/").casefold()
        if fnmatch.fnmatchcase(normalized, pattern) or (
            pattern.startswith("**/") and fnmatch.fnmatchcase(normalized, pattern[3:])
        ):
            return True
    return False


def safe_asset_path(value: str) -> bool:
    path = PurePosixPath(str(value).replace("\\", "/"))
    parts = {part.casefold() for part in path.parts}
    return bool(path.parts) and not path.is_absolute() and not any(
        part in {"..", ".git", ".github", ".agents", ".codex", "secrets", "node_modules", "target", "dist", "__pycache__", "test", "tests"}
        for part in parts
    ) and not any(":" in part or part.startswith(".env") for part in parts)


def changed_asset_kind(path: str, project: dict[str, Any] | None = None, *, declared_kind: str = "") -> str:
    """Recognize deployable assets, never source XML or arbitrary test evidence."""
    if not safe_asset_path(path):
        return ""
    normalized = str(path).replace("\\", "/").casefold()
    name = normalized.rsplit("/", 1)[-1]
    project = project or {}
    if name.endswith(".sql"):
        return "sql"
    if declared_kind == "rollback_note" and Path(name).suffix in {".txt", ".md"}:
        return "rollback_note"
    if re.search(r"(?:^|/)docs?/.*\.json$", normalized):
        return ""
    if name in {"pom.xml", "package.json", "package-lock.json", "tsconfig.json"} or re.search(
        r"(?:\.view\.xml|(?:mapper|mapping)\.xml|\.form\.xml)$", name
    ) or "/tbp_config/runtime/module/" in "/" + normalized:
        return ""
    suffix = Path(name).suffix
    patterns = [*CONFIG_PATTERNS, *(project.get("config_patterns") or [])]
    if suffix == ".xml":
        # Existing presets used **/*.xml: that must not turn every source file into a deployment file.
        patterns = [pattern for pattern in patterns if str(pattern).casefold() not in {"*.xml", "**/*.xml"}]
    if declared_kind == "config" and suffix in {".xml", ".json", ".js", ".sh", ".ps1", ".txt", ".toml"}:
        return "config"
    if matches_patterns(normalized, patterns):
        return "config"
    return ""


def enforce_artifact_policy(project: dict[str, Any]) -> dict[str, Any]:
    """Do not let stale project/queued-task whitelists undo the global contract."""
    result = dict(project)
    policy = dict(result.get("artifact_policy") or {})
    policy.update(
        allowed_changed_asset_kinds=sorted(CHANGED_ASSET_KINDS),
        allowed_user_facing_kinds=sorted(BUSINESS_ARTIFACT_KINDS),
    )
    if result.get("project_key") and not result.get("simulation_mode"):
        policy["require_packages"] = True
    result["artifact_policy"] = policy
    result["sql_patterns"] = list(dict.fromkeys([*SQL_PATTERNS, *(result.get("sql_patterns") or [])]))
    result["config_patterns"] = list(dict.fromkeys([*CONFIG_PATTERNS, *(result.get("config_patterns") or [])]))
    return result


def configuration_template(content: str) -> str:
    """Keep deployable syntax but replace literal secrets with server-side placeholders."""
    placeholder = "${SET_ON_SERVER}"
    secret_key = r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|credential)[\w.-]*"
    try:
        value = json.loads(content)
        def redact(item):
            if isinstance(item, dict):
                return {key: placeholder if re.fullmatch(secret_key, str(key), re.I) else redact(entry)
                        for key, entry in item.items()}
            if isinstance(item, list):
                return [redact(entry) for entry in item]
            if isinstance(item, str):
                return re.sub(r'(?i)(://[^\s/:@]+:)[^\s/@]+(@)', rf'\g<1>{placeholder}\2', item)
            return item
        content = json.dumps(redact(value), ensure_ascii=False, indent=2) + "\n"
    except ValueError:
        pass
    content = re.sub(
        rf'(?im)^(\s*["\']?{secret_key}["\']?\s*[:=]\s*)([^\r\n]+)',
        lambda m: m[1] + ('"' + placeholder + '",' if m[2].strip().endswith(',') else '"' + placeholder + '"'),
        content,
    )
    content = re.sub(rf'(?i)(\b{secret_key}\s*=\s*["\'])[^"\']*(["\'])', rf'\g<1>{placeholder}\2', content)
    content = re.sub(rf'(?i)(<(?:property|add|param)\b[^>]*\b(?:name|key)=["\']{secret_key}["\'][^>]*\bvalue=["\'])[^"\']*(["\'])',
                     rf'\g<1>{placeholder}\2', content)
    content = re.sub(rf'(?is)(<({secret_key})\b[^>]*>).*?(</\2\s*>)', rf'\g<1>{placeholder}\3', content)
    content = re.sub(r'(?i)(://[^\s/:@]+:)[^\s/@]+(@)', rf'\g<1>{placeholder}\2', content)
    content = re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', placeholder, content)
    return content
