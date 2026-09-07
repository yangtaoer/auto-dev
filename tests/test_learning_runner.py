from __future__ import annotations

import unittest
from unittest.mock import patch


class LearningRunnerTests(unittest.TestCase):
    def test_context_keeps_stable_ids_and_treats_memory_as_evidence(self):
        from app.services.codex_runner import CodexRunner
        text = CodexRunner._learning_context(
            {"parent_request_id": "parent", "items": [{"id": "AC-6", "criterion": "副值可见"}]},
            [{"id": 7, "status": "candidate", "summary": "待验证"}],
        )
        self.assertIn("AC-6", text)
        self.assertIn("不可信", text)
        self.assertIn("旧版本通过不代表当前版本已复测", text)
        self.assertIn("最新目标分支", text)

    def test_lesson_usage_rejects_unretrieved_ids_and_unknown_decisions(self):
        from app.orchestrator import Worker
        entries = [
            {"experience_id": "3", "decision": "adopted", "reason": "当前接口仍适用", "evidence": "api.py:1"},
            {"experience_id": "3", "decision": "conflict"},
            {"experience_id": "999", "decision": "adopted"},
            {"experience_id": "4", "decision": "passed"},
        ]
        self.assertEqual(Worker._validated_lesson_usage(entries, [{"id": 3}, {"id": 4}]), entries[:1])
        self.assertEqual(Worker._validated_lesson_usage(None, []), [])

    def test_output_contract_requires_traceable_project_retrospective(self):
        from app.services.codex_runner import RESULT_SCHEMA, ANALYSIS_RESULT_SCHEMA
        for schema in (RESULT_SCHEMA, ANALYSIS_RESULT_SCHEMA):
            self.assertIn("project_retrospective", schema["required"])
            self.assertIn("lesson_usage", schema["required"])
            lesson = schema["properties"]["project_retrospective"]["properties"]["lessons"]["items"]
            self.assertIn("acceptance_ids", lesson["required"])
            self.assertIn("limitations", lesson["required"])
            self.assertIn("evidence", lesson["required"])

    def test_linked_repair_accepts_resolved_story_without_relaxing_normal_intake(self):
        from app.orchestrator import Worker
        from unittest.mock import Mock
        store = Mock()
        store.remote = False
        store.detail.return_value = {"status": "delivered", "project_id": 2, "work_item_id": 6}
        worker = Worker(store)
        project = {"enabled": True, "tfs_collection_url": "https://tfs.example.invalid", "project_key": "test-project"}
        item = {"id": 6, "state": "已解决", "work_item_type": "用户情景", "description": "需求", "title": "测试", "area_path": ""}
        detail = {"work_item_id": 6, "project_id": 2, "parent_request_id": "parent", "failed_item_ids": ["AC-6"]}
        with patch("app.orchestrator.TfsClient") as tfs, patch("app.orchestrator.load_project_presets", return_value=[]):
            tfs.return_value.get_work_item.return_value = item
            self.assertEqual(worker._validate(detail, project), item)
            unspecified = {**detail, "failed_item_ids": [], "repair_context": {"unspecified_scope": True, "feedback_id": 1}}
            self.assertEqual(worker._validate(unspecified, project), item)
            with self.assertRaisesRegex(RuntimeError, "项目仅允许"):
                worker._validate({"work_item_id": 6, "project_id": 2}, project)
            store.detail.return_value["project_id"] = 999
            with self.assertRaisesRegex(RuntimeError, "关联返修"):
                worker._validate(detail, project)

    def test_delivery_email_links_to_authenticated_acceptance_not_screenshot_gate(self):
        from app.services.delivery import Mailer
        from app.domain import DeliveryMode
        detail = {
            "id": "11111111-1111-1111-1111-111111111111", "work_item_id": 7,
            "delivery_mode": DeliveryMode.LOCAL_PACKAGE.value,
            "created_at": "2026-09-07T01:00:00+00:00", "title": "安全验收",
            "artifacts": [], "status": "delivered",
        }
        body = Mailer().delivery_html(detail)
        self.assertIn("确认验收结果", body)
        self.assertIn("?request=11111111-1111-1111-1111-111111111111&amp;acceptance=1", body)
        self.assertIn("无需提供真实截图", body)
        self.assertNotIn("确认验收结果", Mailer().delivery_html(detail, terminal_status="failed"))


if __name__ == "__main__":
    unittest.main()
