from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class FollowupTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import db
        from app.config import settings
        from app.main import app, current_user
        from app.services import request_followups
        self.db, self.service, self.settings = db, request_followups, settings
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        replacements = dict(data_dir=Path(self.temp.name), worker_enabled=False,
                            seed_demo=True, environment="development", runner_token="followup-test-token")
        prior = {key: getattr(settings, key) for key in replacements}
        for key, value in replacements.items():
            object.__setattr__(settings, key, value)
        self.addCleanup(lambda: [object.__setattr__(settings, key, value) for key, value in prior.items()])
        db.init_db()
        self.owner = db.row("SELECT * FROM users WHERE username='pm'")
        self.admin = db.row("SELECT * FROM users WHERE username='admin'")
        with db.transaction() as conn:
            conn.execute("INSERT INTO users(username,display_name,email,password_hash,role,created_at) VALUES('other','另一人','other@test.invalid','unused','pm',?)", (db.utc_now(),))
        self.other = db.row("SELECT * FROM users WHERE username='other'")
        self.actor = self.owner
        old = dict(app.dependency_overrides)
        app.dependency_overrides[current_user] = lambda: self.actor
        self.addCleanup(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(old)))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.project = db.row("SELECT * FROM projects ORDER BY id LIMIT 1")
        self.request = db.create_delivery_request(self.project, self.owner['id'], 1707176,
                                                  self.project['delivery_mode'], ['pm@example.com'])
        db.update_request(self.request, status='delivered', title='个人语音开关', completed_at=db.utc_now(),
                          result_summary='按用户保存', commit_hash='pinned-delivery-commit')
        self.path = f'/api/requests/{self.request}/followups'
        self.headers = {'Authorization': 'Bearer followup-test-token'}

    def queue(self, key='question-one', question='换浏览器还能保留开关吗？'):
        response = self.client.post(self.path, json={'question': question, 'idempotency_key': key})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['item']

    def claim(self):
        response = self.client.post('/api/runner/followups/claim', headers=self.headers,
                                    json={'runner_id': self.project['runner_id']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['item']

    def finish(self, item, answer='## 结论\n\n按用户持久化，换浏览器仍然保留。'):
        return self.service.update(item['id'], item['runner_id'], item['claim_token'], status='completed', answer=answer)

    def test_persisted_chat_elapsed_and_model_snapshot_without_changing_delivery(self):
        before = self.db.request_detail(self.request)
        item = self.queue()
        self.assertNotIn('claim_token', item)
        self.assertEqual(item['model'], {'model': self.settings.codex_model, 'effort': self.settings.codex_reasoning_effort})
        claimed = self.claim()
        self.assertEqual(claimed['history'], [])
        with self.db.transaction() as conn:
            conn.execute('UPDATE request_followups SET started_at=? WHERE id=?',
                         ((datetime.now(UTC) - timedelta(seconds=75)).isoformat(), item['id']))
            conn.execute("INSERT INTO platform_settings(key,value,updated_at) VALUES('codex',?,?)",
                         (json.dumps({'model':'different-model','effort':'low'}), self.db.utc_now()))
        completed = self.finish(claimed)
        self.assertGreaterEqual(completed['elapsed_seconds'], 75)
        self.assertEqual(completed['model'], item['model'])
        self.db.init_db()  # Existing DB migration must keep the entire conversation.
        self.assertEqual(self.client.get(self.path).json()['items'][0]['answer'], completed['answer'])
        self.assertEqual(self.db.request_detail(self.request), before)
        self.queue('question-two', '这个表是新增的吗？')
        self.assertEqual(self.claim()['history'], [{'question': item['question'], 'answer': completed['answer']}])

    def test_idempotency_concurrency_and_authenticated_claim(self):
        first = self.queue()
        self.assertEqual(self.queue()['id'], first['id'])
        self.assertEqual(self.client.post(self.path, json={'question':'不同问题', 'idempotency_key':'question-one'}).status_code, 409)
        self.assertEqual(self.client.post(self.path, json={'question':'另一个问题', 'idempotency_key':'question-other'}).status_code, 409)
        self.assertEqual(self.client.post('/api/runner/followups/claim', json={'runner_id':self.project['runner_id']}).status_code, 401)
        self.assertIsNone(self.service.claim('wrong-runner'))
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda _: self.service.claim(self.project['runner_id']), range(3)))
        self.assertEqual(sum(bool(item) for item in claims), 1)
        claimed = next(item for item in claims if item)
        with self.assertRaises(PermissionError):
            self.service.update(claimed['id'], claimed['runner_id'], 'wrong-token', status='completed', answer='bad')
        with self.assertRaises(ValueError):
            self.finish(claimed, '')
        self.finish(claimed)
        self.finish(claimed)  # Lost response replay is safe.
        with self.assertRaises(RuntimeError):
            self.finish(claimed, 'late different answer')

    def test_owner_admin_acceptor_only_and_delivered_only(self):
        self.actor = self.other
        self.assertEqual(self.client.get(self.path).status_code, 403)
        self.assertEqual(self.client.post(self.path, json={'question':'hello','idempotency_key':'other-question'}).status_code, 403)
        self.db.update_request(self.request, acceptance_owner_id=self.other['id'])
        self.assertEqual(self.client.get(self.path).status_code, 200)
        first = self.queue()
        self.finish(self.claim())
        self.actor = self.admin
        self.queue('admin-question')
        self.finish(self.claim())
        self.actor = self.owner
        self.db.update_request(self.request, status='running')
        self.assertEqual(self.client.post(self.path, json={'question':'hello','idempotency_key':'running-question'}).status_code, 422)
        self.assertFalse(self.client.get(f'/api/requests/{self.request}').json()['request']['can_followup'])
        self.assertEqual(first['actor_id'], self.other['id'])

    def test_disconnection_expires_and_recovery_preserves_old_question(self):
        item = self.queue()
        claimed = self.claim()
        stale = (datetime.now(UTC)-timedelta(minutes=6)).isoformat()
        with self.db.transaction() as conn:
            conn.execute('UPDATE request_followups SET updated_at=? WHERE id=?', (stale, item['id']))
        history = self.client.get(self.path).json()['items']
        self.assertEqual(history[0]['status'], 'failed')
        self.assertIn('连接中断', history[0]['error_message'])
        self.assertEqual(history[0]['question'], item['question'])
        with self.assertRaises(RuntimeError):
            self.finish(claimed)
        self.queue('recovery-question')
        self.assertEqual(len(self.client.get(self.path).json()['items']), 2)

    def test_worker_executes_separate_conversation_and_failure_is_not_task_failure(self):
        from app.orchestrator import Worker
        from app.store import LocalStore
        before = self.db.request_detail(self.request)
        self.queue()
        worker = Worker(store=LocalStore())
        with patch('app.orchestrator.CodexRunner.answer_followup', return_value=SimpleNamespace(thread_id='answer-thread', result={'answer':'**已核验**'})) as answer:
            self.assertTrue(worker._process_followup())
        options = answer.call_args.kwargs
        self.assertEqual(options['detail']['commit_hash'], 'pinned-delivery-commit')
        self.assertTrue(options['cwd'].is_relative_to(self.settings.data_dir / 'followups'))
        self.assertEqual(self.service.list_for_request(self.request)[0]['status'], 'completed')
        self.queue('failed-question')
        with patch('app.orchestrator.CodexRunner.answer_followup', side_effect=RuntimeError('connection lost')):
            worker._process_followup()
        self.assertEqual(self.service.list_for_request(self.request)[-1]['status'], 'failed')
        self.assertEqual(worker.current_request_ids, [])
        self.assertEqual(self.db.request_detail(self.request), before)

    def test_sdk_uses_read_only_separate_thread_no_mcp_and_current_model(self):
        from app.services.codex_runner import CodexRunner
        from openai_codex import Sandbox, ApprovalMode
        config_root = Path(self.temp.name)/'codex'
        config_root.mkdir()
        (config_root/'config.toml').write_text('[mcp_servers.tfs]\ncommand="unused"\n[mcp_servers.dm7]\ncommand="unused"\n', encoding='utf-8')
        codex = MagicMock()
        codex.thread_start.return_value.id = 'read-only-answer'
        with patch.dict(os.environ, {'CODEX_HOME':str(config_root)}), \
             patch('openai_codex.Codex', return_value=MagicMock(__enter__=MagicMock(return_value=codex))), \
             patch('openai_codex.CodexConfig') as configuration, \
             patch('app.services.codex_runner.resolve_codex_runtime', return_value={'path':'test-codex'}), \
             patch.object(CodexRunner, '_collect_output', return_value=json.dumps({'answer':'### 表\n\n已有表'})):
            result = CodexRunner().answer_followup(cwd=Path(self.temp.name), detail=self.db.request_detail(self.request),
                question='请解释实现', history=[{'question':'以前问题','answer':'以前回答'}],
                model_config={'model':'gpt-6.1-sol','effort':'xhigh'}, on_event=lambda *_:None, is_cancelled=lambda:False)
        self.assertEqual(result.thread_id, 'read-only-answer')
        kwargs = codex.thread_start.call_args.kwargs
        self.assertEqual(kwargs['sandbox'], Sandbox.read_only)
        self.assertEqual(kwargs['approval_mode'], ApprovalMode.deny_all)
        codex.thread_resume.assert_not_called()
        self.assertIn('mcp_servers={}', configuration.call_args.kwargs['config_overrides'])
        if os.name == 'nt':
            self.assertIn('windows.sandbox="unelevated"', configuration.call_args.kwargs['config_overrides'])
        turn = codex.thread_start.return_value.turn.call_args
        self.assertEqual(turn.kwargs['model'], 'gpt-6.1-sol')
        self.assertEqual(turn.kwargs['effort'], 'xhigh')
        self.assertIn('pinned-delivery-commit', turn.args[0][0].text)
        self.assertIn('以前回答', turn.args[0][0].text)

    def joint_fixture(self, *, title=None, status='delivered', intake_status='failed'):
        now = self.db.utc_now()
        with self.db.transaction() as conn:
            for key,name,aliases in [('app','网络发令APP',['网络下令APP']), ('pc','遂宁网络发令',['遂宁网络下令'])]:
                conn.execute("INSERT INTO projects(project_key,name,routing_title_keywords,delivery_mode,tfs_collection_url,tfs_project,created_at,updated_at) VALUES(?,?,?,'local_package','http://tfs.invalid','Area',?,?)", (key,name,json.dumps(aliases),now,now))
        projects = [self.db.row('SELECT * FROM projects WHERE project_key=?',(key,)) for key in ('app','pc')]
        ids = self.db.create_joint_delivery_requests(projects,self.owner['id'],1707804,[],[], 'joint-app-test', [{'project_key':'app'},{'project_key':'pc'}])
        for index,identifier in enumerate(ids):
            self.db.update_request(identifier,status=status,title=title or '【网络下令APP】南充、遂宁网络下令APP刷新问题',completed_at=now, commit_hash=f'preserve-commit-{index}')
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO request_intakes(id,work_item_id,requester_id,runner_id,status,title,result_request_ids,classification_summary,created_at,updated_at) VALUES('joint-app-test',1707804,?,?,?, ?,?,?,?,?)", (self.owner['id'],self.project['runner_id'],intake_status,title or '【网络下令APP】南充、遂宁网络下令APP刷新问题',json.dumps(ids),json.dumps([{'project_key':'app'},{'project_key':'pc'}]),now,now))
        return ids

    def test_historical_false_joint_is_audited_redirected_and_not_replayed(self):
        from app.services.routing_corrections import repair_app_groups
        ids = self.joint_fixture()
        self.assertEqual(repair_app_groups(), 1)
        self.assertEqual(repair_app_groups(), 0)
        for index,identifier in enumerate(ids):
            item = self.db.request_detail(identifier)
            self.assertEqual(item['status'], 'delivered')
            self.assertEqual(item['commit_hash'],f'preserve-commit-{index}')
            self.assertIsNone(item['joint_group_id'])
            self.assertEqual(item['joint_project_count'],1)
        self.assertEqual(self.db.row('SELECT routing_superseded_by FROM delivery_requests WHERE id=?',(ids[1],))['routing_superseded_by'],ids[0])
        detail = self.client.get(f'/api/requests/{ids[1]}').json()['request']
        self.assertEqual(detail['id'],ids[0])
        visible = self.client.get('/api/delivery-records').json()['items']
        self.assertIn(ids[0],[item['id'] for item in visible])
        self.assertNotIn(ids[1],[item['id'] for item in visible])
        self.assertIsNotNone(self.db.row("SELECT * FROM audit_logs WHERE action='routing.app_scope_corrected'"))
        self.db.init_db()
        self.assertEqual(self.db.row("SELECT status FROM request_intakes WHERE id='joint-app-test'")['status'],'delivered')

    def test_historical_repair_does_not_touch_true_joint_active_or_cancelled_groups(self):
        from app.services.routing_corrections import repair_app_groups
        for title,status,intake_status in [('【网络下令APP】【遂宁网络发令】联合开发','delivered','failed'),
                                         ('【网络下令APP】遂宁APP','running','failed'),
                                         ('【网络下令APP】遂宁APP','delivered','cancelled')]:
            with self.subTest(title=title,status=status,intake_status=intake_status):
                ids = self.joint_fixture(title=title,status=status,intake_status=intake_status)
                self.assertEqual(repair_app_groups(),0)
                self.assertEqual(self.db.request_detail(ids[0])['joint_group_id'],'joint-app-test')
                with self.db.transaction() as conn:
                    conn.execute("DELETE FROM request_intakes WHERE id='joint-app-test'")
                    conn.execute('DELETE FROM delivery_requests WHERE id IN (?,?)', ids)
                    conn.execute("DELETE FROM projects WHERE project_key IN ('app','pc')")


