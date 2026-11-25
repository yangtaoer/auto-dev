"""Detect procedural statements in database delivery scripts."""
from __future__ import annotations

import re


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


def delivery_script_problem(kind: str, content: str) -> str:
    if kind == "rollback_note":
        if not content.strip():
            return "旧数据回退说明为空"
    elif kind == "sql":
        code = _sql_code(content)
        if not code.strip().strip(";").strip():
            return "SQL 脚本为空或只有注释"
        if _PROCEDURE.search(code) or _ANONYMOUS_BLOCK.search(code):
            return "交付脚本禁止定义、修改、删除或调用存储过程，也禁止使用匿名过程块或动态执行。请改用直接 SQL，旧数据无法安全回退时交付人工回退说明"
    return ""
