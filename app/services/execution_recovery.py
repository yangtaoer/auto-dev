"""Recover orphaned execution records from a fresh runner heartbeat, never from age alone."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from .. import db


ACTIVE = frozenset({'validating','developing','submitting','building','releasing','capturing','delivering'})
REVIEW_MODES = frozenset({'sichuan_auto_review','product_manual_review','sichuan_review_local_package'})
STALE_SECONDS = 120
SESSION_GRACE_SECONDS = 90


def decoded(value, fallback):
    if isinstance(value,type(fallback)):
        return value
    try:
        result=db.json_value(value,fallback)
    except TypeError:
        return fallback
    return result if isinstance(result,type(fallback)) else fallback


def timestamp(value: str | None) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=UTC)


def active_ids(runner: dict | None) -> set[str]:
    runner = runner or {}
    detail = decoded(runner.get('detail'), {})
    return set(detail.get('current_request_ids') or []) | ({runner['current_request_id']} if runner.get('current_request_id') else set())


def session_conflict(runner: dict | None, session: str, now: datetime | None = None) -> bool:
    if not runner or runner.get('state') == 'stopping':
        return False
    current = decoded(runner.get('detail'), {}).get('execution_session_id')
    if not session and not current:
        return False
    return current != session and (bool(active_ids(runner)) or runner.get('state')=='working') and (
        (now or datetime.now(UTC)) - timestamp(runner.get('last_seen_at')) < timedelta(seconds=SESSION_GRACE_SECONDS)
    )


def submission_ready(detail: dict) -> bool:
    if detail.get('task_type') == 'analysis' or detail.get('delivery_mode') not in REVIEW_MODES:
        return False
    states = decoded(detail.get('repository_states'), [])
    changed = [state for state in states if state.get('changed_files') or state.get('commit_hash') or state.get('pr_id')]
    return bool(changed) and bool(detail.get('result_summary')) and all(
        re.fullmatch(r'[a-fA-F0-9]{40,64}', str(state.get('commit_hash') or ''))
        and state.get('worktree_path') and state.get('branch') and state.get('base_branch')
        and state.get('status') in {'pushed','waiting_merge','completed'}
        for state in changed
    )


def recover(runner_id: str, session_id: str) -> dict:
    if not session_id:
        raise ValueError('恢复检查需要有效的执行器会话标识')
    now = datetime.now(UTC)
    now_text = db.utc_now()
    recovered, interrupted = [], []
    with db.transaction() as conn:
        value = conn.execute('SELECT * FROM runners WHERE runner_id=?', (runner_id,)).fetchone()
        runner = dict(value) if value else None
        current_session = db.json_value((runner or {}).get('detail'), {}).get('execution_session_id')
        if not runner or current_session != session_id or now-timestamp(runner.get('last_seen_at')) > timedelta(seconds=60):
            raise ValueError('恢复检查需要当前执行器会话的有效心跳')
        live = active_ids(runner)
        records = conn.execute(
            "SELECT * FROM delivery_requests WHERE runner_id=? AND status IN ('validating','developing','submitting','building','releasing','capturing','delivering')",
            (runner_id,),
        ).fetchall()
        for value in records:
            detail = dict(value)
            rid = detail['id']
            if rid in live or now-timestamp(detail.get('updated_at')) < timedelta(seconds=STALE_SECONDS):
                continue
            if conn.execute("SELECT 1 FROM request_controls WHERE request_id=? AND status IN ('pending','running','waiting_merge')", (rid,)).fetchone():
                continue
            eligible = detail['status'] == 'submitting' and submission_ready(detail)
            previous = db.json_value(detail.get('recovery_context'), {})
            context = {**previous, 'kind':'submission' if eligible else 'interrupted',
                       'state':'pending' if eligible else 'needs_review', 'detected_at':now_text,
                       'last_checkpoint_at':detail['updated_at'], 'previous_status':detail['status'],
                       'execution_session_id':session_id, 'recoveries':int(previous.get('recoveries') or 0)+1}
            message = (
                '执行器中断，已保留推送提交；将核对远端 PR 后从提交检查点自动接续，不重新研发'
                if eligible else
                '执行器中断，当前阶段无法确认远端副作用；现场已保留，请先核对提交、构建或部署结果，不自动重复研发或发版'
            )
            status = 'waiting_retry' if eligible else 'failed'
            changed = conn.execute(
                'UPDATE delivery_requests SET status=?,recovery_context=?,error_message=?,next_poll_at=?,completed_at=?,updated_at=? WHERE id=? AND status=? AND updated_at=?',
                (status,json.dumps(context,ensure_ascii=False),message,now_text if eligible else None,
                 None if eligible else now_text,now_text,rid,detail['status'],detail['updated_at']),
            ).rowcount
            if not changed:
                continue
            conn.execute('UPDATE delivery_steps SET status=?,message=? WHERE request_id=? AND step_code=?',
                         ('pending' if eligible else 'failed',message,rid,detail.get('current_step')))
            conn.execute('INSERT INTO delivery_events(request_id,level,event_type,message,metadata,created_at) VALUES(?,?,?,?,?,?)',
                         (rid,'warning','execution.interrupted',message,json.dumps(context,ensure_ascii=False),now_text))
            (recovered if eligible else interrupted).append(rid)
    return {'recovered':recovered,'interrupted':interrupted}
