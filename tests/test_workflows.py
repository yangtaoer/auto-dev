from __future__ import annotations

import json
import os
import tempfile
import unittest
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
from app.orchestrator import worker  # noqa: E402
from app.project_catalog import load_project_presets, resolve_project_for_work_item  # noqa: E402
from app.services.codex_runner import CodexRunner  # noqa: E402
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
        self.assertTrue({"package", "sql", "config", "report"}.issubset(kinds))
        self.assertTrue(any(event["event_type"] == "delivery.policy_enforced" for event in detail["events"]))

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
                "version": "0.3.3",
                "state": "idle",
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
        self.assertGreaterEqual(dashboard["stats"]["total"], 1)
        self.assertIn("today_total", dashboard["stats"])

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
                "repository_path": "C:\\work\\demo",
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
