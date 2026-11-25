"""Detect procedural statements in database delivery scripts."""
from __future__ import annotations

import re
from collections.abc import Iterable


SQL_BUNDLE_NAME = "数据库交付.sql"
JOINT_SQL_BUNDLE_NAME = "联合数据库交付.sql"
SQL_BUNDLE_HEADER = "-- AutoDev 数据库交付脚本"
_SECTION = "-- === 源脚本 "
_SOURCE_LINE = "-- | "
_PURPOSE = "-- 用途 "


def prefer_consolidated_sql(artifacts: list[dict]) -> list[dict]:
    """Keep existing records readable and show only the bundle when one exists."""
    bundle = next((item for item in reversed(artifacts) if item.get("kind") == "sql"
                   and item.get("name") in {SQL_BUNDLE_NAME, JOINT_SQL_BUNDLE_NAME}), None)
    return [item for item in artifacts if item.get("kind") != "sql" or item is bundle] if bundle else artifacts


def sql_bundle_sections(content: str) -> list[tuple[str, str, str]]:
    sections = []
    for block in content.split("\n" + _SECTION)[1:]:
        source, _, lines = block.partition("\n")
        block_lines = lines.split("\n")
        purpose = next((line[len(_PURPOSE):] for line in block_lines if line.startswith(_PURPOSE)), "")
        sql = "\n".join(line[len(_SOURCE_LINE):] for line in block_lines if line.startswith(_SOURCE_LINE))
        sections.append((source.strip(), purpose, sql))
    return sections


def bundle_delivery_sql(sources: Iterable[tuple[str, str, str]]) -> str:
    """Preserve source boundaries and leave mutually exclusive writes inactive."""
    lines = [
        SQL_BUNDLE_HEADER,
        "-- 各段适用条件请参阅用途及原脚本注释。预检、安装、升级与回退可能互斥。",
        '-- 选取适用的原 SQL 段，去掉每行开头的 "-- | " 后执行。直接运行整个文件不会执行任何语句。',
    ]
    for name, purpose, sql in sources:
        source = " ".join(name.split())
        description = " ".join(purpose.split())
        lines.extend(("", f"{_SECTION}{source}"))
        if description:
            lines.append(f"{_PURPOSE}{description}")
        normalized = sql.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        lines.extend(_SOURCE_LINE + line for line in normalized.split("\n"))
    return "\n".join(lines) + "\n"


_SQL_NON_CODE = re.compile(r"""
    /\*(?:!|M!)\d*\s*(?P<executable>.*?)\*/
    | --[^\r\n]* | /\*.*?\*/
    | (?P<literal>
        q'(?:\[.*?\]|\(.*?\)|\{.*?\}|<.*?>|(?P<delimiter>.) .*? (?P=delimiter))'
        | \$(?P<tag>[a-z_]\w*|)\$.*?\$(?P=tag)\$
        | '(?:''|\\.|[^'\\])*' | "(?:""|[^"])*"
        | \x60(?:\x60\x60|[^\x60])*\x60 | \[(?:\]\]|[^\]])*\]
    )
""", re.IGNORECASE | re.DOTALL | re.VERBOSE)

_PROCEDURE = re.compile(r"""
    \b(?:
        (?:CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?
            (?:(?:NON)?EDITIONABLE\s+)?(?:DEFINER\s*=[^;]+?\s+)?
          | (?:ALTER|DROP|RENAME)\s+)(?:PROCEDURE|PROC)\b
      | CALL\s+[\w?$]+(?:\s*\.\s*[\w?$]+)*\s*\(
      | EXEC(?:UTE)?\b(?:\s*\(|\s+(?!(?:AS|ON|TO|FROM|ANY|FUNCTION)\b)[\w?@])
    )
    | (?:^|[;{])\s*(?:\?\s*=\s*)?CALL\s+[\w?]
    | (?:^|;)\s*DO\b\s+(?:LANGUAGE\s+[\w?]+\s+)?\?
""", re.IGNORECASE | re.MULTILINE | re.VERBOSE)

_ANONYMOUS_BLOCK = re.compile(r"""
    (?:\A|;|^\s*(?:GO(?:[ \t]+\d+)?|/|(?:SET|DELIMITER)[ \t]+[^\r\n]+)[ \t]*$)
    \s*(?:DECLARE\b.*?)?BEGIN\b
    (?!\s*(?:;|\Z|(?:WORK|TRAN(?:SACTION)?|DISTRIBUTED|IMMEDIATE|EXCLUSIVE|DEFERRED|
                      ISOLATION|READ|(?:NOT\s+)?DEFERRABLE)\b))
""", re.IGNORECASE | re.MULTILINE | re.DOTALL | re.VERBOSE)


def _sql_code(sql: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if match["executable"] is not None:
            return f" {_sql_code(match['executable'])} "
        return "?" if match["literal"] is not None else " "

    return _SQL_NON_CODE.sub(replace, sql)


def _sql_problem(content: str) -> str:
    code = _sql_code(content)
    if not code.strip().strip(";").strip():
        return "SQL 脚本为空或只有注释"
    if _PROCEDURE.search(code) or _ANONYMOUS_BLOCK.search(code):
        return "交付脚本禁止定义、修改、删除或调用存储过程，也禁止使用匿名过程块或动态执行。请改用直接 SQL，旧数据无法安全回退时交付人工回退说明"
    return ""


def delivery_script_problem(kind: str, content: str) -> str:
    if kind == "rollback_note":
        if not content.strip():
            return "旧数据回退说明为空"
    elif kind == "sql":
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        if normalized.partition("\n")[0] == SQL_BUNDLE_HEADER:
            sections = sql_bundle_sections(normalized)
            if not sections or any(line.strip() and not line.startswith("-- ") for line in normalized.split("\n")):
                return "SQL 合并文件须保留可分段提取的脚本及注释前缀"
            for source, _purpose, sql in sections:
                if problem := _sql_problem(sql):
                    return f"{source}：{problem}"
            return ""
        return _sql_problem(content)
    return ""
