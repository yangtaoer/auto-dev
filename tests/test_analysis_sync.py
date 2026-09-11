from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AnalysisSyncTests(unittest.TestCase):
    def setUp(self):
        from app import db
        from app.config import settings
        self.db = db
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        original = {key: getattr(settings, key) for key in ('data_dir', 'seed_demo', 'environment', 'worker_enabled', 'runner_token')}
        for key, value in dict(data_dir=Path(self.temp.name), seed_demo=True, environment='development', worker_enabled=False, runner_token='test-sync-token').items():
            object.__setattr__(settings, key, value)
        self.addCleanup(lambda: [object.__setattr__(settings, key, value) for key, value in original.items()])
        db.init_db()
        self.project = db.row('SELECT * FROM projects LIMIT 1')
        self.admin = db.row("SELECT * FROM users WHERE role='admin' LIMIT 1")
        self.result = {'decision': 'completed', 'summary': '分析完成，未修改代码', 'root_cause': '关联数据缺失'}

    def request(self, status='failed', report=True):
        rid = self.db.create_delivery_request(self.project, self.admin['id'], 1684296, 'local_package', [], [], task_type='analysis')
        self.db.update_request(rid, status=status, current_step='deliver', analysis_result=self.result,
                               completed_at=self.db.utc_now(), error_message='TFS 400',
                               policy_snapshot={**self.db.request_detail(rid)['policy_snapshot'], 'simulation_mode': False})
        if report:
            self.db.add_artifact(rid, 'analysis_report', 'existing-report.md', external_url='https://example.invalid/report.md')
        return rid

    def test_tfs_preserves_existing_version_and_guards_revision(self):
        from app.services.tfs import TfsClient, ACTUAL_DELIVERY_VERSION_FIELD
        c = TfsClient('https://tfs.test/DefaultCollection', pat='test')
        with patch.object(c, 'get_work_item', return_value={'state': '新建', 'revision': 8, 'actual_delivery_version': 'V3.6'}), patch.object(c, '_request', return_value={'fields': {'System.State': '已解决'}}) as request:
            c.complete_analysis(1, '<p>report</p>')
        body = request.call_args.kwargs['json']
        self.assertEqual(body[0], {'op': 'test', 'path': '/rev', 'value': 8})
        self.assertFalse(any(i['path'].endswith(ACTUAL_DELIVERY_VERSION_FIELD) for i in body))

    def test_lost_write_response_reconciles_before_replay(self):
        from app.services.tfs import TfsClient, TfsError
        c = TfsClient('https://tfs.test/DefaultCollection', pat='test')
        with patch.object(c, 'get_work_item', side_effect=[{'state': '新建', 'revision': 8}, {'state': '已解决', 'revision': 9, 'delivery_artifacts': '<p>report</p>'}]), patch.object(c, '_request', side_effect=TfsError('写入结果未知')) as request:
            with self.assertRaises(TfsError):
                c.complete_analysis(1, '<p>report</p>')
            self.assertEqual(c.complete_analysis(1, '<p>report</p>')['state'], '已解决')
        self.assertEqual(request.call_count, 1)

    def test_get_work_item_exposes_existing_version_and_report(self):
        from app.services.tfs import TfsClient, ACTUAL_DELIVERY_VERSION_FIELD, DELIVERY_ARTIFACTS_FIELD
        c = TfsClient('https://tfs.test/DefaultCollection', pat='test')
        with patch.object(c, '_request', return_value={'rev': 2, 'fields': {ACTUAL_DELIVERY_VERSION_FIELD: 'V2', DELIVERY_ARTIFACTS_FIELD: 'report'}}):
            result = c.get_work_item(1)
        self.assertEqual(result['actual_delivery_version'], 'V2')
        self.assertEqual(result['delivery_artifacts'], 'report')

    def test_sync_error_retains_download_and_only_retries_delivery(self):
        from app.orchestrator import Worker
        from app.services.tfs import TfsError
        rid = self.request()
        worker = Worker()
        with patch('app.orchestrator.TfsClient') as tfs, patch.object(worker, '_send_status_email') as mail, patch.object(worker, 'run_request') as analyze, patch.object(worker, '_send_terminal_email') as failed_mail:
            tfs.return_value.complete_analysis.side_effect = TfsError('TF401320 实际交付版本 Required InvalidEmpty')
            worker._complete_analysis(rid, self.result)
            pending = self.db.request_detail(rid)
            self.assertEqual(pending['status'], 'waiting_analysis_sync')
            self.assertIn('报告可下载', pending['error_message'])
            self.assertIsNone(pending['completed_at'])
            self.assertIsNotNone(pending['next_poll_at'])
            self.assertEqual(len(pending['artifacts']), 1)
            mail.assert_not_called()
            failed_mail.assert_not_called()
            tfs.return_value.complete_analysis.side_effect = None
            tfs.return_value.complete_analysis.return_value = {'state': '已解决'}
            worker.poll_merge(rid)
            done = self.db.request_detail(rid)
            self.assertEqual(done['status'], 'delivered')
            self.assertEqual(done['analysis_result'], self.result)
            self.assertEqual(done['artifacts'][0]['id'], pending['artifacts'][0]['id'])
            self.assertEqual(len(done['artifacts']), 1)
            analyze.assert_not_called()
            mail.assert_called_once()

    def test_mail_failure_does_not_turn_completed_analysis_into_failure(self):
        from app.orchestrator import Worker
        rid = self.request()
        worker = Worker()
        with patch('app.orchestrator.TfsClient') as tfs, patch.object(worker, '_send_status_email', side_effect=RuntimeError('SMTP unavailable')) as mail:
            tfs.return_value.complete_analysis.return_value = {'state': '已解决'}
            worker._complete_analysis(rid, self.result)
            mail.assert_called_once()
        self.assertEqual(self.db.request_detail(rid)['status'], 'waiting_analysis_sync')

    def test_first_attempt_generates_one_report_then_reuses_it(self):
        from app.orchestrator import Worker
        rid = self.request(report=False)
        worker = Worker()
        with patch('app.orchestrator.TfsClient') as tfs, patch.object(worker, '_send_status_email'):
            tfs.return_value.complete_analysis.return_value = {'state': '已解决'}
            worker._complete_analysis(rid, self.result)
            worker._complete_analysis(rid, self.result)
        self.assertEqual(self.db.request_detail(rid)['status'], 'delivered')
        reports = [a for a in self.db.request_detail(rid)['artifacts'] if a['kind'] == 'analysis_report']
        self.assertEqual(len(reports), 1)
        self.assertTrue(Path(reports[0]['local_path']).is_file())

    def test_legacy_failed_task_reuses_id_and_can_be_claimed_locally(self):
        from app.services.analysis_sync import retry
        from app.store import LocalStore
        rid = self.request()
        result = retry(rid, self.admin)
        self.assertEqual(result['id'], rid)
        self.assertTrue(result['reuse_report'])
        self.assertEqual(LocalStore().next_waiting(), rid)
        retry(rid, self.admin)
        self.assertIsNone(LocalStore().next_waiting())
        self.assertEqual(self.db.row('SELECT count(*) AS n FROM delivery_requests')['n'], 1)

    def test_recovery_rejects_wrong_user_missing_report_and_cancelled(self):
        from app.services.analysis_sync import retry
        rid = self.request(report=False)
        with self.assertRaises(PermissionError):
            retry(rid, {'id': -1, 'role': 'pm'})
        with self.assertRaises(RuntimeError):
            retry(rid, self.admin)
        self.db.add_artifact(rid, 'analysis_report', 'report.md')
        self.db.update_request(rid, status='cancelled')
        with self.assertRaises(RuntimeError):
            retry(rid, self.admin)

    def test_cancelled_task_is_not_synced(self):
        from app.orchestrator import Worker
        from app.services.task_cancellation import TaskCancelled
        rid = self.request(status='cancelled')
        with patch('app.orchestrator.TfsClient') as tfs:
            with self.assertRaises(TaskCancelled):
                Worker()._complete_analysis(rid, self.result)
            tfs.assert_not_called()

    def test_api_recovery_permission_and_cloud_claim(self):
        from fastapi.testclient import TestClient
        from app.main import app, current_user
        from app.config import settings
        rid = self.request()
        self.db.update_request(rid, runner_id='test-sync-runner')
        app.dependency_overrides[current_user] = lambda: self.admin
        self.addCleanup(lambda: app.dependency_overrides.pop(current_user, None))
        with TestClient(app) as client:
            response = client.post(f'/api/requests/{rid}/retry')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['id'], rid)
            response = client.get('/api/runner/pollable', params={'runner_id': 'test-sync-runner'}, headers={'Authorization': f'Bearer {settings.runner_token}'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['request']['id'], rid)
            app.dependency_overrides[current_user] = lambda: {'id': -1, 'role': 'pm'}
            self.assertEqual(client.post(f'/api/requests/{rid}/retry-analysis-sync').status_code, 403)
