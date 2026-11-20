import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class WorkbenchRecoveryTests(unittest.TestCase):
    def setUp(self):
        from app import db

        temp = tempfile.TemporaryDirectory(prefix="autodev-stage-tests-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        config = patch.object(db, "settings", SimpleNamespace(data_dir=root, db_path=root / "test.db"))
        config.start()
        self.addCleanup(config.stop)
        self.db = db
        with db.transaction() as conn:
            conn.executescript(db.SCHEMA)
            conn.execute("CREATE VIEW visible_delivery_requests AS SELECT * FROM delivery_requests WHERE routing_superseded_by IS NULL")
            for uid, role in ((1, "admin"), (2, "pm"), (3, "pm")):
                conn.execute("""INSERT INTO users(id,username,display_name,email,password_hash,role,created_at)
                    VALUES(?,?,?,?,?,?,?)""", (uid, f"user{uid}", f"用户{uid}", f"{uid}@example.invalid", "unused", role, db.utc_now()))
            conn.execute("""INSERT INTO projects(project_key,name,delivery_mode,tfs_collection_url,tfs_project,simulation_mode,created_at,updated_at)
                    VALUES('nanchong-network-command','南充网络发令','sichuan_auto_review',
                    'http://dev.tellhowsoft.com/DefaultCollection','DCS',0,?,?)""", (db.utc_now(), db.utc_now()))
        self.project = db.row("SELECT * FROM projects LIMIT 1")
        self.actor = {"id": 2, "role": "pm"}

    def create(self, item, owner=2, **fields):
        request_id = self.db.create_delivery_request(self.project, owner, item, "sichuan_auto_review", [], ["auto_release"])
        self.db.update_request(request_id, **fields)
        return request_id

    def failure(self):
        from app.services.stage_recovery import failed_release_metadata

        request_id = self.create(1001, status="failed", current_step="release")
        result = {"buildId": 123, "definitionId": 1956, "result": "succeeded", "expectedArtifactFound": True,
                  "resultsUrl": "http://dev.tellhowsoft.com/DefaultCollection/DCS/_build/results?buildId=123",
                  "deployment14": {"status": "failed-rolled-back", "eligible": True}}
        self.db.add_event(request_id, "release.failed", "部署失败", metadata=failed_release_metadata(result))
        return request_id, result

    def test_home_includes_assigned_acceptance_and_separates_manual_waiting_from_system_work(self):
        from app.services import project_learning, task_workbench

        assigned = self.create(1001, owner=1, status="delivered", acceptance_owner_id=2)
        private = self.create(1002, owner=3, status="delivered")
        supplement = self.create(1003, status="waiting_input", current_step="clarify")
        merge = self.create(1004, status="waiting_merge", current_step="deliver")
        developing = self.create(1005, status="developing", current_step="develop")
        online = {"yangtao-pc": True}
        lanes = task_workbench.load(self.actor, {}, online, str)
        self.assertEqual([item["id"] for item in lanes["acceptance"]], [assigned])
        self.assertEqual([item["id"] for item in lanes["supplement"]], [supplement])
        self.assertEqual([(item["id"], item["label"]) for item in lanes["waiting"]], [(merge, "查看 PR")])
        self.assertEqual([item["id"] for item in lanes["executing"]], [developing])
        self.assertNotIn(private, {row["id"] for group in lanes.values() for row in group})
        project_learning.get_acceptance(assigned)
        project_learning.submit_feedback(assigned, self.actor, [], overall_status="passed", idempotency_key="workbench-feedback")
        self.assertEqual(task_workbench.load(self.actor, {}, online, str)["acceptance"], [])

    def test_offline_work_is_in_the_system_queue_and_keeps_the_execution_checkpoint(self):
        from app.services import task_workbench

        request_id = self.create(1001, status="developing", current_step="develop")
        heartbeat = "2026-11-20T00:00:00+00:00"
        lanes = task_workbench.load(self.actor, {"yangtao-pc": {"last_seen_at": heartbeat}}, {}, str)
        self.assertEqual(lanes["executing"], [])
        item = lanes["queue"][0]
        self.assertEqual((item["id"], item["display_status"], item["confirmed_at"]), (request_id, "runner_disconnected", heartbeat))
        self.assertEqual(self.db.request_detail(request_id)["status"], "developing")

    def test_recovery_is_idempotent_and_older_runners_cannot_claim_it(self):
        from app.services import stage_recovery, task_controls

        request_id, _ = self.failure()
        with self.assertRaises(PermissionError):
            task_controls.queue(request_id, stage_recovery.DEPLOY, {"id": 3, "role": "pm"})
        first = task_controls.queue(request_id, stage_recovery.DEPLOY, self.actor)
        self.assertEqual(first["id"], task_controls.queue(request_id, stage_recovery.DEPLOY, self.actor)["id"])
        self.assertIsNone(task_controls.claim("yangtao-pc", []))
        claimed = task_controls.claim("yangtao-pc", [], supports_stage_recovery=True)
        self.assertEqual(first["id"], claimed["id"])
        self.assertEqual(claimed["result"]["plan"]["build_id"], 123)
        self.assertEqual(self.db.request_detail(request_id)["status"], stage_recovery.WAITING)
        self.assertIsNone(task_controls.claim("yangtao-pc", [], supports_stage_recovery=True))
        self.assertEqual(self.db.request_detail(request_id)["status"], "failed")

    def test_previous_build_deployment_and_sync_preserve_the_original_task(self):
        from app.orchestrator import Worker
        from app.services import stage_recovery, task_controls
        from app.store import LocalStore

        request_id, result = self.failure()
        task_controls.queue(request_id, stage_recovery.DEPLOY, self.actor)
        worker = Worker(LocalStore())
        receipt = {**result, "artifactsUrl": result["resultsUrl"] + "&view=artifacts",
                   "deployment14": {"status": "deployed", "eligible": True, "buildId": 123}}
        tfs = Mock()
        tfs.complete_delivery.return_value = {"state": "已解决"}
        with patch("app.orchestrator.TfsPipelineReleaseService") as service, patch("app.orchestrator.TfsClient", return_value=tfs), \
                patch.object(worker.artifacts, "validate_artifact_policy", return_value=[]), patch.object(worker, "_send_status_email"):
            service.return_value.retry_deployment.return_value = receipt
            self.assertTrue(worker._process_control())
            service.return_value.run.assert_not_called()
            service.return_value.retry_deployment.assert_called_once_with("南充网络发令", definition_id=1956, build_id=123)
            tfs.complete_delivery.assert_called_once()
        self.assertEqual(self.db.request_detail(request_id)["status"], "delivered")
        self.assertEqual(self.db.request_detail(request_id)["controls"][0]["status"], "completed")
        self.assertEqual(self.db.row("SELECT COUNT(*) n FROM delivery_requests")["n"], 1)

    def test_sync_resumes_from_recorded_tfs_completion(self):
        from app.orchestrator import Worker
        from app.services import stage_recovery, task_controls
        from app.store import LocalStore

        request_id = self.create(1001, status="failed", current_step="deliver")
        self.db.add_event(request_id, "delivery.sync_started", "交付物已核对")
        self.db.add_event(request_id, "tfs.delivery_completed", "已回写 TFS")
        task_controls.queue(request_id, stage_recovery.SYNC, self.actor)
        worker = Worker(LocalStore())
        with patch("app.orchestrator.TfsClient") as tfs, patch.object(worker.artifacts, "validate_artifact_policy", return_value=[]), \
                patch.object(worker, "_send_status_email") as notify:
            self.assertTrue(worker._process_control())
            tfs.assert_not_called()
            notify.assert_called_once_with(request_id, action_required=False)
        self.assertEqual(self.db.request_detail(request_id)["status"], "delivered")

    def test_old_release_record_cannot_restore_over_a_newer_request(self):
        from app.services import stage_recovery, task_controls

        request_id, _ = self.failure()
        self.create(1001, status="delivered")
        with self.assertRaisesRegex(RuntimeError, "较新的执行记录"):
            task_controls.queue(request_id, stage_recovery.DEPLOY, self.actor)
        self.assertFalse(self.db.request_detail(request_id)["controls"])

    def test_deployment_resume_rejects_a_newer_environment_build_under_its_lock(self):
        mock_fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=Mock())
        with patch.dict(sys.modules, {"fcntl": mock_fcntl}):
            from app.services import netorder_14_remote

            root = self.db.settings.data_dir
            root.joinpath(".env").write_text("NETORDER_RELEASE=./releases/124\n", encoding="utf-8")
            plan = {"status": "ready", "mode": "existing-compose", "composeDirectory": str(root),
                    "releaseVariable": "NETORDER_RELEASE", "applicationUrl": "http://example.invalid/"}
            with patch.object(netorder_14_remote, "fcntl", mock_fcntl), patch.object(netorder_14_remote, "checked_area", return_value=root), \
                    patch.object(netorder_14_remote, "package_valid"), patch.object(netorder_14_remote, "preflight", return_value=plan), \
                    patch.object(netorder_14_remote, "compose") as compose:
                outcome = netorder_14_remote.deploy({"target": {}, "package": {"buildId": 123}, "preventOlderBuild": True})
            self.assertEqual(outcome["reason"], "target-already-on-newer-build")
            mock_fcntl.flock.assert_called_once()
            compose.assert_not_called()