class RegionalAppRoutingTests(unittest.TestCase):
    def test_explicit_app_scope_ignores_region_lists_and_pc_references(self):
        from app.project_catalog import load_project_presets, resolve_projects_for_work_item
        projects = load_project_presets()
        for title in ['【网络下令APP】解决南充、乐山、泸州、阿坝、巴中、资阳、遂宁网络下令APP页面刷新和下拉问题',
                      '【南充网络下令APP】页面刷新', '【网络发令APP】和遂宁网络发令使用同一接口']:
            with self.subTest(title=title):
                selected,_,_ = resolve_projects_for_work_item(1707804,projects,work_item={
                    'title':title, 'area_path':'XiNanArea-New\\四川省区团队',
                    'description':'参考遂宁网络下令、成都网络发令的已有代码，修复APP页面。'})
                self.assertEqual([item['project_key'] for item in selected],['network-command-app'])

    def test_unbracketed_regional_app_suffix_does_not_select_pc(self):
        from app.project_catalog import load_project_presets, resolve_projects_for_work_item
        selected,_,_ = resolve_projects_for_work_item(1707804,load_project_presets(),work_item={
            'title':'遂宁网络下令APP页面刷新问题', 'area_path':'XiNanArea-New\\四川省区团队'})
        self.assertEqual([item['project_key'] for item in selected],['network-command-app'])
