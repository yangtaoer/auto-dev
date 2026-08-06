from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


TEST_DATA = tempfile.TemporaryDirectory()
os.environ["AUTODEV_DATA_DIR"] = TEST_DATA.name
os.environ["AUTODEV_WORKER_ENABLED"] = "0"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
os.environ["BOOTSTRAP_PM_PASSWORD"] = "pm123456"
os.environ["AUTODEV_RUNNER_TOKEN"] = "test-runner-token"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.local_runner_main import load_project_presets  # noqa: E402
from app.orchestrator import worker  # noqa: E402


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
