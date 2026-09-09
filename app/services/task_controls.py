"""Durable user commands. Restart never reuses the cancelled task's workspace."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from ..db import transaction, utc_now, project_for_api, json_value
from ..domain import PIPELINE_STEPS

TERMINAL = {'delivered', 'failed', 'rejected', 'cancelled'}


def queue(request_id: str, action: str, actor: dict) -> dict:
    if action not in {'cancel', 'restart', 'rollback'}:
        raise ValueError('未知任务操作')
    with transaction() as conn:
        conn.execute('BEGIN IMMEDIATE')
        source = conn.execute('SELECT * FROM delivery_requests WHERE id=?', (request_id,)).fetchone()
        if not source:
            raise LookupError('任务不存在')
        if actor['role'] != 'admin' and actor['id'] != source['requester_id']:
            raise PermissionError('只有管理员或任务发起人可以操作')
        prior = conn.execute("SELECT * FROM request_controls WHERE request_id=? AND action=? ORDER BY created_at DESC LIMIT 1", (request_id, action)).fetchone()
        if prior and prior['status'] != 'failed':
            return dict(prior)
        if conn.execute("SELECT 1 FROM request_controls WHERE request_id=? AND status IN ('pending','running','waiting_merge')", (request_id,)).fetchone():
            raise RuntimeError('该任务已有操作正在处理，请等待完成')
        if action == 'rollback':
            if source['status'] != 'delivered' or source['task_type'] == 'analysis':
                raise RuntimeError('仅已完成的代码研发任务支持回退；问题分析不产生代码改动')
            states = json_value(source['repository_states'], [])
            if not any(s.get('changed_files') and (s.get('merge_commit') or s.get('commit_hash')) for s in states):
                raise RuntimeError('该任务没有可核验的仓库提交记录，不能猜测回退范围')
            if prior:
                raise RuntimeError('上次回退未完成，请先核对记录中的提交/PR，不能重复发起回退')
        elif source['status'] == 'delivered' or (action == 'cancel' and source['status'] in TERMINAL):
            raise RuntimeError('任务已经结束')
        elif not conn.execute('SELECT 1 FROM projects WHERE id=? AND enabled=1', (source['project_id'],)).fetchone() and action == 'restart':
            raise RuntimeError('项目已停用，不能重新开始')
        now, control_id = utc_now(), str(uuid.uuid4())
        if action != 'rollback':
            conn.execute("UPDATE delivery_requests SET status='cancelled',completed_at=?,updated_at=? WHERE id=?", (now, now, request_id))
        message = {'cancel': '已请求取消，等待执行器停止当前工作并关闭未合并 PR；已合并代码不会自动撤销',
                   'restart': '等待原任务停止；将使用新会话、新工作区从最新目标分支重新开始',
                   'rollback': '等待执行器核对本次提交，生成可审计的反向提交；不自动回滚数据库或已部署环境'}[action]
        conn.execute('INSERT INTO request_controls(id,request_id,action,actor_id,runner_id,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                     (control_id, request_id, action, actor['id'], source['runner_id'], message, now, now))
        conn.execute('INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)',
                     (request_id, 'warning', 'control.' + action, message, now))
        conn.execute('INSERT INTO audit_logs(actor_id,action,target_type,target_id,detail,created_at) VALUES(?,?,?,?,?,?)',
                     (actor['id'], 'request.' + action, 'delivery_request', request_id, json.dumps({'control_id': control_id}), now))
        return dict(conn.execute('SELECT * FROM request_controls WHERE id=?', (control_id,)).fetchone())


def claim(runner_id: str, active_ids: list[str]) -> dict | None:
    with transaction() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # The runner reports all locally active operations under its claim lock.
        # A previous process may have died after a remote write: never replay a
        # rollback blindly, but allow idempotent cancel/restart cleanup to resume.
        for stale in conn.execute("SELECT * FROM request_controls WHERE runner_id=? AND status='running'", (runner_id,)).fetchall():
            if stale['request_id'] not in active_ids:
                state = 'failed' if stale['action'] == 'rollback' else 'pending'
                message = '执行器曾中断，请核对已记录的回退提交与 PR；为防止重复回退，未自动重放' if state == 'failed' else '执行器重启后继续处理原操作'
                conn.execute('UPDATE request_controls SET status=?,message=?,updated_at=? WHERE id=?', (state, message, utc_now(), stale['id']))
        for control in conn.execute("SELECT * FROM request_controls WHERE runner_id=? AND status IN ('pending','waiting_merge') AND (next_poll_at IS NULL OR next_poll_at<=?) ORDER BY created_at", (runner_id, utc_now())).fetchall():
            if control['request_id'] in active_ids:
                continue  # Wait for the actual worker, not merely a cancelled DB status.
            source = conn.execute('SELECT project_id FROM delivery_requests WHERE id=?', (control['request_id'],)).fetchone()
            if control['action'] == 'rollback' and control['status'] == 'pending' and conn.execute("SELECT 1 FROM delivery_requests WHERE project_id=? AND status NOT IN ('delivered','failed','rejected','cancelled')", (source['project_id'],)).fetchone():
                continue
            # A single control per runner avoids simultaneous multi-repository rollback writes.
            if conn.execute("SELECT 1 FROM request_controls WHERE runner_id=? AND status='running'", (runner_id,)).fetchone():
                return None
            conn.execute("UPDATE request_controls SET status='running',updated_at=? WHERE id=?", (utc_now(), control['id']))
            result = dict(control)
            result['result'] = json_value(result['result'], {})
            return result
    return None


def update(control_id: str, *, status: str, message: str, result: dict) -> None:
    if status not in {'running', 'waiting_merge', 'completed', 'failed'}:
        raise ValueError('未知操作状态')
    now = utc_now()
    with transaction() as conn:
        conn.execute('UPDATE request_controls SET status=?,message=?,result=?,next_poll_at=?,updated_at=? WHERE id=?',
                     (status, message[:3000], json.dumps(result, ensure_ascii=False),
                      (datetime.now(UTC) + timedelta(seconds=20)).isoformat(), now, control_id))
        source = conn.execute('SELECT request_id,action FROM request_controls WHERE id=?', (control_id,)).fetchone()
        if source and status in {'completed', 'failed'}:
            conn.execute('INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)',
                         (source['request_id'], 'error' if status == 'failed' else 'info', 'control.' + status, message[:3000], now))
        if source and source['action'] == 'rollback' and status == 'completed':
            experience = conn.execute('SELECT * FROM project_experiences WHERE request_id=?', (source['request_id'],)).fetchone()
            if experience and experience['status'] != 'deprecated':
                conn.execute("UPDATE project_experiences SET status='deprecated',updated_at=? WHERE id=?", (now, experience['id']))
                conn.execute("INSERT INTO project_experience_revisions(experience_id,status,content,reason,created_at) VALUES(?,'deprecated',?,?,?)", (experience['id'], experience['content'], '原需求代码已回退，不再作为后续研发的有效经验', now))


def restart(control_id: str) -> str:
    """Publish a fully initialized new request atomically, preserving repair scope."""
    with transaction() as conn:
        conn.execute('BEGIN IMMEDIATE')
        control = conn.execute('SELECT * FROM request_controls WHERE id=? AND action=\'restart\'', (control_id,)).fetchone()
        if not control:
            raise LookupError('重新开始操作不存在')
        result = json_value(control['result'], {})
        if result.get('new_request_id'):
            return result['new_request_id']
        if control['status'] != 'running':
            raise RuntimeError('需等待执行器停止原任务并核查 PR 后再重新开始')
        source = dict(conn.execute('SELECT * FROM delivery_requests WHERE id=?', (control['request_id'],)).fetchone())
        if source['status'] != 'cancelled':
            raise RuntimeError('原任务尚未停止')
        project = conn.execute('SELECT * FROM projects WHERE id=? AND enabled=1', (source['project_id'],)).fetchone()
        if not project:
            raise RuntimeError('项目已停用')
        request_id, now = str(uuid.uuid4()), utc_now()
        fields = {name: source[name] for name in (
            'project_id', 'work_item_id', 'requester_id', 'acceptance_owner_id', 'delivery_mode',
            'notification_emails', 'delivery_options', 'task_type', 'title', 'parent_request_id', 'root_request_id',
            'repair_round', 'failed_item_ids', 'protected_item_ids', 'repair_context')}
        fields.update(id=request_id, runner_id=project['runner_id'], status='queued', current_step='validate',
                      policy_snapshot=json.dumps(project_for_api(dict(project)), ensure_ascii=False), created_at=now, updated_at=now)
        fields['repair_context'] = json.dumps({**json_value(source['repair_context'], {}), '_restart_source_id': source['id']}, ensure_ascii=False)
        conn.execute(f"INSERT INTO delivery_requests({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", tuple(fields.values()))
        conn.executemany("INSERT INTO delivery_steps(request_id,step_code,name,status) VALUES(?,?,?,'pending')", [(request_id, code, name) for code, name in PIPELINE_STEPS])
        if source['parent_request_id']:
            conn.execute('INSERT INTO acceptance_items(request_id,item_id,position,criterion,requirement_revision,source,created_at) SELECT ?,item_id,position,criterion,requirement_revision,source,? FROM acceptance_items WHERE request_id=?', (request_id, now, source['id']))
            conn.execute('UPDATE acceptance_repairs SET request_id=? WHERE request_id=?', (request_id, source['id']))
        conn.execute('INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)',
                     (request_id, 'info', 'request.restarted', '从已停止任务 ' + source['id'][:8] + ' 重新开始；重新读取需求与最新目标分支', now))
        result['new_request_id'] = request_id
        conn.execute("UPDATE request_controls SET status='completed',result=?,message=?,updated_at=? WHERE id=?", (json.dumps(result), '已使用新会话和新工作区重新排队', now, control_id))
        return request_id
