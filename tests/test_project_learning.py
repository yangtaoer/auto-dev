from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class ProjectLearningTests(unittest.TestCase):
    """Import settings only after unittest discovery; never touch the operator database."""

    def setUp(self) -> None:
        from app import db
        from app.config import settings
        from app.services import project_learning
        self.db = db
        self.learning = project_learning
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        overrides = {"data_dir": Path(self.directory.name), "worker_enabled": False,
                     "seed_demo": True, "environment": "development"}
        originals = {key: getattr(settings, key) for key in overrides}
        for key, value in overrides.items():
            object.__setattr__(settings, key, value)
        self.addCleanup(lambda: [object.__setattr__(settings, key, value) for key, value in originals.items()])
        db.init_db()
        self.admin = db.row("SELECT * FROM users WHERE username='admin'")
        self.owner = db.row("SELECT * FROM users WHERE username='pm'")
        self.project = db.row("SELECT * FROM projects LIMIT 1")
        self.request_id = self.make_request(901)

    def make_request(self, work_item_id: int, project: dict | None = None) -> str:
        project = project or self.project
        request_id = self.db.create_delivery_request(project, self.owner["id"], work_item_id,
                                                     project["delivery_mode"], ["test@example.com"])
        self.db.update_request(request_id, title="单位查询和副值待办", work_item_revision=8)
        self.learning.ensure_acceptance(request_id, ["单位查询使用当前登录单位", "副值看到自己的待办"], revision=8)
        self.db.update_request(request_id, status="delivered", result_summary="修复查询单位和待办角色条件", commit_hash="build-a",
                               acceptance_ledger=[{"id": "AC-1", "criterion": "单位查询使用当前登录单位", "status": "completed", "tests": ["单元断言"], "evidence": ["构建通过"]}])
        return request_id

    def feedback(self, items: list[dict], **kwargs) -> dict:
        return self.learning.submit_feedback(self.request_id, self.owner, items,
                  tested_version=kwargs.pop("tested_version", "build-a"), environment=kwargs.pop("environment", "验收环境甲"),
                  idempotency_key=kwargs.pop("idempotency_key", "feedback-1"), **kwargs)

    def test_frozen_criteria_stable_and_model_result_not_human_acceptance(self) -> None:
        result = self.learning.ensure_acceptance(self.request_id, ["更改过的内容"], revision=9)
        self.assertEqual([item["id"] for item in result["items"]], ["AC-01", "AC-02"])
        self.assertEqual(result["items"][0]["requirement_revision"], 8)
        self.assertEqual(result["items"][0]["development_status"], "completed")
        self.assertEqual(result["items"][0]["human_status"], "unverified")
        self.assertEqual(result["status"], "pending")

    def test_html_and_inline_numbered_criteria(self) -> None:
        self.assertEqual(self.learning._criteria("<ul><li>登录</li><li>查询</li></ul><script>bad()</script>"), ["登录", "查询"])
        self.assertEqual(self.learning._criteria("1、登录；2、查询；3、退出"), ["1、登录", "2、查询", "3、退出"])
        with self.assertRaises(ValueError):
            self.learning._criteria([str(number) for number in range(101)])

    def test_feedback_is_append_only_idempotent_and_stale_safe(self) -> None:
        items = [{"id": "AC-1", "status": "failed", "actual": "显示旧单位"}]
        first = self.feedback(items, expected_latest_feedback_id=0)
        again = self.feedback(items, expected_latest_feedback_id=0)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["items"][0]["expected"], "单位查询使用当前登录单位")
        with self.assertRaises(RuntimeError):
            self.feedback([{"id": "AC-1", "status": "passed"}])
        with self.assertRaises(RuntimeError):
            self.feedback([{"id": "AC-1", "status": "passed"}], idempotency_key="feedback-2", expected_latest_feedback_id=0)
        second = self.feedback([{"id": "AC-1", "status": "passed"}], idempotency_key="feedback-2", expected_latest_feedback_id=first["id"])
        bundle = self.learning.get_acceptance(self.request_id)
        self.assertEqual(len(bundle["rounds"]), 2)
        self.assertEqual(bundle["rounds"][0]["items"][0]["status"], "failed")
        self.assertEqual(bundle["latest_feedback_id"], second["id"])
        self.assertEqual(bundle["items"][1]["human_status"], "unverified")

    def test_human_acceptance_does_not_carry_between_versions_or_environments(self) -> None:
        self.feedback([{"id": "AC-1", "status": "passed"}])
        self.feedback([{"id": "AC-2", "status": "passed"}], idempotency_key="version-b", tested_version="build-b")
        self.assertEqual(self.learning.get_acceptance(self.request_id)["items"][0]["human_status"], "unverified")
        self.feedback([{"id": "AC-1", "status": "passed"}], idempotency_key="env-b", tested_version="build-b", environment="验收环境乙")
        result = self.learning.get_acceptance(self.request_id)
        self.assertEqual(result["items"][1]["human_status"], "unverified")
        self.assertEqual(result["environment"], "验收环境乙")

    def test_unknown_duplicate_and_non_actionable_feedback_rejected(self) -> None:
        for items in ([{"id": "AC-99", "status": "passed"}], [{"id": "AC-1", "status": "failed"}],
                      [{"id": "AC-1", "status": "passed"}, {"id": "AC-01", "status": "passed"}]):
            with self.assertRaises(ValueError):
                self.feedback(items)
        self.assertEqual(self.learning.get_acceptance(self.request_id)["rounds"], [])

    def test_preview_conservatively_parses_separate_failures(self) -> None:
        preview = self.learning.preview_feedback(self.request_id, "第1点未完成，第2点看不到待办，其余通过")
        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual({item["id"]: item["status"] for item in preview["items"]}, {"AC-01": "failed", "AC-02": "failed"})
        self.assertEqual(self.learning.get_acceptance(self.request_id)["rounds"], [])
        uncertain = self.learning.preview_feedback(self.request_id, "第1点不一定通过；第2点未验证")
        self.assertEqual(uncertain["items"], [])

    def test_preview_only_marks_remaining_pass_when_explicit(self) -> None:
        preview = self.learning.preview_feedback(self.request_id, "第1点未通过")
        self.assertEqual(len(preview["items"]), 1)
        preview = self.learning.preview_feedback(self.request_id, "第1点未通过，其余通过")
        self.assertEqual({item["id"]: item["status"] for item in preview["items"]}, {"AC-01": "failed", "AC-02": "passed"})

    def test_atomic_repair_is_linked_idempotent_and_does_not_copy_human_pass(self) -> None:
        feedback = self.feedback([{"id": "AC-1", "status": "failed", "actual": "仍显示旧值"}, {"id": "AC-2", "status": "passed"}])
        with ThreadPoolExecutor(max_workers=2) as executor:
            repairs = list(executor.map(lambda _: self.learning.create_repair(self.request_id, self.owner, feedback["id"]), [1, 2]))
        self.assertEqual(repairs[0]["id"], repairs[1]["id"])
        repair = repairs[0]
        self.assertEqual(repair["root_request_id"], self.request_id)
        self.assertEqual(repair["parent_request_id"], self.request_id)
        self.assertEqual(repair["repair_round"], 1)
        self.assertEqual(repair["failed_item_ids"], ["AC-01"])
        self.assertEqual(repair["protected_item_ids"], ["AC-02"])
        self.assertEqual(self.learning.get_acceptance(repair["id"])["status"], "pending")
        self.assertEqual(self.db.request_detail(self.request_id)["status"], "delivered")

    def test_candidate_dossier_promoted_only_after_full_human_acceptance(self) -> None:
        experience = self.learning.list_experiences(self.project["id"])["items"][0]
        self.assertEqual(experience["status"], "candidate")
        self.assertEqual(experience["acceptance_summary"]["unverified"], 2)
        with self.assertRaises(ValueError):
            self.learning.set_experience_status(experience["id"], "verified", self.admin["id"], "模型认为完成")
        self.feedback([{"id": "AC-1", "status": "passed"}, {"id": "AC-2", "status": "passed"}])
        verified = self.learning.get_experience(experience["id"])
        self.assertEqual(verified["status"], "verified")
        self.assertGreaterEqual(len(verified["revisions"]), 2)
        self.assertEqual(verified["test_environment_validation"], "deferred")
        self.learning.set_experience_status(experience["id"], "deprecated", self.admin["id"], "新版本逻辑已替代")
        self.learning.sync_experience(self.request_id)
        self.assertEqual(self.learning.get_experience(experience["id"])["status"], "deprecated")
        self.assertEqual(self.learning.retrieve_lessons(self.project["project_key"], 901, "单位查询"), [])

    def test_related_different_requirement_retrieved_but_other_project_isolated(self) -> None:
        related = self.learning.retrieve_lessons(self.project["project_key"], 902, "副值待办与单位查询")
        self.assertEqual([item["request_id"] for item in related], [self.request_id])
        self.assertIn("同项目相关", related[0]["match_reason"])
        self.assertEqual(self.learning.retrieve_lessons("unrelated-project", 901, "单位查询"), [])
        self.assertEqual(self.learning.retrieve_lessons(self.project["project_key"], 901, "单位查询", request_id=self.request_id), [])

    def test_sync_is_idempotent_and_dossier_redacts_secret_material(self) -> None:
        self.db.update_request(self.request_id, project_retrospective={"scope": "单位查询", "implementation": "token=hidden-value password=supersecret",
                               "lessons": [{"lesson": "按登录单位查询", "access_token": "never-store"}]})
        first = self.learning.sync_experience(self.request_id)
        second = self.learning.sync_experience(self.request_id)
        self.assertEqual(first["updated_at"], second["updated_at"])
        rendered = str(self.learning.get_experience(first["id"]))
        self.assertNotIn("supersecret", rendered)
        self.assertNotIn("never-store", rendered)
        self.assertNotIn("hidden-value", rendered)

    def test_repeat_init_preserves_feedback_and_additive_schema(self) -> None:
        self.feedback([{"id": "AC-1", "status": "passed"}])
        self.db.init_db()
        self.assertEqual(len(self.learning.get_acceptance(self.request_id)["rounds"]), 1)
        self.assertEqual(self.learning.backfill_experiences(), 0)
        columns = {row["name"] for row in self.db.rows("PRAGMA table_info(delivery_requests)")}
        self.assertTrue({"repair_context", "project_retrospective", "acceptance_owner_id"}.issubset(columns))

    def test_legacy_wrong_json_types_and_null_evidence_do_not_break_delivery(self) -> None:
        self.db.update_request(self.request_id, status="delivered", project_retrospective="[]", repository_states="{}",
                               acceptance_ledger=[{"id": "AC-1", "status": "completed", "tests": None, "evidence": None}])
        item = self.learning.get_acceptance(self.request_id)["items"][0]
        self.assertEqual(item["tests"], [])
        self.assertEqual(item["evidence"], [])
        self.assertEqual(self.learning.sync_experience(self.request_id)["retrospective"], {})


if __name__ == "__main__":
    unittest.main()
