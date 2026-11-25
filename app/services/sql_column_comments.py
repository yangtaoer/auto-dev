"""Find explicit column definitions that lack database column comments."""
from __future__ import annotations

import re
from collections.abc import Iterator


_NAME = re.compile(r"\$\{[A-Za-z_]\w*\}|#?[\w$]+")
_TOKEN = re.compile(r"(?P<batch>^[ \t]*(?:GO(?:[ \t]+\d+)?|/)[ \t]*\r?$)|"
                    + _NAME.pattern + r"|[^\s]", re.IGNORECASE | re.MULTILINE)
_TABLE_MODIFIERS = {"or", "replace", "global", "local", "temp", "temporary", "unlogged", "external"}
_TABLE_CLAUSES = {"constraint", "primary", "foreign", "unique", "check", "index", "key", "fulltext",
                  "spatial", "exclude", "period", "partition", "like", "supplemental", "default"}
_ALTER_ACTIONS = {"add", "alter", "modify", "change", "drop", "rename", "enable", "disable",
                  "validate", "set", "attach", "detach", "rebuild"}
_COLUMN_ACTIONS = {"add", "alter", "modify", "change"}


def _tokens(sql: str, non_code: re.Pattern) -> Iterator[str]:
    start = 0
    for match in non_code.finditer(sql):
        yield from (";" if item["batch"] else item[0] for item in _TOKEN.finditer(sql, start, match.start()))
        if match["executable"] is not None:
            yield from _tokens(match["executable"], non_code)
        elif match["literal"] is not None:
            yield match[0]
        start = match.end()
    yield from (";" if item["batch"] else item[0] for item in _TOKEN.finditer(sql, start))


def _statements(tokens: Iterator[str]) -> Iterator[list[str]]:
    current = []
    schema_statement = None
    for token in tokens:
        if token == ";":
            if current:
                yield current
            current = []
            schema_statement = None
        else:
            if schema_statement is None:
                schema_statement = token.casefold() in {"create", "alter", "comment"}
            if schema_statement:
                current.append(token)
    if current:
        yield current


def _identifier(token: str) -> str:
    if len(token) >= 2 and token[0] in '"`[':
        return token[1:-1].replace(token[-1] * 2, token[-1])
    return token if _NAME.fullmatch(token) else ""


def _qualified(tokens: list[str], index: int) -> tuple[tuple[str, ...], int]:
    name = []
    while index < len(tokens) and _identifier(tokens[index]):
        name.append(tokens[index])
        index += 1
        if index == len(tokens) or tokens[index] != ".":
            break
        index += 1
    return tuple(name), index


def _column_key(name: tuple[str, ...]) -> tuple[str, ...]:
    # Bare identifiers use the delivery database's folded form. Preserve
    # quoted identifiers and schema placeholders as written.
    return tuple(token.upper() if _NAME.fullmatch(token) and not token.startswith("${") else _identifier(token)
                 for token in name)


def _group(tokens: list[str]) -> list[str]:
    """Read the first parenthesized definition list, excluding its delimiters."""
    depth = 0
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if not depth:
                return tokens[1:index]
    return tokens[1:]


def _parts(tokens: list[str]) -> Iterator[list[str]]:
    depth = 0
    start = 0
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token == "," and not depth:
            yield tokens[start:index]
            start = index + 1
    if start < len(tokens):
        yield tokens[start:]


def _nonempty_string(tokens: list[str]) -> bool:
    if tokens and tokens[0] == "=":
        tokens = tokens[1:]
    if tokens and tokens[0].casefold() in {"n", "e"}:
        tokens = tokens[1:]
    if not tokens:
        return False
    token = tokens[0]
    if token.startswith(("'", '"')):
        return bool(token[1:-1].strip())
    if token[:2].casefold() == "q'":
        return bool(token[3:-2].strip())
    tag = re.match(r"\$(?:[A-Za-z_]\w*)?\$", token)
    return bool(tag and token[tag.end():-tag.end()].strip())


def _definition(part: list[str]) -> tuple[str, bool | None] | None:
    if not part or part[0].casefold() in _TABLE_CLAUSES:
        return None
    column = part[0]
    if not _identifier(column):
        return None
    depth = 0
    for index, token in enumerate(part[1:], 1):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif not depth and token.casefold() == "comment":
            return column, _nonempty_string(part[index + 1:])
    return column, None


def _alter_definitions(tokens: list[str]) -> Iterator[list[str]]:
    action = ""
    for part in _parts(tokens):
        if not part:
            continue
        head = part[0].casefold()
        if head in _ALTER_ACTIONS:
            action = head
            part = part[1:]
        elif action not in {"add", "modify"}:
            continue
        if action not in _COLUMN_ACTIONS or not part:
            continue
        if part[0].casefold() == "column":
            part = part[1:]
        if [token.casefold() for token in part[:3]] == ["if", "not", "exists"]:
            part = part[3:]
        elif [token.casefold() for token in part[:2]] == ["if", "exists"]:
            part = part[2:]
        if action == "change":
            part = part[1:]  # The new column name follows the original name.
        if part and part[0] == "(":
            yield from _parts(_group(part))
        elif part:
            yield part


def missing_column_comments(sql: str, non_code: re.Pattern) -> list[str]:
    fields: dict[tuple[str, ...], str] = {}
    annotated: dict[tuple[str, ...], bool] = {}
    for statement in _statements(_tokens(sql, non_code)):
        words = [word.casefold() for word in statement]
        if words[:3] == ["comment", "on", "column"]:
            column, end = _qualified(statement, 3)
            if len(column) >= 2 and words[end:end + 1] == ["is"]:
                annotated[_column_key(column)] = _nonempty_string(statement[end + 1:])
            continue
        create = words[0] == "create"
        index = 1
        while create and index < len(words) and words[index] in _TABLE_MODIFIERS:
            index += 1
        if words[0] not in {"create", "alter"} or words[index:index + 1] != ["table"]:
            continue
        index += 1
        if words[index:index + 3] == ["if", "not", "exists"]:
            index += 3
        elif words[index:index + 2] == ["if", "exists"]:
            index += 2
        if not create and words[index:index + 1] == ["only"]:
            index += 1
        table, index = _qualified(statement, index)
        if not table or index == len(statement):
            continue
        if create and statement[index] != "(":
            continue
        definitions = _parts(_group(statement[index:])) if create else _alter_definitions(statement[index:])
        for definition in definitions:
            if field := _definition(definition):
                column, inline = field
                name = (*table, column)
                key = _column_key(name)
                fields[key] = ".".join(_identifier(token) for token in name)
                if create or inline is not None:
                    annotated[key] = bool(inline)
    return [label for key, label in fields.items() if not annotated.get(key)]
