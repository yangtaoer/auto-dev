"""Evidence-backed project memory. Human acceptance never comes from model output.

This module deliberately does not connect to, deploy, or run a test environment.
Feedback is append-only; revisions retain the provenance of every derived dossier.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from html.parser import HTMLParser
from typing import Any

from .. import db
from ..domain import PIPELINE_STEPS


HUMAN_STATUSES = {"passed", "failed", "unverified"}
DESCRIPTION_SOURCE = "requirement_description_v2"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    if not isinstance(value, str):
        return fallback
    parsed = db.json_value(value, fallback)
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _text(value: Any, limit: int = 4000) -> str:
    value = str(value or "")[:limit]
    value = re.sub(r"(?i)(password|passwd|(?:access[_-]?)?token|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", value)
    return re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)


class _CriteriaHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"li", "p", "div", "br", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"li", "p", "div", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _criteria(criteria: Any) -> list[str]:
    if isinstance(criteria, str):
        parser = _CriteriaHTML()
        parser.feed(criteria[:60000])
        plain = "".join(parser.parts)
        # Split explicit numbered inline lists without splitting prose at every comma.
        plain = re.sub(r"[；;]\s*(?=\d+[、.．）)])", "\n", plain)
        plain = re.sub(r"(?<![\d.])\s+(?=\d+[、．）)])", "\n", plain)
        values: list[Any] = plain.splitlines()
    elif isinstance(criteria, list):
        values = criteria
    else:
        values = []
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("criterion") or value.get("text") or value.get("title") or ""
        text = _text(value).strip()
        if text and text not in result:
            result.append(text)
    if len(result) > 100:
        raise ValueError("验收项超过 100 项，请先按需求范围拆分任务，不能静默遗漏验收项")
    return result


def description_criteria(description: str) -> list[str]:
    """Keep explicit requirement points together with their explanatory paragraphs.

    Unnumbered prose is one scope, not one acceptance item per HTML formatting line.
    Top-level HTML list items are explicit points; nested lists stay with their parent.
    """
    class DescriptionHTML(_CriteriaHTML):
        def __init__(self) -> None:
            super().__init__()
            self.list_depth = 0

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag in {"ul", "ol"}:
                self.list_depth += 1
            if tag == "li" and self.list_depth == 1:
                self.parts.append("\n\u241e")
            super().handle_starttag(tag, attrs)

        def handle_endtag(self, tag: str) -> None:
            super().handle_endtag(tag)
            if tag in {"ul", "ol"}:
                self.list_depth = max(0, self.list_depth - 1)

    parser = DescriptionHTML()
    parser.feed(str(description or "")[:60000])
    plain = "".join(parser.parts).strip()
    if "\u241e" in plain:
        sections = plain.split("\u241e")
        prefix, values = sections[0].strip(), [value.strip() for value in sections[1:]]
    else:
        marker = r"(?:\d+[、．）)]|\d+\.(?!\d)\s*|[一二三四五六七八九十]+[、．）)]|[（(](?:\d+|[一二三四五六七八九十]+)[）)])"
        # Inline numbered points may immediately follow the prior sentence.
        pattern = re.compile(r"(?m)(?:^\s*|(?<=[；;。\n])\s*|(?<=\s))(?=" + marker + r")")
        starts = sorted({match.end() for match in pattern.finditer(plain)})
        if starts:
            prefix = plain[:starts[0]].strip()
            values = [plain[start:end].strip(" ;；\n") for start, end in zip(starts, starts[1:] + [len(plain)])]
        else:
            prefix, values = "", [plain]
    values = [_text(value, 60000).strip() for value in values if value.strip()]
    if prefix and values:
        # Preserve introductory scope restrictions without inventing another checkbox.
        values[0] = _text(prefix, 60000) + "\n" + values[0]
    if len(values) > 100:
        raise ValueError("需求分点超过 100 项，请先按功能范围拆分任务")
    return values


def _request(conn: sqlite3.Connection, request_id: str) -> dict[str, Any]:
    row = conn.execute("""SELECT r.*,p.project_key,p.name project_name FROM delivery_requests r
                          JOIN projects p ON p.id=r.project_id WHERE r.id=?""", (request_id,)).fetchone()
    if not row:
        raise LookupError("任务不存在")
    return dict(row)


def _actor(conn: sqlite3.Connection, user: dict[str, Any] | int) -> dict[str, Any]:
    actor_id = user.get("id") if isinstance(user, dict) else user
    row = conn.execute("SELECT id,role,display_name,active FROM users WHERE id=?", (actor_id,)).fetchone()
    if not row or not row["active"]:
        raise PermissionError("用户无效或已停用")
    return dict(row)


def _authorize(request: dict[str, Any], user: dict[str, Any]) -> None:
    owner = request.get("acceptance_owner_id") or request["requester_id"]
    if user["role"] != "admin" and user["id"] not in {owner, request["requester_id"]}:
        raise PermissionError("仅指定验收人或管理员可提交验收反馈")


def _ensure(conn: sqlite3.Connection, request: dict[str, Any], criteria: Any, revision: int | None, source: str) -> None:
    existing = conn.execute("SELECT source FROM acceptance_items WHERE request_id=?", (request["id"],)).fetchall()
    if existing:
        # Never renumber human history or an existing repair contract. Upgrade only
        # unreviewed legacy lists, once, from the actual stored requirement description.
        legacy = all(row["source"] in {"requirement", "description", "historical_self_report", "requirement_summary"} for row in existing)
        has_history = conn.execute("SELECT 1 FROM acceptance_feedback WHERE request_id=? LIMIT 1", (request["id"],)).fetchone()
        has_child = conn.execute("SELECT 1 FROM delivery_requests WHERE parent_request_id=? LIMIT 1", (request["id"],)).fetchone()
        can_upgrade = ((source == DESCRIPTION_SOURCE and criteria is not None) or (criteria is None and request["status"] == "delivered"))
        description = criteria if source == DESCRIPTION_SOURCE and criteria is not None else request.get("requirement_summary")
        if not (legacy and can_upgrade and description and not has_history and not has_child and not request.get("parent_request_id")):
            return
        criteria, source = description, DESCRIPTION_SOURCE
        conn.execute("DELETE FROM acceptance_items WHERE request_id=?", (request["id"],))
    values = description_criteria(criteria) if source == DESCRIPTION_SOURCE and isinstance(criteria, str) else _criteria(criteria)
    if not values:
        values = description_criteria(request.get("requirement_summary") or request.get("title") or "验证本次需求是否完成")
        source = DESCRIPTION_SOURCE
    now = db.utc_now()
    conn.executemany("""INSERT INTO acceptance_items(request_id,item_id,position,criterion,requirement_revision,source,created_at)
                        VALUES(?,?,?,?,?,?,?)""",
                     [(request["id"], f"AC-{index:02}", index, value, revision, source, now)
                      for index, value in enumerate(values, 1)])


def ensure_acceptance(request_id: str, criteria: Any = None, revision: int | None = None, source: str = "requirement") -> dict[str, Any]:
    with db.transaction() as conn:
        request = _request(conn, request_id)
        _ensure(conn, request, criteria, revision if revision is not None else request.get("work_item_revision"), source)
        return _acceptance(conn, request)


def _canonical_id(value: Any) -> str:
    match = re.fullmatch(r"(?:AC-?)?0*(\d+)", str(value or "").strip(), re.I)
    return f"AC-{int(match.group(1)):02}" if match else str(value or "")


def _round(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["items"] = _load(result.get("items"), [])
    result.pop("payload_hash", None)
    result.pop("idempotency_key", None)
    return result


def _acceptance(conn: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    items = [dict(row) for row in conn.execute("SELECT * FROM acceptance_items WHERE request_id=? ORDER BY position", (request["id"],))]
    rounds = [_round(row) for row in conn.execute("""SELECT f.*,x.request_id repair_request_id FROM acceptance_feedback f
              LEFT JOIN acceptance_repairs x ON x.feedback_id=f.id WHERE f.request_id=? ORDER BY f.id""", (request["id"],))]
    version = rounds[-1]["tested_version"] if rounds else ""
    environment = rounds[-1]["environment"] if rounds else ""
    feedback: dict[str, Any] = {}
    for entry in rounds:
        if entry["tested_version"] != version or entry["environment"] != environment:
            continue
        if entry.get("overall_status"):
            feedback.clear()  # A whole-requirement verdict supersedes older partial rounds.
        for item in entry["items"]:
            feedback[item["id"]] = {**item, "tested_version": version, "environment": entry["environment"],
                "actor_name": entry["actor_name"], "created_at": entry["created_at"], "feedback_id": entry["id"]}
    ledger = [item for item in _load(request.get("acceptance_ledger"), []) if isinstance(item, dict)]
    for item in items:
        item["id"] = item.pop("item_id")
        # Match a criterion first; ID fallback only when model copied the frozen ID faithfully.
        dev = next((entry for entry in ledger if str(entry.get("criterion", "")).strip() == item["criterion"].strip()), None)
        if dev is None and item["source"] != DESCRIPTION_SOURCE:
            dev = next((entry for entry in ledger if _canonical_id(entry.get("id")) == item["id"]), {})
        dev = dev or {}
        item["development_status"] = dev.get("status", "unreported")
        item["tests"] = [_text(value, 2000) for value in dev["tests"][:30]] if isinstance(dev.get("tests"), list) else []
        item["evidence"] = [_text(value, 2000) for value in dev["evidence"][:30]] if isinstance(dev.get("evidence"), list) else []
        item["human_status"] = feedback.get(item["id"], {}).get("status", "unverified")
        item["feedback"] = feedback.get(item["id"])
        item["automatic_test_status"] = "deferred"
    passed = sum(item["human_status"] == "passed" for item in items)
    failed = sum(item["human_status"] == "failed" for item in items)
    status = "accepted" if items and passed == len(items) else "partial" if passed else "changes_requested" if failed else "pending"
    if rounds and rounds[-1].get("overall_status") == "failed":
        status = "changes_requested"
    return {"items": items, "rounds": rounds, "status": status, "tested_version": version, "environment": environment,
            "requirement_revision": items[0]["requirement_revision"] if items else request.get("work_item_revision"),
            "latest_feedback_id": rounds[-1]["id"] if rounds else 0,
            "acceptance_owner_id": request.get("acceptance_owner_id") or request["requester_id"],
            "root_request_id": request.get("root_request_id") or request["id"],
            "parent_request_id": request.get("parent_request_id"), "repair_round": request.get("repair_round", 0),
            "failed_item_ids": _load(request.get("failed_item_ids"), []),
            "protected_item_ids": _load(request.get("protected_item_ids"), []),
            "repair_context": _load(request.get("repair_context"), {}),
            "summary": {"total": len(items), "passed": passed, "failed": failed, "unverified": len(items) - passed - failed}}


def get_acceptance(request_id: str) -> dict[str, Any]:
    with db.transaction() as conn:
        request = _request(conn, request_id)
        if request["status"] == "delivered":
            _ensure(conn, request, None, request.get("work_item_revision"), DESCRIPTION_SOURCE)
        return _acceptance(conn, request)


def assign_acceptance_owner(request_id: str, user_id: int, actor_id: int) -> dict[str, Any]:
    with db.transaction() as conn:
        request = _request(conn, request_id)
        actor = _actor(conn, actor_id)
        if actor["role"] != "admin" and actor["id"] != request["requester_id"]:
            raise PermissionError("仅提出人或管理员可指定验收人")
        _actor(conn, user_id)
        conn.execute("UPDATE delivery_requests SET acceptance_owner_id=?,updated_at=? WHERE id=?", (user_id, db.utc_now(), request_id))
        conn.execute("INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
                     (actor_id, "acceptance.assign", "request", request_id, _json({"from": request.get("acceptance_owner_id") or request["requester_id"], "to": user_id}), db.utc_now()))
        return _acceptance(conn, _request(conn, request_id))


def submit_feedback(request_id: str, user: dict[str, Any] | int, items: list[dict[str, Any]], raw_feedback: str = "",
                    tested_version: str = "", environment: str = "", idempotency_key: str = "",
                    expected_latest_feedback_id: int | None = None, overall_status: str | None = None,
                    failed_item_ids: list[str] | None = None) -> dict[str, Any]:
    if overall_status not in {None, "passed", "failed"}:
        raise ValueError("验收结论必须为通过或不通过")
    failed_ids = [_canonical_id(value) for value in (failed_item_ids or [])]
    if len(failed_ids) > 100 or len(set(failed_ids)) != len(failed_ids):
        raise ValueError("未通过项不能重复，最多 100 项")
    if (overall_status and items) or (failed_ids and overall_status != "failed"):
        raise ValueError("整体验收与逐项反馈不能混用，通过时不能选择未通过项")
    if not isinstance(items, list) or (not items and not overall_status) or len(items) > 100:
        raise ValueError("请至少确认一个验收项，最多 100 项")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("提交反馈需要有效的幂等标识")
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in HUMAN_STATUSES:
            raise ValueError("验收状态必须是 passed、failed 或 unverified")
        normalized.append({"id": _canonical_id(item.get("id")), "status": item["status"],
                           "actual": _text(item.get("actual")), "expected": _text(item.get("expected")), "note": _text(item.get("note"))})
    if len({item["id"] for item in normalized}) != len(normalized):
        raise ValueError("验收项不能重复")
    payload = {"items": normalized, "raw_feedback": _text(raw_feedback, 12000), "tested_version": _text(tested_version, 300), "environment": _text(environment, 500)}
    if overall_status:
        payload.update(overall_status=overall_status, failed_item_ids=sorted(failed_ids))
    with db.transaction() as conn:
        request = _request(conn, request_id)
        actor = _actor(conn, user)
        _authorize(request, actor)
        payload_hash = hashlib.sha256(_json({**payload, "actor_id": actor["id"]}).encode()).hexdigest()
        existing = conn.execute("SELECT * FROM acceptance_feedback WHERE request_id=? AND idempotency_key=?", (request_id, idempotency_key)).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise RuntimeError("同一个提交标识不能用于不同反馈")
            return _round(existing)
        if request["status"] != "delivered":
            raise ValueError("仅已交付任务可提交验收反馈")
        _ensure(conn, request, None, request.get("work_item_revision"), "historical_self_report")
        acceptance = _acceptance(conn, request)
        if expected_latest_feedback_id is not None and expected_latest_feedback_id != acceptance["latest_feedback_id"]:
            raise RuntimeError("验收反馈已更新，请刷新后确认最新结果再提交")
        valid = {item["id"] for item in acceptance["items"]}
        if any(item["id"] not in valid for item in normalized) or any(item_id not in valid for item_id in failed_ids):
            raise ValueError("反馈包含不存在的验收项，请刷新验收清单")
        if overall_status:
            normalized = [{"id": item["id"], "status": "passed" if overall_status == "passed" else "failed" if item["id"] in failed_ids else "unverified",
                           "actual": "", "expected": "", "note": ""} for item in acceptance["items"]]
        criteria_by_id = {item["id"]: item["criterion"] for item in acceptance["items"]}
        for item in normalized:
            if item["status"] == "failed" and not item["expected"]:
                item["expected"] = criteria_by_id[item["id"]]
        version = payload["tested_version"] or f"交付记录 {request_id[:8]} / {request.get('commit_hash') or '当前交付'}"
        now = db.utc_now()
        cursor = conn.execute("""INSERT INTO acceptance_feedback(request_id,actor_id,actor_name,raw_feedback,tested_version,
                              environment,items,requirement_revision,idempotency_key,payload_hash,created_at,overall_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (request_id, actor["id"], actor["display_name"], payload["raw_feedback"], version,
                               payload["environment"], _json(normalized), acceptance["requirement_revision"], idempotency_key, payload_hash, now, overall_status or ""))
        conn.execute("INSERT INTO delivery_events(request_id,level,event_type,message,metadata,created_at) VALUES(?,?,?,?,?,?)",
                     (request_id, "info", "acceptance.feedback", "提出人验收反馈已保存", _json({"feedback_id": cursor.lastrowid, "actor_id": actor["id"]}), now))
        _sync(conn, request)
        return _round(conn.execute("SELECT * FROM acceptance_feedback WHERE id=?", (cursor.lastrowid,)).fetchone())


