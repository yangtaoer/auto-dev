from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile


def jar_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return stream.getvalue()


class ReleaseIntegrityTests(unittest.TestCase):
    def setUp(self):
        # Discovery must not freeze application settings before the workflow fixtures.
        from app.services import tfs_release_integrity as integrity, tfs_pipeline_runner as runner
        self.integrity, self.runner = integrity, runner
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = Mock(base_url="https://tfs.invalid/DefaultCollection", tfs_project="DCS")
        self.dep = {"purpose": "dependency", "stage": 1, "repository": "dcsd-example", "definitionId": 10,
                    "sourceBranch": "refs/heads/dev", "sourceVersion": "a" * 40, "tfsProject": "DCS"}
        self.main = {"purpose": "delivery", "stage": 2, "repository": "starter", "definitionId": 20,
                     "sourceBranch": "refs/heads/dev", "sourceVersion": "b" * 40, "tfsProject": "DCS", "expectedArtifact": "drop"}
        self.project = {"releaseIntegrity": {"version": 1, "artifactUrlPrefix": "https://nexus.invalid/snapshots/"}}
        self.guard = integrity.ReleaseIntegrity(self.project, [self.dep, self.main], {"DCS": self.client}, self.root)
        self.module = jar_bytes({"META-INF/resources/feature.js": b"new feature", "example/Service.class": b"original class"})
        self.proof = {"repository": self.dep["repository"], "definitionId": 10, "buildId": 101,
                      "sourceVersion": "a" * 40, "moduleSha256": hashlib.sha256(self.module).hexdigest(),
                      "manifest": integrity.module_manifest(self.module)}

    def build(self, **updates):
        return {"id": 101, "definition": {"id": 10}, "sourceBranch": "refs/heads/dev", "sourceVersion": "a" * 40,
                "status": "completed", "result": "succeeded", **updates}

    def package(self, module):
        p = self.root / "dcsd-springboot-example.jar"
        p.write_bytes(jar_bytes({"BOOT-INF/lib/dcsd-example-1.0-SNAPSHOT.jar": module}))
        return p

    def test_guangan_workflow_explicitly_covers_all_eight_modules_before_main(self):
        catalog = {"projects": [{"standardName": "广安网络发令", "pipelines": [{"definitionId": 5481, "deploymentAudit": {"checkedAt": "retained"}}]},
                                {"standardName": "other", "pipelines": [{"definitionId": 99}]}]}
        result = self.integrity.apply_workflows(catalog)
        pipelines = result["projects"][0]["pipelines"]
        self.assertEqual(len([p for p in pipelines if p["purpose"] == "dependency" and p["stage"] == 1]), 8)
        self.assertEqual([p["definitionId"] for p in pipelines if p["purpose"] == "delivery"], [5481])
        self.assertEqual(pipelines[-1]["stage"], 2)
        self.assertEqual(pipelines[-1]["deploymentAudit"]["status"], "linked-releases-found")
        self.assertIn(579, [r["releaseDefinitionId"] for r in pipelines[-1]["deploymentAudit"]["releases"]])
        self.assertIn(9913, [p["definitionId"] for p in pipelines])
        self.assertEqual(result["projects"][1]["pipelines"], [{"definitionId": 99}])

    def test_build_reuse_requires_exact_commit_branch_and_parameters(self):
        self.assertTrue(self.integrity.build_matches(self.build(), self.dep))
        self.assertFalse(self.integrity.build_matches(self.build(sourceVersion="c" * 40), self.dep))
        self.assertFalse(self.integrity.build_matches(self.build(sourceBranch="refs/heads/other"), self.dep))
        self.dep["parameters"] = {"proof": "expected"}
        self.assertFalse(self.integrity.build_matches(self.build(parameters='{"proof":"old"}'), self.dep))
        self.assertTrue(self.integrity.build_matches(self.build(parameters='{"proof":"expected"}'), self.dep))

    def test_freeze_rejects_missing_required_commit_before_any_queue(self):
        self.guard.required = {"commits": {"dcsd-example": ["c" * 40]}}
        def read(method, url):
            if "/refs?" in url:
                return {"value": [{"name": "refs/heads/dev", "objectId": "a" * 40}]}
            if "/diffs/commits?" in url:
                return {"commonCommit": "d" * 40}
            return {"id": "repo-id"}
        self.client.request_json.side_effect = read
        with self.assertRaisesRegex(RuntimeError, "未包含需求提交"):
            self.guard.freeze()
        self.client.queue_build.assert_not_called()

    def test_freeze_includes_merged_requirements_instead_of_latest_success_only(self):
        self.guard.plans = [self.dep]
        self.guard.required = {"commits": {"dcsd-example": ["c" * 40]}}
        self.client.request_json.side_effect = [{"id": "repo-id"}, {"value": [{"name": "refs/heads/dev", "objectId": "a" * 40}]}, {"commonCommit": "c" * 40}]
        self.guard.freeze()
        self.assertEqual(self.dep["sourceVersion"], "a" * 40)
        self.assertEqual(self.dep["repositoryId"], "repo-id")

    def test_old_successful_dependency_is_not_reused_for_new_commit(self):
        self.client.recent_builds.return_value = [self.build(sourceVersion="c" * 40)]
        with patch.object(self.guard, "receipt") as receipt:
            self.assertIsNone(self.guard.reuse_dependency(self.client, self.dep))
        receipt.assert_not_called()

    def test_publication_receipt_missing_or_replaced_snapshot_causes_rebuild(self):
        self.client.recent_builds.return_value = [self.build()]
        self.client.get_build.return_value = self.build()
        with patch.object(self.guard, "receipt", side_effect=RuntimeError("missing publish receipt")):
            self.assertIsNone(self.guard.reuse_dependency(self.client, self.dep))
        with patch.object(self.guard, "read_log", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "发布 Maven JAR"):
                self.guard.receipt(self.client, self.dep, self.build())
        line = "Uploaded to snapshots: https://nexus.invalid/snapshots/com/dcsd-example/1.0-SNAPSHOT/dcsd-example-1.0-20260101.010101-1.jar (12 B)"
        metadata = b"<metadata><versioning><snapshotVersions><snapshotVersion><extension>jar</extension><value>1.0-20260102.010101-2</value></snapshotVersion></snapshotVersions></versioning></metadata>"
        with patch.object(self.guard, "read_log", return_value=[line]), patch.object(self.guard, "read_public", return_value=metadata):
            with self.assertRaisesRegex(RuntimeError, "不能复用"):
                self.guard.receipt(self.client, self.dep, self.build())

    def test_verified_dependency_is_reused_and_all_dependencies_are_required(self):
        self.client.recent_builds.return_value = [self.build()]
        self.client.get_build.return_value = self.build()
        with patch.object(self.guard, "receipt", return_value=self.proof):
            self.assertEqual(self.guard.reuse_dependency(self.client, self.dep)["id"], 101)
        with self.assertRaisesRegex(RuntimeError, "尚未全部发布"):
            self.guard.delivery_parameters()
        self.guard.proofs[10] = self.proof
        encoded = self.guard.delivery_parameters()[self.integrity.PARAMETER]
        self.assertEqual(json.loads(base64.b64decode(encoded))["modules"]["dcsd-example"]["sourceVersion"], "a" * 40)

    def test_encrypted_classes_are_allowed_only_with_identical_resources_and_class_set(self):
        transformed = jar_bytes({"META-INF/resources/feature.js": b"new feature", "example/Service.class": b"official encryption output"})
        result = self.integrity.verify_package(self.package(transformed), [self.proof])
        self.assertTrue(result[0]["verified"])
        old = jar_bytes({"META-INF/resources/feature.js": b"old feature", "example/Service.class": b"old class"})
        with self.assertRaisesRegex(RuntimeError, "资源与目标提交构建不一致"):
            self.integrity.verify_package(self.package(old), [self.proof])
        missing = jar_bytes({"META-INF/resources/feature.js": b"new feature"})
        with self.assertRaisesRegex(RuntimeError, "类目录不完整"):
            self.integrity.verify_package(self.package(missing), [self.proof])

    def test_repeated_archive_entries_are_rejected(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("a.js", b"old")
            with self.assertWarns(UserWarning):
                z.writestr("a.js", b"new")
        with self.assertRaisesRegex(RuntimeError, "重复"):
            self.integrity.module_manifest(b.getvalue())

    def test_historical_package_without_source_proof_cannot_deploy(self):
        with self.assertRaisesRegex(RuntimeError, "没有依赖校验信息"):
            self.guard.restore(self.client, self.main, self.build())

    def test_historical_proof_reuses_content_checked_cache_and_rejects_corruption(self):
        self.guard.proofs[10] = self.proof
        params = self.guard.delivery_parameters()
        cache = self.guard.cache / (self.proof["moduleSha256"] + ".json")
        cache.write_text(json.dumps(self.proof), encoding="utf-8")
        cache.with_suffix(".jar").write_bytes(self.module)
        build = self.build(definition={"id": 20}, sourceVersion="b" * 40, parameters=json.dumps(params))
        self.guard.restore(self.client, self.main, build)
        self.assertEqual(self.guard.proofs[10]["manifest"], self.proof["manifest"])
        cache.with_suffix(".jar").write_bytes(b"corrupt cache")
        with self.assertRaisesRegex(RuntimeError, "缓存损坏"):
            self.guard.restore(self.client, self.main, build)

    def test_gate_execution_requires_success_and_matching_receipt(self):
        self.guard.proofs[10] = self.proof
        self.main["parameters"] = self.guard.delivery_parameters()
        build = self.build(definition={"id": 20}, sourceVersion="b" * 40, parameters=json.dumps(self.main["parameters"]))
        self.client.request_json.return_value = {"records": []}
        with self.assertRaisesRegex(RuntimeError, "服务器端依赖校验"):
            self.guard.verify_gate_execution(self.client, self.main, build)
        timeline = {"records": [{"name": self.integrity.GATE_NAME, "result": "succeeded", "log": {"id": 5}}]}
        self.client.request_json.side_effect = [timeline, {"value": ["AUTODEV_MODULES_VERIFIED:wrong"]}]
        with self.assertRaisesRegex(RuntimeError, "缺少本轮依赖校验回执"):
            self.guard.verify_gate_execution(self.client, self.main, build)
        digest = hashlib.sha256(base64.b64decode(self.main["parameters"][self.integrity.PARAMETER])).hexdigest()
        self.client.request_json.side_effect = [timeline, {"value": ["AUTODEV_MODULES_VERIFIED:" + digest]}]
        self.guard.verify_gate_execution(self.client, self.main, build)

    def test_gate_definition_requires_real_script_and_source_sync(self):
        self.client.get_definition.return_value = {"repository": {"properties": {"skipSyncSource": "true"}}}
        with self.assertRaisesRegex(RuntimeError, "跳过了源码同步"):
            self.guard.validate_definition(self.client, self.main)
        self.client.get_definition.return_value = {"repository": {}, "build": [{"enabled": True, "inputs": {"goals": "clean package"}}]}
        with self.assertRaisesRegex(RuntimeError, "缺少有效"):
            self.guard.validate_definition(self.client, self.main)

    def test_queue_pins_source_and_never_reuses_wrong_active_commit(self):
        args = SimpleNamespace(force_new=False, poll_seconds=1, timeout_seconds=10)
        self.client.get_build.return_value = self.build(id=98, sourceVersion="c" * 40, status="inProgress")
        self.client.queue_build.return_value = self.build(id=102, status="notStarted")
        with patch.object(self.guard, "reuse_dependency", return_value=None), patch.object(self.runner, "wait_for_build") as wait:
            result, action = self.runner.guarded_build(self.client, self.dep, {"id": 98}, self.guard, args)
        wait.assert_called_once()
        self.client.queue_build.assert_called_once_with(10, "refs/heads/dev", source_version="a" * 40, parameters=None)
        self.assertEqual((result["id"], action), (102, "queued"))

    def test_main_build_request_contains_pinned_parameters(self):
        client = self.runner.TfsBuildClient("https://tfs.invalid", "DCS", "not-a-real-token")
        with patch.object(client, "request_json", return_value={"id": 1}) as post:
            client.queue_build(20, "refs/heads/dev", source_version="b" * 40, parameters={"proof": "frozen"})
        payload = post.call_args.args[2]
        self.assertEqual(payload["sourceVersion"], "b" * 40)
        self.assertEqual(json.loads(payload["parameters"]), {"proof": "frozen"})

    def test_guarded_build_cannot_succeed_without_package_integrity(self):
        result = {"result": "succeeded", "artifactValid": True, "releaseIntegrityRequired": True}
        self.assertEqual(self.runner.build_result_exit_code(result), 6)
        result["releaseIntegrity"] = {"status": "verified"}
        self.assertEqual(self.runner.build_result_exit_code(result), 0)

    def test_service_rejects_success_without_full_proof(self):
        from app.services.pipeline_release import TfsPipelineReleaseService, TfsPipelineReleaseError
        fake = SimpleNamespace(tfs_pat="not-a-real-token", tfs_pipeline_timeout_seconds=10)
        service = TfsPipelineReleaseService(script_path=self.root / "fake.py")
        result = {"result": "succeeded", "expectedArtifactFound": True, "artifactsUrl": "https://tfs.invalid/artifact"}
        with patch("app.services.pipeline_release.settings", fake), patch.object(service, "_invoke", return_value=result):
            with self.assertRaisesRegex(TfsPipelineReleaseError, "不能标记为已交付"):
                service.run("广安网络发令", required_commits={"dcsd-example": ["a" * 40]}, require_integrity=True)

    def test_coalesced_release_keeps_commits_from_every_member(self):
        from app.services.release_policy import commits_for_release, existing_proof, requires_integrity, verified
        details = [{"repository_states": [{"name": "a", "commit_hash": "a" * 40, "merge_commit": "b" * 40}]},
                   {"repository_states": [{"name": "a", "commit_hash": "c" * 40}]}]
        self.assertEqual(commits_for_release(details), {"a": ["a" * 40, "b" * 40, "c" * 40]})
        self.assertTrue(requires_integrity({"project_key": "guangan-network-command"}))
        self.assertFalse(requires_integrity({"project_key": "guangan-network-command", "simulation_mode": True}))
        self.assertFalse(verified({"releaseIntegrity": {"status": "verified"}}))
        with self.assertRaisesRegex(RuntimeError, "不能继续"):
            existing_proof({"events": []}, "https://tfs.invalid/old")

    def test_bridge_preserves_original_and_exports_versioned_runner(self):
        from app.services.pipeline_bridge import install_pipeline_bridge, MARKER
        skill = self.root / "skills/tfs-run-pipeline"
        (skill / "scripts").mkdir(parents=True)
        original = skill / "scripts/run_tfs_pipeline.py"
        original.write_text("# original skill\n", encoding="utf-8")
        project = self.root / "platform"
        runner = project / "app/services/tfs_pipeline_runner.py"
        runner.parent.mkdir(parents=True)
        runner.write_text("def main():\n    print('VERIFIED_RUNNER_ENTRY')\n    return 0\n", encoding="utf-8")
        self.assertTrue(install_pipeline_bridge(project, skill))
        first = original.read_bytes()
        self.assertTrue(first.decode().startswith(MARKER))
        self.assertEqual(len(list(original.parent.glob("run_tfs_pipeline.before-autodev-*.py"))), 1)
        self.assertTrue(install_pipeline_bridge(project, skill))
        self.assertEqual(first, original.read_bytes())
        result = subprocess.run([sys.executable, "-B", str(original)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERIFIED_RUNNER_ENTRY", result.stdout)

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK not available")
    def test_real_jdk_gate_blocks_old_module_before_publishing(self):
        path = self.package(self.module)
        manifest = {"version": 1, "modules": {"dcsd-example": {"sha256": hashlib.sha256(self.module).hexdigest()}}}
        source = self.integrity.GATE_FILE.with_name("AutoDevVerifyModules.java")
        compiled = subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-source", "8", "-target", "8", "-Xlint:-options",
                                   "-d", str(self.root), str(source)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        def run():
            encoded = base64.b64encode(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).decode()
            return subprocess.run([shutil.which("java"), "-cp", str(self.root), "AutoDevVerifyModules", str(self.root), encoded],
                                   capture_output=True, text=True, encoding="utf-8")
        success = run()
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertIn("AUTODEV_MODULES_VERIFIED:", success.stdout)
        self.package(jar_bytes({"META-INF/resources/feature.js": b"stale code"}))
        failed = run()
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("AUTODEV_MODULES_VERIFIED:", failed.stdout)

    def test_failure_summary_prefers_real_gate_error_over_cleanup_noise(self):
        from app.services.pipeline_release import TfsPipelineReleaseService
        text = 'failed build\n##[error]Missing JDK executable\nCleaning credentials\nCompleted job'
        summary = TfsPipelineReleaseService._diagnostic_summary(text)
        self.assertIn('Missing JDK executable', summary)
        self.assertNotIn('Cleaning credentials', summary)


class VerifiedReleaseRecordTests(unittest.TestCase):
    def setUp(self):
        from app import db
        from app.config import settings
        from app.services.verified_release import record
        self.db, self.record = db, record
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        previous = {k: getattr(settings, k) for k in ("data_dir", "seed_demo", "environment", "worker_enabled")}
        for k, v in {"data_dir": Path(self.temporary.name), "seed_demo": True, "environment": "development", "worker_enabled": False}.items():
            object.__setattr__(settings, k, v)
        self.addCleanup(lambda: [object.__setattr__(settings, k, v) for k, v in previous.items()])
        db.init_db()
        self.project = db.row("SELECT * FROM projects LIMIT 1")
        self.user = db.row("SELECT * FROM users WHERE role='admin' LIMIT 1")
        self.rid = self.request(101)
        self.old = db.add_artifact(self.rid, "release_artifact", "old package", external_url="http://dev.tellhowsoft.com/DefaultCollection/DCS/_build/results?buildId=1")
        workflow = json.loads((Path(__file__).resolve().parents[1] / "app/services/release_workflows.json").read_text("utf-8"))["projects"]["广安网络发令"]
        modules = [{"repository": p["repository"], "definitionId": p["definitionId"], "buildId": p["definitionId"],
                    "sourceVersion": "a" * 40, "moduleSha256": "b" * 64, "verified": True}
                   for p in workflow["pipelines"] if p["purpose"] == "dependency"]
        self.result = {"definitionId": 5481, "buildId": 2, "pipelineName": "广安", "result": "succeeded", "expectedArtifactFound": True,
                       "artifactsUrl": "http://dev.tellhowsoft.com/DefaultCollection/DCS/_build/results?buildId=2&view=artifacts",
                       "releaseIntegrity": {"version": 1, "status": "verified", "preEncryptionModulesVerified": True,
                                            "postEncryptionResourcesVerified": True, "modules": modules}}

    def request(self, number, status="releasing"):
        rid = self.db.create_delivery_request(self.project, self.user["id"], number, "sichuan_auto_review", [], ["auto_release"])
        self.db.update_request(rid, status=status, policy_snapshot={"project_key": "guangan-network-command", "simulation_mode": False,
            "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection"})
        return rid

    def test_verified_repair_retires_old_download_without_deleting_history(self):
        first = self.record(self.rid, self.result)
        second = self.record(self.rid, dict(reversed(list(self.result.items()))))
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["artifact_id"], second["artifact_id"])
        self.assertEqual(self.db.row("SELECT kind FROM delivery_artifacts WHERE id=?", (self.old,))["kind"], "superseded_release_artifact")
        visible = self.db.rows("SELECT * FROM delivery_artifacts WHERE request_id=? AND kind='release_artifact'", (self.rid,))
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["external_url"], self.result["artifactsUrl"])
        self.assertEqual(self.db.row("SELECT status FROM delivery_requests WHERE id=?", (self.rid,))["status"], "releasing")

    def test_incomplete_evidence_and_other_host_cannot_replace_deliverable(self):
        bad = json.loads(json.dumps(self.result))
        bad["releaseIntegrity"]["modules"].pop()
        for result in (bad, {**self.result, "artifactsUrl": "https://other.invalid/_build/results?buildId=2"}):
            with self.assertRaises(ValueError):
                self.record(self.rid, result)
        self.assertEqual(self.db.row("SELECT kind FROM delivery_artifacts WHERE id=?", (self.old,))["kind"], "release_artifact")

    def test_interrupted_batch_recovers_full_verified_evidence(self):
        from app.services import release_coordination
        self.db.update_request(self.rid, status="capturing")
        claimed = release_coordination.claim(self.rid)
        self.assertEqual(claimed["action"], "run")
        self.db.update_request(self.rid, status="releasing")
        self.record(self.rid, self.result)
        self.db.update_request(self.rid, status="failed")
        other = self.request(102, "capturing")
        release_coordination.claim(other)
        batch = self.db.row("SELECT status,result FROM release_batches WHERE id=?", (claimed["batch_id"],))
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(json.loads(batch["result"])["releaseIntegrity"]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
