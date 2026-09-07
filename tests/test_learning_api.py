from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LearningApiTests(unittest.TestCase):
    """Use an isolated database; importing this module never loads app settings."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        from app.config import settings
        from app import db
        from app.main import app, current_user
        from app.services import project_learning

        self.db = db
        self.learning = project_learning
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        replacements = {
            "data_dir": Path(self.directory.name), "worker_enabled": False,
            "seed_demo": True, "environment": "development", "runner_token": "learning-test-runner",
        }
        original = {name: getattr(settings, name) for name in replacements}
        for name, value in replacements.items():
            object.__setattr__(settings, name, value)
        self.addCleanup(lambda: [object.__setattr__(settings, name, value) for name, value in original.items()])
        db.init_db()
        self.admin = db.row("SELECT * FROM users WHERE username='admin'")
        self.owner = db.row("SELECT * FROM users WHERE username='pm'")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,display_name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,?,?,?)",
                ("acceptor", "指定验收人", "acceptor@example.com", "not-used", "pm", 1, db.utc_now()),
            )
        self.assignee = db.row("SELECT * FROM users WHERE username='acceptor'")
        self.actor = self.owner
        overrides = dict(app.dependency_overrides)
        app.dependency_overrides[current_user] = lambda: self.actor
        self.addCleanup(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(overrides)))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.project = db.row("SELECT * FROM projects ORDER BY id LIMIT 1")
        self.request_id = db.create_delivery_request(
            self.project, self.owner["id"], 810001, self.project["delivery_mode"],
            ["pm@example.com"], ["auto_release"],
        )
        db.update_request(self.request_id, title="单位查询与角色待办优化", work_item_revision=7)
        self.learning.ensure_acceptance(
            self.request_id,
            [{"id": f"AC-{index}", "criterion": f"验收功能 {index}"} for index in range(1, 5)],
            revision=7,
        )
        db.update_request(
            self.request_id, status="delivered", completed_at=db.utc_now(),
            commit_hash="abc123", result_summary="完成单位查询和待办",
            acceptance_ledger=[
                {"id": f"AC-{index}", "criterion": f"验收功能 {index}", "status": "completed",
                 "repositories": ["backend"], "files": ["query.py"], "tests": ["unit checks"], "evidence": ["assertion passed"]}
                for index in range(1, 5)
            ],
        )

    def acceptance(self) -> dict:
        response = self.client.get(f"/api/requests/{self.request_id}/acceptance")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["acceptance"]

    def feedback_payload(self, *, key: str = "feedback-round-one", latest: int = 0) -> dict:
        return {
            "items": [
                {"id": f"AC-{index}", "status": "failed" if index == 3 else "passed",
                 "actual": "仍然显示旧值" if index == 3 else "", "expected": "显示最新值" if index == 3 else ""}
                for index in range(1, 5)
            ],
            "raw_feedback": "第3点未通过，其余通过", "tested_version": "abc123",
            "environment": "提出人已有环境", "idempotency_key": key,
            "expected_latest_feedback_id": latest,
        }

    def submit_feedback(self, **kwargs) -> dict:
        response = self.client.post(
            f"/api/requests/{self.request_id}/acceptance/feedback", json=self.feedback_payload(**kwargs),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_non_owner_cannot_read_or_submit_acceptance(self) -> None:
        self.actor = self.assignee
        path = f"/api/requests/{self.request_id}"
        self.assertEqual(self.client.get(path + "/acceptance").status_code, 403)
        self.assertEqual(self.client.post(path + "/acceptance/feedback", json=self.feedback_payload()).status_code, 403)
        self.assertEqual(self.client.post(path + "/acceptance/preview", json={"text": "全部通过"}).status_code, 403)

    def test_assignment_grants_acceptance_not_task_management(self) -> None:
        path = f"/api/requests/{self.request_id}"
        assigned = self.client.put(path + "/acceptance/assignee", json={"user_id": self.assignee["id"]})
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.actor = self.assignee
        self.assertEqual(self.client.get(path).status_code, 200)
        permissions = self.client.get(path + "/acceptance").json()
        self.assertTrue(permissions["can_accept"])
        self.assertFalse(permissions["can_assign"])
        self.assertEqual(permissions["users"], [])
        self.assertEqual(self.client.post(path + "/cancel").status_code, 403)
        self.assertEqual(self.client.put(path + "/acceptance/assignee", json={"user_id": self.admin["id"]}).status_code, 403)
        records = self.client.get("/api/delivery-records").json()["items"]
        self.assertIn(self.request_id, [item["id"] for item in records])
        self.submit_feedback()

    def test_preview_is_read_only_and_never_infers_unstated_pass(self) -> None:
        response = self.client.post(
            f"/api/requests/{self.request_id}/acceptance/preview", json={"text": "第3点未通过"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertTrue(preview["requires_confirmation"])
        self.assertFalse(any(item["status"] == "passed" for item in preview["items"]))
        self.assertEqual(self.acceptance()["latest_feedback_id"], 0)

    def test_feedback_is_idempotent_and_stale_form_is_rejected(self) -> None:
        first = self.submit_feedback()
        replay = self.submit_feedback()
        self.assertEqual(first["feedback"]["id"], replay["feedback"]["id"])
        response = self.client.post(
            f"/api/requests/{self.request_id}/acceptance/feedback",
            json=self.feedback_payload(key="feedback-stale-round", latest=0),
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.acceptance()["rounds"]), 1)

    def test_feedback_refuses_unknown_ids_and_undelivered_tasks(self) -> None:
        payload = self.feedback_payload()
        payload["items"][0]["id"] = "AC-999"
        response = self.client.post(f"/api/requests/{self.request_id}/acceptance/feedback", json=payload)
        self.assertEqual(response.status_code, 422, response.text)
        self.db.update_request(self.request_id, status="developing")
        response = self.client.post(f"/api/requests/{self.request_id}/acceptance/feedback", json=self.feedback_payload())
        self.assertEqual(response.status_code, 409, response.text)

    def test_explicit_repair_is_linked_and_preserves_delivery_settings(self) -> None:
        feedback = self.submit_feedback()["feedback"]
        payload = {"feedback_id": feedback["id"], "idempotency_key": "repair-round-one"}
        path = f"/api/requests/{self.request_id}/acceptance/repair"
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        repair = response.json()["request"]
        self.assertNotEqual(repair["id"], self.request_id)
        self.assertEqual(repair["parent_request_id"], self.request_id)
        self.assertEqual(repair["requester_id"], self.owner["id"])
        self.assertEqual(repair["delivery_options"], ["auto_release"])
        self.assertEqual(repair["notification_emails"], ["pm@example.com"])
        self.assertEqual(repair["failed_item_ids"], ["AC-03"])
        self.assertEqual(set(repair["protected_item_ids"]), {"AC-01", "AC-02", "AC-04"})
        self.assertEqual(repair["status"], "queued")
        self.assertEqual(self.client.post(path, json=payload).json()["request"]["id"], repair["id"])
        self.assertEqual(self.db.request_detail(self.request_id)["status"], "delivered")

    def test_feedback_does_not_itself_start_repair(self) -> None:
        self.submit_feedback()
        count = self.db.row("SELECT COUNT(*) count FROM delivery_requests")["count"]
        self.assertEqual(count, 1)

    def test_requester_can_still_accept_after_assigning_another_reviewer(self) -> None:
        assigned = self.client.put(
            f"/api/requests/{self.request_id}/acceptance/assignee", json={"user_id": self.assignee["id"]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.submit_feedback()

    def test_repair_does_not_duplicate_an_active_request_for_the_requirement(self) -> None:
        feedback = self.submit_feedback()["feedback"]
        self.db.create_delivery_request(
            self.project, self.owner["id"], 810001, self.project["delivery_mode"], ["pm@example.com"],
        )
        response = self.client.post(
            f"/api/requests/{self.request_id}/acceptance/repair",
            json={"feedback_id": feedback["id"], "idempotency_key": "repair-conflicting-round"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.db.row("SELECT COUNT(*) count FROM delivery_requests")["count"], 2)

    def test_failed_repair_retry_keeps_frozen_scope_and_resolved_story_admission(self) -> None:
        from app.orchestrator import Worker

        feedback = self.submit_feedback()["feedback"]
        response = self.client.post(
            f"/api/requests/{self.request_id}/acceptance/repair",
            json={"feedback_id": feedback["id"], "idempotency_key": "repair-before-retry"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        failed_id = response.json()["request"]["id"]
        self.db.update_request(failed_id, status="failed", error_message="temporary build failure")
        retried = self.client.post(f"/api/requests/{failed_id}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        retry_id = retried.json()["id"]
        retry = self.db.request_detail(retry_id)
        self.assertEqual(retry["parent_request_id"], self.request_id)
        self.assertEqual(retry["root_request_id"], self.request_id)
        self.assertEqual(retry["repair_round"], 1)
        self.assertEqual(retry["repair_context"]["retry_of_request_id"], failed_id)
        self.assertEqual(retry["repair_context"]["feedback_id"], feedback["id"])
        self.assertEqual(retry["failed_item_ids"], ["AC-03"])
        self.assertEqual(set(retry["protected_item_ids"]), {"AC-01", "AC-02", "AC-04"})
        self.assertEqual(retry["delivery_options"], ["auto_release"])
        acceptance = self.learning.get_acceptance(retry_id)
        self.assertEqual(len(acceptance["items"]), 4)
        self.assertTrue(all(item["human_status"] == "unverified" for item in acceptance["items"]))
        self.assertEqual(self.db.request_detail(failed_id)["status"], "failed")
        self.assertEqual(self.client.post(f"/api/requests/{failed_id}/retry").json()["id"], retry_id)
        self.assertEqual(self.acceptance()["rounds"][0]["repair_request_id"], retry_id)
        project = self.db.project_for_api(self.project)
        project["simulation_mode"] = False
        work_item = {
            "id": 810001, "revision": 7, "title": "单位查询与角色待办优化", "state": "已解决",
            "work_item_type": "用户情景", "area_path": project["tfs_area_path"],
            "description": "修复验收未通过的单位查询", "acceptance_criteria": "显示最新值",
        }
        with patch("app.orchestrator.TfsClient") as client, patch("app.orchestrator.load_project_presets", return_value=[]):
            client.return_value.get_work_item.return_value = work_item
            self.assertEqual(Worker()._validate(retry, project)["state"], "已解决")

    def test_admin_cannot_turn_self_report_into_human_verified_experience(self) -> None:
        self.actor = self.admin
        experiences = self.client.get("/api/admin/project-experiences").json()["items"]
        response = self.client.patch(
            f"/api/admin/project-experiences/{experiences[0]['id']}",
            json={"status": "verified", "reason": "模型报告已完成"},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_feedback_missing_stale_guard_is_rejected(self) -> None:
        payload = self.feedback_payload()
        payload.pop("expected_latest_feedback_id")
        response = self.client.post(f"/api/requests/{self.request_id}/acceptance/feedback", json=payload)
        self.assertEqual(response.status_code, 422, response.text)

    def test_admin_experience_list_detail_and_lifecycle_are_scoped(self) -> None:
        path = "/api/admin/project-experiences"
        self.assertEqual(self.client.get(path).status_code, 403)
        self.actor = self.admin
        response = self.client.get(path, params={"project_id": self.project["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        experiences = response.json()["items"]
        self.assertEqual(len(experiences), 1)
        experience = experiences[0]
        self.assertEqual(experience["status"], "candidate")
        detail = self.client.get(f"{path}/{experience['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        retired = self.client.patch(f"{path}/{experience['id']}", json={"status": "deprecated", "reason": "当前实现已替换"})
        self.assertEqual(retired.status_code, 200, retired.text)
        self.assertEqual(retired.json()["experience"]["status"], "deprecated")
        self.actor = self.owner
        self.assertEqual(self.client.get(f"{path}/{experience['id']}").status_code, 403)

    def test_runner_learning_endpoints_require_token_and_project_match(self) -> None:
        path = f"/api/runner/requests/{self.request_id}/acceptance"
        self.assertEqual(self.client.post(path, json={"criteria": "1. 完成功能"}).status_code, 401)
        headers = {"Authorization": "Bearer learning-test-runner"}
        response = self.client.post(path, headers=headers, json={"criteria": "1. 完成功能", "revision": 7})
        self.assertEqual(response.status_code, 200, response.text)
        mismatch = self.client.get("/api/runner/project-experiences", headers=headers, params={
            "project_key": self.project["project_key"], "work_item_id": 9, "request_id": self.request_id,
        })
        self.assertEqual(mismatch.status_code, 422, mismatch.text)
        forbidden = self.client.patch(
            f"/api/runner/requests/{self.request_id}", headers=headers,
            json={"fields": {"acceptance_owner_id": self.assignee["id"]}},
        )
        self.assertEqual(forbidden.status_code, 400, forbidden.text)

    def test_remote_store_search_uses_bounded_post_body(self) -> None:
        import httpx
        from app.store import RemoteStore

        store = RemoteStore("https://control.example.test", "test-token", "test-runner")
        self.addCleanup(store.close)
        response = httpx.Response(200, json={"lessons": [{"id": 1}]}, request=httpx.Request("POST", "https://control.example.test"))
        with patch.object(store, "_request", return_value=response) as request:
            lessons = store.relevant_experiences("demo-sichuan", 810001, "需求内容" * 4000, self.request_id)
        self.assertEqual(lessons, [{"id": 1}])
        self.assertEqual(request.call_args.args, ("POST", "/api/runner/project-experiences/search"))
        self.assertEqual(len(request.call_args.kwargs["json"]["query"]), 12000)

    def test_login_deep_link_only_preserves_safe_request_uuid(self) -> None:
        with patch("app.main.get_session_user", return_value=None):
            response = self.client.get(f"/?request={self.request_id}&acceptance=1", follow_redirects=False)
            self.assertEqual(response.headers["location"], f"/login?request={self.request_id}&acceptance=1")
            unsafe = self.client.get("/?request=https://example.com&next=https://example.com", follow_redirects=False)
            self.assertEqual(unsafe.headers["location"], "/login")

    def test_unauthenticated_acceptance_returns_401(self) -> None:
        from app.main import app, current_user

        override = app.dependency_overrides.pop(current_user)
        try:
            response = self.client.get(f"/api/requests/{self.request_id}/acceptance")
            self.assertEqual(response.status_code, 401)
        finally:
            app.dependency_overrides[current_user] = override


if __name__ == "__main__":
    unittest.main()
