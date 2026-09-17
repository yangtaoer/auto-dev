import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class BuildLogTests(unittest.TestCase):
    def test_ansi_removed_and_tail_retained_within_api_limit(self):
        from app.services.command_logs import bounded_log
        value = bounded_log('\x1b[31;1m前端依赖安装失败\x1b[0m\n' + 'x' * 5000 + '\n退出码 -1073740791')
        self.assertLessEqual(len(value), 2000)
        self.assertNotIn('\x1b', value)
        self.assertIn('前端依赖安装失败', value)
        self.assertIn('-1073740791', value)

    @unittest.skipUnless(os.name == 'nt', 'Windows code page')
    def test_mixed_java_ansi_and_powershell_utf8(self):
        from app.services.command_logs import decode_output
        with patch('ctypes.windll.kernel32.GetACP', return_value=936):
            value = decode_output('编译成功\n'.encode('gbk') + '前端异常\n'.encode('utf-8'))
        self.assertEqual(value, '编译成功\n前端异常\n')

    def test_complete_log_saved_and_summary_is_small(self):
        from app.config import settings
        from app.services.command_logs import command_failure
        with tempfile.TemporaryDirectory() as folder:
            original = settings.data_dir
            object.__setattr__(settings, 'data_dir', Path(folder))
            try:
                exc = command_failure(1, ('信息\n' * 3000).encode(), b'\x1b[31mERROR native crash\x1b[0m', '../unsafe')
                self.assertLessEqual(len(str(exc)), 1800)
                self.assertIn('ERROR native crash', str(exc))
                logs = list(Path(folder).rglob('*.log'))
                self.assertEqual(len(logs), 1)
                self.assertGreater(logs[0].stat().st_size, 9000)
                self.assertNotIn('\x1b', logs[0].read_text(encoding='utf-8'))
            finally:
                object.__setattr__(settings, 'data_dir', original)

    def test_remote_event_and_step_are_bounded_before_http(self):
        from app.store import RemoteStore
        s = RemoteStore('https://example.invalid', 'test', 'runner')
        self.addCleanup(s.client.close)
        with patch.object(s, '_request') as request, patch.object(s, '_json'):
            s.add_event('id', 'failure', 'x' * 5000 + 'END')
            self.assertLessEqual(len(request.call_args.kwargs['json']['message']), 2000)
            s.update_step('id', 'deliver', 'failed', 'x' * 5000 + 'END')
            self.assertLessEqual(len(request.call_args.kwargs['json']['message']), 2000)

    def test_failed_event_does_not_prevent_terminal_email(self):
        from app.orchestrator import Worker
        store = Mock(remote=False)
        store.get_status.return_value = 'building'
        store.detail.return_value = {'current_step': 'deliver'}
        store.add_event.side_effect = RuntimeError('event API unavailable')
        worker = Worker(store=store)
        with patch.object(worker, '_send_terminal_email') as notify:
            worker._fail('id', RuntimeError('\x1b[31m' + 'x' * 5000))
            notify.assert_called_once_with('id')
        self.assertLessEqual(len(store.update_request.call_args.kwargs['error_message']), 1800)

    def test_resume_only_builds_existing_pushed_commit(self):
        from app.orchestrator import Worker
        store = Mock(remote=False)
        detail = {'id': 'id', 'status': 'failed', 'current_step': 'deliver', 'task_type': 'development',
                  'delivery_mode': 'local_package', 'work_item_id': 1, 'policy_snapshot': {},
                  'repository_states': [{'status': 'base_pushed', 'base_branch_commit': 'a' * 40, 'worktree_path': 'worktree'}]}
        store.detail.return_value = detail
        store.get_status.return_value = 'failed'
        worker = Worker(store=store)
        with patch.object(worker, '_verify_build_baseline') as verify, patch.object(worker, '_deliver_local_package') as build, patch.object(worker, 'run_request') as model:
            worker.resume_local_delivery('id')
            verify.assert_called_once()
            build.assert_called_once()
            model.assert_not_called()
        detail['status'] = 'cancelled'
        with self.assertRaises(RuntimeError):
            worker.resume_local_delivery('id')

    def test_stale_baseline_recovery_does_not_mutate_task(self):
        from app.orchestrator import Worker
        store = Mock(remote=False)
        store.detail.return_value = {'status': 'failed', 'current_step': 'deliver', 'delivery_mode': 'local_package',
                                    'repository_states': [{'status': 'base_pushed', 'base_branch_commit': 'a', 'worktree_path': 'worktree'}]}
        worker = Worker(store=store)
        with patch.object(worker, '_verify_build_baseline', side_effect=RuntimeError('目标分支已变化')):
            with self.assertRaises(RuntimeError):
                worker.resume_local_delivery('id')
        store.update_request.assert_not_called()


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell unavailable')
class FrontendBuildTests(unittest.TestCase):
    def test_isolated_install_retry_and_source_preservation(self):
        script = Path(__file__).resolve().parents[1] / 'local-runner/project-scripts/bazhong-frontend-build.ps1'
        for scenario in ('success', 'transient', 'invalid', 'compile'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                front = root / 'front'
                front.mkdir()
                manifest = b'{"name":"test","scripts":{"build":"vite build"}}'
                (front / 'package.json').write_bytes(manifest)
                (front / 'node_modules').mkdir()
                (front / 'node_modules/original').write_text('preserve')
                (front / 'dist').mkdir()
                (front / 'dist/old.html').write_text('old build')
                fake = root / 'fake.ps1'
                fake.write_text('''
$count = Join-Path $PSScriptRoot 'count.txt'
if ($args[0] -in @('ci','install')) {
    $n = if (Test-Path $count) {[int](Get-Content $count)} else {0}
    ($n+1) | Set-Content $count
    if ((Test-Path node_modules/original)) {throw 'source dependencies leaked into staging'}
    if ('SCENARIO' -eq 'transient' -and $n -eq 0) { $global:LASTEXITCODE=-1073740791; return }
    if ('SCENARIO' -eq 'invalid') { Write-Output 'ERESOLVE invalid dependency'; $global:LASTEXITCODE=1; return }
    New-Item -ItemType Directory node_modules -Force | Out-Null
    $global:LASTEXITCODE=0
} else {
    if ('SCENARIO' -eq 'compile') { Write-Output 'compile error'; $global:LASTEXITCODE=1; return }
    New-Item -ItemType Directory dist -Force | Out-Null
    'new build' | Set-Content dist/index.html
    $global:LASTEXITCODE=0
}
'''.replace('SCENARIO', scenario), encoding='utf-8')
                result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-File', str(script), '-FrontendRoot', str(front),
                                         '-CacheRoot', str(root / 'cache'), '-NodeDirectory', str(root), '-NpmRunner', str(fake)],
                                        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
                self.assertEqual(result.returncode == 0, scenario in ('success', 'transient'), result.stdout + result.stderr)
                self.assertEqual((front / 'package.json').read_bytes(), manifest)
                self.assertEqual((front / 'node_modules/original').read_text(), 'preserve')
                self.assertEqual(int((root / 'count.txt').read_text(encoding='utf-8-sig')), 2 if scenario == 'transient' else 1)
                self.assertFalse(any((root / 'cache').glob('build-*/node_modules')))
                if scenario in ('success', 'transient'):
                    self.assertTrue((front / 'dist/index.html').is_file())
                    self.assertFalse((front / 'dist/old.html').exists())
                    self.assertEqual(len(list((root / 'cache').glob('build-*/previous-dist/old.html'))), 1)
                else:
                    self.assertTrue((front / 'dist/old.html').is_file())
