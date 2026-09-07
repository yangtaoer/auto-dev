from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        from app import db
        from app.config import settings
        self.db = db
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        original = {key: getattr(settings, key) for key in ('data_dir', 'seed_demo', 'environment', 'worker_enabled')}
        for key, value in dict(data_dir=Path(self.temp.name), seed_demo=True, environment='development', worker_enabled=False).items():
            object.__setattr__(settings, key, value)
        self.addCleanup(lambda: [object.__setattr__(settings, key, value) for key, value in original.items()])
        db.init_db()
        self.project = db.row('SELECT * FROM projects LIMIT 1')
        self.admin = db.row("SELECT * FROM users WHERE role='admin' LIMIT 1")
        self.counter = 90000

    def request(self, status='developing', **fields):
        self.counter += 1
        request_id = self.db.create_delivery_request(self.project, self.admin['id'], self.counter, 'sichuan_auto_review', [], ['auto_release'])
        self.db.update_request(request_id, status=status, **fields)
        return request_id

    def test_same_project_defers_then_shares_one_batch(self):
        from app.services import release_coordination as releases
        first, second = self.request('capturing'), self.request()
        decision = releases.claim(first)
        self.assertEqual(decision['action'], 'wait')
        self.assertIn(second, decision['pending'])
        self.db.update_request(second, status='capturing')
        decision = releases.claim(second, 'same-http-call')
        self.assertEqual(decision['action'], 'run')
        self.assertEqual(set(decision['members']), {first, second})
        self.assertEqual(releases.claim(second, 'same-http-call')['action'], 'run')
        self.assertEqual(releases.claim(second, 'another-worker')['action'], 'wait')
        self.assertEqual(releases.claim(first)['action'], 'wait')
        result = {'buildId': 15, 'artifactsUrl': 'https://tfs/build/15'}
        releases.finish(second, decision['batch_id'], result)
        self.assertEqual(releases.claim(first)['result'], result)
        self.assertEqual(releases.claim(second)['action'], 'reuse')

    def test_failed_cancelled_or_paused_later_task_does_not_lose_pending_release(self):
        from app.services import release_coordination as releases
        for status in ('failed', 'cancelled', 'waiting_approval', 'waiting_input'):
            first, other = self.request('capturing'), self.request()
            self.assertEqual(releases.claim(first)['action'], 'wait')
            self.db.update_request(other, status=status)
            claimed = releases.claim(first)
            self.assertEqual(claimed['action'], 'run')
            releases.finish(first, claimed['batch_id'], {'artifactsUrl': 'https://tfs/ok'})
            self.db.update_request(first, status='delivered')

    def test_concurrent_claims_never_start_duplicate_releases(self):
        from app.services import release_coordination as releases
        ids = [self.request('capturing') for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(releases.claim, ids))
        self.assertEqual(sum(item['action']=='run' for item in decisions), 1)
        self.assertEqual(self.db.row("SELECT COUNT(*) n FROM release_batches")['n'], 1)

    def test_arrivals_after_release_starts_and_branch_changes_are_not_covered(self):
        from app.services import release_coordination as releases
        first = self.request('capturing')
        batch = releases.claim(first)
        late = self.request()
        releases.finish(first, batch['batch_id'], {'artifactsUrl': 'https://tfs/old'})
        self.db.update_request(first, status='delivered')
        self.db.update_request(late, status='capturing')
        late_batch = releases.claim(late)
        self.assertEqual(late_batch['action'], 'run')
        self.assertNotEqual(batch['batch_id'], late_batch['batch_id'])
        releases.finish(late, late_batch['batch_id'], {}, failed=True)
        self.assertEqual(releases.claim(late)['action'], 'failed')

    def test_different_project_and_analysis_do_not_delay_release(self):
        from app.services import release_coordination as releases
        first = self.request('capturing')
        self.request(task_type='analysis')
        self.project = dict(self.project, project_key='other', name='Other')
        with self.db.transaction() as conn:
            columns = [key for key in self.project if key != 'id']
            cursor = conn.execute(f"INSERT INTO projects({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [self.project[key] for key in columns])
            self.project['id'] = cursor.lastrowid
        self.request()
        self.assertEqual(releases.claim(first)['action'], 'run')

    def test_different_branch_waiters_do_not_share_incompatible_artifacts(self):
        from app.services import release_coordination as releases
        first = self.request('capturing')
        second = self.request('capturing')
        snapshot = self.db.request_detail(second)['policy_snapshot']
        self.db.update_request(second, policy_snapshot={**snapshot, 'base_branch':'other'})
        self.assertEqual(releases.claim(first)['action'], 'wait')
        batch = releases.claim(second)
        self.assertEqual(batch['members'], [second])
        releases.finish(second, batch['batch_id'], {'artifactsUrl':'https://tfs/other'})
        self.db.update_request(second, status='delivered')
        self.assertEqual(releases.claim(first)['action'], 'run')

    def test_not_ready_or_cancelled_task_never_claims_release(self):
        from app.services import release_coordination as releases
        for status in ('queued','developing','waiting_merge','cancelled'):
            self.assertEqual(releases.claim(self.request(status))['action'], 'stop')

    def test_terminal_release_owner_does_not_strand_shared_waiters(self):
        from app.services import release_coordination as releases
        first, second = self.request('capturing'), self.request('capturing')
        releases.claim(first)
        releases.claim(second)
        self.db.update_request(second, status='failed')
        self.assertEqual(releases.claim(first)['action'], 'failed')
        self.assertEqual(self.db.row('SELECT COUNT(*) n FROM release_batches')['n'], 1)

    def test_recoverable_read_failure_is_resumed_in_place_only_by_admin(self):
        from app.main import continue_waiting_approval_request, ContinueRequestInput, public_request_payload
        from app.services.tfs import recoverable_preflight_failure
        request_id = self.request('failed', current_step='validate', error_message='[WinError 10053] aborted',
                                  codex_thread_id='original', repository_states=[{'name':'repo','worktree_path':self.temp.name}])
        detail = self.db.request_detail(request_id)
        self.assertTrue(public_request_payload(detail)['can_continue_in_place'])
        self.assertFalse(recoverable_preflight_failure({**detail,'current_step':'release'}))
        self.assertFalse(recoverable_preflight_failure({**detail,'pr_id':5}))
        result = continue_waiting_approval_request(request_id, ContinueRequestInput(prompt='继续原现场'), self.admin)
        self.assertEqual(result['id'], request_id)
        resumed = self.db.request_detail(request_id)
        self.assertEqual(resumed['status'], 'queued')
        self.assertEqual(resumed['codex_thread_id'], 'original')
        self.assertEqual(resumed['repository_states'], detail['repository_states'])

    def test_worker_shared_release_completes_both_without_second_pipeline(self):
        from app.orchestrator import Worker
        from app.store import LocalStore
        first, second = self.request('capturing'), self.request()
        worker = Worker(store=LocalStore())
        with patch.object(worker, '_send_status_email'), patch.object(worker, '_ensure_auto_release', wraps=worker._ensure_auto_release) as release:
            worker._complete_delivery(first)
            self.assertEqual(self.db.request_detail(first)['status'], 'waiting_release')
            self.db.update_request(second, status='capturing')
            worker._complete_delivery(second)
            worker.poll_merge(first)
        self.assertEqual(release.call_count, 1)
        details = [self.db.request_detail(value) for value in (first, second)]
        self.assertTrue(all(value['status']=='delivered' for value in details))
        links = [next(item['external_url'] for item in value['artifacts'] if item['kind']=='release_artifact') for value in details]
        self.assertEqual(links[0], links[1])

    def test_admin_model_settings_authorization_validation_and_frozen_task(self):
        from fastapi.testclient import TestClient
        from app.main import app, current_user
        from app.services import model_settings
        models = [{'model':'gpt-6-astra','name':'GPT-6 Astra','efforts':['high','ultra'],'default_effort':'high'},
                  {'model':'gpt-5.6-sol','name':'GPT-5.6 Sol','efforts':['low','high'],'default_effort':'low'}]
        actor = {'role':'pm'}
        old = dict(app.dependency_overrides)
        app.dependency_overrides[current_user] = lambda: actor
        self.addCleanup(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(old)))
        with TestClient(app) as client, patch.object(model_settings, 'catalog', return_value=models):
            self.assertEqual(client.get('/api/admin/model-settings').status_code, 403)
            self.assertEqual(client.put('/api/admin/model-settings', json={'model':'gpt-6-astra','effort':'high'}).status_code, 403)
            actor = self.admin
            self.assertEqual(client.get('/api/admin/model-settings').status_code, 200)
            first = self.request()
            previous = model_settings.for_request(first)
            self.assertEqual(client.put('/api/admin/model-settings', json={'model':'unknown','effort':'high'}).status_code, 422)
            self.assertEqual(client.put('/api/admin/model-settings', json={'model':'gpt-5.6-sol','effort':'ultra'}).status_code, 422)
            payload = {'model':'gpt-5.6-sol','effort':'low'}
            self.assertEqual(client.put('/api/admin/model-settings', json=payload).status_code, 200)
            self.assertEqual(model_settings.for_request(first), previous)
            self.assertEqual(model_settings.for_request(self.request()), payload)
            self.db.init_db()
            self.assertEqual(model_settings.current(), payload)

    def test_transient_tfs_reads_retry_fresh_connection_but_never_replay_writes(self):
        from app.services.tfs import TfsClient, TfsConnectionError, TfsError
        client = TfsClient('https://tfs', pat='test-token')
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        ok = httpx.Response(200, json={'id':1})
        reset = httpx.ReadError('[WinError 10053] aborted')
        with patch.object(client, '_client', return_value=connection) as factory, patch('app.services.tfs.time.sleep'):
            connection.request.side_effect = [reset, ok]
            self.assertEqual(client._request('GET','https://tfs/item'), {'id':1})
            self.assertEqual(factory.call_count, 2)
            connection.request.side_effect = reset
            with self.assertRaises(TfsConnectionError): client._request('GET','https://tfs/item')
            connection.request.reset_mock()
            with self.assertRaises(TfsError): client._request('POST','https://tfs/build')
            self.assertEqual(connection.request.call_count, 1)

    def test_preflight_disconnect_waits_and_preserves_original_start(self):
        from app.orchestrator import Worker
        from app.services.tfs import TfsConnectionError
        from app.store import LocalStore
        request_id = self.request('queued', started_at='2026-09-01T00:00:00+00:00', codex_thread_id='old-thread', supplement_answers=[{'id':'resume','answer':'continue'}])
        worker = Worker(store=LocalStore())
        with patch.object(worker, '_validate', side_effect=TfsConnectionError('reset')), patch.object(worker, '_send_status_email') as email:
            worker.run_request(request_id)
        detail = self.db.request_detail(request_id)
        self.assertEqual(detail['status'], 'waiting_retry')
        self.assertEqual(detail['codex_thread_id'], 'old-thread')
        self.assertEqual(detail['started_at'], '2026-09-01T00:00:00+00:00')
        self.assertIsNotNone(detail['next_poll_at'])
        email.assert_not_called()

    def test_ordinary_gaps_auto_repair_in_same_thread_critical_risks_do_not(self):
        from app.orchestrator import Worker
        from app.store import LocalStore
        request_id = self.request()
        worker = Worker(store=LocalStore())
        gap = {'decision':'completed','blocking_risks':['验收项未完成'],'risks':[]}
        success = {'decision':'completed','blocking_risks':[],'risks':[]}
        kwargs = dict(request_id=request_id, repository_states=[], project={}, task_type='development')
        with patch('app.orchestrator.CodexRunner.run', side_effect=[SimpleNamespace(result=gap,thread_id='same'),SimpleNamespace(result=success,thread_id='same')]) as run:
            self.assertEqual(worker._run_with_recovery(**kwargs).result, success)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.kwargs['resume_thread_id'], 'same')
        critical = dict(gap, blocking_risks=['重大安全隐患：存在越权访问'])
        with patch('app.orchestrator.CodexRunner.run', return_value=SimpleNamespace(result=critical,thread_id='same')) as run:
            self.assertEqual(worker._run_with_recovery(**kwargs).result, critical)
            self.assertEqual(run.call_count, 1)

    def test_screenshot_and_deployment_evidence_are_advisory_not_human_questions(self):
        from app.services.development_risks import development_risks, critical_risk
        from app.services.blocker_summary import summarize_blocker
        from app.services.quality_gates import evaluate_development_quality
        result = {'blocking_risks':['真实截图不可用','前端部署验证未完成：asset_manifest_checked'], 'risks':[]}
        self.assertEqual(development_risks(result)[1], [])
        self.assertFalse(critical_risk('未发现重大安全隐患'))
        self.assertTrue(critical_risk('业务逻辑冲突：取消已生效指令会导致数据丢失'))
        self.assertEqual(summarize_blocker({'error_message':result['blocking_risks'][1]})['decision_required'], '')
        gate = evaluate_development_quality({'quality_profile':{'visual':{'frontend_patterns':['**/*.vue'],'deployment_checks':['asset_manifest_checked']}}}, [{'name':'ui','changed_files':['Page.vue']}], result)
        self.assertFalse(gate['blockers'])
        self.assertTrue(gate['warnings'])
        self.assertTrue(development_risks({'blocking_risks':['截图不可用，接口未实现']})[1])

    def test_post_delivery_acceptance_stays_unverified_without_gating_development(self):
        from app.services.quality_gates import evaluate_development_quality, is_post_delivery_acceptance
        for text in ('2、配合现场测试无误。', '用户验收通过', '协助客户验收确认'):
            self.assertTrue(is_post_delivery_acceptance(text))
        for text in ('现场测试按钮未实现', '配合现场测试并修复查询功能', '2、实现现场测试入口', '新增接口'):
            self.assertFalse(is_post_delivery_acceptance(text))
        result = {'acceptance_ledger':[{'id':'AC-02','criterion':'2、配合现场测试无误。','status':'partial','evidence':['本机自动测试通过；未操作现场']} ]}
        profile = {'quality_profile':{'require_acceptance_ledger':True}}
        gate = evaluate_development_quality(profile, [], result)
        self.assertEqual(gate['blockers'], [])
        self.assertTrue(gate['warnings'])
        self.assertEqual(result['acceptance_ledger'][0]['status'], 'partial')
        result['acceptance_ledger'][0]['criterion'] = '新增签收接口'
        self.assertIn('未完成验收项', '；'.join(evaluate_development_quality(profile, [], result)['blockers']))

    def test_manifest_never_appears_in_delivery_or_mail_but_business_json_survives(self):
        from app.domain import visible_delivery_artifacts
        from app.services.delivery import Mailer
        artifacts = [{'kind':'delivery_manifest','name':'delivery-validation-manifest.json'}, {'kind':'config','name':'delivery-validation-manifest.json'}, {'kind':'config','name':'business.json'}]
        self.assertEqual([item['name'] for item in visible_delivery_artifacts('local_package', artifacts)], ['business.json'])
        detail = {'id':'example','work_item_id':1,'delivery_mode':'sichuan_auto_review','artifacts':artifacts,'status':'delivered','created_at':self.db.utc_now()}
        self.assertNotIn('delivery-validation-manifest.json', Mailer().delivery_html(detail))


if __name__ == '__main__':
    unittest.main()