def preview_feedback(request_id: str, text: str) -> dict[str, Any]:
    bundle = ensure_acceptance(request_id)
    result: dict[str, dict[str, Any]] = {}
    # Only explicit numbered clauses are interpreted; no inference from silence.
    pattern = re.compile(r"(?:第\s*)?((?:AC-)?\d+(?:\s*[、,，和及 ]\s*(?:AC-)?\d+)*)\s*(?:项|点)?\s*(.+?)(?=[。；;\n]|[,，]\s*(?:第\s*)?(?:AC-)?\d+\s*(?:项|点)|[,，]\s*(?:其余|其他|剩余)|$)", re.I)
    warnings: list[str] = []
    valid = {item["id"] for item in bundle["items"]}
    for match in pattern.finditer(text[:12000]):
        clause = match.group(2)
        if re.search(r"未验证|未验收|不一定|不确定|尚未|待验证|可能|似乎|不知道", clause):
            continue
        status = "failed" if re.search(r"未完成|未通过|不通过|失败|不符合|有问题|不正确|看不到|仍然", clause) else "passed" if re.search(r"通过|完成|符合预期", clause) else None
        if status is None:
            continue
        for number in re.findall(r"\d+", match.group(1)):
            item_id = _canonical_id(number)
            if item_id not in valid:
                warnings.append(f"{item_id} 不在当前验收清单中，未自动采用")
                continue
            result[item_id] = {"id": item_id, "status": status, "actual": _text(clause) if status == "failed" else "", "expected": "", "note": ""}
    if re.search(r"(?:其余|其他|剩余).{0,5}(?:均|都|全部)?(?:验证)?通过", text) and result:
        for item_id in valid:
            result.setdefault(item_id, {"id": item_id, "status": "passed", "actual": "", "expected": "", "note": "依据明确的“其余通过”，仍需确认"})
    if not result:
        warnings.append("未识别到明确的编号与结论，请逐项选择；不会自动提交或把未提及项视为通过")
    return {"items": list(result.values()), "warnings": warnings, "requires_confirmation": True,
            "latest_feedback_id": bundle["latest_feedback_id"], "raw_feedback": _text(text, 12000)}


