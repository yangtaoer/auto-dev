from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx


TEST_DATA = tempfile.TemporaryDirectory()
os.environ["AUTODEV_DATA_DIR"] = TEST_DATA.name
os.environ["AUTODEV_WORKER_ENABLED"] = "0"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
os.environ["BOOTSTRAP_PM_PASSWORD"] = "pm123456"
os.environ["AUTODEV_RUNNER_TOKEN"] = "test-runner-token"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.orchestrator import Worker, worker  # noqa: E402
from app.project_catalog import load_project_presets, resolve_project_for_work_item  # noqa: E402
from app.services.codex_runner import CodexRunner  # noqa: E402
from app.services.delivery import Mailer, changed_files  # noqa: E402
from app.store import RemoteStore  # noqa: E402


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        response = cls.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert response.status_code == 200, response.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        TEST_DATA.cleanup()

    def create_project(self, key: str, mode: str, *, allow_override: bool = False) -> int:
        payload = {
            "project_key": key,
            "name": f"测试项目 {key}",
            "enabled": True,
            "simulation_mode": True,
            "delivery_mode": mode,
            "allow_requirement_override": allow_override,
            "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
            "tfs_project": "XiNanArea-New",
            "tfs_area_path": "XiNanArea-New\\四川省区团队",
            "repository_path": "",
            "base_branch": "dev",
            "build_command": "",
            "package_patterns": ["target/*.jar"],
            "sql_patterns": ["**/*.sql"],
            "config_patterns": ["**/*.yml"],
            "protected_patterns": ["**/common/**"],
            "notification_cc": "",
        }
        response = self.client.post("/api/projects", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]["id"]

    def submit_and_process(self, project_id: int, work_item_id: int) -> dict:
        response = self.client.post("/api/requests", json={"project_id": project_id, "work_item_id": work_item_id})
        self.assertEqual(response.status_code, 200, response.text)
        request_id = response.json()["id"]
        worker.process_once()
        detail = self.client.get(f"/api/requests/{request_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        return detail.json()["request"]

    def test_local_package_delivery(self) -> None:
        project_id = self.create_project("test-local", "local_package")
        detail = self.submit_and_process(project_id, 910001)
        self.assertEqual(detail["status"], "delivered")
        kinds = {artifact["kind"] for artifact in detail["artifacts"]}
        self.assertTrue({"package", "sql", "config"}.issubset(kinds))
        self.assertNotIn("report", kinds)
        self.assertTrue(any(event["event_type"] == "delivery.policy_enforced" for event in detail["events"]))

    def test_delivery_email_is_branded_and_uses_china_standard_time(self) -> None:
        detail = {
            "id": "mail-preview-request",
            "work_item_id": 910014,
            "title": "优化交付邮件",
            "requirement_summary": "统一邮件样式与时间格式。",
            "project_name": "邮件测试项目",
            "requester_name": "杨涛",
            "delivery_mode": "local_package",
            "result_summary": "已完成品牌模板与北京时间转换。",
            "created_at": "2026-08-06T03:50:00+00:00",
            "started_at": "2026-08-06T03:51:21+00:00",
            "completed_at": "2026-08-06T03:56:45+00:00",
            "branch_name": "feature/910014-yangtao",
            "commit_hash": "abcdef1234567890",
            "pr_url": None,
            "artifacts": [
                {
                    "id": 1,
                    "kind": "package",
                    "name": "autodev-mail-demo.zip",
                    "external_url": "https://auto-dev-oss.oss-cn-chengdu.aliyuncs.com/demo.zip",
                }
            ],
        }
        mailer = Mailer()
        rendered = mailer.delivery_html(detail)
        self.assertEqual(mailer.sender_address().display_name, "AutoDev 全自助研发交付")
        self.assertEqual(
            mailer.delivery_subject(detail),
            "【AutoDev · 已交付】TFS #910014｜优化交付邮件",
        )
        self.assertIn("AUTODEV · DELIVERY SIGNAL", rendered)
        self.assertIn("2026-08-06 11:50:00（UTC+8）", rendered)
        self.assertIn("2026-08-06 11:56:45（UTC+8）", rendered)
        self.assertIn("5 分 24 秒", rendered)
        self.assertIn("下载产物", rendered)
        self.assertIn("提交人", rendered)
        self.assertIn("cid:autodev-brand-mark", rendered)
        self.assertIn('width="860"', rendered)
        self.assertIn('max-width:860px', rendered)
        self.assertIn('width="25%"', rendered)
        self.assertIn('width="33.33%"', rendered)
        message = mailer.build_message(
            to=["recipient@example.com"], subject=mailer.delivery_subject(detail), html_body=rendered
        )
        inline_images = [part for part in message.walk() if part.get_content_type() == "image/png"]
        self.assertEqual(len(inline_images), 1)
        self.assertEqual(inline_images[0]["Content-ID"], "<autodev-brand-mark>")
        self.assertEqual(inline_images[0].get_filename(), "autodev-mark.png")

        waiting = mailer.delivery_html({**detail, "completed_at": None, "pr_url": "https://tfs.test/pr/14"}, action_required=True)
        self.assertIn("需要项目经理协同处理", waiting)
        self.assertIn("打开 PR 并安排合并", waiting)
        self.assertIn("进行中", waiting)

    def test_runner_can_send_branded_test_email(self) -> None:
        project_id = self.create_project("test-branded-email", "local_package")
        detail = self.submit_and_process(project_id, 910015)
        headers = {"Authorization": "Bearer test-runner-token"}
        with patch("app.main.Mailer.configured", return_value=True), patch("app.main.Mailer.send") as sender:
            response = self.client.post(
                f"/api/runner/requests/{detail['id']}/test-email",
                headers=headers,
                json={"recipient": "yangtao2@tellhow.com"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["recipient"], "yangtao2@tellhow.com")
        sender.assert_called_once()
        self.assertIn("【AutoDev · 测试邮件】", sender.call_args.kwargs["subject"])
        self.assertIn("cid:autodev-brand-mark", sender.call_args.kwargs["html_body"])
        template = self.client.get("/api/runner/email-template", headers=headers)
        self.assertEqual(template.status_code, 200, template.text)
        self.assertEqual(template.json()["template"], "compact-wide")
        self.assertEqual(template.json()["card_width"], 860)

    def test_two_requests_can_be_submitted_concurrently_and_both_delivered(self) -> None:
        project_id = self.create_project("test-concurrent", "local_package")

        def submit(work_item_id: int) -> tuple[int, dict]:
            response = self.client.post(
                "/api/requests", json={"project_id": project_id, "work_item_id": work_item_id}
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, (910012, 910013)))

        self.assertEqual([status for status, _ in results], [200, 200])
        request_ids = [payload["id"] for _, payload in results]
        self.assertEqual(len(set(request_ids)), 2)
        self.assertTrue(all(payload["status"] == "queued" for _, payload in results))

        worker.process_once()
        worker.process_once()
        details = [self.client.get(f"/api/requests/{request_id}").json()["request"] for request_id in request_ids]
        self.assertTrue(all(detail["status"] == "delivered" for detail in details))
        dashboard = self.client.get("/api/dashboard").json()
        submitted = [item for item in dashboard["recent"] if item["id"] in request_ids]
        self.assertEqual(len(submitted), 2)
        self.assertTrue(all(item["requester_name"] == "系统管理员" for item in submitted))
        self.assertTrue(all(item["completed_at"] for item in submitted))
        self.assertTrue(all(item["duration_seconds"] is not None for item in submitted))
        self.assertTrue(all(item["artifacts"] for item in submitted))
        self.assertTrue(all(not any(a["kind"] == "report" for a in item["artifacts"]) for item in submitted))

    def test_codex_live_result_is_chinese_markdown_without_raw_json_or_system_events(self) -> None:
        raw = json.dumps(
            {
                "summary": "已完成测试注释修改。",
                "changed_files": ["src/Test.java"],
                "acceptance_mapping": ["已覆盖自动研发验证"],
                "risks": [],
                "sql_changes": [],
                "config_changes": [],
            },
            ensure_ascii=False,
        )
        runner = CodexRunner()
        self.assertIsNone(
            runner._live_event("item/agentMessage/delta", {"delta": raw, "itemId": "agent-1"})
        )
        event = runner._live_event(
            "item/completed",
            {"item": {"id": "agent-1", "type": "agentMessage", "text": raw}},
        )
        self.assertEqual(event["kind"], "assistant")
        self.assertEqual(event["format"], "markdown")
        self.assertIn("### 研发结论", event["content"])
        self.assertIn("### 变更文件", event["content"])
        self.assertNotIn('"summary"', event["content"])
        self.assertIsNone(runner._live_event("turn/completed", {"turn": {"status": "completed"}}))

    def test_changed_files_includes_new_untracked_delivery_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "AutoDev Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "autodev-test@example.com"], check=True
            )
            tracked = repository / "README.md"
            tracked.write_text("initial\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "initial"], check=True)
            base_commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            new_sql = repository / "sql" / "upgrade.sql"
            new_sql.parent.mkdir()
            new_sql.write_text("SELECT 1;\n", encoding="utf-8")
            self.assertIn("sql/upgrade.sql", changed_files(repository, base_commit))

    def test_remote_store_retries_transient_gateway_failure(self) -> None:
        request = httpx.Request("PATCH", "https://cloud.test/api/runner/requests/request-1")
        responses = [
            httpx.Response(502, request=request),
            httpx.Response(200, json={"ok": True}, request=request),
        ]
        store = RemoteStore("https://cloud.test", "runner-token", "runner-1")
        try:
            with patch.object(store.client, "request", side_effect=responses) as mocked, patch(
                "app.store.time.sleep"
            ) as sleeper:
                store.update_request("request-1", status="building")
            self.assertEqual(mocked.call_count, 2)
            sleeper.assert_called_once_with(0.4)
        finally:
            store.close()

    def test_sichuan_review_then_merge(self) -> None:
        project_id = self.create_project("test-sichuan", "sichuan_auto_review")
        detail = self.submit_and_process(project_id, 910002)
        self.assertEqual(detail["status"], "waiting_merge")
        response = self.client.post(f"/api/requests/{detail['id']}/simulate-merge")
        self.assertEqual(response.status_code, 200, response.text)
        completed = self.client.get(f"/api/requests/{detail['id']}").json()["request"]
        self.assertEqual(completed["status"], "delivered")
        self.assertTrue(any(item["kind"] in {"merge_screenshot", "merge_evidence"} for item in completed["artifacts"]))

    def test_product_review_emails_before_and_after_merge(self) -> None:
        project_id = self.create_project("test-product", "product_manual_review")
        detail = self.submit_and_process(project_id, 910003)
        self.assertEqual(detail["status"], "waiting_merge")
        self.assertTrue(any(item["name"] == "review-email-preview.html" for item in detail["artifacts"]))
        response = self.client.post(f"/api/requests/{detail['id']}/simulate-merge")
        self.assertEqual(response.status_code, 200, response.text)
        completed = self.client.get(f"/api/requests/{detail['id']}").json()["request"]
        self.assertEqual(completed["status"], "delivered")
        self.assertTrue(any(item["name"] == "delivery-email-preview.html" for item in completed["artifacts"]))

    def test_admin_can_override_delivery_mode_for_one_requirement(self) -> None:
        project_id = self.create_project("test-override", "product_manual_review", allow_override=True)
        response = self.client.post(
            "/api/requests",
            json={"project_id": project_id, "work_item_id": 910004, "delivery_mode": "local_package"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        request_id = response.json()["id"]
        worker.process_once()
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(detail["delivery_mode"], "local_package")
        self.assertEqual(detail["status"], "delivered")

    def test_cloud_runner_claims_updates_and_uploads_artifacts(self) -> None:
        project_id = self.create_project("test-cloud-runner", "local_package")
        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910005}
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["id"]
        headers = {"Authorization": "Bearer test-runner-token"}

        heartbeat = self.client.post(
            "/api/runner/heartbeat",
            headers=headers,
            json={"runner_id": "yangtao-pc", "hostname": "test-pc", "version": "0.2.0", "state": "idle"},
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        claim = self.client.post(
            "/api/runner/claim", headers=headers, json={"runner_id": "yangtao-pc"}
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["request"]["id"], request_id)

        updated = self.client.patch(
            f"/api/runner/requests/{request_id}",
            headers=headers,
            json={"fields": {"title": "云端协议测试", "progress": 42}},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        uploaded = self.client.post(
            f"/api/runner/requests/{request_id}/artifacts",
            headers=headers,
            data={"kind": "sql", "name": "upgrade.sql", "external_url": ""},
            files={"file": ("upgrade.sql", b"SELECT 1;", "text/plain")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        oss_url = "https://auto-dev-oss.oss-cn-chengdu.aliyuncs.com/autodev/test/package.zip?x=1&signature=test"
        external = self.client.post(
            f"/api/runner/requests/{request_id}/artifacts",
            headers=headers,
            data={"kind": "package", "name": "package.zip", "external_url": oss_url},
        )
        self.assertEqual(external.status_code, 200, external.text)
        notify = self.client.post(
            f"/api/runner/requests/{request_id}/notify",
            headers=headers,
            json={"action_required": False},
        )
        self.assertEqual(notify.status_code, 200, notify.text)
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        kinds = {item["kind"] for item in detail["artifacts"]}
        self.assertTrue({"sql", "package", "email_preview"}.issubset(kinds))
        package = next(item for item in detail["artifacts"] if item["kind"] == "package")
        self.assertEqual(package["external_url"], oss_url)
        preview = next(item for item in detail["artifacts"] if item["kind"] == "email_preview")
        html = Path(preview["local_path"]).read_text(encoding="utf-8")
        self.assertIn("auto-dev-oss.oss-cn-chengdu.aliyuncs.com", html)

        unauthenticated = self.client.post(
            "/api/runner/claim", json={"runner_id": "yangtao-pc"}
        )
        self.assertEqual(unauthenticated.status_code, 401)

    def test_codex_live_output_is_watcher_gated_and_not_persisted(self) -> None:
        project_id = self.create_project("test-live-codex", "local_package")
        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910011}
        )
        request_id = created.json()["id"]
        headers = {"Authorization": "Bearer test-runner-token"}
        updated = self.client.patch(
            f"/api/runner/requests/{request_id}", headers=headers, json={"fields": {"status": "developing"}}
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        event_count = len(self.client.get(f"/api/requests/{request_id}").json()["request"]["events"])

        started = self.client.post(f"/api/requests/{request_id}/codex-watch/start")
        self.assertEqual(started.status_code, 200, started.text)
        watcher = started.json()
        active = self.client.get(
            f"/api/runner/requests/{request_id}/codex-watch/active", headers=headers
        )
        self.assertTrue(active.json()["active"])
        published = self.client.post(
            f"/api/runner/requests/{request_id}/codex-watch/events",
            headers=headers,
            json={"events": [{"kind": "assistant", "content": "only-live-output", "group": "agent-1", "delta": True}]},
        )
        self.assertEqual(published.json()["accepted"], 1)
        polled = self.client.get(
            f"/api/requests/{request_id}/codex-watch/{watcher['watcher_id']}?after={watcher['cursor']}"
        )
        self.assertEqual(polled.json()["events"][0]["content"], "only-live-output")
        stopped = self.client.post(
            f"/api/requests/{request_id}/codex-watch/{watcher['watcher_id']}/stop"
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertFalse(
            self.client.get(f"/api/runner/requests/{request_id}/codex-watch/active", headers=headers).json()["active"]
        )
        self.assertEqual(
            len(self.client.get(f"/api/requests/{request_id}").json()["request"]["events"]), event_count
        )

    def test_admin_dashboard_receives_codex_capacity_and_global_stats(self) -> None:
        headers = {"Authorization": "Bearer test-runner-token"}
        heartbeat = self.client.post(
            "/api/runner/heartbeat",
            headers=headers,
            json={
                "runner_id": "quota-test-runner",
                "hostname": "quota-pc",
                "version": "0.4.0",
                "state": "idle",
                "current_request_ids": [],
                "max_concurrency": 5,
                "codex_usage": {
                    "available": True,
                    "plan_type": "prolite",
                    "primary": {"used_percent": 42, "remaining_percent": 58},
                    "credits": {"balance": "0", "has_credits": False, "unlimited": False},
                },
            },
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        dashboard = self.client.get("/api/dashboard").json()
        runner = next(item for item in dashboard["runners"] if item["runner_id"] == "quota-test-runner")
        self.assertEqual(runner["codex_usage"]["primary"]["remaining_percent"], 58)
        self.assertEqual(runner["max_concurrency"], 5)
        self.assertGreaterEqual(dashboard["stats"]["total"], 1)
        self.assertIn("today_total", dashboard["stats"])
        self.assertEqual(dashboard["capacity"]["limit"], 5)

    def test_new_intake_is_immediately_visible_and_home_shows_version(self) -> None:
        self.create_project("test-instant-board", "local_package")
        created = self.client.post("/api/requests", json={"work_item_id": 910020})
        self.assertEqual(created.status_code, 200, created.text)
        intake_id = created.json()["id"]

        dashboard = self.client.get("/api/dashboard").json()
        visible = next(item for item in dashboard["active"] if item.get("intake_id") == intake_id)
        self.assertEqual(visible["status"], "routing")
        self.assertEqual(visible["current_activity"], "任务已提交，等待执行器扫描")
        self.assertGreaterEqual(dashboard["capacity"]["queued"], 1)

        page = self.client.get("/")
        self.assertIn("SYSTEM v0.4.0", page.text)
        self.assertIn("control-strip", page.text)
        script = self.client.get("/static/app.js").text
        self.assertIn("addOptimisticIntake", script)

        headers = {"Authorization": "Bearer test-runner-token"}
        claimed = self.client.post(
            "/api/runner/intakes/claim", headers=headers, json={"runner_id": "yangtao-pc"}
        ).json()["intake"]
        self.assertEqual(claimed["id"], intake_id)
        routed = self.client.post(
            f"/api/runner/intakes/{intake_id}/route",
            headers=headers,
            json={"runner_id": "yangtao-pc", "project_key": "test-instant-board"},
        )
        self.assertEqual(routed.status_code, 200, routed.text)
        worker.process_once()

    def test_worker_never_exceeds_five_parallel_tasks(self) -> None:
        class QueueStore:
            remote = False

            def __init__(self) -> None:
                self.items = [f"parallel-{index}" for index in range(7)]
                self.lock = threading.Lock()

            def next_queued(self):
                with self.lock:
                    return self.items.pop(0) if self.items else None

            @staticmethod
            def next_waiting():
                return None

            @staticmethod
            def add_artifact(*_args, **_kwargs):
                return 1

            @staticmethod
            def detail(_request_id):
                return None

        store = QueueStore()
        parallel_worker = Worker(store=store, max_concurrency=5)
        release = threading.Event()
        five_started = threading.Event()
        all_finished = threading.Event()
        lock = threading.Lock()
        running = 0
        peak = 0
        finished = 0

        def blocking_run(_request_id: str) -> None:
            nonlocal running, peak, finished
            with lock:
                running += 1
                peak = max(peak, running)
                if running == 5:
                    five_started.set()
            release.wait(3)
            with lock:
                running -= 1
                finished += 1
                if finished == 7:
                    all_finished.set()

        parallel_worker.run_request = blocking_run
        parallel_worker.start()
        try:
            self.assertTrue(five_started.wait(2), "五个并发槽位未能同时启动")
            time.sleep(0.1)
            self.assertEqual(peak, 5)
            self.assertEqual(len(parallel_worker.current_request_ids), 5)
            release.set()
            self.assertTrue(all_finished.wait(2), "排队任务未在槽位释放后继续执行")
            self.assertLessEqual(peak, 5)
        finally:
            release.set()
            parallel_worker.stop()

    def test_project_menu_is_hidden_from_project_manager(self) -> None:
        admin_page = self.client.get("/")
        self.assertIn("自助项目", admin_page.text)
        self.client.post("/api/auth/logout")
        login = self.client.post("/api/auth/login", json={"username": "pm", "password": "pm123456"})
        self.assertEqual(login.status_code, 200, login.text)
        pm_page = self.client.get("/")
        self.assertNotIn("自助项目", pm_page.text)
        pm_dashboard = self.client.get("/api/dashboard")
        self.assertEqual(pm_dashboard.status_code, 200, pm_dashboard.text)
        self.assertIn("stats", pm_dashboard.json())
        self.client.post("/api/auth/logout")
        restored = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(restored.status_code, 200, restored.text)

    def test_number_only_request_is_routed_by_local_runner(self) -> None:
        self.create_project("test-auto-route", "local_package")
        created = self.client.post("/api/requests", json={"work_item_id": 910008})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["routing"])
        intake_id = created.json()["id"]
        headers = {"Authorization": "Bearer test-runner-token"}

        claimed = self.client.post(
            "/api/runner/intakes/claim",
            headers=headers,
            json={"runner_id": "yangtao-pc"},
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["intake"]["id"], intake_id)

        routed = self.client.post(
            f"/api/runner/intakes/{intake_id}/route",
            headers=headers,
            json={"runner_id": "yangtao-pc", "project_key": "test-auto-route"},
        )
        self.assertEqual(routed.status_code, 200, routed.text)
        request_id = routed.json()["request_id"]
        intake = self.client.get(f"/api/intakes/{intake_id}").json()["intake"]
        self.assertEqual(intake["status"], "routed")
        self.assertEqual(intake["result_request_id"], request_id)

        worker.process_once()
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(detail["project_key"], "test-auto-route")
        self.assertEqual(detail["delivery_mode"], "local_package")
        self.assertEqual(detail["status"], "delivered")

    @patch("app.project_catalog.TfsClient.get_work_item")
    def test_local_catalog_routes_to_most_specific_area_path(self, get_work_item) -> None:
        get_work_item.return_value = {
            "id": 910009,
            "area_path": "XiNanArea-New\\四川省区团队\\巴中",
        }
        common = {
            "enabled": True,
            "simulation_mode": False,
            "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
        }
        projects = [
            {**common, "project_key": "root", "tfs_area_path": "XiNanArea-New\\四川省区团队"},
            {**common, "project_key": "bazhong", "tfs_area_path": "XiNanArea-New\\四川省区团队\\巴中"},
        ]
        project, item = resolve_project_for_work_item(910009, projects)
        self.assertEqual(project["project_key"], "bazhong")
        self.assertEqual(item["id"], 910009)
        get_work_item.assert_called_once_with(910009)

    def test_runner_syncs_read_only_project_catalog(self) -> None:
        headers = {"Authorization": "Bearer test-runner-token"}

        def preset(key: str, name: str) -> dict:
            return {
                "project_key": key,
                "name": name,
                "enabled": True,
                "simulation_mode": True,
                "delivery_mode": "local_package",
                "allow_requirement_override": False,
                "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
                "tfs_project": "XiNanArea-New",
                "tfs_area_path": "XiNanArea-New\\四川省区团队",
                "reviewer_name": "朱星舟",
                "repository_path": "C:\\work\\demo",
                "repository_paths": ["C:\\work\\demo", "C:\\work\\demo-api"],
                "base_branch": "dev",
                "build_command": "echo test",
                "package_patterns": ["target/*.jar"],
                "sql_patterns": ["**/*.sql"],
                "config_patterns": ["**/*.yml"],
                "protected_patterns": ["**/production/**"],
                "notification_cc": "",
                "runner_id": "ignored-by-cloud",
            }

        first = self.client.put(
            "/api/runner/projects",
            headers=headers,
            json={
                "runner_id": "catalog-test-runner",
                "projects": [preset("catalog-one", "目录项目一"), preset("catalog-two", "目录项目二")],
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual({item["runner_id"] for item in first.json()["projects"]}, {"catalog-test-runner"})

        second = self.client.put(
            "/api/runner/projects",
            headers=headers,
            json={"runner_id": "catalog-test-runner", "projects": [preset("catalog-one", "目录项目一（已更新）")]},
        )
        self.assertEqual(second.status_code, 200, second.text)
        visible = self.client.get("/api/projects").json()["projects"]
        visible_keys = {item["project_key"] for item in visible}
        self.assertIn("catalog-one", visible_keys)
        self.assertNotIn("catalog-two", visible_keys)
        synced = next(item for item in visible if item["project_key"] == "catalog-one")
        self.assertEqual(synced["name"], "目录项目一（已更新）")
        self.assertEqual(synced["reviewer_name"], "朱星舟")
        self.assertEqual(synced["repository_paths"], ["C:\\work\\demo", "C:\\work\\demo-api"])
        cleared = self.client.put(
            "/api/runner/projects",
            headers=headers,
            json={"runner_id": "catalog-test-runner", "projects": []},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)

    def test_local_project_preset_catalog_contains_bazhong(self) -> None:
        projects = load_project_presets()
        bazhong = next(item for item in projects if item["project_key"] == "bazhong-self-developed")
        self.assertEqual(bazhong["name"], "巴中自巡航-自研")
        self.assertEqual(bazhong["runner_id"], "yangtao-pc")

    def test_local_project_preset_catalog_contains_chengdu_multi_repository_review(self) -> None:
        projects = load_project_presets()
        chengdu = next(item for item in projects if item["project_key"] == "chengdu-network-command")
        self.assertEqual(chengdu["name"], "成都网络发令")
        self.assertEqual(chengdu["tfs_area_path"], "DCS\\国网网络发令")
        self.assertEqual(chengdu["delivery_mode"], "sichuan_auto_review")
        self.assertEqual(chengdu["reviewer_name"], "朱星舟")
        self.assertEqual(chengdu["base_branch"], "dev")
        self.assertEqual(len(chengdu["repository_paths"]), 9)

    def test_multi_repository_merge_waits_for_all_prs_then_delivers(self) -> None:
        class Store:
            remote = False

            def __init__(self) -> None:
                self.updates: list[dict] = []
                self.events: list[tuple[str, str]] = []
                self.request = {
                    "id": "multi-pr-request",
                    "status": "waiting_merge",
                    "policy_snapshot": {
                        "simulation_mode": False,
                        "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
                    },
                    "repository_states": [
                        {
                            "name": "repo-one",
                            "repository_path": "C:\\work\\repo-one",
                            "pr_id": 101,
                            "pr_url": "https://tfs.test/pr/101",
                        },
                        {
                            "name": "repo-two",
                            "repository_path": "C:\\work\\repo-two",
                            "pr_id": 102,
                            "pr_url": "https://tfs.test/pr/102",
                        },
                    ],
                }

            def detail(self, request_id: str) -> dict:
                return self.request

            def update_request(self, request_id: str, **fields) -> None:
                self.request.update(fields)
                self.updates.append(fields)

            def add_event(self, request_id: str, event_type: str, message: str, **kwargs) -> None:
                self.events.append((event_type, message))

            def add_artifact(self, *args, **kwargs) -> int:
                return 1

        store = Store()
        multi_worker = Worker(store=store)
        pull_requests = [
            {"id": 101, "status": "completed", "merge_commit": "merge-one"},
            {"id": 102, "status": "completed", "merge_commit": "merge-two"},
        ]
        with patch("app.orchestrator.TfsClient.get_pull_request", side_effect=pull_requests), patch.object(
            multi_worker.artifacts, "create_merge_evidence"
        ) as evidence, patch.object(multi_worker, "_complete_delivery") as completed:
            multi_worker.poll_merge("multi-pr-request")

        completed.assert_called_once_with("multi-pr-request")
        self.assertEqual(evidence.call_count, 2)
        self.assertEqual(store.request["merge_commit"], "merge-one")
        self.assertTrue(all(item["status"] == "completed" for item in store.request["repository_states"]))
        self.assertEqual(
            [event for event, _ in store.events].count("pr.merged"),
            2,
        )

    def test_user_multiple_emails_edit_disable_and_request_selection(self) -> None:
        username = "multi_mail_pm"
        created = self.client.post(
            "/api/users",
            json={
                "username": username,
                "display_name": "多邮箱项目经理",
                "emails": ["pm.primary@example.com", "pm.backup@example.com"],
                "password": "password123",
                "role": "pm",
                "active": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user = created.json()["user"]
        self.assertEqual(user["emails"], ["pm.primary@example.com", "pm.backup@example.com"])

        project_id = self.create_project("multi-mail-project", "local_package")
        with TestClient(app) as pm_client:
            logged_in = pm_client.post(
                "/api/auth/login", json={"username": username, "password": "password123"}
            )
            self.assertEqual(logged_in.status_code, 200, logged_in.text)
            submitted = pm_client.post(
                "/api/requests",
                json={
                    "project_id": project_id,
                    "work_item_id": 910006,
                    "notification_emails": ["pm.backup@example.com"],
                },
            )
            self.assertEqual(submitted.status_code, 200, submitted.text)
            detail = pm_client.get(f"/api/requests/{submitted.json()['id']}").json()["request"]
            self.assertEqual(detail["notification_emails"], ["pm.backup@example.com"])
            rejected = pm_client.post(
                "/api/requests",
                json={
                    "project_id": project_id,
                    "work_item_id": 910007,
                    "notification_emails": ["not-owned@example.com"],
                },
            )
            self.assertEqual(rejected.status_code, 422, rejected.text)

        edited = self.client.put(
            f"/api/users/{user['id']}",
            json={
                "username": username,
                "display_name": "多邮箱项目经理（已编辑）",
                "emails": ["pm.new@example.com", "pm.backup@example.com"],
                "role": "pm",
                "active": False,
            },
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertFalse(edited.json()["user"]["active"])
        self.assertEqual(edited.json()["user"]["emails"][0], "pm.new@example.com")
        disabled_login = self.client.post(
            "/api/auth/login", json={"username": username, "password": "password123"}
        )
        self.assertEqual(disabled_login.status_code, 401, disabled_login.text)


if __name__ == "__main__":
    unittest.main()
