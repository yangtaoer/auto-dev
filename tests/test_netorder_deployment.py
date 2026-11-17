from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class NetorderDeploymentTests(unittest.TestCase):
    def setUp(self):
        # The deployed worker runs on Linux. Tests simulate its project lock.
        lock_api = SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4)
        with patch.dict(sys.modules, {"fcntl": lock_api}):
            self.worker = importlib.import_module("app.services.netorder_14_remote")
        lock_patch = patch.object(self.worker, "fcntl", lock_api)
        lock_patch.start()
        self.addCleanup(lock_patch.stop)

    def subnet(self, networks, port=28116):
        with patch.object(self.worker, "command", return_value="network-id"), \
                patch.object(self.worker, "docker_json", return_value=networks):
            return self.worker.free_subnet(port)

    def test_builtin_networks_with_null_or_missing_ipam_are_supported(self):
        for network in [{"IPAM": {"Config": None}}, {"IPAM": {"Config": []}},
                        {"IPAM": {}}, {"IPAM": None}, {}]:
            with self.subTest(network=network):
                self.assertEqual(self.subnet([network]), "10.214.116.0/24")

    def test_existing_ipv4_subnets_remain_excluded_with_builtin_networks(self):
        networks = [{"IPAM": {"Config": None}}, {"IPAM": {"Config": [
            {"Subnet": "10.214.116.0/24"}, {"Subnet": "10.214.32.0/23"},
            {"Subnet": "fd00::/80"}, {}]}}]
        self.assertEqual(self.subnet(networks), "10.214.34.0/24")

    def test_no_docker_networks_uses_preferred_subnet(self):
        with patch.object(self.worker, "command", return_value=""), \
                patch.object(self.worker, "docker_json") as inspect:
            self.assertEqual(self.worker.free_subnet(28116), "10.214.116.0/24")
        inspect.assert_not_called()

    def test_exhausted_subnet_range_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "No free configured Docker subnet"):
            self.subnet([{"IPAM": {"Config": [{"Subnet": "10.214.0.0/16"}]}}])

    def test_bootstrap_zookeeper_configuration_is_readable_after_privilege_drop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = root/'legacy-auth'
            auth.mkdir()
            (auth/'auth.jar').write_bytes(b'auth')
            plan = {'composeDirectory': str(root/'compose'), 'legacyAppDirectory': str(root),
                    'authDirectory': str(auth), 'authConfig': str(auth/'application.properties'),
                    'zooConfig': str(auth/'zoo.cfg'), 'releaseVariable': 'NC_RELEASE_PATH',
                    'httpPort': 28116, 'containerHttpPort': 28116, 'authPort': 28111,
                    'zkPort': 22211, 'requiredListeningPorts': [28116]}
            with patch.object(self.worker, 'text', return_value='server.port=28116\n'), \
                    patch.object(self.worker, 'java_secret', return_value='test-only'), \
                    patch.object(self.worker, 'free_subnet', return_value='10.214.116.0/24'), \
                    patch.object(self.worker, 'compose'), \
                    patch.object(self.worker, 'atomic', wraps=self.worker.atomic) as atomic:
                self.worker.bootstrap(plan, {'profile': 'sichuannc', 'directory': 'nc'},
                                      {'buildId': 1243166, 'jar': 'application.jar'})
            calls = [call for call in atomic.call_args_list if call.args[0].name == 'zoo.cfg']
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].args[2], 0o644)
            self.assertIn('dataLogDir=/data', calls[0].args[1])
            private = [call for call in atomic.call_args_list if call.args[0].name == '.env.jvm']
            self.assertEqual(len(private[0].args), 2)

    def test_shared_zookeeper_storage_keeps_snapshots_and_logs_in_data_directory(self):
        for log_setting in ['', 'dataLogDir=/data\n']:
            with self.subTest(log_setting=log_setting), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                legacy = root/'legacy/version-2'
                legacy.mkdir(parents=True)
                (legacy/'snapshot.1').write_bytes(b'snapshot')
                (legacy/'log.1').write_bytes(b'transactions')
                config = root/'zoo.cfg'
                config.write_text('dataDir=/data\n' + log_setting, encoding='utf-8')
                plan = {'composeDirectory': str(root/'compose'), 'zooConfig': str(config)}
                old_zk = {'Mounts': [{'Destination': '/data', 'Source': str(legacy.parent)}]}
                self.worker.clone_zk_state(plan, old_zk)
                self.assertEqual((root/'compose/data/zk/version-2/snapshot.1').read_bytes(), b'snapshot')
                self.assertEqual((root/'compose/data/zk/version-2/log.1').read_bytes(), b'transactions')
                self.assertFalse((root/'compose/data/zk-datalog').exists())

    def test_separate_zookeeper_storage_retains_both_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, logs = root/'legacy-data', root/'legacy-logs'
            data.mkdir()
            logs.mkdir()
            (data/'snapshot.1').write_bytes(b'snapshot')
            (logs/'log.1').write_bytes(b'transactions')
            config = root/'zoo.cfg'
            config.write_text('dataDir=/data\ndataLogDir=/datalog\n', encoding='utf-8')
            plan = {'composeDirectory': str(root/'compose'), 'zooConfig': str(config)}
            old_zk = {'Mounts': [{'Destination': '/data', 'Source': str(data)},
                                 {'Destination': '/datalog', 'Source': str(logs)}]}
            self.worker.clone_zk_state(plan, old_zk)
            self.assertTrue((root/'compose/data/zk/snapshot.1').is_file())
            self.assertTrue((root/'compose/data/zk-datalog/log.1').is_file())
            self.assertFalse((root/'compose/data/zk-datalog/snapshot.1').exists())

    def test_failed_bootstrap_preserves_compose_for_a_fresh_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            area = root/'nc'
            release = area/'compose/releases/1243166'
            release.mkdir(parents=True)
            jar = 'dcsd-springboot-sichuannc-example.jar'
            files = []
            for name in [jar, 'sjt_agent.jar', 'license.txt']:
                (release/name).write_bytes(b'official test package')
                files.append({'name': name, 'size': 21, 'sha256': hashlib.sha256(b'official test package').hexdigest()})
            package = {'buildId': 1243166, 'definitionId': 1956, 'jar': jar, 'files': files}
            target = {'standardName': '南充网络发令', 'directory': 'nc', 'profile': 'sichuannc', 'definitionId': 1956}
            compose_file = area/'compose/compose.yaml'
            plan = {'status': 'ready', 'mode': 'bootstrap-compose', 'containerName': 'nc-netorder',
                    'composeDirectory': str(compose_file.parent), 'composeFile': str(compose_file),
                    'legacyAppDirectory': str(area/'legacy'),
                    'applicationUrl': 'http://192.168.101.14:28116/netorder/'}

            def fail_after_writing_compose(*args):
                compose_file.write_text('{}', encoding='utf-8')
                raise RuntimeError('injected startup failure')

            with patch.object(self.worker, 'ROOT', root), \
                    patch.object(self.worker, 'preflight', return_value=plan), \
                    patch.object(self.worker, 'inspect', return_value=None), \
                    patch.object(self.worker, 'snapshot', return_value={}), \
                    patch.object(self.worker, 'command'), \
                    patch.object(self.worker, 'compose') as compose, \
                    patch.object(self.worker, 'runtime', return_value={'health': 'healthy'}), \
                    patch.object(self.worker, 'bootstrap', side_effect=fail_after_writing_compose) as bootstrap:
                result = self.worker.deploy({'root': str(root), 'target': target, 'package': package})
                bootstrap.side_effect = lambda *args: compose_file.write_text('{}', encoding='utf-8')
                retried = self.worker.deploy({'root': str(root), 'target': target, 'package': package})
            self.assertEqual(result['status'], 'failed-rolled-back')
            saved_compose = Path(result['backupDirectory'])/'failed-compose.yaml'
            self.assertEqual(saved_compose.read_text('utf-8'), '{}')
            self.assertTrue((release/jar).is_file())
            self.assertEqual(retried['status'], 'deployed', retried.get('reason'))
            starts = [call.args[1] for call in compose.call_args_list if call.args[1][0] == 'up']
            self.assertIn('--force-recreate', starts[0])

    def test_installing_worker_preserves_original_and_is_idempotent(self):
        from app.services.pipeline_bridge import install_deployment_worker
        with tempfile.TemporaryDirectory() as directory:
            project, skill = Path(directory)/"project", Path(directory)/"skill"
            source = project/"app/services/netorder_14_remote.py"
            target = skill/"scripts/netorder_14_remote.py"
            source.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            source.write_bytes(b"# maintained worker\n")
            target.write_bytes(b"# original worker\n")
            install_deployment_worker(project, skill)
            install_deployment_worker(project, skill)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            backups = list(target.parent.glob("netorder_14_remote.before-autodev-*.py"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"# original worker\n")

    def test_pipeline_displays_structured_deployment_failure(self):
        from app.services.pipeline_release import TfsPipelineReleaseError, TfsPipelineReleaseService
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory)/"pipeline.py"
            script.touch()
            service = TfsPipelineReleaseService(script_path=script)
            for rollback_error in [None, "previous service failed to start"]:
                with self.subTest(rollback_error=rollback_error):
                    result = {"buildId": 1243166, "result": "succeeded", "deployment14": {
                        "eligible": True,
                        "status": "failed-rollback-failed" if rollback_error else "failed-rolled-back",
                        "reason": "'NoneType' object is not iterable", "rollbackError": rollback_error}}
                    completed = SimpleNamespace(returncode=5, stdout=json.dumps({"deliveries": [result]}),
                                                stderr="download build 1243166: 188743680 bytes")
                    with patch("app.services.pipeline_release.subprocess.run", return_value=completed), \
                            patch.object(service, "_build_failure_diagnostics") as build_diagnostics:
                        with self.assertRaises(TfsPipelineReleaseError) as caught:
                            service._invoke("南充网络发令", [])
                    build_diagnostics.assert_not_called()
                    self.assertIn("NoneType", str(caught.exception))
                    self.assertIn(result["deployment14"]["status"], str(caught.exception))
                    self.assertNotIn("188743680 bytes", str(caught.exception))
                    self.assertFalse(caught.exception.transient)
                    self.assertEqual(caught.exception.result, result)
                    if rollback_error:
                        self.assertIn(rollback_error, str(caught.exception))