def create_repair(request_id: str, user: dict[str, Any] | int, feedback_id: int | None = None,
                  idempotency_key: str = "") -> dict[str, Any]:
    """Queue one repair per feedback round and preserve all original acceptance facts."""
    with db.transaction() as conn:
        request = _request(conn, request_id)
        actor = _actor(conn, user)
        _authorize(request, actor)
        if request["status"] != "delivered":
            raise ValueError("仅已交付任务可发起验收返修")
        bundle = _acceptance(conn, request)
        feedback_id = feedback_id or bundle["latest_feedback_id"]
        feedback = conn.execute("SELECT * FROM acceptance_feedback WHERE id=? AND request_id=?", (feedback_id, request_id)).fetchone()
        if not feedback:
            raise ValueError("请先提交明确的未通过项反馈")
        linked = conn.execute("SELECT request_id FROM acceptance_repairs WHERE feedback_id=?", (feedback_id,)).fetchone()
        if linked:
            repair_id = linked["request_id"]
        else:
            if feedback_id != bundle["latest_feedback_id"]:
                raise RuntimeError("已有较新反馈，请基于最新反馈发起返修")
            failed = [item for item in bundle["items"] if item["human_status"] == "failed"]
            protected = [item for item in bundle["items"] if item["human_status"] == "passed"]
            unspecified_scope = feedback["overall_status"] == "failed" and not failed
            if not failed and not unspecified_scope:
                raise ValueError("当前没有未通过的验收项，不需要返修")
            active = conn.execute("""SELECT id FROM delivery_requests WHERE project_id=? AND work_item_id=?
                         AND status NOT IN ('delivered','rejected','failed','cancelled')""", (request["project_id"], request["work_item_id"])).fetchone()
            if active:
                raise RuntimeError("该项目需求已有任务执行中，请先等待或处理现有任务")
            newer = conn.execute("SELECT id FROM delivery_requests WHERE parent_request_id=? ORDER BY created_at DESC LIMIT 1", (request_id,)).fetchone()
            if newer:
                raise RuntimeError("该交付已有返修轮次，请在最新返修任务上继续验收")
            project_row = conn.execute("SELECT * FROM projects WHERE id=? AND enabled=1", (request["project_id"],)).fetchone()
            if not project_row:
                raise ValueError("项目不存在或已停用")
            project = dict(project_row)
            repair_id = str(uuid.uuid4())
            now = db.utc_now()
            root_id = request.get("root_request_id") or request_id
            failed_ids = [item["id"] for item in failed]
            protected_ids = [item["id"] for item in protected]
            context = {"parent_request_id": request_id, "root_request_id": root_id, "feedback_id": feedback_id,
                       "raw_feedback": _text(feedback["raw_feedback"], 12000), "tested_version": feedback["tested_version"],
                       "environment": feedback["environment"], "failed_items": failed, "protected_items": protected,
                       "unspecified_scope": unspecified_scope,
                       "review_items": bundle["items"] if unspecified_scope else [],
                       "instructions": "仅针对未通过验收项及必要依赖精确返修；已通过项是上一版本证据，必须保护。修改公共逻辑时补充受影响项回归说明；不得声称新版已获人工验收。测试环境自动部署和验证本期暂不执行。"}
            if unspecified_scope:
                context["instructions"] = "提出人确认整体未通过，但未指定具体项。先对照需求描述、当前实现与自检定位差异，再精确修复；review_items 是待排查范围，不代表每项均失败。不得假定未指定项通过，不得扩大改造范围；本期不操作测试环境。"
            values = {"id": repair_id, "work_item_id": request["work_item_id"], "work_item_revision": request.get("work_item_revision"),
                      "project_id": request["project_id"], "requester_id": request["requester_id"],
                      "acceptance_owner_id": request.get("acceptance_owner_id") or request["requester_id"],
                      "runner_id": project.get("runner_id", request["runner_id"]), "delivery_mode": request["delivery_mode"],
                      "title": request["title"], "requirement_summary": request["requirement_summary"],
                      "status": "queued", "current_step": "validate", "policy_snapshot": _json(db.project_for_api(project)),
                      "notification_emails": request["notification_emails"], "delivery_options": request["delivery_options"],
                      "task_type": request["task_type"], "root_request_id": root_id, "parent_request_id": request_id,
                      "repair_round": int(request.get("repair_round") or 0) + 1, "failed_item_ids": _json(failed_ids),
                      "protected_item_ids": _json(protected_ids), "repair_context": _json(context), "created_at": now, "updated_at": now}
            conn.execute(f"INSERT INTO delivery_requests({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
            conn.executemany("INSERT INTO delivery_steps(request_id,step_code,name,status) VALUES(?,?,?,'pending')",
                             [(repair_id, code, name) for code, name in PIPELINE_STEPS])
            conn.execute("""INSERT INTO acceptance_items(request_id,item_id,position,criterion,requirement_revision,source,created_at)
                         SELECT ?,item_id,position,criterion,requirement_revision,source,? FROM acceptance_items WHERE request_id=?""", (repair_id, now, request_id))
            conn.execute("INSERT INTO acceptance_repairs(feedback_id,request_id,actor_id,created_at) VALUES(?,?,?,?)", (feedback_id, repair_id, actor["id"], now))
            conn.execute("INSERT INTO delivery_events(request_id,level,event_type,message,metadata,created_at) VALUES(?,?,?,?,?,?)",
                         (repair_id, "info", "acceptance.repair", "根据提出人未通过项启动关联返修", _json({"parent_request_id": request_id, "feedback_id": feedback_id}), now))
            conn.execute("INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
                         (actor["id"], "acceptance.repair", "request", repair_id, _json({"parent_request_id": request_id, "failed_item_ids": failed_ids}), now))
    return db.request_detail(repair_id) or {}


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[bounded]"
    if isinstance(value, dict):
        return {str(key): _sanitize(item, depth + 1) for key, item in list(value.items())[:100]
                if not re.search(r"password|secret|token|credential|connection_string|local_path|workspace_path", str(key), re.I)}
    if isinstance(value, list):
        return [_sanitize(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _text(value, 12000)
    return value


def _sync(conn: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any] | None:
    if request["status"] != "delivered":
        return None
    _ensure(conn, request, None, request.get("work_item_revision"), "historical_self_report")
    acceptance = _acceptance(conn, request)
    retrospective = _sanitize(_load(request.get("project_retrospective"), {}))
    repositories = []
    for state in _load(request.get("repository_states"), []):
        if isinstance(state, dict):
            repositories.append({key: _sanitize(state[key]) for key in ("name", "base_branch", "changed_files", "commit_hash", "merge_commit", "build_commit", "pr_url") if state.get(key)})
    lessons = []
    for item in acceptance["items"]:
        lessons.append({"acceptance_id": item["id"], "criterion": item["criterion"], "human_status": item["human_status"],
                        "development_status": item["development_status"], "tests": item["tests"], "evidence": item["evidence"],
                        "feedback": item["feedback"], "source": item["source"]})
    content = _sanitize({"acceptance_summary": acceptance["summary"], "acceptance_status": acceptance["status"],
                        "lessons": lessons, "retrospective": retrospective,
                        "lesson_usage": _load(request.get("lesson_usage"), []),
                        "feedback_rounds": acceptance["rounds"],
                        "root_request_id": acceptance["root_request_id"], "parent_request_id": acceptance["parent_request_id"],
                        "repair_round": acceptance["repair_round"], "failed_item_ids": acceptance["failed_item_ids"],
                        "protected_item_ids": acceptance["protected_item_ids"],
                        "verification_label": "提出人已验收；经验仅适用于所列项目、版本与场景" if acceptance["status"] == "accepted" else "候选经验：尚未全部获得提出人验收，模型自检不代表人工通过",
                        "evidence": {"repositories": repositories, "pr_url": _text(request.get("pr_url"), 1000),
                                     "commit_hash": _text(request.get("commit_hash"), 100), "merge_commit": _text(request.get("merge_commit"), 100),
                                     "requirement_revision": request.get("work_item_revision"),
                                     "tested_versions": list(dict.fromkeys(entry["tested_version"] for entry in acceptance["rounds"])),
                                     "completed_at": request.get("completed_at")},
                        "regression_suggestions": retrospective.get("regression_suggestions", []),
                        "test_environment_validation": "deferred"})
    scope = _text(retrospective.get("scope") or request.get("requirement_summary") or request["title"], 8000)
    implementation = _text(retrospective.get("implementation") or request.get("result_summary"), 12000)
    packed = _json(content)
    digest = hashlib.sha256(_json([scope, implementation, request["title"], content]).encode()).hexdigest()
    previous = conn.execute("SELECT * FROM project_experiences WHERE request_id=?", (request["id"],)).fetchone()
    status = "verified" if acceptance["status"] == "accepted" else "candidate"
    if previous and previous["status"] == "deprecated":
        status = "deprecated"
    if previous and previous["content_hash"] == digest:
        return _experience(conn, int(previous["id"]))
    now = db.utc_now()
    search_text = _text(" ".join([request["title"], scope, implementation, packed]), 100000)
    if previous:
        experience_id = previous["id"]
        conn.execute("""UPDATE project_experiences SET title=?,status=?,scope_summary=?,implementation_summary=?,content=?,content_hash=?,search_text=?,updated_at=? WHERE id=?""",
                     (request["title"], status, scope, implementation, packed, digest, search_text, now, experience_id))
    else:
        cursor = conn.execute("""INSERT INTO project_experiences(request_id,project_id,work_item_id,title,status,scope_summary,implementation_summary,content,content_hash,search_text,created_at,updated_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (request["id"], request["project_id"], request["work_item_id"], request["title"], status, scope, implementation, packed, digest, search_text, now, now))
        experience_id = cursor.lastrowid
    conn.execute("INSERT INTO project_experience_revisions(experience_id,status,content,reason,created_at) VALUES(?,?,?,?,?)",
                 (experience_id, status, _json({"scope_summary": scope, "implementation_summary": implementation, **content}), "交付证据或提出人反馈更新；未自动推断根因", now))
    return _experience(conn, int(experience_id))


def _experience(conn: sqlite3.Connection, experience_id: int, detail: bool = False) -> dict[str, Any] | None:
    row = conn.execute("""SELECT e.*,p.project_key,p.name project_name FROM project_experiences e
                         JOIN projects p ON p.id=e.project_id WHERE e.id=?""", (experience_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result.update(_load(result.pop("content"), {}))
    result.pop("content_hash", None)
    result.pop("search_text", None)
    if result["status"] == "deprecated":
        result["verification_label"] = "此经验已停用，仅供追溯，不再注入新需求"
    if detail:
        result["revisions"] = [{**dict(row), "content": _load(row["content"], {})} for row in conn.execute(
            "SELECT * FROM project_experience_revisions WHERE experience_id=? ORDER BY id DESC LIMIT 100", (experience_id,))]
    return result


def sync_experience(request_id: str) -> dict[str, Any] | None:
    with db.transaction() as conn:
        return _sync(conn, _request(conn, request_id))


def backfill_experiences(project_id: int | None = None) -> int:
    """Import only missing completed dossiers; old self-reports stay unconfirmed."""
    with db.transaction() as conn:
        condition = "AND r.project_id=?" if project_id is not None else ""
        requests = conn.execute(f"""SELECT r.id FROM delivery_requests r LEFT JOIN project_experiences e ON e.request_id=r.id
                                    WHERE r.status='delivered' AND e.id IS NULL {condition} ORDER BY r.created_at LIMIT 1000""",
                                (project_id,) if project_id is not None else ()).fetchall()
        for request in requests:
            _sync(conn, _request(conn, request["id"]))
        return len(requests)


def list_experiences(project_id: int | None = None, query: str = "", status: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    backfill_experiences(project_id)
    conditions: list[str] = []
    params: list[Any] = []
    if project_id is not None:
        conditions.append("project_id=?")
        params.append(project_id)
    if status:
        if status not in {"candidate", "verified", "deprecated"}:
            raise ValueError("无效的经验状态")
        conditions.append("status=?")
        params.append(status)
    if query.strip():
        conditions.append("search_text LIKE ? ESCAPE '\\'")
        params.append("%" + query[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with db.transaction() as conn:
        total = conn.execute("SELECT COUNT(*) FROM project_experiences" + where, tuple(params)).fetchone()[0]
        ids = conn.execute("SELECT id FROM project_experiences" + where + " ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
                           (*params, max(1, min(100, int(limit))), max(0, int(offset)))).fetchall()
        return {"items": [_experience(conn, item["id"]) for item in ids], "total": total}


def get_experience(experience_id: int) -> dict[str, Any] | None:
    with db.transaction() as conn:
        return _experience(conn, experience_id, detail=True)


def set_experience_status(experience_id: int, status: str, actor_id: int, reason: str) -> dict[str, Any]:
    if status not in {"candidate", "verified", "deprecated"}:
        raise ValueError("经验状态必须是 candidate、verified 或 deprecated")
    if not reason.strip():
        raise ValueError("请填写状态变更原因，保留可追溯依据")
    with db.transaction() as conn:
        actor = _actor(conn, actor_id)
        if actor["role"] != "admin":
            raise PermissionError("仅管理员可维护项目经验")
        experience = _experience(conn, experience_id)
        if not experience:
            raise LookupError("项目经验不存在")
        if status == "verified" and experience.get("acceptance_status") != "accepted":
            raise ValueError("未获得逐项人工验收的经验不能标为已验证")
        now = db.utc_now()
        conn.execute("UPDATE project_experiences SET status=?,updated_at=? WHERE id=?", (status, now, experience_id))
        conn.execute("INSERT INTO project_experience_revisions(experience_id,status,content,reason,actor_id,created_at) VALUES(?,?,?,?,?,?)",
                     (experience_id, status, _json(experience), _text(reason), actor["id"], now))
        conn.execute("INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
                     (actor["id"], "project_experience.status", "project_experience", str(experience_id), _json({"from": experience["status"], "to": status, "reason": _text(reason)}), now))
        return _experience(conn, experience_id, detail=True) or {}


def _tokens(text: str) -> set[str]:
    result = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]{2,}", text.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        result.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return result - {"需求", "项目", "功能", "进行", "完成", "开发", "修改", "需要", "平台", "网络", "发令"}


def retrieve_lessons(project_key: str, work_item_id: int, query: str, request_id: str = "", limit: int = 5) -> list[dict[str, Any]]:
    project = db.row("SELECT id FROM projects WHERE project_key=?", (project_key,))
    if not project:
        return []
    backfill_experiences(project["id"])
    query_tokens = _tokens(query[:20000])
    with db.transaction() as conn:
        candidates = conn.execute("""SELECT id,work_item_id,search_text,status FROM project_experiences
                        WHERE project_id=? AND request_id<>? AND status<>'deprecated' ORDER BY updated_at DESC LIMIT 1000""",
                                  (project["id"], request_id)).fetchall()
        ranked = []
        for candidate in candidates:
            same = candidate["work_item_id"] == work_item_id
            overlap = query_tokens & _tokens(candidate["search_text"])
            if not same and not overlap:
                continue
            score = (1000 if same else 0) + len(overlap) * 3 + (2 if candidate["status"] == "verified" else 0)
            ranked.append((score, candidate["id"], same, sorted(overlap)))
        ranked.sort(reverse=True)
        results = []
        for score, experience_id, same, matched in ranked[:max(1, min(10, int(limit)))]:
            item = _experience(conn, experience_id) or {}
            # Keep memory context bounded; full immutable source stays available by ID.
            item.pop("feedback_rounds", None)
            item["implementation_summary"] = str(item.get("implementation_summary", ""))[:4000]
            item["scope_summary"] = str(item.get("scope_summary", ""))[:2500]
            item["lessons"] = item.get("lessons", [])[:12]
            if isinstance(item.get("retrospective"), dict):
                retrospective_lessons = item["retrospective"].get("lessons")
                item["retrospective"]["lessons"] = retrospective_lessons[:8] if isinstance(retrospective_lessons, list) else []
            item["experience_id"] = experience_id
            item["relevance_score"] = score
            item["match_reason"] = "同项目同一 TFS 需求的历史交付" if same else "同项目相关功能/模块：" + "、".join(matched[:12])
            item["usage_constraint"] = "仅作为带来源的线索，先对照最新需求与代码；候选记录不是已验证结论，用户反馈不是已证明根因，地区规则不得跨项目套用。"
            results.append(item)
        return results
