from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

def load_app():
    # Discovery must not freeze app settings before the API fixtures set test env.
    global db, controls, prepare, execute, git, run_command, TaskCancelled, interrupt_on_cancel, sanitized_process_env
    from app import db
    from app.services import task_controls as controls
    from app.services.code_rollback import prepare, execute
    from app.services.delivery import git, run_command
    from app.services.task_cancellation import TaskCancelled, interrupt_on_cancel
    from app.services.process_env import sanitized_process_env


class TaskControlTests(unittest.TestCase):
    def setUp(self):
        load_app()
        temp = tempfile.TemporaryDirectory(prefix='autodev-controls-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        cfg = SimpleNamespace(data_dir=self.root, db_path=self.root / 'test.db')
        patcher = patch.object(db, 'settings', cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        with db.transaction() as conn:
            conn.executescript(db.SCHEMA)
            conn.execute("INSERT INTO users(id,username,display_name,email,password_hash,role,created_at) VALUES(1,'test','Test','x@example.invalid','unused','admin',?)", (db.utc_now(),))
            conn.execute("INSERT INTO projects(project_key,name,delivery_mode,tfs_collection_url,tfs_project,created_at,updated_at) VALUES('fixture','Fixture','local_package','https://example.invalid','Fixture',?,?)", (db.utc_now(), db.utc_now()))
        self.actor = {'id': 1, 'role': 'admin'}
        self.project = db.row('SELECT * FROM projects LIMIT 1')

    def request(self, number=1, status='developing'):
        request_id = db.create_delivery_request(self.project, 1, number, 'local_package', [])
        db.update_request(request_id, status=status)
        return request_id

    def test_cancel_is_idempotent_and_late_completion_cannot_resurrect(self):
        rid = self.request()
        first = controls.queue(rid, 'cancel', self.actor)
        self.assertEqual(first['id'], controls.queue(rid, 'cancel', self.actor)['id'])
        db.update_request(rid, status='delivered', commit_hash='a' * 40)
        self.assertEqual(db.request_detail(rid)['status'], 'cancelled')
        self.assertIsNone(controls.claim('yangtao-pc', [rid]))
        self.assertEqual(controls.claim('yangtao-pc', [])['id'], first['id'])

    def test_restart_new_workspace_and_idempotence(self):
        rid = self.request()
        db.update_request(rid, codex_thread_id='old-session', repository_states=[{'worktree_path': 'old'}], commit_hash='a' * 40)
        control = controls.queue(rid, 'restart', self.actor)
        with self.assertRaises(RuntimeError):
            controls.restart(control['id'])
        controls.claim('yangtao-pc', [])
        new_id = controls.restart(control['id'])
        fresh = db.request_detail(new_id)
        self.assertEqual(new_id, controls.restart(control['id']))
        self.assertNotEqual(new_id, rid)
        self.assertEqual(fresh['status'], 'queued')
        self.assertIsNone(fresh['codex_thread_id'])
        self.assertIsNone(fresh['commit_hash'])
        self.assertEqual(fresh['repository_states'], [])
        self.assertEqual(fresh['repair_context']['_restart_source_id'], rid)
        self.assertEqual(len(fresh['steps']), 7)

    def test_permissions_and_completed_constraints(self):
        rid = self.request()
        with self.assertRaises(PermissionError):
            controls.queue(rid, 'cancel', {'id': 2, 'role': 'pm'})
        db.update_request(rid, status='delivered')
        for action in ['restart', 'cancel', 'rollback']:
            with self.assertRaises(RuntimeError):
                controls.queue(rid, action, self.actor)

    def test_conflicting_actions_and_disabled_project(self):
        rid = self.request()
        controls.queue(rid, 'cancel', self.actor)
        with self.assertRaises(RuntimeError):
            controls.queue(rid, 'restart', self.actor)
        control = controls.claim('yangtao-pc', [])
        controls.update(control['id'], status='completed', message='done', result={})
        with db.transaction() as conn:
            conn.execute('UPDATE projects SET enabled=0')
        with self.assertRaises(RuntimeError):
            controls.queue(rid, 'restart', self.actor)

    def test_rollback_waits_for_project_then_blocks_new_work(self):
        from app.store import LocalStore
        delivered = self.request(status='delivered')
        db.update_request(delivered, repository_states=[{'changed_files': ['a.js'], 'commit_hash': 'a' * 40}])
        control = controls.queue(delivered, 'rollback', self.actor)
        active = self.request(2)
        self.assertIsNone(controls.claim('yangtao-pc', []))
        db.update_request(active, status='failed')
        self.assertEqual(controls.claim('yangtao-pc', [])['id'], control['id'])
        self.request(3, 'queued')
        self.assertIsNone(LocalStore().next_queued())
        controls.update(control['id'], status='waiting_merge', message='waiting', result={})
        with db.transaction() as conn:
            conn.execute('UPDATE request_controls SET next_poll_at=NULL WHERE id=?', (control['id'],))
        # Queued arrivals must not deadlock polling the PR they are waiting for.
        self.assertEqual(controls.claim('yangtao-pc', [])['id'], control['id'])

    def test_interrupted_rollback_is_not_replayed(self):
        rid = self.request(status='delivered')
        db.update_request(rid, repository_states=[{'changed_files': ['a.js'], 'commit_hash': 'a' * 40}])
        control = controls.queue(rid, 'rollback', self.actor)
        controls.claim('yangtao-pc', [])
        self.assertIsNone(controls.claim('yangtao-pc', [rid]))
        self.assertIsNone(controls.claim('yangtao-pc', []))
        self.assertEqual(db.request_detail(rid)['controls'][0]['status'], 'failed')
        with self.assertRaises(RuntimeError):
            controls.queue(rid, 'rollback', self.actor)

    def test_interrupted_cancel_reclaims_idempotently(self):
        rid = self.request()
        control = controls.queue(rid, 'cancel', self.actor)
        controls.claim('yangtao-pc', [])
        self.assertEqual(controls.claim('yangtao-pc', [])['id'], control['id'])

    def test_completed_rollback_deprecates_experience(self):
        rid = self.request(status='delivered')
        db.update_request(rid, repository_states=[{'changed_files': ['a.js'], 'commit_hash': 'a' * 40}])
        control = controls.queue(rid, 'rollback', self.actor)
        controls.update(control['id'], status='completed', message='rolled back', result={})
        self.assertEqual(db.row('SELECT status FROM project_experiences WHERE request_id=?', (rid,))['status'], 'deprecated')

    def test_worker_waits_for_old_task_before_restart(self):
        from app.orchestrator import Worker
        from app.store import LocalStore
        rid = self.request()
        controls.queue(rid, 'restart', self.actor)
        worker = Worker(store=LocalStore(), max_concurrency=1)
        worker._set_active(rid, True)
        self.assertFalse(worker._process_control())
        worker._set_active(rid, False)
        self.assertTrue(worker._process_control())
        result = db.request_detail(rid)['controls'][0]
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(db.request_detail(result['result']['new_request_id'])['status'], 'queued')

    def test_runner_control_routes_require_authentication(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.config import settings
        rid = self.request()
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertEqual(client.get(f'/api/runner/requests/{rid}/status').status_code, 401)
        self.assertEqual(client.post('/api/runner/controls/claim', json={'runner_id': 'yangtao-pc'}).status_code, 401)
        original = settings.runner_token
        object.__setattr__(settings, 'runner_token', 'controls-fixture-token')
        self.addCleanup(lambda: object.__setattr__(settings, 'runner_token', original))
        headers = {'Authorization': 'Bearer controls-fixture-token'}
        control = controls.queue(rid, 'restart', self.actor)
        response = client.post('/api/runner/controls/claim', json={'runner_id': 'yangtao-pc', 'active_ids': []}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['control']['id'], control['id'])
        response = client.post(f"/api/runner/controls/{control['id']}/restart", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(client.get(f'/api/runner/requests/{rid}/status', headers=headers).json()['status'], 'cancelled')


class RollbackGitTests(unittest.TestCase):
    def setUp(self):
        load_app()
        temp = tempfile.TemporaryDirectory(prefix='autodev-revert-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-b', 'dev')
        git(self.repo, 'config', 'user.name', 'Fixture')
        git(self.repo, 'config', 'user.email', 'fixture@example.invalid')
        self.commit('business.txt', 'before\n', 'baseline')
        self.remote = self.root / 'origin.git'
        git(self.repo, 'clone', '--bare', str(self.repo), str(self.remote))
        git(self.repo, 'remote', 'add', 'origin', str(self.remote))
        self.original = self.commit('business.txt', 'after\n', 'feat(#123): current requirement')
        self.later = self.commit('other.txt', 'another requirement\n', 'other requirement')
        git(self.repo, 'push', 'origin', 'dev')

    def commit(self, path, content, message):
        (self.repo / path).write_text(content, encoding='utf-8')
        git(self.repo, 'add', '--all')
        git(self.repo, 'commit', '-m', message)
        return git(self.repo, 'rev-parse', 'HEAD')

    def prepare(self, commit=None):
        return prepare(self.repo, 'dev', commit or self.original, self.root / 'rollback', 'codex/rollback-fixture', 'revert(#123): fixture')

    def test_reverts_only_this_requirement_preserves_later_work(self):
        result = self.prepare()
        worktree = Path(result['worktree_path'])
        self.assertEqual((worktree / 'business.txt').read_text(), 'before\n')
        self.assertEqual((worktree / 'other.txt').read_text(), 'another requirement\n')
        self.assertEqual(git(self.remote, 'rev-parse', 'dev'), self.later)
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.later)

    def test_multiple_task_commits_reversed_in_order(self):
        second = self.commit('business.txt', 'after second change\n', 'feat(#123): followup')
        git(self.repo, 'push', 'origin', 'dev')
        result = self.prepare([self.original, second])
        self.assertEqual((Path(result['worktree_path']) / 'business.txt').read_text(), 'before\n')
        self.assertTrue((Path(result['worktree_path']) / 'other.txt').exists())

    def test_conflict_retains_scene_without_push(self):
        tip = self.commit('business.txt', 'later conflicting requirement\n', 'later')
        git(self.repo, 'push', 'origin', 'dev')
        with self.assertRaisesRegex(RuntimeError, '冲突'):
            self.prepare()
        self.assertEqual(git(self.remote, 'rev-parse', 'dev'), tip)
        self.assertTrue((self.root / 'rollback').exists())

    def test_invalid_and_root_commits_do_not_create_worktree(self):
        for commit in ['HEAD', 'a' * 40, git(self.repo, 'rev-list', '--max-parents=0', 'HEAD')]:
            with self.assertRaises(RuntimeError):
                self.prepare(commit)
            self.assertFalse((self.root / 'rollback').exists())

    def test_merge_commit_reverts_first_parent_only(self):
        git(self.repo, 'checkout', '-b', 'feature/test')
        self.commit('feature.txt', 'feature\n', 'feature')
        git(self.repo, 'checkout', 'dev')
        git(self.repo, 'merge', '--no-ff', 'feature/test', '-m', 'merge requirement')
        merged = git(self.repo, 'rev-parse', 'HEAD')
        git(self.repo, 'push', 'origin', 'dev')
        result = self.prepare(merged)
        self.assertFalse((Path(result['worktree_path']) / 'feature.txt').exists())
        self.assertTrue((Path(result['worktree_path']) / 'other.txt').exists())

    def test_execute_local_rollback_pushes_inverse_not_history_rewrite(self):
        detail = {'id': 'source123', 'work_item_id': 123, 'delivery_mode': 'local_package',
                  'policy_snapshot': {'repository_path': str(self.repo), 'base_branch': 'dev', 'tfs_collection_url': 'https://example.invalid'},
                  'repository_states': [{'repository_path': str(self.repo), 'name': 'repo', 'base_branch': 'dev',
                     'commit_hash': self.later, 'delivered_commits': [self.original], 'status': 'base_pushed', 'changed_files': ['business.txt']}]}
        control = {'id': 'fixture123', 'result': {}}
        cfg = SimpleNamespace(tfs_pat='', data_dir=self.root)
        with patch('app.services.code_rollback.settings', cfg), patch('app.services.code_rollback.TfsClient'):
            status, message, result = execute(control, detail, Mock())
            self.assertEqual(status, 'completed')
            tip = git(self.remote, 'rev-parse', 'dev')
            self.assertEqual(git(self.remote, 'rev-parse', tip + '^'), self.later)
            self.assertEqual(git(self.remote, 'show', 'dev:business.txt'), 'before')
            self.assertEqual(git(self.remote, 'show', 'dev:other.txt'), 'another requirement')
            self.assertEqual(execute(control, detail, Mock())[0], 'completed')
            self.assertEqual(git(self.remote, 'rev-parse', 'dev'), tip)

    def test_legacy_build_tip_is_not_mistaken_for_task_commit(self):
        detail = {'id': 'source123', 'work_item_id': 123, 'delivery_mode': 'local_package',
                  'policy_snapshot': {'repository_path': str(self.repo), 'base_branch': 'dev', 'tfs_collection_url': 'https://example.invalid'},
                  'repository_states': [{'repository_path': str(self.repo), 'commit_hash': self.later, 'status': 'base_pushed', 'changed_files': ['business.txt']}]}
        with patch('app.services.code_rollback.TfsClient'), self.assertRaisesRegex(RuntimeError, '精确提交映射'):
            execute({'id': 'fixture'}, detail, Mock())

    def test_multi_repository_validation_uses_shared_root_and_changed_side(self):
        detail = {'id': 'source123', 'work_item_id': 123, 'delivery_mode': 'local_package',
                  'policy_snapshot': {'repository_path': str(self.repo), 'repository_paths': [str(self.repo), str(self.root/'unchanged')],
                     'base_branch': 'dev', 'tfs_collection_url': 'https://example.invalid', 'verification_command': 'fixture-verify'},
                  'repository_states': [{'repository_path': str(self.repo), 'name': 'repo', 'commit_hash': self.original,
                     'delivered_commits': [self.original], 'status': 'base_pushed', 'changed_files': ['business.txt']}]}
        with patch('app.services.code_rollback.settings', SimpleNamespace(tfs_pat='', data_dir=self.root)), \
                patch('app.services.code_rollback.TfsClient'), patch('app.services.code_rollback.run_command') as verify:
            execute({'id': 'fixture'}, detail, Mock())
        args, kwargs = verify.call_args
        self.assertEqual(args, ('fixture-verify', self.root/'rollback-workspaces'/'fixture'))
        self.assertTrue((args[1]/'repo'/'.git').exists())
        self.assertEqual(json.loads(kwargs['env_overrides']['AUTODEV_CHANGED_REPOSITORIES']), ['repo'])

    def test_local_package_baseline_requires_pushed_head(self):
        from app.orchestrator import Worker
        Worker._verify_build_baseline(self.repo, {'base_branch': 'dev', 'base_branch_commit': self.later})
        self.commit('not-pushed.txt', 'unpublished\n', 'unpublished')
        with self.assertRaisesRegex(RuntimeError, '不一致'):
            Worker._verify_build_baseline(self.repo, {'base_branch': 'dev', 'base_branch_commit': self.later})

    def test_delivery_sync_records_only_own_commits(self):
        from app.orchestrator import Worker
        git(self.repo, 'checkout', '-b', 'feature/current')
        own = self.commit('new.txt', 'own\n', 'feat(#456): task')
        git(self.repo, 'push', '-u', 'origin', 'feature/current')
        recorded = []
        tip = Worker._sync_local_package_repository(self.repo, repository_name='repo', feature_branch='feature/current',
                    target_branch='dev', changed=True, git_env=sanitized_process_env(), on_delivery_commits=recorded.append)
        self.assertEqual(recorded, [[own]])
        self.assertEqual(tip, git(self.remote, 'rev-parse', 'dev'))


class CancellationTests(unittest.TestCase):
    def setUp(self):
        load_app()

    def test_codex_interrupts_active_turn(self):
        interrupted = threading.Event()
        handle = Mock()
        handle.interrupt.side_effect = interrupted.set
        with interrupt_on_cancel(handle, lambda: True, interval=.01):
            self.assertTrue(interrupted.wait(1))
        handle.interrupt.assert_called_once()

    def test_no_interrupt_after_normal_completion(self):
        handle = Mock()
        with interrupt_on_cancel(handle, lambda: False, interval=.01):
            pass
        handle.interrupt.assert_not_called()

    def test_build_cancel_terminates_owned_process(self):
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise TaskCancelled('test cancellation')
        command = f'"{sys.executable}" -c "import time; time.sleep(25)"'
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(TaskCancelled):
            run_command(command, Path(temp), cancel_check=check)
