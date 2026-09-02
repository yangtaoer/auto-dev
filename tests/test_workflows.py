from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import httpx
from PIL import Image


TEST_DATA = tempfile.TemporaryDirectory()
os.environ["AUTODEV_DATA_DIR"] = TEST_DATA.name
os.environ["AUTODEV_WORKER_ENABLED"] = "0"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
os.environ["BOOTSTRAP_PM_PASSWORD"] = "pm123456"
os.environ["AUTODEV_RUNNER_TOKEN"] = "test-runner-token"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SCHEMA, init_db, update_request, update_step  # noqa: E402
from app.local_runner_main import report_initial_heartbeat  # noqa: E402
from app.orchestrator import Worker, worker  # noqa: E402
from app.project_catalog import (  # noqa: E402
    _repository_origin,
    load_project_presets,
    repository_tfs_paths,
    resolve_project_for_work_item,
    resolve_projects_for_work_item,
    update_project_routing_aliases,
    matching_project_terms,
)
from app.services.development_risks import development_risks  # noqa: E402
from app.services.quality_gates import evaluate_development_quality  # noqa: E402
from app.services.codex_runner import CodexRunner  # noqa: E402
from app.services.dm7_plugin import discover_dm7_plugin  # noqa: E402
from app.services.delivery import (  # noqa: E402
    ArtifactService,
    Mailer,
    added_files,
    changed_files,
    menu_link_from_view_path,
    repository_short_name,
)
from app.services.pipeline_release import TfsPipelineReleaseService  # noqa: E402
from app.services.tfs import (  # noqa: E402
    ACTUAL_DELIVERY_VERSION_FIELD,
    DELIVERY_ARTIFACTS_FIELD,
    LICENSE_PRODUCT_FIELD,
    LICENSE_PRODUCT_LINE_FIELD,
    LICENSE_PROVINCE_FIELD,
    LICENSE_PURPOSE_FIELD,
    LICENSE_REGION_FIELD,
    LICENSE_REQUESTED_AT_FIELD,
    TfsClient,
)
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

    def setUp(self) -> None:
        self.client.post("/api/auth/logout")
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_existing_database_is_migrated_before_joint_index_creation(self) -> None:
        legacy_request_definitions = (
            "    joint_group_id TEXT,\n",
            "    joint_project_index INTEGER NOT NULL DEFAULT 0,\n",
            "    joint_project_count INTEGER NOT NULL DEFAULT 1,\n",
            "    task_type TEXT NOT NULL DEFAULT 'development',\n",
            "    analysis_result TEXT NOT NULL DEFAULT '{}',\n",
        )
        legacy_intake_definitions = (
            "    result_request_ids TEXT NOT NULL DEFAULT '[]',\n",
            "    matched_project_keys TEXT NOT NULL DEFAULT '[]',\n",
            "    classification_summary TEXT NOT NULL DEFAULT '[]',\n",
            "    title TEXT NOT NULL DEFAULT '',\n",
            "    completed_at TEXT,\n",
            "    review_email_sent_at TEXT,\n",
            "    email_sent_at TEXT,\n",
            "    task_type TEXT NOT NULL DEFAULT 'development',\n",
        )
        legacy_schema = SCHEMA.replace("    repository_tfs_paths TEXT NOT NULL DEFAULT '{}',\n", "", 1)
        for definition in legacy_request_definitions:
            legacy_schema = legacy_schema.replace(definition, "", 1)
        intake_start = legacy_schema.index("CREATE TABLE IF NOT EXISTS request_intakes")
        intake_end = legacy_schema.index("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_intake_work_item")
        intake_schema = legacy_schema[intake_start:intake_end]
        for definition in legacy_intake_definitions:
            intake_schema = intake_schema.replace(definition, "", 1)
        legacy_schema = legacy_schema[:intake_start] + intake_schema + legacy_schema[intake_end:]

        original_data_dir = settings.data_dir
        with tempfile.TemporaryDirectory() as directory:
            legacy_data_dir = Path(directory)
            database = legacy_data_dir / "autodev.db"
            conn = sqlite3.connect(database)
            try:
                conn.executescript(legacy_schema)
                conn.commit()
            finally:
                conn.close()
            try:
                object.__setattr__(settings, "data_dir", legacy_data_dir)
                init_db()
                conn = sqlite3.connect(database)
                try:
                    request_columns = {row[1] for row in conn.execute("PRAGMA table_info(delivery_requests)")}
                    intake_columns = {row[1] for row in conn.execute("PRAGMA table_info(request_intakes)")}
                    project_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
                    indexes = {row[1] for row in conn.execute("PRAGMA index_list(delivery_requests)")}
                finally:
                    conn.close()
                self.assertTrue({
                    "joint_group_id", "joint_project_index", "joint_project_count", "task_type", "analysis_result",
                    "history_context", "acceptance_ledger", "quality_gate_result",
                } <= request_columns)
                self.assertTrue({"result_request_ids", "matched_project_keys", "classification_summary", "task_type"} <= intake_columns)
                self.assertTrue({"repository_tfs_paths", "quality_profile", "artifact_policy"} <= project_columns)
                self.assertIn("ix_delivery_requests_joint_group", indexes)
            finally:
                object.__setattr__(settings, "data_dir", original_data_dir)

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

    def submit_and_process(
        self,
        project_id: int,
        work_item_id: int,
        delivery_options: list[str] | None = None,
    ) -> dict:
        payload = {"project_id": project_id, "work_item_id": work_item_id}
        if delivery_options is not None:
            payload["delivery_options"] = delivery_options
        response = self.client.post("/api/requests", json=payload)
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

    def test_existing_target_branch_implementation_is_verified_without_duplicate_pr(self) -> None:
        project_id = self.create_project("test-existing-implementation", "product_manual_review")
        created = self.client.post(
            "/api/requests",
            json={"project_id": project_id, "work_item_id": 910090, "delivery_options": []},
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["id"]
        result = {
            "decision": "already_satisfied",
            "summary": "最新 dev 已包含完整实现，本次仅复核证据。",
            "changed_files": [],
            "acceptance_mapping": ["最新分支实现已覆盖验收项"],
            "acceptance_ledger": [{
                "id": "AC-1", "criterion": "复用既有实现", "status": "completed",
                "repositories": ["demo"], "files": [], "tests": ["目标分支回归通过"],
                "evidence": ["提交 abc123 已进入 dev"],
            }],
            "business_invariants": [],
            "database_validation": {},
            "visual_validation": {},
            "deployment_validation": {},
            "menu_changes": [],
            "existing_implementation": {
                "verified": True,
                "source_commits": ["abc123"],
                "source_prs": ["#321"],
                "evidence": ["dev:src/ExistingFeature.java"],
            },
            "risks": [], "blocking_risks": [], "sql_changes": [], "config_changes": [],
            "database_operations": [], "supplement_requests": [],
        }
        with patch.object(worker, "_simulate_development", return_value=result), patch.object(
            worker, "_send_status_email"
        ) as send_mail:
            worker.process_once()
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(detail["status"], "delivered")
        self.assertIsNone(detail["pr_id"])
        self.assertTrue(any(item["kind"] == "verification_report" for item in detail["artifacts"]))
        self.assertTrue(any(item["event_type"] == "development.already_satisfied" for item in detail["events"]))
        send_mail.assert_called_once_with(request_id, action_required=False)

    def test_problem_analysis_completes_without_code_changes_and_delivers_report(self) -> None:
        project_id = self.create_project("test-analysis", "product_manual_review")
        created = self.client.post(
            "/api/requests",
            json={
                "project_id": project_id,
                "work_item_id": 910120,
                "task_type": "analysis",
                "delivery_options": [],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["id"]
        worker.process_once()

        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(detail["task_type"], "analysis")
        self.assertEqual(detail["task_type_label"], "问题分析")
        self.assertEqual(detail["status"], "delivered")
        self.assertEqual(detail["status_label"], "分析完成")
        self.assertEqual(detail["delivery_options"], [])
        self.assertEqual(detail["analysis_result"]["decision"], "completed")
        self.assertEqual(detail["analysis_result"]["changed_files"], [])
        self.assertEqual(detail["analysis_result"]["confidence"], "high")
        self.assertIn("调用条件未满足", detail["analysis_result"]["root_cause"])
        kinds = {artifact["kind"] for artifact in detail["artifacts"]}
        self.assertIn("analysis_report", kinds)
        self.assertNotIn("package", kinds)
        self.assertNotIn("release_artifact", kinds)
        report = next(item for item in detail["artifacts"] if item["kind"] == "analysis_report")
        report_text = Path(report["local_path"]).read_text(encoding="utf-8")
        self.assertIn("问题分析报告", report_text)
        self.assertIn("证据链", report_text)
        self.assertIn("工作区零改动", report_text)
        self.assertEqual(
            [step["step_code"] for step in detail["steps"]],
            ["validate", "prepare", "develop", "clarify", "deliver"],
        )
        self.assertEqual(next(step for step in detail["steps"] if step["step_code"] == "develop")["name"], "DevCore 问题分析")
        self.assertTrue(any(event["event_type"] == "analysis.delivered" for event in detail["events"]))
        page = self.client.get("/")
        self.assertIn('id="open-analysis"', page.text)
        self.assertIn('id="analysis-mode-note"', page.text)
        self.assertIn('value="analysis"', page.text)

    def test_problem_analysis_email_uses_analysis_semantics(self) -> None:
        detail = {
            "id": "analysis-mail",
            "work_item_id": 910121,
            "title": "页面空白问题分析",
            "requirement_summary": "只分析原因，不修改代码。",
            "project_name": "南充网络发令",
            "requester_name": "项目经理",
            "delivery_mode": "product_manual_review",
            "task_type": "analysis",
            "status": "delivered",
            "result_summary": "已定位组织关系不匹配。",
            "created_at": "2026-08-28T01:00:00+00:00",
            "started_at": "2026-08-28T01:01:00+00:00",
            "completed_at": "2026-08-28T01:08:00+00:00",
            "branch_name": "feature/910121-yangtao",
            "commit_hash": None,
            "pr_url": None,
            "artifacts": [{"id": 88, "kind": "analysis_report", "name": "问题分析报告.md", "external_url": "https://example.test/report.md"}],
            "delivery_options": [],
        }
        mailer = Mailer()
        self.assertEqual(
            mailer.delivery_subject(detail),
            "【AutoDev · 分析完成】TFS #910121｜页面空白问题分析",
        )
        rendered = mailer.delivery_html(detail)
        self.assertIn("问题分析完成", rendered)
        self.assertIn("分析耗时", rendered)
        self.assertIn("分析报告 / ANALYSIS REPORT", rendered)
        self.assertIn("下载报告", rendered)
        self.assertNotIn("代码信息 / CODE DELIVERY", rendered)

    def test_missing_critical_information_waits_for_user_and_resumes_same_task(self) -> None:
        project_id = self.create_project("test-supplement", "local_package")
        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910101}
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["id"]
        needs_input = {
            "decision": "needs_input",
            "summary": "需要确认季度最终分的权威数据来源。",
            "changed_files": [],
            "acceptance_mapping": ["已完成现状分析"],
            "risks": ["缺少权威口径"],
            "sql_changes": [],
            "config_changes": [],
            "database_operations": ["已检查本机开发库元数据"],
            "supplement_requests": [
                {
                    "id": "quarter-score-source",
                    "question": "季度最终分以哪张表或接口为准？",
                    "reason": "该数据决定折算结果。",
                    "suggested_answer": "请提供表名、主键和结算状态字段。",
                    "required": True,
                }
            ],
        }
        with patch.object(worker, "_simulate_development", return_value=needs_input):
            worker.process_once()
        waiting = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(waiting["status"], "waiting_input")
        self.assertEqual(waiting["current_step"], "clarify")
        self.assertEqual(waiting["supplement_requests"][0]["id"], "quarter-score-source")
        self.assertIn("权威数据来源", waiting["result_summary"])
        clarify = next(item for item in waiting["steps"] if item["step_code"] == "clarify")
        self.assertEqual(clarify["status"], "running")
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(dashboard["stats"]["waiting_input"], 1)
        self.assertFalse(any(item["id"] == request_id for item in dashboard["active"]))

        missing = self.client.post(f"/api/requests/{request_id}/supplement", json={"answers": []})
        self.assertEqual(missing.status_code, 422)
        supplied = self.client.post(
            f"/api/requests/{request_id}/supplement",
            json={"answers": [{"id": "quarter-score-source", "answer": "以 TH_BIZ_QUARTER_RESULT 的 RESOLVED 记录为准。"}]},
        )
        self.assertEqual(supplied.status_code, 200, supplied.text)
        self.assertEqual(supplied.json()["id"], request_id)
        queued = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["supplement_answers"][0]["id"], "quarter-score-source")

        completed = {
            "decision": "completed",
            "summary": "已按补充的权威季度结果表完成研发。",
            "changed_files": ["src/demo/FeatureService.java"],
            "acceptance_mapping": ["季度最终分已接入"],
            "risks": [],
            "sql_changes": ["sql/upgrade.sql"],
            "config_changes": [],
            "database_operations": ["核验 TH_BIZ_QUARTER_RESULT 结构"],
            "supplement_requests": [],
        }
        with patch.object(worker, "_simulate_development", return_value=completed):
            worker.process_once()
        delivered = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(delivered["status"], "delivered")
        self.assertTrue(any(event["event_type"] == "development.input_supplied" for event in delivered["events"]))

    def test_dm7_plugin_discovery_builds_direct_mcp_mount_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_root = Path(directory)
            plugin_root = codex_root / "plugins" / "cache" / "dm7-database-local" / "dm7-database" / "0.1-test"
            skill = plugin_root / "skills" / "dm7-database" / "SKILL.md"
            launcher = plugin_root / "scripts" / "launch-mcp.ps1"
            skill.parent.mkdir(parents=True)
            launcher.parent.mkdir(parents=True)
            skill.write_text("# DM7", encoding="utf-8")
            launcher.write_text("Write-Output ready", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_root)}, clear=False):
                capability = discover_dm7_plugin()
        self.assertTrue(capability.available)
        self.assertEqual(capability.skill_path, skill)
        mounted = "\n".join(capability.config_overrides)
        self.assertIn("mcp_servers.dm7_autodev.command", mounted)
        self.assertIn("launch-mcp.ps1", mounted)
        self.assertIn('default_tools_approval_mode="approve"', mounted)
        self.assertNotIn("password", mounted.casefold())

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
        self.assertEqual(mailer.sender_address().display_name, "AutoDev · 自主研发交付")
        self.assertEqual(
            mailer.delivery_subject(detail),
            "【AutoDev · 已交付】TFS #910014｜优化交付邮件",
        )
        self.assertIn("AutoDev · DELIVERY SIGNAL", rendered)
        self.assertIn("2026-08-06 11:50:00（UTC+8）", rendered)
        self.assertIn("2026-08-06 11:56:45（UTC+8）", rendered)
        self.assertIn("5 分 24 秒", rendered)
        self.assertIn("下载产物", rendered)
        self.assertIn("提交人", rendered)
        self.assertIn("cid:autodev-brand-mark", rendered)
        self.assertIn("background:#171813", rendered)
        self.assertIn("background:#e8e3d8", rendered)
        self.assertIn("#246b5a", mailer.delivery_html(detail, action_required=True))
        self.assertNotIn("#62e59b", rendered)
        self.assertNotIn("#087a49", rendered)
        self.assertIn('width="860"', rendered)
        self.assertIn('max-width:860px', rendered)
        self.assertIn('width="25%"', rendered)
        self.assertIn('width="33.33%"', rendered)
        message = mailer.build_message(
            to=["recipient@example.com"], subject=mailer.delivery_subject(detail), html_body=rendered
        )
        inline_images = [part for part in message.walk() if part.get_content_type() == "image/png"]
        self.assertEqual(message["Cc"], "yangtao2@tellhow.com")
        self.assertEqual(len(inline_images), 1)
        self.assertEqual(inline_images[0]["Content-ID"], "<autodev-brand-mark>")
        self.assertEqual(inline_images[0].get_filename(), "autodev-email-mark.png")

        waiting = mailer.delivery_html({**detail, "completed_at": None, "pr_url": "https://tfs.test/pr/14"}, action_required=True)
        self.assertIn("需要项目经理协同处理", waiting)
        self.assertIn("请逐个联系有权限的同事审核并合并以下 PR", waiting)
        self.assertIn("主仓库 · PR #", waiting)
        self.assertIn("进行中", waiting)

        input_detail = {
            **detail,
            "status": "waiting_input",
            "completed_at": None,
            "supplement_requests": [
                {
                    "id": "score-source",
                    "question": "季度最终分以哪张表为准？",
                    "reason": "决定绩效折算口径。",
                    "suggested_answer": "提供表名与结算状态字段。",
                    "required": True,
                }
            ],
        }
        input_mail = mailer.delivery_html(input_detail, action_required=True)
        self.assertEqual(
            mailer.delivery_subject(input_detail, action_required=True),
            "【AutoDev · 待补充】TFS #910014｜优化交付邮件",
        )
        self.assertIn("AutoDev · INPUT SIGNAL", input_mail)
        self.assertIn("等待补充研发信息", input_mail)
        self.assertIn("季度最终分以哪张表为准", input_mail)
        self.assertIn("登录 AutoDev 补充并继续", input_mail)
        self.assertNotIn("需要项目经理协同处理", input_mail)

        blocked_detail = {
            **detail,
            "status": "waiting_approval",
            "completed_at": None,
            "error_message": "阻塞风险：缺少 APP 客户端仓库 <client>",
            "artifacts": [],
        }
        blocked_mail = mailer.delivery_html(blocked_detail, action_required=True)
        self.assertIn("AutoDev · 待确认", mailer.delivery_subject(blocked_detail, action_required=True))
        self.assertIn("等待风险确认", blocked_mail)
        self.assertIn("缺少 APP 客户端仓库 &lt;client&gt;", blocked_mail)
        self.assertNotIn("无需 PR", blocked_mail)
        self.assertNotIn("等待代码合并", blocked_mail)
        self.assertNotIn("请逐个联系有权限的同事审核并合并", blocked_mail)

        failed_detail = {**detail, "status": "failed", "error_message": "Filename too long"}
        failed = mailer.delivery_html(failed_detail, terminal_status="failed")
        self.assertEqual(
            mailer.delivery_subject(failed_detail, terminal_status="failed"),
            "【AutoDev · 执行失败】TFS #910014｜优化交付邮件",
        )
        self.assertIn("AutoDev · TERMINAL SIGNAL", failed)
        self.assertIn("研发执行失败", failed)
        self.assertIn("Filename too long", failed)
        self.assertIn("终止原因 / TERMINATION REASON", failed)
        self.assertIn("任务耗时", failed)
        self.assertIn("已有产物 / AVAILABLE FILES", failed)
        self.assertIn("任务已取消", mailer.delivery_subject(failed_detail, terminal_status="cancelled"))
        self.assertIn("准入驳回", mailer.delivery_subject(failed_detail, terminal_status="rejected"))

    def test_product_review_waiting_email_lists_every_pr_without_code_files(self) -> None:
        detail = {
            "id": "product-review-mail",
            "work_item_id": 910017,
            "title": "新增网络发令总览",
            "requirement_summary": "新增总览视图。",
            "project_name": "南充网络发令",
            "requester_name": "杨涛",
            "delivery_mode": "product_manual_review",
            "result_summary": "已完成开发，等待产品审核。",
            "created_at": "2026-08-07T01:00:00+00:00",
            "started_at": "2026-08-07T01:01:00+00:00",
            "completed_at": None,
            "branch_name": "feature/910017-yangtao",
            "commit_hash": "abcdef1234567890",
            "pr_id": 201,
            "pr_url": "https://tfs.test/pr/201",
            "repository_states": [
                {"name": "dcsd-direct-ui", "repository_short_name": "direct-ui", "pr_id": 201, "pr_url": "https://tfs.test/pr/201"},
                {"name": "dcsd-notice-srv", "repository_short_name": "notice-srv", "pr_id": 202, "pr_url": "https://tfs.test/pr/202"},
            ],
            "artifacts": [
                {"id": 1, "kind": "config", "name": "secret-source.yml", "external_url": "https://files.test/source.yml"},
                {"id": 2, "kind": "menu_link", "name": "/direct/views/operationTicketOverview", "external_url": "/direct/views/operationTicketOverview"},
            ],
        }
        rendered = Mailer().delivery_html(detail, action_required=True)
        self.assertIn("direct-ui · PR #201", rendered)
        self.assertIn("notice-srv · PR #202", rendered)
        self.assertIn("https://tfs.test/pr/201", rendered)
        self.assertIn("https://tfs.test/pr/202", rendered)
        self.assertIn("/direct/views/operationTicketOverview", rendered)
        self.assertIn("新增视图菜单链接", rendered)
        self.assertNotIn("secret-source.yml", rendered)
        self.assertNotIn("代码信息 / CODE DELIVERY", rendered)

        fallback_detail = {
            **detail,
            "delivery_mode": "sichuan_auto_review",
            "repository_states": [
                {**detail["repository_states"][0], "status": "completed"},
                {**detail["repository_states"][1], "status": "waiting_merge"},
            ],
        }
        fallback = Mailer().delivery_html(fallback_detail, action_required=True)
        self.assertNotIn("direct-ui · PR #201", fallback)
        self.assertIn("notice-srv · PR #202", fallback)
        self.assertNotIn("代码信息 / CODE DELIVERY", fallback)

    def test_view_xml_path_becomes_menu_link_and_tfs_manifest_contains_artifacts(self) -> None:
        source = "src/main/resources/META-INF/resources/tbp_config/runtime/module/direct/views/operationTicketOverview.view.xml"
        self.assertEqual(menu_link_from_view_path(source), "/direct/views/operationTicketOverview")
        self.assertIsNone(menu_link_from_view_path(source.replace(".view.xml", ".form.xml")))
        service = ArtifactService(public_base_url="https://auto.example.test")
        detail = {
            "delivery_mode": "product_manual_review",
            "delivery_options": ["merge_screenshot", "license_request", "auto_release"],
            "artifacts": [
                {"id": 7, "kind": "merge_screenshot", "name": "notice-srv · PR #202 · 合并截图.png", "external_url": "https://oss.test/pr-202.png"},
                {"id": 8, "kind": "menu_link", "name": "/direct/views/operationTicketOverview", "external_url": "/direct/views/operationTicketOverview"},
                {"id": 9, "kind": "config", "name": "source.yml", "external_url": "https://oss.test/source.yml"},
                {"id": 10, "kind": "license_request", "name": "License 授权申请 #1652475", "external_url": "https://tfs.test/_workitems/edit/1652475"},
                {"id": 11, "kind": "release_artifact", "name": "自动发版 · Build #1170472", "external_url": "https://tfs.test/DCS/_build/results?buildId=1170472&view=artifacts"},
            ],
        }
        manifest = service.delivery_manifest_html(detail)
        self.assertIn("https://oss.test/pr-202.png", manifest)
        self.assertIn("/direct/views/operationTicketOverview", manifest)
        self.assertIn("新增视图菜单链接", manifest)
        self.assertIn("License 授权申请", manifest)
        self.assertIn("https://tfs.test/_workitems/edit/1652475", manifest)
        self.assertIn("自动发版产物", manifest)
        self.assertIn("buildId=1170472", manifest)
        self.assertNotIn("source.yml", manifest)

    def test_only_new_view_xml_files_become_menu_link_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "AutoDev Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "autodev-test@example.com"],
                check=True,
            )
            views = repository / "src/main/resources/META-INF/resources/tbp_config/runtime/module/direct/views"
            views.mkdir(parents=True)
            existing = views / "existing.view.xml"
            existing.write_text("<view version='1' />\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "initial"], check=True)
            base_commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            existing.write_text("<view version='2' />\n", encoding="utf-8")
            added = views / "operationTicketOverview.view.xml"
            added.write_text("<view />\n", encoding="utf-8")
            records: list[tuple[str, str, str]] = []

            def record(_request_id: str, kind: str, name: str, _local_path: str, external_url: str) -> int:
                records.append((kind, name, external_url))
                return len(records)

            service = ArtifactService(
                recorder=record,
                detail_loader=lambda _request_id: {"artifacts": []},
            )
            ids = service.collect_menu_links("new-page-request", repository, base_commit)
            self.assertIn(
                "src/main/resources/META-INF/resources/tbp_config/runtime/module/direct/views/operationTicketOverview.view.xml",
                added_files(repository, base_commit),
            )
            self.assertEqual(ids, [1])
            self.assertEqual(
                records,
                [("menu_link", "/direct/views/operationTicketOverview", "/direct/views/operationTicketOverview")],
            )
            self.assertNotIn("/direct/views/existing", {item[1] for item in records})

    def test_tfs_license_application_embeds_merge_screenshots_and_required_fields(self) -> None:
        client = TfsClient("https://tfs.test/DefaultCollection", pat="test-pat")
        calls: list[tuple[str, str, dict]] = []

        def fake_request(method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            if "/wiql" in url:
                return {"workItems": []}
            if "/attachments" in url:
                attachment_number = sum(1 for _, value, _ in calls if "/attachments" in value)
                return {"url": f"https://tfs.test/attachments/image-{attachment_number}.png"}
            if "/workitems/$" in url:
                return {
                    "id": 1653001,
                    "_links": {"html": {"href": "https://tfs.test/_workitems/edit/1653001"}},
                }
            raise AssertionError(f"未预期的 TFS 请求：{method} {url}")

        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "notice.png"
            second = Path(temporary) / "direct.png"
            first.write_bytes(b"\x89PNG\r\n\x1a\nnotice")
            second.write_bytes(b"\x89PNG\r\n\x1a\ndirect")
            with patch.object(client, "_request", side_effect=fake_request):
                result = client.create_license_application(
                    request_id="license-request-001",
                    source_work_item_id=910030,
                    delivery_project_name="四川省调网络发令",
                    screenshot_sources=[("notice-srv 合并截图.png", str(first)), ("direct-ui 合并截图.png", str(second))],
                )

        create_call = next(item for item in calls if "/workitems/$" in item[1])
        patch_body = create_call[2]["json"]
        fields = {item["path"].removeprefix("/fields/"): item["value"] for item in patch_body}
        self.assertEqual(fields["System.Title"], "【四川省调网络发令】现场自测包申请")
        self.assertEqual(fields["System.AssignedTo"], r"TELLHOW\zhoudanping")
        self.assertEqual(fields[LICENSE_PROVINCE_FIELD], "四川")
        self.assertEqual(fields[LICENSE_REGION_FIELD], "西南地区部")
        self.assertEqual(fields[LICENSE_PRODUCT_LINE_FIELD], "调度产品线")
        self.assertEqual(fields[LICENSE_PRODUCT_FIELD], "主配网调度运行指挥系统")
        self.assertEqual(fields[LICENSE_PURPOSE_FIELD], "本地自研需求测试")
        self.assertTrue(fields[LICENSE_REQUESTED_AT_FIELD].endswith("Z"))
        self.assertEqual(fields["System.Description"].count("<img "), 2)
        self.assertNotIn("<a ", fields["System.Description"])
        attachment_relations = [item for item in patch_body if item["path"] == "/relations/-"]
        self.assertEqual(len(attachment_relations), 2)
        self.assertTrue(all(item["value"]["rel"] == "AttachedFile" for item in attachment_relations))
        self.assertIn("AutoDev-license-request-001", fields["System.Tags"])
        self.assertIn("License%E6%8E%88%E6%9D%83%E7%94%B3%E8%AF%B7", create_call[1])
        self.assertEqual(result["id"], 1653001)
        self.assertEqual(result["url"], "https://tfs.test/_workitems/edit/1653001")
        self.assertTrue(result["created"])

    def test_tfs_requirement_images_are_downloaded_for_codex_with_auth_context(self) -> None:
        client = TfsClient("https://tfs.test/DefaultCollection", pat="test-pat")
        work_item = {
            "id": 910031,
            "revision": 7,
            "description": (
                '<p>现场现象</p><img src="https://tfs.test/DefaultCollection/_apis/wit/attachments/one?fileName=screen-one.png">'
            ),
            "acceptance_criteria": "<p>以截图为准</p>",
            "relations": [
                {
                    "rel": "AttachedFile",
                    "url": "https://tfs.test/DefaultCollection/_apis/wit/attachments/two?fileName=screen-two.jpg",
                    "attributes": {"name": "screen-two.jpg"},
                },
                {
                    "rel": "AttachedFile",
                    "url": "https://tfs.test/DefaultCollection/_apis/wit/attachments/three?fileName=mapper.xml",
                    "attributes": {"name": "mapper.xml"},
                },
            ],
        }
        png = b"\x89PNG\r\n\x1a\nrequirement"
        with tempfile.TemporaryDirectory() as directory, patch.object(
            client, "_download_requirement_image", return_value=(png, "image/png")
        ) as download:
            result = client.download_requirement_images(work_item, Path(directory))
            downloaded = [Path(value) for value in result["paths"]]
            self.assertEqual(download.call_count, 2)
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(downloaded), 2)
            self.assertTrue(all(path.is_file() for path in downloaded))
            self.assertEqual({path.suffix for path in downloaded}, {".png"})

        events: list[tuple[str, str]] = []
        with patch.object(
            TfsClient,
            "download_requirement_images",
            return_value={"paths": [r"C:\autodev\requirements\screen-one.png"], "errors": []},
        ):
            context = CodexRunner._requirement_image_context(
                work_item,
                {"tfs_collection_url": "https://tfs.test/DefaultCollection"},
                lambda event, message: events.append((event, message)),
            )
        self.assertIn("必须逐张", context)
        self.assertIn("screen-one.png", context)
        self.assertEqual(events[0][0], "tfs.images_downloaded")

    def test_tfs_delivery_artifacts_updates_discovered_html_field(self) -> None:
        client = TfsClient("https://tfs.test/DefaultCollection", pat="test-pat")
        with patch.object(client, "_request", return_value={}) as request:
            client.update_delivery_artifacts(910017, "<ul><li>交付产物</li></ul>")
        args, kwargs = request.call_args
        self.assertEqual(args[0], "PATCH")
        self.assertIn("/workitems/910017", args[1])
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json-patch+json")
        self.assertEqual(kwargs["json"][0]["path"], f"/fields/{DELIVERY_ARTIFACTS_FIELD}")

    def test_tfs_delivery_completion_sets_resolved_state_and_actual_version(self) -> None:
        client = TfsClient("https://tfs.test/DefaultCollection", pat="test-pat")
        updated = {
            "fields": {
                "System.State": "已解决",
                ACTUAL_DELIVERY_VERSION_FIELD: "V1.0",
            }
        }
        with patch.object(client, "get_work_item", return_value={"state": "已评审"}), patch.object(
            client, "_request", return_value=updated
        ) as request:
            result = client.complete_delivery(910018, "<ul><li>交付产物</li></ul>")
        patch_body = request.call_args.kwargs["json"]
        self.assertIn({"op": "replace", "path": "/fields/System.State", "value": "已解决"}, patch_body)
        self.assertIn(
            {"op": "add", "path": f"/fields/{ACTUAL_DELIVERY_VERSION_FIELD}", "value": "V1.0"},
            patch_body,
        )
        self.assertEqual(result["state"], "已解决")
        self.assertEqual(result["actual_version"], "V1.0")

    def test_favicon_is_transparent_symbol_without_square_plate(self) -> None:
        with Image.open(Path("app/static/brand/favicon.ico")) as source:
            icon = source.convert("RGBA")
        self.assertEqual(icon.getpixel((0, 0))[3], 0)
        self.assertLess(icon.getbbox()[1], icon.height)

        with Image.open(Path("app/static/brand/autodev-sidebar-mark.png")) as source:
            sidebar_mark = source.convert("RGBA")
        self.assertEqual(sidebar_mark.size, (560, 200))
        self.assertEqual(sidebar_mark.getpixel((0, 0))[3], 0)

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

    def test_cancelled_and_failed_tasks_send_terminal_email_once(self) -> None:
        project_id = self.create_project("test-terminal-mail", "local_package")
        cancelled_request = self.client.post(
            "/api/requests",
            json={"project_id": project_id, "work_item_id": 910016},
        ).json()["id"]
        with patch("app.main.Mailer.configured", return_value=True), patch("app.main.Mailer.send") as sender:
            cancelled = self.client.post(f"/api/requests/{cancelled_request}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        sender.assert_called_once()
        self.assertIn("【AutoDev · 任务已取消】", sender.call_args.kwargs["subject"])
        self.assertIn("研发任务已取消", sender.call_args.kwargs["html_body"])
        cancelled_detail = self.client.get(f"/api/requests/{cancelled_request}").json()["request"]
        self.assertEqual(cancelled_detail["status"], "cancelled")
        self.assertTrue(cancelled_detail["email_sent_at"])
        self.assertTrue(any(event["event_type"] == "mail.terminal_sent" for event in cancelled_detail["events"]))
        repeated_cancel = self.client.post(f"/api/requests/{cancelled_request}/cancel")
        self.assertEqual(repeated_cancel.status_code, 409, repeated_cancel.text)

        failed_request = self.client.post(
            "/api/requests",
            json={"project_id": project_id, "work_item_id": 910017},
        ).json()["id"]
        with patch("app.orchestrator.Mailer.configured", return_value=True), patch(
            "app.orchestrator.Mailer.send"
        ) as failed_sender:
            worker._fail(failed_request, RuntimeError("模拟 Git 工作区创建失败"))
        failed_sender.assert_called_once()
        self.assertIn("【AutoDev · 执行失败】", failed_sender.call_args.kwargs["subject"])
        self.assertIn("模拟 Git 工作区创建失败", failed_sender.call_args.kwargs["html_body"])
        failed_detail = self.client.get(f"/api/requests/{failed_request}").json()["request"]
        self.assertEqual(failed_detail["status"], "failed")
        self.assertTrue(failed_detail["email_sent_at"])

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

    def test_app_delivery_collects_sql_but_not_xml_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "dcsd-app-starter"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "AutoDev Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "autodev-test@example.com"], check=True)
            mapper = repository / "src" / "Mapper.xml"
            sql = repository / "sql" / "upgrade.sql"
            mapper.parent.mkdir()
            sql.parent.mkdir()
            mapper.write_text("<mapper/>\n", encoding="utf-8")
            sql.write_text("SELECT 1;\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "initial"], check=True)
            base_commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            mapper.write_text("<mapper><select/></mapper>\n", encoding="utf-8")
            sql.write_text("SELECT 2;\n", encoding="utf-8")
            recorded: list[tuple[str, str]] = []
            service = ArtifactService(
                recorder=lambda _request_id, kind, name, _local_path, _external_url="": (
                    recorded.append((kind, name)) or len(recorded)
                )
            )
            service.collect_changed_assets(
                "app-artifact-policy",
                repository,
                base_commit,
                {"sql_patterns": ["**/*.sql"], "config_patterns": []},
                repository_name="dcsd-app-starter",
            )
        self.assertEqual(recorded, [("sql", "dcsd-app-starter/sql/upgrade.sql")])

    def test_prepare_worktrees_enables_long_paths_and_rolls_back_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def create_repository(name: str, branch: str = "dev") -> Path:
                repository = root / name
                origin = root / f"{name}.git"
                subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
                subprocess.run(["git", "init", "-q", "-b", branch, str(repository)], check=True)
                subprocess.run(["git", "-C", str(repository), "config", "user.name", "AutoDev Test"], check=True)
                subprocess.run(
                    ["git", "-C", str(repository), "config", "user.email", "autodev-test@example.com"],
                    check=True,
                )
                (repository / "README.md").write_text("initial\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
                subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "initial"], check=True)
                subprocess.run(["git", "-C", str(repository), "remote", "add", "origin", str(origin)], check=True)
                subprocess.run(["git", "-C", str(repository), "push", "-q", "-u", "origin", branch], check=True)
                return repository

            first = create_repository("first-repo")
            second = create_repository("second-repo", "chongqing")
            worktree_root = root / "worktrees"

            class RecordingStore:
                remote = False

                @staticmethod
                def detail(*_args, **_kwargs):
                    return None

                @staticmethod
                def add_event(*_args, **_kwargs) -> None:
                    return None

                @staticmethod
                def add_artifact(*_args, **_kwargs) -> int:
                    return 1

            worktree_worker = Worker(store=RecordingStore())
            original_run = subprocess.run

            def fail_second_worktree(command, *args, **kwargs):
                if (
                    isinstance(command, list)
                    and "worktree" in command
                    and "add" in command
                    and str(second) in command
                ):
                    raise subprocess.CalledProcessError(
                        128,
                        command,
                        stderr="error: unable to create file deep/path: Filename too long",
                    )
                return original_run(command, *args, **kwargs)

            project = {
                "repository_paths": [str(first), str(second)],
                "base_branch": "dev",
                "repository_base_branches": {"second-repo": "chongqing"},
            }
            with patch(
                "app.config.Settings.worktree_dir",
                new_callable=PropertyMock,
                return_value=worktree_root,
            ), patch("app.orchestrator.subprocess.run", side_effect=fail_second_worktree):
                with self.assertRaisesRegex(RuntimeError, "Filename too long"):
                    worktree_worker._prepare_worktrees(
                        "rollback-request",
                        {"id": 910099},
                        project,
                    )

            for repository in (first, second):
                longpaths = original_run(
                    ["git", "-C", str(repository), "config", "--get", "core.longpaths"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                self.assertEqual(longpaths, "true")
                branch = original_run(
                    ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", "refs/heads/feature/910099-yangtao"]
                ).returncode
                self.assertNotEqual(branch, 0)
            self.assertFalse(worktree_root.joinpath("rollback-request").exists())

    def test_local_package_rebases_and_pushes_latest_dev_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = root / "origin.git"
            seed = root / "seed"
            feature = root / "feature"
            concurrent = root / "concurrent"

            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "dev", str(seed)], check=True)
            for repository in (seed,):
                subprocess.run(["git", "-C", str(repository), "config", "user.name", "AutoDev Test"], check=True)
                subprocess.run(
                    ["git", "-C", str(repository), "config", "user.email", "autodev-test@example.com"],
                    check=True,
                )
            (seed / "README.md").write_text("initial\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(seed), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "initial"], check=True)
            subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(origin)], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "dev"], check=True)

            subprocess.run(["git", "clone", "-q", "-b", "dev", str(origin), str(feature)], check=True)
            subprocess.run(["git", "-C", str(feature), "config", "user.name", "AutoDev Test"], check=True)
            subprocess.run(
                ["git", "-C", str(feature), "config", "user.email", "autodev-test@example.com"], check=True
            )
            subprocess.run(["git", "-C", str(feature), "checkout", "-q", "-b", "feature/910200-yangtao"], check=True)
            (feature / "feature.txt").write_text("delivery change\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(feature), "add", "feature.txt"], check=True)
            subprocess.run(["git", "-C", str(feature), "commit", "-q", "-m", "feature change"], check=True)
            subprocess.run(
                ["git", "-C", str(feature), "push", "-q", "-u", "origin", "feature/910200-yangtao"],
                check=True,
            )

            # 模拟功能分支提交后，另一个开发者又向 dev 推送了一次更新。
            subprocess.run(["git", "clone", "-q", "-b", "dev", str(origin), str(concurrent)], check=True)
            subprocess.run(["git", "-C", str(concurrent), "config", "user.name", "Concurrent Developer"], check=True)
            subprocess.run(
                ["git", "-C", str(concurrent), "config", "user.email", "concurrent@example.com"], check=True
            )
            (concurrent / "concurrent.txt").write_text("latest dev\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(concurrent), "add", "concurrent.txt"], check=True)
            subprocess.run(["git", "-C", str(concurrent), "commit", "-q", "-m", "latest dev change"], check=True)
            subprocess.run(["git", "-C", str(concurrent), "push", "-q", "origin", "dev"], check=True)

            final_commit = Worker._sync_local_package_repository(
                feature,
                repository_name="delivery-repo",
                feature_branch="feature/910200-yangtao",
                target_branch="dev",
                changed=True,
                git_env=os.environ.copy(),
            )
            remote_dev = subprocess.run(
                ["git", "-C", str(feature), "rev-parse", "origin/dev"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            remote_feature = subprocess.run(
                ["git", "-C", str(feature), "rev-parse", "origin/feature/910200-yangtao"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            history = subprocess.run(
                ["git", "-C", str(feature), "log", "--format=%s", "-3", "origin/dev"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

            self.assertEqual(final_commit, remote_dev)
            self.assertEqual(final_commit, remote_feature)
            self.assertIn("feature change", history)
            self.assertIn("latest dev change", history)
            self.assertTrue((feature / "feature.txt").exists())
            self.assertTrue((feature / "concurrent.txt").exists())

    @patch("app.orchestrator.TfsClient.create_pull_request")
    def test_repository_specific_base_branch_is_used_as_pr_target(self, create_pull_request) -> None:
        create_pull_request.return_value = {"PullRequestId": 310, "WebUrl": "https://tfs.test/pr/310"}
        result = worker._create_pr(
            "branch-override-request",
            {"result_summary": "重庆 starter 调整"},
            {
                "simulation_mode": False,
                "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
                "base_branch": "dev",
            },
            Path("C:/work/dcsd-springboot-starter"),
            {"id": 910100, "title": "重庆网络发令"},
            "feature/910100-yangtao",
            target_branch="chongqing",
        )
        self.assertEqual(result["id"], 310)
        self.assertEqual(create_pull_request.call_args.args[2], "chongqing")

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

    def test_artifact_limit_defaults_to_one_gib_in_config_and_templates(self) -> None:
        self.assertEqual(type(settings).__dataclass_fields__["max_artifact_mb"].default, 1024)
        root = Path(__file__).resolve().parents[1]
        for name in (".env.example", "local-runner/.env.runner.example",
                     "deploy/backend/.env.backend.example", "deploy/cloud/.env.production.example"):
            self.assertIn("AUTODEV_MAX_ARTIFACT_MB=1024", (root / name).read_text(encoding="utf-8"))

    def test_oss_upload_accepts_over_200_mib_but_rejects_one_gib_and_above(self) -> None:
        limit = 1024 * 1024 * 1024
        store = RemoteStore("https://cloud.test", "runner-token", "runner-1")
        response = httpx.Response(200, json={"artifact_id": 123}, request=httpx.Request("POST", "https://cloud.test"))
        try:
            for size in (200 * 1024 * 1024 + 1, limit - 1, limit, limit + 1):
                with self.subTest(size=size):
                    artifact = SimpleNamespace(name="app.jar", is_file=lambda: True,
                                               stat=lambda: SimpleNamespace(st_size=size))
                    store.oss_storage = Mock()
                    store.oss_storage.upload.return_value = ("objects/app.jar", "https://oss.test/app.jar")
                    with patch.object(type(settings), "max_artifact_mb", new_callable=PropertyMock, return_value=1024), \
                         patch("app.store.Path", return_value=artifact), \
                         patch.object(store, "_request", return_value=response) as register:
                        if size < limit:
                            self.assertEqual(store.add_artifact("upload-test", "package", "app.jar", "app.jar"), 123)
                            store.oss_storage.upload.assert_called_once_with("upload-test", "package", "app.jar", artifact)
                            self.assertEqual(register.call_args.kwargs["data"]["external_url"], "https://oss.test/app.jar")
                            self.assertNotIn("files", register.call_args.kwargs)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "必须小于 1024 MB"):
                                store.add_artifact("upload-test", "package", "app.jar", "app.jar")
                            store.oss_storage.upload.assert_not_called()
                            register.assert_not_called()
        finally:
            store.close()

    def test_cloud_upload_uses_exclusive_limit_and_removes_partial_files(self) -> None:
        project_id = self.create_project("test-upload-boundary", "local_package")
        created = self.client.post("/api/requests", json={"project_id": project_id, "work_item_id": 930210})
        request_id = created.json()["id"]
        headers = {"Authorization": "Bearer test-runner-token"}
        # Use a 1 MiB configured threshold to exercise streaming boundaries without
        # allocating GiB-sized request bodies. The default 1 GiB value is tested above.
        with patch.object(type(settings), "max_artifact_mb", new_callable=PropertyMock, return_value=1):
            for size in (1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1):
                response = self.client.post(
                    f"/api/runner/requests/{request_id}/artifacts", headers=headers,
                    data={"kind": "package", "name": "app.jar"},
                    files={"file": ("app.jar", b"x" * size, "application/octet-stream")},
                )
                self.assertEqual(response.status_code, 200 if size < 1024 * 1024 else 413, response.text)
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(len(detail["artifacts"]), 1)
        saved = list((settings.delivery_dir / request_id / "uploads").iterdir())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].stat().st_size, 1024 * 1024 - 1)
        update_request(request_id, status="cancelled")

    def test_sichuan_review_then_merge(self) -> None:
        project_id = self.create_project("test-sichuan", "sichuan_auto_review")
        detail = self.submit_and_process(
            project_id, 910002, ["merge_screenshot", "license_request"]
        )
        self.assertEqual(detail["status"], "waiting_merge")
        response = self.client.post(f"/api/requests/{detail['id']}/simulate-merge")
        self.assertEqual(response.status_code, 200, response.text)
        completed = self.client.get(f"/api/requests/{detail['id']}").json()["request"]
        self.assertEqual(completed["status"], "delivered")
        screenshots = [item for item in completed["artifacts"] if item["kind"] == "merge_screenshot"]
        self.assertEqual(len(screenshots), 1)
        self.assertIn("PR #", screenshots[0]["name"])
        licenses = [item for item in completed["artifacts"] if item["kind"] == "license_request"]
        self.assertEqual(len(licenses), 1)
        self.assertIn("License 授权申请", licenses[0]["name"])
        self.assertFalse(any(item["kind"] == "release_artifact" for item in completed["artifacts"]))
        self.assertNotIn("pull_request", {item["kind"] for item in completed["artifacts"]})

    def test_sichuan_review_falls_back_to_product_review_when_pr_stays_active(self) -> None:
        detail = {
            "id": "sichuan-fallback-request",
            "status": "waiting_merge",
            "delivery_mode": "sichuan_auto_review",
            "policy_snapshot": {
                "simulation_mode": False,
                "tfs_collection_url": "https://tfs.test/DefaultCollection",
            },
            "repository_states": [
                {
                    "name": "dcsd-direct-ui-sichuan",
                    "repository_path": "C:/work/direct",
                    "pr_id": 301,
                    "pr_url": "https://tfs.test/pr/301",
                    "status": "waiting_merge",
                },
                {
                    "name": "dcsd-notice-srv-sichuan",
                    "repository_path": "C:/work/notice",
                    "pr_id": 302,
                    "pr_url": "https://tfs.test/pr/302",
                    "status": "waiting_merge",
                },
            ],
        }

        class RecordingStore:
            remote = False

            def __init__(self) -> None:
                self.value = detail
                self.events: list[tuple[str, str]] = []
                self.steps: list[tuple[str, str, str]] = []

            def detail(self, _request_id: str) -> dict:
                return self.value

            def update_request(self, _request_id: str, **fields) -> None:
                self.value.update(fields)

            def update_step(self, _request_id: str, step: str, status: str, message: str = "") -> None:
                self.steps.append((step, status, message))

            def add_event(self, _request_id: str, event_type: str, message: str, **_kwargs) -> None:
                self.events.append((event_type, message))

            def get_status(self, _request_id: str) -> str:
                return self.value["status"]

            @staticmethod
            def add_artifact(*_args, **_kwargs) -> int:
                return 1

        store = RecordingStore()
        fallback_worker = Worker(store=store)
        pr_results = {
            301: {"id": 301, "status": "active", "merge_commit": ""},
            302: {"id": 302, "status": "completed", "merge_commit": "merged302"},
        }
        with patch("app.orchestrator.TfsClient") as tfs_client, patch.object(
            fallback_worker, "_send_status_email"
        ) as send_email:
            tfs_client.return_value.get_pull_request.side_effect = (
                lambda _repo_path, pr_id: pr_results[pr_id]
            )
            fallback_worker.poll_merge(detail["id"])
            fallback_worker.poll_merge(detail["id"])

        send_email.assert_called_once_with(detail["id"], action_required=True)
        pending_state, completed_state = store.value["repository_states"]
        self.assertEqual(pending_state["review_strategy"], "product_manual_review")
        self.assertTrue(pending_state["manual_review_fallback_at"])
        self.assertEqual(completed_state["status"], "completed")
        self.assertEqual(completed_state["merge_commit"], "merged302")
        self.assertFalse(completed_state.get("manual_review_fallback_at"))
        self.assertEqual(
            len([event for event in store.events if event[0] == "pr.auto_complete_fallback"]),
            1,
        )
        self.assertIn("已转产品审核", store.steps[0][2])

    def test_sichuan_review_keeps_waiting_when_auto_complete_is_blocked_by_scan(self) -> None:
        detail = {
            "id": "sichuan-scan-wait-request",
            "status": "waiting_merge",
            "delivery_mode": "sichuan_auto_review",
            "policy_snapshot": {
                "simulation_mode": False,
                "tfs_collection_url": "https://tfs.test/DefaultCollection",
            },
            "repository_states": [
                {
                    "name": "dcsd-notice-srv-sichuan",
                    "repository_path": "C:/work/notice",
                    "pr_id": 766451,
                    "pr_url": "https://tfs.test/pr/766451",
                    "status": "waiting_merge",
                }
            ],
        }

        class RecordingStore:
            remote = False

            def __init__(self) -> None:
                self.value = detail
                self.events: list[tuple[str, str]] = []
                self.steps: list[tuple[str, str, str]] = []

            def detail(self, _request_id: str) -> dict:
                return self.value

            def update_request(self, _request_id: str, **fields) -> None:
                self.value.update(fields)

            def update_step(self, _request_id: str, step: str, status: str, message: str = "") -> None:
                self.steps.append((step, status, message))

            def add_event(self, _request_id: str, event_type: str, message: str, **_kwargs) -> None:
                self.events.append((event_type, message))

            def get_status(self, _request_id: str) -> str:
                return self.value["status"]

            @staticmethod
            def add_artifact(*_args, **_kwargs) -> int:
                return 1

        store = RecordingStore()
        scan_worker = Worker(store=store)
        pr = {
            "id": 766451,
            "status": "active",
            "merge_commit": "",
            "merge_status": "queued",
            "auto_complete_enabled": True,
        }
        with patch("app.orchestrator.TfsClient") as tfs_client, patch.object(
            scan_worker, "_send_status_email"
        ) as send_email:
            tfs_client.return_value.get_pull_request.return_value = pr
            scan_worker.poll_merge(detail["id"])
            scan_worker.poll_merge(detail["id"])

        send_email.assert_not_called()
        state = store.value["repository_states"][0]
        self.assertTrue(state["auto_complete_enabled"])
        self.assertEqual(state["merge_status"], "queued")
        self.assertFalse(state.get("manual_review_fallback_at"))
        self.assertEqual(
            len([event for event in store.events if event[0] == "pr.auto_complete_waiting"]),
            1,
        )
        self.assertFalse(any(event[0] == "pr.auto_complete_fallback" for event in store.events))
        self.assertIn("等待代码扫描", store.steps[-1][2])

    def test_product_review_emails_before_and_after_merge(self) -> None:
        project_id = self.create_project("test-product", "product_manual_review")
        detail = self.submit_and_process(
            project_id,
            910003,
            ["merge_screenshot", "license_request", "auto_release"],
        )
        self.assertEqual(detail["status"], "waiting_merge")
        self.assertTrue(any(item["name"] == "review-email-preview.html" for item in detail["artifacts"]))
        self.assertNotIn("pull_request", {item["kind"] for item in detail["artifacts"]})
        response = self.client.post(f"/api/requests/{detail['id']}/simulate-merge")
        self.assertEqual(response.status_code, 200, response.text)
        completed = self.client.get(f"/api/requests/{detail['id']}").json()["request"]
        self.assertEqual(completed["status"], "delivered")
        self.assertTrue(any(item["name"] == "delivery-email-preview.html" for item in completed["artifacts"]))
        self.assertEqual(
            len([item for item in completed["artifacts"] if item["kind"] == "merge_screenshot"]),
            1,
        )
        self.assertEqual(
            len([item for item in completed["artifacts"] if item["kind"] == "license_request"]),
            1,
        )
        releases = [item for item in completed["artifacts"] if item["kind"] == "release_artifact"]
        self.assertEqual(len(releases), 1)
        self.assertIn("view=artifacts", releases[0]["external_url"])
        self.assertNotIn("pull_request", {item["kind"] for item in completed["artifacts"]})

    def test_review_delivery_defaults_to_auto_release_only(self) -> None:
        project_id = self.create_project("test-default-release", "product_manual_review")
        detail = self.submit_and_process(project_id, 910031)
        self.assertEqual(detail["delivery_options"], ["auto_release"])
        self.client.post(f"/api/requests/{detail['id']}/simulate-merge")
        completed = self.client.get(f"/api/requests/{detail['id']}").json()["request"]
        self.assertEqual(completed["status"], "delivered")
        kinds = {item["kind"] for item in completed["artifacts"]}
        self.assertIn("release_artifact", kinds)
        self.assertNotIn("merge_screenshot", kinds)
        self.assertNotIn("license_request", kinds)
        release_step = next(step for step in completed["steps"] if step["step_code"] == "release")
        self.assertEqual(release_step["status"], "completed")

    def test_pipeline_release_skill_adapter_resolves_and_runs_without_pat_on_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory) / "fake_pipeline_skill.py"
            script_path.write_text(
                """import json
import os
import sys

arguments = sys.argv[1:]
assert \"test-pipeline-pat\" not in arguments
project_name = arguments[arguments.index(\"--project-name\") + 1]
if \"--confirm-run\" in arguments:
    assert os.environ.get(\"TFS_PAT\") == \"test-pipeline-pat\"
    print(json.dumps({
        \"standardName\": project_name,
        \"pipelineName\": \"standard-release\",
        \"definitionId\": 1974,
        \"sourceBranch\": \"refs/heads/dev\",
        \"buildId\": 1170472,
        \"buildNumber\": \"20260818.1\",
        \"result\": \"succeeded\",
        \"expectedArtifactFound\": True,
        \"artifacts\": [\"drop\"],
        \"artifactsUrl\": \"http://dev.test/DCS/_build/results?buildId=1170472&view=artifacts\"
    }))
else:
    print(json.dumps({
        \"standardName\": project_name,
        \"pipelineName\": \"standard-release\",
        \"definitionId\": 1974,
        \"sourceBranch\": \"refs/heads/dev\",
        \"definitionUrl\": \"http://dev.test/DCS/_build?definitionId=1974\"
    }))
""",
                encoding="utf-8",
            )
            fake_settings = SimpleNamespace(
                tfs_pat="test-pipeline-pat",
                tfs_pipeline_timeout_seconds=30,
            )
            with patch("app.services.pipeline_release.settings", fake_settings):
                service = TfsPipelineReleaseService(script_path=script_path)
                plan = service.resolve_plan("四川省调网络发令")
                result = service.run("四川省调网络发令")

        self.assertEqual(plan["definitionId"], 1974)
        self.assertEqual(result["result"], "succeeded")
        self.assertTrue(result["expectedArtifactFound"])
        self.assertIn("buildId=1170472", result["artifactsUrl"])

    def test_pipeline_release_normalizes_workflow_result_and_retries_malformed_maven_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory) / "fake_pipeline_skill.py"
            script_path.write_text(
                """import json
import os
import sys
from pathlib import Path

counter_path = Path(__file__).with_suffix(".count")
attempt = int(counter_path.read_text() if counter_path.exists() else "0") + 1
counter_path.write_text(str(attempt))
base = {
    "standardName": "四川省调网络发令",
    "pipelineName": "标准版springboot-四川省调",
    "definitionId": 1974,
    "tfsProject": "DCS",
    "sourceBranch": "refs/heads/dev",
}
if "--confirm-run" not in sys.argv:
    print(json.dumps({**base, "definitionUrl": "http://dev.test/DCS/_build?definitionId=1974"}))
elif attempt == 1:
    failed = {
        **base,
        "buildId": 1178410,
        "result": "failed",
        "resultsUrl": "http://dev.test/DefaultCollection/DCS/_build/results?buildId=1178410&view=results",
    }
    print(json.dumps({"result": "failed", "deliveries": [failed]}))
    print("Malformed " + chr(92) + "uxxxx encoding.", file=sys.stderr)
    raise SystemExit(3)
else:
    succeeded = {
        **base,
        "buildId": 1178440,
        "buildNumber": "20260826.2",
        "result": "succeeded",
        "expectedArtifactFound": True,
        "artifacts": ["drop"],
        "artifactsUrl": "http://dev.test/DefaultCollection/DCS/_build/results?buildId=1178440&view=artifacts",
    }
    print(json.dumps({"action": "workflow-run", "result": "succeeded", "deliveries": [succeeded]}))
""",
                encoding="utf-8",
            )
            fake_settings = SimpleNamespace(
                tfs_pat="test-pipeline-pat",
                tfs_pipeline_timeout_seconds=30,
            )
            with patch("app.services.pipeline_release.settings", fake_settings):
                result = TfsPipelineReleaseService(script_path=script_path).run("四川省调网络发令")

        self.assertEqual(result["buildId"], 1178440)
        self.assertEqual(result["retryCount"], 1)
        self.assertEqual(result["retryHistory"][0]["buildId"], 1178410)
        self.assertIn("Malformed", result["retryHistory"][0]["reason"])

    def test_tfs_pull_request_exposes_auto_complete_and_merge_status(self) -> None:
        client = TfsClient("https://tfs.test/DefaultCollection", pat="test-pat")
        with patch.object(
            client,
            "repository_info",
            return_value=SimpleNamespace(project="DCS", name="repo", id="repo-id"),
        ), patch.object(
            client,
            "_request",
            return_value={
                "pullRequestId": 766451,
                "status": "active",
                "mergeStatus": "queued",
                "autoCompleteSetBy": {"id": "reviewer-id", "displayName": "杨涛"},
                "repository": {"name": "repo"},
                "sourceRefName": "refs/heads/feature/1663309-yangtao",
                "targetRefName": "refs/heads/dev",
            },
        ):
            result = client.get_pull_request("C:/work/repo", 766451)

        self.assertTrue(result["auto_complete_enabled"])
        self.assertEqual(result["merge_status"], "queued")

    def test_real_pr_screenshot_uses_repository_short_name_and_is_idempotent(self) -> None:
        self.assertEqual(repository_short_name("dcsd-notice-srv-sichuancd-dm"), "notice-srv")
        self.assertEqual(repository_short_name("th-dc-biz-bazhong"), "bazhong")
        recorded: list[tuple] = []

        def recorder(*args) -> int:
            recorded.append(args)
            return 31

        with tempfile.TemporaryDirectory() as delivery_dir, patch(
            "app.config.Settings.delivery_dir",
            new_callable=PropertyMock,
            return_value=Path(delivery_dir),
        ):
            service = ArtifactService(recorder=recorder, detail_loader=lambda _: {"artifacts": []})

            def capture(pr: dict, pr_url: str, image_path: Path) -> None:
                self.assertEqual(pr["id"], 757862)
                self.assertIn("pullrequest/757862", pr_url)
                image_path.write_bytes(b"\x89PNG\r\n\x1a\nreal-browser-image")

            with patch.object(service, "_capture_real_pr_page", side_effect=capture) as screenshot:
                artifact_id = service.create_merge_evidence(
                    "real-pr-request",
                    {"id": 757862, "status": "completed"},
                    "http://dev.tellhowsoft.com/DefaultCollection/DCS/_git/repo/pullrequest/757862",
                    repository_name="dcsd-notice-srv-sichuancd-dm",
                )

            self.assertEqual(artifact_id, 31)
            screenshot.assert_called_once()
            self.assertEqual(recorded[0][1], "merge_screenshot")
            self.assertEqual(recorded[0][2], "notice-srv · PR #757862 · 合并截图.png")
            self.assertTrue(Path(recorded[0][3]).is_file())

            existing = ArtifactService(
                recorder=recorder,
                detail_loader=lambda _: {
                    "artifacts": [
                        {
                            "id": 44,
                            "kind": "merge_screenshot",
                            "name": "notice-srv · PR #757862 · 合并截图.png",
                        }
                    ]
                },
            )
            with patch.object(existing, "_capture_real_pr_page") as duplicate_capture:
                self.assertEqual(
                    existing.create_merge_evidence(
                        "real-pr-request",
                        {"id": 757862},
                        "http://dev.tellhowsoft.com/pr/757862",
                        repository_name="dcsd-notice-srv-sichuancd-dm",
                    ),
                    44,
                )
            duplicate_capture.assert_not_called()

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

        started = self.client.post(f"/api/requests/{request_id}/devcore-watch/start")
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
            f"/api/requests/{request_id}/devcore-watch/{watcher['watcher_id']}?after={watcher['cursor']}"
        )
        self.assertEqual(polled.json()["events"][0]["content"], "only-live-output")
        stopped = self.client.post(
            f"/api/requests/{request_id}/devcore-watch/{watcher['watcher_id']}/stop"
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
                "version": "0.4.5",
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
        self.assertEqual(runner["devcore_usage"]["primary"]["remaining_percent"], 58)
        self.assertNotIn("codex_usage", runner)
        self.assertEqual(runner["max_concurrency"], 5)
        self.assertGreaterEqual(dashboard["stats"]["total"], 1)
        self.assertIn("today_total", dashboard["stats"])
        self.assertEqual(dashboard["capacity"]["limit"], 5)

    def test_failed_task_can_be_retried_with_original_delivery_context(self) -> None:
        project_id = self.create_project("test-retry", "product_manual_review")
        created = self.client.post(
            "/api/requests",
            json={
                "project_id": project_id,
                "work_item_id": 910023,
                "notification_emails": ["admin@example.com"],
                "delivery_options": ["merge_screenshot", "auto_release"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        original_id = created.json()["id"]
        update_request(original_id, status="failed", error_message="模拟执行失败")

        retried = self.client.post(f"/api/requests/{original_id}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertNotEqual(retried.json()["id"], original_id)
        new_detail = self.client.get(f"/api/requests/{retried.json()['id']}").json()["request"]
        self.assertEqual(new_detail["status"], "queued")
        self.assertEqual(new_detail["project_id"], project_id)
        self.assertEqual(new_detail["work_item_id"], 910023)
        self.assertEqual(new_detail["delivery_mode"], "product_manual_review")
        self.assertEqual(new_detail["notification_emails"], ["admin@example.com"])
        self.assertEqual(new_detail["delivery_options"], ["merge_screenshot", "auto_release"])
        original = self.client.get(f"/api/requests/{original_id}").json()["request"]
        self.assertTrue(any(event["event_type"] == "request.retried" for event in original["events"]))
        second_retry = self.client.post(f"/api/requests/{original_id}/retry")
        self.assertEqual(second_retry.status_code, 409)
        update_request(retried.json()["id"], status="cancelled")

    def test_retry_rejects_non_failed_task(self) -> None:
        project_id = self.create_project("test-retry-state", "local_package")
        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910024}
        )
        response = self.client.post(f"/api/requests/{created.json()['id']}/retry")
        self.assertEqual(response.status_code, 409)
        update_request(created.json()["id"], status="cancelled")

    def test_admin_can_continue_waiting_approval_with_prompt_in_same_task(self) -> None:
        project_id = self.create_project("test-admin-continue", "product_manual_review")
        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910025}
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["id"]
        update_request(
            request_id,
            status="waiting_approval",
            current_step="develop",
            progress=48,
            codex_thread_id="thread-to-resume",
            repository_states=[{
                "name": "demo",
                "worktree_path": TEST_DATA.name,
                "base_commit": "base",
                "branch": "feature/910025-yangtao",
            }],
            error_message="缺少真实页面截图",
        )

        response = self.client.post(
            f"/api/requests/{request_id}/continue",
            json={"prompt": "需求没有要求截图，请使用 production 构建和路由断言继续。"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], request_id)
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(detail["status"], "queued")
        self.assertEqual(detail["current_step"], "validate")
        self.assertNotIn("codex_thread_id", detail)
        self.assertEqual(detail["error_message"], "")
        self.assertIn("production 构建", detail["supplement_answers"][-1]["answer"])
        self.assertEqual(detail["supplement_requests"][-1]["question"], "管理员继续执行指示")
        self.assertTrue(any(event["event_type"] == "development.admin_continued" for event in detail["events"]))
        update_request(request_id, status="cancelled")

    def test_only_admin_can_continue_waiting_approval(self) -> None:
        project_id = self.create_project("test-admin-continue-role", "local_package")
        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910026}
        )
        request_id = created.json()["id"]
        update_request(
            request_id,
            status="waiting_approval",
            codex_thread_id="thread-to-resume",
            repository_states=[{"name": "demo", "worktree_path": TEST_DATA.name}],
        )
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"username": "pm", "password": "pm123456"})
        response = self.client.post(f"/api/requests/{request_id}/continue", json={"prompt": "继续"})
        self.assertEqual(response.status_code, 403)
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        update_request(request_id, status="cancelled")

    def test_admin_analytics_exposes_platform_distributions(self) -> None:
        analytics = self.client.get("/api/admin/analytics")
        self.assertEqual(analytics.status_code, 200, analytics.text)
        payload = analytics.json()
        self.assertIn("success_rate", payload["overview"])
        self.assertEqual(len(payload["daily_trend"]), 14)
        self.assertIn("status_distribution", payload)
        self.assertIn("project_distribution", payload)
        page = self.client.get("/")
        self.assertIn("统计看板", page.text)
        self.assertIn('id="analytics-projects"', page.text)

        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"username": "pm", "password": "pm123456"})
        forbidden = self.client.get("/api/admin/analytics")
        self.assertEqual(forbidden.status_code, 403)

    def test_delivery_records_support_pagination_filters_and_requester_scope(self) -> None:
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        project_id = self.create_project("ledger-pagination-project", "local_package")
        for index in range(13):
            work_item_id = 931000 + index
            created = self.client.post(
                "/api/requests",
                json={"project_id": project_id, "work_item_id": work_item_id},
            )
            self.assertEqual(created.status_code, 200, created.text)
            status = "failed" if index % 3 == 0 else "delivered"
            update_request(
                created.json()["id"],
                status=status,
                title=f"交付台账筛选样例 {index:02d}",
                completed_at="2026-08-26T04:00:00+00:00",
            )

        second_page = self.client.get(
            "/api/delivery-records",
            params={"project_key": "ledger-pagination-project", "page": 2, "page_size": 5},
        )
        self.assertEqual(second_page.status_code, 200, second_page.text)
        ledger = second_page.json()
        self.assertEqual(ledger["pagination"], {"page": 2, "page_size": 5, "total": 13, "total_pages": 3})
        self.assertEqual(len(ledger["items"]), 5)
        self.assertTrue(all(item["project_key"] == "ledger-pagination-project" for item in ledger["items"]))

        failed = self.client.get(
            "/api/delivery-records",
            params={"project_key": "ledger-pagination-project", "status": "failed", "page_size": 50},
        ).json()
        self.assertEqual(failed["pagination"]["total"], 5)
        self.assertTrue(all(item["status"] == "failed" for item in failed["items"]))
        keyword = self.client.get(
            "/api/delivery-records",
            params={"project_key": "ledger-pagination-project", "keyword": "931007"},
        ).json()
        self.assertEqual(keyword["pagination"]["total"], 1)
        self.assertEqual(keyword["items"][0]["work_item_id"], 931007)
        page = self.client.get("/")
        self.assertIn('id="record-filters"', page.text)
        self.assertIn('id="record-pagination"', page.text)
        self.assertIn('name="task_type"', page.text)

        analysis = self.client.post(
            "/api/requests",
            json={
                "project_id": project_id,
                "work_item_id": 931098,
                "task_type": "analysis",
                "delivery_options": [],
            },
        )
        self.assertEqual(analysis.status_code, 200, analysis.text)
        update_request(
            analysis.json()["id"],
            status="delivered",
            title="交付台账中的问题分析",
            completed_at="2026-08-26T04:02:00+00:00",
        )
        analysis_records = self.client.get(
            "/api/delivery-records",
            params={"project_key": "ledger-pagination-project", "task_type": "analysis"},
        )
        self.assertEqual(analysis_records.status_code, 200, analysis_records.text)
        self.assertEqual(analysis_records.json()["pagination"]["total"], 1)
        self.assertEqual(analysis_records.json()["items"][0]["task_type"], "analysis")

        created_user = self.client.post(
            "/api/users",
            json={
                "username": "ledger_scope_pm",
                "display_name": "台账范围项目经理",
                "emails": ["ledger.scope@example.com"],
                "password": "password123",
                "role": "pm",
                "active": True,
            },
        )
        self.assertEqual(created_user.status_code, 200, created_user.text)
        with TestClient(app) as pm_client:
            logged_in = pm_client.post(
                "/api/auth/login", json={"username": "ledger_scope_pm", "password": "password123"}
            )
            self.assertEqual(logged_in.status_code, 200, logged_in.text)
            own = pm_client.post(
                "/api/requests",
                json={"project_id": project_id, "work_item_id": 931099},
            )
            self.assertEqual(own.status_code, 200, own.text)
            update_request(
                own.json()["id"],
                status="delivered",
                title="项目经理自己的交付记录",
                completed_at="2026-08-26T04:05:00+00:00",
            )
            scoped = pm_client.get(
                "/api/delivery-records",
                params={"project_key": "ledger-pagination-project", "page_size": 50},
            )
            self.assertEqual(scoped.status_code, 200, scoped.text)
            self.assertEqual(scoped.json()["pagination"]["total"], 1)
            self.assertEqual(scoped.json()["items"][0]["work_item_id"], 931099)

    def test_public_task_payload_masks_engine_identity(self) -> None:
        project_id = self.create_project("test-devcore-mask", "local_package")
        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910025}
        )
        request_id = created.json()["id"]
        headers = {"Authorization": "Bearer test-runner-token"}
        self.client.patch(
            f"/api/runner/requests/{request_id}",
            headers=headers,
            json={
                "fields": {
                    "status": "developing",
                    "result_summary": "Codex 正在处理",
                    "codex_thread_id": "secret-engine-thread",
                }
            },
        )
        self.client.post(
            f"/api/runner/requests/{request_id}/events",
            headers=headers,
            json={"event_type": "codex.event", "message": "Codex 已完成一段分析"},
        )
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        rendered = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("Codex", rendered)
        self.assertNotIn("codex_thread_id", detail)
        self.assertIn("DevCore", rendered)
        self.assertNotIn("Codex", self.client.get("/").text)
        update_request(request_id, status="cancelled")

    def test_new_intake_is_immediately_visible_and_home_shows_version(self) -> None:
        self.create_project("test-instant-board", "local_package")
        headers = {"Authorization": "Bearer test-runner-token"}
        stopped = self.client.post(
            "/api/runner/heartbeat",
            headers=headers,
            json={"runner_id": "yangtao-pc", "hostname": "test-pc", "version": "1.0", "state": "stopping"},
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)
        created = self.client.post("/api/requests", json={"work_item_id": 910020})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertFalse(created.json()["runner_online"])
        self.assertEqual(created.json()["status"], "waiting_runner")
        self.assertIn("执行器当前离线", created.json()["message"])
        intake_id = created.json()["id"]

        dashboard = self.client.get("/api/dashboard").json()
        visible = next(item for item in dashboard["active"] if item.get("intake_id") == intake_id)
        self.assertEqual(visible["status"], "waiting_runner")
        self.assertIn("执行器当前离线", visible["current_activity"])
        self.assertEqual(dashboard["counts"]["waiting_runner"], 1)
        self.assertGreaterEqual(dashboard["capacity"]["queued"], 1)

        intake = self.client.get(f"/api/intakes/{intake_id}").json()["intake"]
        self.assertFalse(intake["runner_online"])
        self.assertEqual(intake["display_status"], "waiting_runner")

        online = self.client.post(
            "/api/runner/heartbeat",
            headers=headers,
            json={"runner_id": "yangtao-pc", "hostname": "test-pc", "version": "1.0", "state": "idle"},
        )
        self.assertEqual(online.status_code, 200, online.text)
        dashboard = self.client.get("/api/dashboard").json()
        visible = next(item for item in dashboard["active"] if item.get("intake_id") == intake_id)
        self.assertEqual(visible["status"], "routing")

        page = self.client.get("/")
        self.assertEqual(page.text.count("SYSTEM V1.0-Alpha.23"), 1)
        self.assertIn("/static/editorial-ui.css", page.text)
        self.assertIn("AutoDev", page.text)
        self.assertIn("/static/brand/autodev-sidebar-mark.png", page.text)
        self.assertNotIn("DELIVERY LOOP", page.text)
        self.assertNotIn("系统版本 / VERSION", page.text)
        self.assertIn("control-strip", page.text)
        self.assertIn('id="project-guide"', page.text)
        self.assertIn("支持项目与别名", page.text)
        self.assertIn("可自主研发项目", page.text)
        self.assertIn("project-guide", page.text)
        self.assertIn('<span>自主项目</span>', page.text)
        self.assertIn('id="sidebar-orb-character"', page.text)
        self.assertIn("new AutoDevOrb", page.text)
        self.assertIn("environment: 'dark'", page.text)
        self.assertIn("window.sidebarCharacter = sidebarCharacter", page.text)
        self.assertIn('data-filter-select="task_type"', page.text)
        self.assertIn('data-filter-date="date_from"', page.text)
        self.assertNotIn('type="date"', page.text)
        self.assertEqual(page.text.count('name="delivery_options"'), 3)
        self.assertIn('value="auto_release" checked', page.text)
        self.assertNotIn('value="merge_screenshot" checked', page.text)
        self.assertNotIn('value="license_request" checked', page.text)

        script = self.client.get("/static/app.js").text
        self.assertIn("addOptimisticIntake", script)
        self.assertIn("renderActiveRuns", script)
        self.assertIn("merge-screenshot-grid", script)
        self.assertIn("merge-screenshot-download", script)
        self.assertIn("下载原图", page.text)
        self.assertIn("openArtifactPreview", script)
        self.assertIn("visibleArtifacts", script)
        self.assertIn("recent.slice(0,5)", script)
        self.assertNotIn("recent.slice(0,8)", script)
        self.assertNotIn("activeEl.innerHTML=state.dashboard.active", script)
        self.assertIn("const projectRequest=api('/api/projects')", script)
        self.assertIn("api('/api/notification-recipients')", script)
        self.assertIn("delivery_options", script)
        self.assertIn("release_artifact", script)
        self.assertIn("renderProjectGuide", script)
        self.assertIn("renderLedgerCalendar", script)
        self.assertIn("renderContinuationPanel", script)
        self.assertIn("/continue", script)
        self.assertIn("继续执行并保留现场", script)
        self.assertIn("syncSidebarOrbState", script)
        self.assertIn("characterState='working'", script)
        self.assertIn("characterState='sleeping'", script)
        self.assertNotIn("const TASK_MOTION =", script)
        self.assertNotIn("taskMotionMarkup", script)
        self.assertNotIn("task-kinetic", script)
        self.assertIn("ledger-lock", script)
        self.assertIn("repository_tfs_paths", script)
        self.assertNotIn("project-guide-trigger')?.addEventListener('click'", script)

        login_template = Path("app/templates/login.html").read_text(encoding="utf-8")
        self.assertIn("login-logo-lockup", login_template)
        self.assertIn("autodev-sidebar-mark.png", login_template)
        self.assertIn("Auto<span>Dev</span>", login_template)
        self.assertIn('id="login-orb-character"', login_template)
        self.assertIn("new AutoDevOrb", login_template)
        self.assertIn("environment: 'light'", login_template)
        self.assertIn("state: 'curious'", login_template)
        self.assertIn("autodev-orb-canvas", login_template)
        self.assertIn("PROJECT PIPELINE", login_template)
        self.assertIn("交付离场", login_template)
        self.assertNotIn("brand-float", Path("app/static/brand-ui.css").read_text(encoding="utf-8"))
        self.assertNotIn("login-logo-plate", login_template)
        self.assertNotIn("CONTINUOUS CODE DELIVERY", login_template)
        brand_styles = Path("app/static/brand-ui.css").read_text(encoding="utf-8")
        self.assertIn("width: 255px", brand_styles)
        self.assertIn(".project-guide-panel", brand_styles)
        self.assertIn(".project-guide:hover .project-guide-panel", brand_styles)
        editorial_styles = Path("app/static/editorial-ui.css").read_text(encoding="utf-8")
        self.assertIn("--editorial-orange: #e9572b", editorial_styles)
        self.assertIn("--editorial-orange-ink: #a3381f", editorial_styles)
        self.assertIn("--editorial-white: #f3f0e8", editorial_styles)
        self.assertIn(".record-filters input,", editorial_styles)
        self.assertIn(".ledger-calendar-days button.selected", editorial_styles)
        self.assertIn(".repository-paths > a", editorial_styles)
        self.assertIn("select option { background: #fffdf7; color: var(--editorial-ink); }", editorial_styles)
        self.assertIn(".run-card.joint-run-card,", editorial_styles)
        self.assertIn(".waiting-runner-signal {", editorial_styles)
        self.assertIn(".supplement-copy textarea {", editorial_styles)
        self.assertIn(".continuation-panel {", editorial_styles)
        self.assertNotIn(".task-kinetic {", editorial_styles)
        self.assertIn(".login-character-stage .autodev-orb", editorial_styles)
        self.assertIn("@keyframes login-route-marker", editorial_styles)
        self.assertIn(".loop-progress::after", editorial_styles)
        self.assertIn("login-route-scan 7.2s cubic-bezier", editorial_styles)
        self.assertNotIn("login-route-scan 5.6s steps", editorial_styles)
        self.assertIn(".autodev-orb-canvas", editorial_styles)
        self.assertIn(".sidebar-character-stage .autodev-orb-fallback", editorial_styles)
        self.assertIn(".sidebar-character-stage { display: none; }", editorial_styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", editorial_styles)
        self.assertIn(".control-strip .metrics.admin-metrics", editorial_styles)
        self.assertIn("@media (max-width: 900px)", editorial_styles)
        local_client = Path("app/local_client.py").read_text(encoding="utf-8")
        self.assertIn('ACID = "#e9572b"', local_client)
        self.assertIn('ACCENT_INK = "#a3381f"', local_client)
        self.assertIn('ON_DARK_MUTED = "#bcb6aa"', local_client)
        self.assertIn('PANEL = "#fbf8f0"', local_client)

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

    def test_queued_request_tracks_runner_offline_without_changing_workflow_status(self) -> None:
        project_id = self.create_project("test-offline-queue", "local_package")
        headers = {"Authorization": "Bearer test-runner-token"}
        self.client.post(
            "/api/runner/heartbeat",
            headers=headers,
            json={"runner_id": "yangtao-pc", "hostname": "test-pc", "version": "1.0", "state": "stopping"},
        )

        created = self.client.post(
            "/api/requests", json={"project_id": project_id, "work_item_id": 910026}
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["status"], "waiting_runner")
        self.assertFalse(created.json()["runner_online"])

        detail = self.client.get(f"/api/requests/{created.json()['id']}").json()["request"]
        self.assertEqual(detail["status"], "queued")
        self.assertEqual(detail["display_status"], "waiting_runner")
        self.assertEqual(detail["status_label"], "等待执行器上线")
        self.assertIn("待执行器上线", detail["display_message"])

        dashboard = self.client.get("/api/dashboard").json()
        queued = next(item for item in dashboard["active"] if item["id"] == created.json()["id"])
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["display_status"], "waiting_runner")
        self.assertEqual(dashboard["counts"]["waiting_runner"], 1)
        self.assertEqual(dashboard["counts"]["queued"], 0)
        update_request(created.json()["id"], status="cancelled")
        self.client.post(
            "/api/runner/heartbeat",
            headers=headers,
            json={"runner_id": "yangtao-pc", "hostname": "test-pc", "version": "1.0", "state": "idle"},
        )

    def test_local_runner_restart_waits_for_port_and_console_reports_result(self) -> None:
        install_script = Path("local-runner/install.ps1").read_text(encoding="utf-8-sig")
        startup_script = Path("local-runner/install-startup-task.ps1").read_text(encoding="utf-8-sig")
        restart_script = Path("local-runner/restart.ps1").read_text(encoding="utf-8-sig")
        stop_script = Path("local-runner/stop.ps1").read_text(encoding="utf-8-sig")
        status_script = Path("local-runner/status.ps1").read_text(encoding="utf-8-sig")
        client_source = Path("app/local_client.py").read_text(encoding="utf-8")

        self.assertIn("http://127.0.0.1:$MonitorPort/healthz", restart_script)
        self.assertIn("AddSeconds(45)", restart_script)
        self.assertIn("Start-ScheduledTask", restart_script)
        self.assertIn("Get-RunnerProcesses", stop_script)
        self.assertIn("app\\.local_runner_main", stop_script)
        self.assertNotIn("CommandLine.Contains($ProjectRoot)", stop_script)
        self.assertIn("本机接口状态", status_script)
        self.assertIn('"启动执行器", "restart.ps1"', client_source)
        self.assertIn("subprocess.run(", client_source)
        self.assertIn("capture_output=True", client_source)
        self.assertIn("正在重新连接 DevCore 实时会话", client_source)
        self.assertIn('if name == "logs"', client_source)
        self.assertIn('text.see("end")', client_source)
        self.assertIn("执行器仍有", restart_script)
        self.assertIn("param([switch]$Force)", restart_script)
        self.assertIn("New-ScheduledTaskTrigger -AtStartup", install_script)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", install_script)
        self.assertIn("RepetitionInterval (New-TimeSpan -Minutes 5)", install_script)
        self.assertIn("MultipleInstances IgnoreNew", install_script)
        self.assertIn("RestartCount 99", install_script)
        self.assertIn("WindowsBuiltInRole]::Administrator", startup_script)
        self.assertIn("-Verb RunAs", startup_script)
        self.assertIn('Join-Path $RunnerDir "restart.ps1"', startup_script)

    def test_initial_runner_heartbeat_failure_does_not_abort_startup(self) -> None:
        class OfflineStore:
            def heartbeat(self, *_args, **_kwargs) -> None:
                raise OSError("network is not ready")

        class OnlineStore:
            def __init__(self) -> None:
                self.calls: list[tuple[tuple, dict]] = []

            def heartbeat(self, *args, **kwargs) -> None:
                self.calls.append((args, kwargs))

        self.assertFalse(report_initial_heartbeat(OfflineStore(), {"available": False}, 5))
        online = OnlineStore()
        self.assertTrue(report_initial_heartbeat(online, {"available": True}, 5))
        self.assertEqual(online.calls[0][0], ("starting",))
        self.assertEqual(online.calls[0][1]["current_request_ids"], [])
        self.assertEqual(online.calls[0][1]["max_concurrency"], 5)

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
        self.assertIn("<span>自主项目</span>", admin_page.text)
        self.client.post("/api/auth/logout")
        login_page = self.client.get("/login")
        self.assertIn("editorial-ui.css?v=1.0-Alpha.23-login", login_page.text)
        self.assertIn("autodev-sidebar-mark.png?v=1.0-Alpha.23", login_page.text)
        login = self.client.post("/api/auth/login", json={"username": "pm", "password": "pm123456"})
        self.assertEqual(login.status_code, 200, login.text)
        pm_page = self.client.get("/")
        self.assertNotIn("<span>自主项目</span>", pm_page.text)
        self.assertIn('id="project-guide"', pm_page.text)
        self.assertIn("支持项目与别名", pm_page.text)
        self.assertEqual(pm_page.text.count("SYSTEM V1.0-Alpha.23"), 1)
        self.assertNotIn("系统版本 / VERSION", pm_page.text)
        self.assertNotIn("sidebar-version", pm_page.text)

        pm_dashboard = self.client.get("/api/dashboard")
        self.assertEqual(pm_dashboard.status_code, 200, pm_dashboard.text)
        self.assertIn("stats", pm_dashboard.json())
        pm_projects = self.client.get("/api/projects")
        self.assertEqual(pm_projects.status_code, 200, pm_projects.text)
        self.assertIn("projects", pm_projects.json())
        self.client.post("/api/auth/logout")
        restored = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(restored.status_code, 200, restored.text)

    def test_login_character_matches_authorized_reference_source(self) -> None:
        asset_root = Path("app/static/grok-character")
        manifest = json.loads((asset_root / "SOURCE.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"], "https://github.com/blessonism/grok-icon-study")
        self.assertEqual(manifest["commit"], "647e9bd7c60290c42a738fad586589b3f36a4680")
        for relative_path, expected_hash in manifest["files"].items():
            content = (asset_root / relative_path).read_text(encoding="utf-8")
            normalized = content.replace("\r\n", "\n").rstrip("\r\n").encode("utf-8")
            self.assertEqual(hashlib.sha256(normalized).hexdigest(), expected_hash, relative_path)

        login_template = Path("app/templates/login.html").read_text(encoding="utf-8")
        index_template = Path("app/templates/index.html").read_text(encoding="utf-8")
        expected_order = [
            "geometry-data.js",
            "src/math.js",
            "src/tables.js",
            "src/pose.js",
            "src/tricks.js",
            "src/fx.js",
            "src/eyes.js",
            "src/character.js",
        ]
        for template in (login_template, index_template):
            positions = [template.index(f"/static/grok-character/{path}") for path in expected_order]
            self.assertEqual(positions, sorted(positions))
            self.assertGreater(template.index("/static/orb-character.js"), positions[-1])

        orb_script = Path("app/static/orb-character.js").read_text(encoding="utf-8")
        self.assertIn("class AutoDevOrb", orb_script)
        self.assertIn("getContext('webgl'", orb_script)
        self.assertIn("const BRAND_ORANGE = '#f0522d'", orb_script)
        self.assertIn("const EYE_INK = '#171813'", orb_script)
        self.assertIn("prefers-reduced-motion: reduce", orb_script)
        self.assertIn("document.addEventListener('visibilitychange'", orb_script)
        self.assertIn("inkFlat: BRAND_ORANGE", orb_script)

    def test_pipeline_step_keeps_first_start_and_exposes_duration(self) -> None:
        project_id = self.create_project("test-step-timing", "local_package")
        created = self.client.post(
            "/api/requests",
            json={"project_id": project_id, "work_item_id": 910021},
        )
        self.assertEqual(created.status_code, 200, created.text)
        request_id = created.json()["id"]
        self.addCleanup(update_request, request_id, status="cancelled")

        with patch(
            "app.db.utc_now",
            side_effect=[
                "2026-08-07T01:00:00+00:00",
                "2026-08-07T01:00:30+00:00",
                "2026-08-07T01:02:05+00:00",
            ],
        ):
            update_step(request_id, "validate", "running", "开始校验")
            update_step(request_id, "validate", "running", "校验中")
            update_step(request_id, "validate", "completed", "校验完成")

        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        step = next(item for item in detail["steps"] if item["step_code"] == "validate")
        self.assertEqual(step["started_at"], "2026-08-07T01:00:00+00:00")
        self.assertEqual(step["finished_at"], "2026-08-07T01:02:05+00:00")
        self.assertEqual(step["duration_seconds"], 125)

        script = self.client.get("/static/app.js").text
        self.assertIn("timeline-times", script)
        self.assertIn("fmtStepTime", script)

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

    def test_number_only_analysis_preserves_task_type_through_local_routing(self) -> None:
        self.create_project("test-analysis-route", "product_manual_review")
        created = self.client.post(
            "/api/requests",
            json={"work_item_id": 910108, "task_type": "analysis", "delivery_options": []},
        )
        self.assertEqual(created.status_code, 200, created.text)
        intake_id = created.json()["id"]
        headers = {"Authorization": "Bearer test-runner-token"}
        claimed = self.client.post(
            "/api/runner/intakes/claim",
            headers=headers,
            json={"runner_id": "yangtao-pc"},
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["intake"]["task_type"], "analysis")
        routed = self.client.post(
            f"/api/runner/intakes/{intake_id}/route",
            headers=headers,
            json={"runner_id": "yangtao-pc", "project_key": "test-analysis-route"},
        )
        self.assertEqual(routed.status_code, 200, routed.text)
        request_id = routed.json()["request_id"]
        worker.process_once()
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(detail["task_type"], "analysis")
        self.assertEqual(detail["status"], "delivered")
        self.assertEqual(detail["delivery_mode"], "product_manual_review")
        self.assertEqual(detail["commit_hash"], None)
        self.assertEqual(detail["pr_url"], None)

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

    @patch("app.project_catalog.TfsClient.get_work_item")
    def test_requirement_content_is_classified_into_multiple_projects(self, get_work_item) -> None:
        get_work_item.return_value = {
            "id": 910109,
            "title": "【网络发令APP】【四川省调网络发令】联合现场功能",
            "area_path": "XiNanArea-New\\四川省区团队",
            "description": (
                "<p>网络发令APP新增移动端待办入口。</p>"
                "<p>四川省调网络发令新增对应的后端状态接口。</p>"
                "<p>两端字段定义和状态值必须保持一致。</p>"
            ),
            "acceptance_criteria": "APP 与省调服务联调通过。",
        }
        common = {
            "enabled": True,
            "simulation_mode": False,
            "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
            "tfs_area_path": "XiNanArea-New\\四川省区团队",
        }
        projects = [
            {
                **common,
                "project_key": "network-command-app",
                "name": "网络发令APP",
                "routing_title_keywords": ["网络发令APP"],
            },
            {
                **common,
                "project_key": "sichuan-dispatch-network-command",
                "name": "四川省调网络发令",
                "routing_title_keywords": ["四川省调网络发令", "省调网络发令"],
            },
        ]
        matched, _, classification = resolve_projects_for_work_item(910109, projects)
        self.assertEqual(
            [item["project_key"] for item in matched],
            ["network-command-app", "sichuan-dispatch-network-command"],
        )
        self.assertIn("移动端待办入口", classification[0]["scoped_sections"][0])
        self.assertTrue(any("字段定义" in item for item in classification[0]["shared_sections"]))
        self.assertIn("后端状态接口", classification[1]["scoped_sections"][0])

    @patch("app.main.TfsClient.complete_delivery")
    def test_joint_intake_creates_children_and_finalizes_once(self, complete_delivery) -> None:
        first_project_id = self.create_project("joint-app", "local_package")
        second_project_id = self.create_project("joint-service", "product_manual_review")
        project_rows = self.client.get("/api/projects").json()["projects"]
        project_keys = [
            next(item["project_key"] for item in project_rows if item["id"] == first_project_id),
            next(item["project_key"] for item in project_rows if item["id"] == second_project_id),
        ]
        created = self.client.post("/api/requests", json={"work_item_id": 910110})
        self.assertEqual(created.status_code, 200, created.text)
        intake_id = created.json()["id"]
        headers = {"Authorization": "Bearer test-runner-token"}
        claimed = self.client.post(
            "/api/runner/intakes/claim", headers=headers, json={"runner_id": "yangtao-pc"}
        )
        self.assertEqual(claimed.json()["intake"]["id"], intake_id)
        routed = self.client.post(
            f"/api/runner/intakes/{intake_id}/route",
            headers=headers,
            json={
                "runner_id": "yangtao-pc",
                "project_keys": project_keys,
                "work_item_title": "【联合应用】【联合服务】联合研发",
                "classification": [
                    {"project_key": project_keys[0], "matched_terms": ["联合应用"]},
                    {"project_key": project_keys[1], "matched_terms": ["联合服务"]},
                ],
            },
        )
        self.assertEqual(routed.status_code, 200, routed.text)
        request_ids = routed.json()["request_ids"]
        self.assertEqual(len(request_ids), 2)
        first = self.client.get(f"/api/requests/{request_ids[0]}").json()["request"]
        self.assertEqual(first["joint_project_count"], 2)
        self.assertEqual(len(first["joint_children"]), 2)
        self.assertEqual(first["policy_snapshot"]["joint_classification"]["matched_terms"], ["联合应用"])

        update_request(
            request_ids[1],
            status="waiting_merge",
            repository_states=[
                {
                    "name": "joint-service",
                    "repository_short_name": "service",
                    "pr_id": 80110,
                    "pr_url": "http://dev.example/pr/80110",
                }
            ],
        )
        with patch.object(Mailer, "configured", return_value=False):
            review_notice = self.client.post(
                f"/api/runner/requests/{request_ids[1]}/joint-review-notify", headers=headers
            )
        self.assertTrue(review_notice.json()["sent"])
        intake = self.client.get(f"/api/intakes/{intake_id}").json()["intake"]
        self.assertTrue(intake["review_email_sent_at"])

        update_request(request_ids[0], status="delivered", completed_at="2026-08-26T01:00:00+00:00")
        waiting = self.client.post(
            f"/api/runner/requests/{request_ids[0]}/joint-finalize", headers=headers
        )
        self.assertFalse(waiting.json()["finalized"])
        complete_delivery.assert_not_called()

        update_request(request_ids[1], status="delivered", completed_at="2026-08-26T01:01:00+00:00")
        complete_delivery.return_value = {"state": "已解决"}
        with patch.object(Mailer, "configured", return_value=False):
            completed = self.client.post(
                f"/api/runner/requests/{request_ids[1]}/joint-finalize", headers=headers
            )
        self.assertEqual(completed.json()["status"], "delivered")
        complete_delivery.assert_called_once()
        intake = self.client.get(f"/api/intakes/{intake_id}").json()["intake"]
        self.assertEqual(intake["status"], "delivered")
        self.assertTrue(intake["joint"])
        self.assertEqual(len(intake["children"]), 2)

        page = self.client.get("/").text
        self.assertIn("多项目标题示例", page)
        script = self.client.get("/static/app.js").text
        self.assertIn("joint-delivery-panel", script)

    @patch("app.project_catalog.TfsClient.get_work_item")
    def test_local_catalog_routes_same_area_by_title_keyword(self, get_work_item) -> None:
        get_work_item.return_value = {
            "id": 1642902,
            "title": "【成都网络下令】自动化研发测试",
            "area_path": "XiNanArea-New\\四川省区团队",
        }
        common = {
            "enabled": True,
            "simulation_mode": False,
            "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
            "tfs_area_path": "XiNanArea-New\\四川省区团队",
        }
        projects = [
            {**common, "project_key": "bazhong", "routing_title_keywords": ["巴中"]},
            {
                **common,
                "project_key": "chengdu-network-command",
                "routing_title_keywords": ["成都网络下令", "成都网络发令"],
            },
        ]
        project, item = resolve_project_for_work_item(1642902, projects)
        self.assertEqual(project["project_key"], "chengdu-network-command")
        self.assertEqual(item["title"], "【成都网络下令】自动化研发测试")

    @patch("app.orchestrator.TfsClient.get_work_item")
    def test_worker_rejects_a_stale_wrong_project_snapshot(self, get_work_item) -> None:
        get_work_item.return_value = {
            "id": 1642902,
            "revision": 1,
            "title": "【成都网络下令】自动化研发测试",
            "description": "用于验证项目路由。",
            "acceptance_criteria": "路由至成都项目。",
            "state": "新建",
            "work_item_type": "用户情景",
            "area_path": "XiNanArea-New\\四川省区团队",
        }
        project = {
            "enabled": True,
            "simulation_mode": False,
            "name": "巴中自巡航-自研",
            "tfs_collection_url": "http://dev.tellhowsoft.com/DefaultCollection",
            "tfs_area_path": "XiNanArea-New\\四川省区团队",
            "routing_title_keywords": ["巴中"],
            "allowed_work_item_types": ["用户情景"],
            "allowed_states": ["新建", "已评审"],
        }
        with self.assertRaisesRegex(RuntimeError, "未命中项目"):
            worker._validate({"work_item_id": 1642902}, project)

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
                "routing_title_keywords": ["成都网络下令"],
                "repository_path": "C:\\work\\demo",
                "repository_paths": ["C:\\work\\demo", "C:\\work\\demo-api"],
                "repository_tfs_paths": {
                    "demo": "http://dev.tellhowsoft.com/DefaultCollection/DCS/_git/demo",
                    "demo-api": "http://dev.tellhowsoft.com/DefaultCollection/DCS/_git/demo-api",
                },
                "base_branch": "dev",
                "repository_base_branches": {"demo-api": "chongqing"},
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
        self.assertEqual(len(synced["repository_tfs_paths"]), 2)
        self.assertTrue(synced["repository_tfs_paths"]["demo"].endswith("/_git/demo"))
        self.assertEqual(synced["repository_base_branches"], {"demo-api": "chongqing"})
        self.assertEqual(synced["routing_title_keywords"], ["成都网络下令"])
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
        self.assertEqual(chengdu["tfs_area_path"], "XiNanArea-New\\四川省区团队")
        self.assertIn("成都网络下令", chengdu["routing_title_keywords"])
        self.assertEqual(chengdu["delivery_mode"], "sichuan_auto_review")
        self.assertEqual(chengdu["reviewer_name"], "朱星舟")
        self.assertEqual(chengdu["base_branch"], "dev")
        self.assertEqual(len(chengdu["repository_paths"]), 9)

    def test_local_project_preset_catalog_contains_nanchong_sichuan_review(self) -> None:
        projects = load_project_presets()
        nanchong = next(item for item in projects if item["project_key"] == "nanchong-network-command")
        self.assertEqual(nanchong["name"], "南充网络发令")
        self.assertEqual(nanchong["tfs_area_path"], "XiNanArea-New\\四川省区团队")
        self.assertEqual(nanchong["routing_title_keywords"], ["南充网络发令", "南充网络下令"])
        self.assertEqual(nanchong["delivery_mode"], "sichuan_auto_review")
        self.assertEqual(nanchong["reviewer_name"], "朱星舟")
        self.assertEqual(nanchong["base_branch"], "dev")
        self.assertEqual(len(nanchong["repository_paths"]), 7)
        self.assertTrue(all(path.startswith("C:\\work\\workSpaceTellHow\\dcsd-springboot-sichuannc\\") for path in nanchong["repository_paths"]))

    def test_network_command_app_preset_enforces_reviewed_changed_side_packaging(self) -> None:
        projects = {item["project_key"]: item for item in load_project_presets()}
        project = projects["network-command-app"]
        self.assertEqual(project["name"], "网络发令APP")
        self.assertEqual(project["delivery_mode"], "sichuan_review_local_package")
        self.assertEqual(project["reviewer_name"], "朱星舟")
        self.assertEqual(project["base_branch"], "dev")
        self.assertEqual(
            [Path(value).name for value in project["repository_paths"]],
            ["dcsd-app-ui", "dcsd-app-starter"],
        )
        self.assertIn("-ValidateOnly", project["verification_command"])
        self.assertEqual(project["package_patterns"], ["release/ddyxzhyy.zip", "release/dcsd-app-starter*.jar"])
        self.assertEqual(project["config_patterns"], [])
        self.assertIn("_sccd", project["development_instructions"])
        self.assertIn("serviceIdMap", project["development_instructions"])
        self.assertIn("不单独交付", project["development_instructions"])
        self.assertIsInstance(project["repository_tfs_paths"], dict)
        self.assertEqual(project["repository_expectations"]["dcsd-app-ui"], "dcsd-app-ui-sichuan")
        script = (
            Path(__file__).resolve().parents[1]
            / "local-runner"
            / "project-scripts"
            / "network-command-app-package.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("AUTODEV_CHANGED_REPOSITORIES", script)
        self.assertIn("nanchongydzt-build", script)
        self.assertIn("VUE_APP_PLATFORM", script)
        self.assertIn("ddyxzhyy", script)
        self.assertIn("fetchYdztToken", script)

    def test_project_catalog_resolves_display_safe_tfs_origins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "demo-ui"
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                [
                    "git", "-C", str(repository), "remote", "add", "origin",
                    "https://user:secret@dev.example.test/DefaultCollection/DCS/_git/demo-ui/",
                ],
                check=True,
            )
            _repository_origin.cache_clear()
            paths = repository_tfs_paths({"repository_paths": [str(repository)]})
        self.assertEqual(
            paths,
            {"demo-ui": "https://dev.example.test/DefaultCollection/DCS/_git/demo-ui"},
        )

    def test_reviewed_local_package_builds_only_changed_repository_side(self) -> None:
        class Store:
            remote = False

            def __init__(self, workspace: Path) -> None:
                self.request = {
                    "id": "reviewed-package-request",
                    "work_item_id": 930090,
                    "delivery_mode": "sichuan_review_local_package",
                    "policy_snapshot": {
                        "base_branch": "dev",
                        "build_command": "pwsh.exe package.ps1",
                        "package_patterns": ["release/network-command-app-*.zip"],
                    },
                }
                self.steps: list[tuple[str, str, str]] = []
                self.events: list[tuple[str, str]] = []
                self.workspace = workspace

            def detail(self, request_id: str) -> dict:
                return self.request

            def update_request(self, request_id: str, **fields) -> None:
                self.request.update(fields)

            def update_step(self, request_id: str, code: str, status: str, message: str) -> None:
                self.steps.append((code, status, message))

            def add_event(self, request_id: str, event_type: str, message: str, **kwargs) -> None:
                self.events.append((event_type, message))

            def add_artifact(self, *args, **kwargs) -> int:
                return 1

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            ui = workspace / "dcsd-app-ui"
            backend = workspace / "dcsd-app-starter"
            ui.mkdir()
            backend.mkdir()
            states = [
                {
                    "name": "dcsd-app-ui",
                    "worktree_path": str(ui),
                    "base_branch": "dev",
                    "merge_commit": "merge-ui",
                    "changed_files": ["src/views/nanchong/App.vue"],
                },
                {
                    "name": "dcsd-app-starter",
                    "worktree_path": str(backend),
                    "base_branch": "dev",
                    "merge_commit": "merge-backend",
                    "changed_files": [],
                },
            ]
            store = Store(workspace)
            reviewed_worker = Worker(store=store)
            with patch("app.orchestrator.git", side_effect=lambda _path, *args, **_kwargs: "build-ui" if args[:2] == ("rev-parse", "HEAD") else ""), patch(
                "app.orchestrator.subprocess.run", return_value=SimpleNamespace(returncode=0)
            ), patch("app.orchestrator.run_command", return_value="build ok") as build, patch.object(
                reviewed_worker.artifacts, "collect_packages"
            ) as collect, patch.object(reviewed_worker, "_complete_delivery") as complete:
                reviewed_worker._deliver_reviewed_local_package("reviewed-package-request", states)

        build.assert_called_once()
        environment = build.call_args.kwargs["env_overrides"]
        self.assertEqual(json.loads(environment["AUTODEV_CHANGED_REPOSITORIES"]), ["dcsd-app-ui"])
        self.assertEqual(build.call_args.args[1], workspace)
        collect.assert_called_once_with(
            "reviewed-package-request",
            workspace,
            ["release/network-command-app-*.zip"],
            artifact_policy={},
        )
        complete.assert_called_once_with("reviewed-package-request")
        self.assertTrue(any("dcsd-app-ui" in message for _, _, message in store.steps))

    def test_local_project_catalog_contains_new_network_command_projects(self) -> None:
        projects = {item["project_key"]: item for item in load_project_presets()}
        expected = {
            "sichuan-dispatch-network-command": ("四川省调网络发令", "sichuan_auto_review", 9),
            "chongqing-dispatch-network-command": ("重庆市调网络发令", "sichuan_auto_review", 10),
            "aba-network-command": ("阿坝网络发令", "sichuan_auto_review", 9),
            "guangan-network-command": ("广安网络发令", "sichuan_auto_review", 9),
            "bazhong-network-command": ("巴中网络发令", "sichuan_auto_review", 9),
            "suining-network-command": ("遂宁网络发令", "sichuan_auto_review", 9),
            "ziyang-network-command": ("资阳网络发令", "sichuan_auto_review", 9),
        }
        for key, (name, mode, repository_count) in expected.items():
            with self.subTest(project=key):
                project = projects[key]
                self.assertEqual(project["name"], name)
                self.assertEqual(project["delivery_mode"], mode)
                self.assertEqual(project["base_branch"], "dev")
                self.assertEqual(len(project["repository_paths"]), repository_count)
                self.assertTrue(project["build_command"])
        chongqing = projects["chongqing-dispatch-network-command"]
        self.assertEqual(chongqing["repository_base_branches"], {"dcsd-springboot-starter": "chongqing"})
        for key in (
            "sichuan-dispatch-network-command",
            "chongqing-dispatch-network-command",
            "aba-network-command",
            "guangan-network-command",
            "bazhong-network-command",
            "suining-network-command",
            "ziyang-network-command",
        ):
            self.assertEqual(projects[key]["reviewer_name"], "朱星舟")

    @patch("app.project_catalog.TfsClient.get_work_item")
    def test_new_network_command_projects_route_by_title(self, get_work_item) -> None:
        cases = (
            ("【四川省调网络发令】功能优化", "sichuan-dispatch-network-command", "XiNanArea-New\\四川省区团队"),
            ("【重庆市调网络发令】功能优化", "chongqing-dispatch-network-command", "XiNanArea-New\\重庆市调"),
            ("【阿坝网络发令】功能优化", "aba-network-command", "XiNanArea-New\\四川省区团队"),
            ("【广安网络发令】功能优化", "guangan-network-command", "XiNanArea-New\\四川省区团队"),
            ("【巴中网络发令】功能优化", "bazhong-network-command", "XiNanArea-New\\四川省区团队"),
            ("【遂宁网络发令】功能优化", "suining-network-command", "XiNanArea-New\\四川省区团队"),
            ("【资阳网络发令】功能优化", "ziyang-network-command", "XiNanArea-New\\四川省区团队"),
        )
        for index, (title, expected_key, area_path) in enumerate(cases, start=1):
            with self.subTest(project=expected_key):
                get_work_item.return_value = {"id": 920000 + index, "title": title, "area_path": area_path}
                project, _ = resolve_project_for_work_item(920000 + index)
                self.assertEqual(project["project_key"], expected_key)

    def test_local_controller_can_update_project_aliases_for_title_routing(self) -> None:
        with tempfile.TemporaryDirectory() as preset_dir:
            path = Path(preset_dir) / "alias-project.json"
            original = {
                "project_key": "alias-project",
                "name": "别名路由项目",
                "enabled": True,
                "simulation_mode": False,
                "tfs_collection_url": "http://tfs.example.test/DefaultCollection",
                "tfs_area_path": "Area\\Team",
                "repository_path": "C:\\work\\alias-project",
                "routing_title_keywords": ["旧别名"],
            }
            path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            local_settings = SimpleNamespace(project_preset_dir=Path(preset_dir), runner_id="alias-runner")
            with patch("app.project_catalog.settings", local_settings):
                updated = update_project_routing_aliases(
                    "alias-project",
                    ["网络发令", " 网络下令 ", "网络发令"],
                )
                projects = load_project_presets()

            self.assertEqual(updated["routing_title_keywords"], ["网络发令", "网络下令"])
            self.assertEqual(projects[0]["routing_title_keywords"], ["网络发令", "网络下令"])
            self.assertEqual(projects[0]["runner_id"], "alias-runner")
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["repository_path"], original["repository_path"])
            self.assertNotIn("runner_id", persisted)

            with patch.object(
                TfsClient,
                "get_work_item",
                return_value={"title": "【测试】网络下令功能调整", "area_path": "Area\\Team"},
            ):
                matched, _ = resolve_project_for_work_item(910022, projects)
            self.assertEqual(matched["project_key"], "alias-project")

    @patch("app.project_catalog.TfsClient.get_work_item")
    def test_local_catalog_routes_nanchong_network_command_by_title(self, get_work_item) -> None:
        get_work_item.return_value = {
            "id": 1643001,
            "title": "【南充网络发令】自动化研发测试",
            "area_path": "XiNanArea-New\\四川省区团队",
        }
        project, item = resolve_project_for_work_item(1643001)
        self.assertEqual(project["project_key"], "nanchong-network-command")
        self.assertEqual(project["delivery_mode"], "sichuan_auto_review")
        self.assertEqual(item["id"], 1643001)

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
        self.assertEqual(
            [call.kwargs["repository_name"] for call in evidence.call_args_list],
            ["repo-one", "repo-two"],
        )
        self.assertEqual(
            [call.args[1]["id"] for call in evidence.call_args_list],
            [101, 102],
        )
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
            directory = pm_client.get("/api/notification-recipients")
            self.assertEqual(directory.status_code, 200, directory.text)
            selectable = {
                email
                for target in directory.json()["users"]
                for email in target["emails"]
            }
            self.assertIn("pm.backup@example.com", selectable)
            self.assertIn("pm@example.com", selectable)
            submitted = pm_client.post(
                "/api/requests",
                json={
                    "project_id": project_id,
                    "work_item_id": 910006,
                    "notification_emails": ["pm.backup@example.com", "pm@example.com"],
                    "delivery_options": ["auto_release"],
                },
            )
            self.assertEqual(submitted.status_code, 200, submitted.text)
            detail = pm_client.get(f"/api/requests/{submitted.json()['id']}").json()["request"]
            self.assertEqual(
                detail["notification_emails"],
                ["pm.backup@example.com", "pm@example.com"],
            )
            self.assertEqual(detail["delivery_options"], ["auto_release"])
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


    def test_app_titles_do_not_route_to_pc_project(self) -> None:
        projects = load_project_presets()
        for prefix in ("成都网络下令APP", "成都网络发令APP", "成都网络下令 app", "网络发令APP", "成都app"):
            with self.subTest(prefix=prefix):
                item = {"id": 1666884, "title": f"【{prefix}】测试需求", "area_path": "XiNanArea-New\\四川省区团队",
                        "description": "成都网络发令APP修复厂站确认角标。"}
                selected, _, _ = resolve_projects_for_work_item(1666884, projects, work_item=item)
                self.assertEqual([p["project_key"] for p in selected], ["network-command-app"])

    def test_app_and_pc_separate_occurrences_still_allow_joint_work(self) -> None:
        projects = load_project_presets()
        for title in ("【成都网络发令APP】【成都网络发令】联合需求", "【成都网络发令APP+成都网络发令】联合需求"):
            item = {"id": 1, "title": title, "area_path": "XiNanArea-New\\四川省区团队",
                    "description": "<p>成都网络发令APP修改页面。</p><p>成都网络发令修改PC服务。</p><p>两端字段保持一致。</p>"}
            selected, _, classified = resolve_projects_for_work_item(1, projects, work_item=item)
            self.assertEqual({p["project_key"] for p in selected}, {"network-command-app", "chengdu-network-command"})
            pc = next(c for c in classified if c["project_key"] == "chengdu-network-command")
            self.assertFalse(any("APP修改页面" in s for s in pc["scoped_sections"]))
            self.assertIn("两端字段保持一致。", pc["shared_sections"])

    def test_incomplete_bracket_and_ambiguous_alias_are_not_guessed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "未完整匹配"):
            matching_project_terms("【成都网络下令APP】需求", [{"project_key": "pc", "name": "成都网络下令"}])
        with self.assertRaisesRegex(RuntimeError, "歧义"):
            matching_project_terms("【成都app】需求", [
                {"project_key": "one", "name": "甲项目", "routing_title_keywords": ["成都app"]},
                {"project_key": "two", "name": "乙项目", "routing_title_keywords": ["成都app"]},
            ])

    def test_stale_pc_snapshot_is_rejected_before_app_workspace_preparation(self) -> None:
        project = next(p for p in load_project_presets() if p["project_key"] == "chengdu-network-command")
        item = {"id": 1666884, "title": "【成都网络下令APP】测试", "description": "只修改成都APP",
                "state": "新建", "work_item_type": "用户情景", "area_path": "XiNanArea-New\\四川省区团队"}
        with patch("app.orchestrator.TfsClient.get_work_item", return_value=item):
            with self.assertRaisesRegex(RuntimeError, "快照"):
                worker._validate({"work_item_id": item["id"]}, project)

    def test_app_repository_contract_checks_missing_and_wrong_remote(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "dcsd-app-ui"
            (repo / ".git").mkdir(parents=True)
            project = {"repository_paths": [str(repo)], "repository_expectations": {"dcsd-app-starter": "dcsd-app-starter-sichuan"}}
            with self.assertRaisesRegex(RuntimeError, "缺少必需仓库"):
                worker._configured_repository_paths(project)
            project["repository_expectations"] = {"dcsd-app-ui": "dcsd-app-ui-sichuan"}
            with patch("app.orchestrator.git", return_value="http://tfs/DCS/_git/dcsd-app-ui"):
                with self.assertRaisesRegex(RuntimeError, "origin 仓库"):
                    worker._configured_repository_paths(project)
            with patch("app.orchestrator.git", return_value="http://tfs/DCS/_git/dcsd-app-ui-sichuan"):
                self.assertEqual(worker._configured_repository_paths(project), [repo])

    def test_risk_levels_preserve_legacy_caution_and_explicit_blockers(self) -> None:
        advisory = {"decision": "completed", "risks": ["无指定测试票，已用历史样例核验"], "blocking_risks": []}
        self.assertEqual(development_risks(advisory, legacy_review=True)[1], [])
        self.assertTrue(development_risks({"risks": ["未分级旧风险"]}, legacy_review=True)[1])
        self.assertEqual(development_risks({"risks": [], "blocking_risks": ["客户端仓库缺失"]})[1], ["客户端仓库缺失"])
        with self.assertRaisesRegex(RuntimeError, "格式无效"):
            development_risks({"blocking_risks": "bad"})

    def test_advisory_risk_continues_but_blocker_does_not_submit(self) -> None:
        for number, blockers in enumerate(([], ["缺少客户端接入，验收不完整"])):
            project_id = self.create_project(f"test-risk-level-{number}", "sichuan_auto_review")
            created = self.client.post("/api/requests", json={"project_id": project_id, "work_item_id": 930201 + number})
            request_id = created.json()["id"]
            result = {"decision": "completed", "summary": "完成研发", "changed_files": ["demo.java"],
                      "risks": ["缺少截图指定样例，使用等价样例"], "blocking_risks": blockers}
            with patch.object(worker, "_simulate_development", return_value=result), patch.object(worker, "_send_status_email") as mail:
                worker.process_once()
            detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
            if blockers:
                self.assertEqual(detail["status"], "waiting_approval")
                self.assertIsNone(detail["pr_id"])
                mail.assert_called_once_with(request_id, action_required=True)
            else:
                self.assertEqual(detail["status"], "waiting_merge")
                self.assertTrue(detail["pr_id"])
            # Do not leave this fixture ahead of other tests in the merge poll queue.
            update_request(request_id, status="cancelled")

    def test_blocker_without_code_changes_waits_for_confirmation(self) -> None:
        project_id = self.create_project("test-empty-blocker", "sichuan_auto_review")
        created = self.client.post("/api/requests", json={"project_id": project_id, "work_item_id": 930203})
        request_id = created.json()["id"]
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        snapshot = {**detail["policy_snapshot"], "simulation_mode": False}
        update_request(request_id, policy_snapshot=json.dumps(snapshot))
        state = {"name": "demo", "base_commit": "baseline", "branch": "feature/test", "worktree_path": TEST_DATA.name}
        result = {"decision": "completed", "summary": "缺少客户端，未修改代码", "risks": [],
                  "blocking_risks": ["缺少客户端仓库"], "changed_files": []}
        with patch.object(worker, "_validate", return_value={"id": 930203, "title": "APP 测试"}), \
             patch.object(worker, "_validate_delivery_plan"), \
             patch.object(worker, "_prepare_worktrees", return_value=(Path(TEST_DATA.name), [state], "feature/test")), \
             patch("app.orchestrator.CodexRunner.run", return_value=SimpleNamespace(result=result, thread_id="test")), \
             patch("app.orchestrator.changed_files", return_value=[]), \
             patch.object(worker, "_send_status_email") as mail:
            worker.run_request(request_id)
        detail = self.client.get(f"/api/requests/{request_id}").json()["request"]
        self.assertEqual(detail["status"], "waiting_approval")
        self.assertIn("缺少客户端仓库", detail["error_message"])
        self.assertIsNone(detail["pr_id"])
        mail.assert_called_once_with(request_id, action_required=True)
        update_request(request_id, status="cancelled")

    def test_all_real_project_presets_publish_experience_and_machine_gates(self) -> None:
        projects = load_project_presets()
        self.assertGreaterEqual(len(projects), 11)
        for project in projects:
            with self.subTest(project=project["project_key"]):
                self.assertTrue(project["development_instructions"])
                self.assertTrue(project["verification_command"])
                self.assertTrue(project["repository_expectations"])
                self.assertTrue(project["quality_profile"]["history_reuse"])
                self.assertTrue(project["quality_profile"]["require_acceptance_ledger"])
                self.assertTrue(project["artifact_policy"]["allowed_user_facing_kinds"])
        app_project = next(item for item in projects if item["project_key"] == "network-command-app")
        self.assertFalse(app_project["artifact_policy"]["require_manifest"])
        self.assertEqual(app_project["artifact_policy"]["allowed_user_facing_kinds"], ["package", "sql"])
        self.assertEqual(app_project["artifact_policy"]["allowed_package_extensions"], [".zip", ".jar"])
        self.assertIn(".xml", app_project["artifact_policy"]["forbidden_standalone_extensions"])
        self.assertFalse(app_project["quality_profile"]["visual"]["required_for_frontend"])
        self.assertIn("页面截图", app_project["quality_profile"]["visual"]["require_when_requirement_mentions"])

    def test_runner_history_endpoint_returns_prior_evidence_for_same_requirement(self) -> None:
        project_id = self.create_project("test-history-context", "local_package")
        first = self.submit_and_process(project_id, 940001)
        self.assertEqual(first["status"], "delivered")
        response = self.client.get(
            "/api/runner/request-history",
            params={
                "project_key": "test-history-context",
                "work_item_id": 940001,
                "request_id": "new-request",
            },
            headers={"Authorization": "Bearer test-runner-token"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        history = response.json()["history"]
        self.assertEqual(history[0]["id"], first["id"])
        self.assertEqual(history[0]["status"], "delivered")
        self.assertIsInstance(history[0]["quality_gate_result"], dict)
        for state in history[0]["repository_states"]:
            self.assertNotIn("worktree_path", state)
            self.assertNotIn("repository_path", state)

    def test_quality_gate_blocks_duplicate_sql_version_and_unmapped_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "dev", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "AutoDev Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "autodev@example.com"], check=True)
            (repository / "sql").mkdir()
            (repository / "sql" / "V2.0__baseline.sql").write_text("SELECT 1;\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "baseline"], check=True)
            base = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
            (repository / "sql" / "V2_0__feature.sql").write_text("SELECT 2;\n", encoding="utf-8")
            (repository / "Feature.java").write_text("class Feature {}\n", encoding="utf-8")
            state = {
                "name": "demo", "worktree_path": str(repository), "base_commit": base,
                "changed_files": ["sql/V2_0__feature.sql", "Feature.java"],
            }
            project = {
                "quality_profile": {
                    "require_acceptance_ledger": True,
                    "business_invariants": {"required": False, "change_patterns": []},
                    "sql": {"migration_version_guard": True},
                    "visual": {"required_for_frontend": False},
                    "menu": {"require_binding_manifest": False},
                }
            }
            result = {
                "decision": "completed",
                "acceptance_ledger": [{
                    "id": "AC-1", "criterion": "升级 SQL", "status": "completed",
                    "repositories": ["demo"], "files": ["sql/V2_0__feature.sql"],
                    "tests": ["SQL 静态检查"], "evidence": [],
                }],
                "business_invariants": [],
            }
            gate = evaluate_development_quality(project, [state], result)
            self.assertEqual(gate["status"], "blocked")
            joined = "；".join(gate["blockers"])
            self.assertIn("迁移版本 V2.0 冲突", joined)
            self.assertIn("Feature.java", joined)
            self.assertEqual(gate["business_invariants"], [])

    def test_app_visual_gate_requires_screenshot_only_when_requirement_requests_it(self) -> None:
        state = {
            "name": "dcsd-app-ui",
            "worktree_path": TEST_DATA.name,
            "base_commit": "base",
            "changed_files": ["src/views/chengdu/Command.vue"],
        }
        project = {
            "quality_profile": {
                "require_acceptance_ledger": True,
                "business_invariants": {"required": False, "change_patterns": []},
                "visual": {
                    "required_for_frontend": False,
                    "require_when_requirement_mentions": ["页面截图", "以截图为准"],
                    "frontend_patterns": ["dcsd-app-ui/**/*.vue"],
                    "viewports": ["390x844"],
                    "deployment_checks": [
                        "asset_manifest_checked", "directory_layout_checked", "cache_strategy_checked",
                    ],
                },
                "menu": {"require_binding_manifest": False},
            }
        }
        result = {
            "decision": "completed",
            "acceptance_ledger": [{
                "id": "AC-1", "criterion": "只修改成都逻辑", "status": "completed",
                "repositories": ["dcsd-app-ui"],
                "files": ["src/views/chengdu/Command.vue"],
                "tests": ["production 构建通过", "8 条路由断言通过"],
                "evidence": [],
            }],
            "business_invariants": [],
            "visual_validation": {
                "status": "blocked", "routes": ["/chengdu/command"], "viewports": [],
                "screenshots": [], "notes": ["当前无浏览器后端"],
            },
            "deployment_validation": {
                "asset_manifest_checked": True,
                "directory_layout_checked": True,
                "cache_strategy_checked": True,
                "notes": [],
            },
        }

        normal = evaluate_development_quality(
            project, [state], result, requirement_text="只修复成都逻辑，不影响其他地市"
        )
        self.assertEqual(normal["status"], "passed")
        self.assertFalse(any("截图验收" in blocker for blocker in normal["blockers"]))
        check = next(item for item in normal["checks"] if item["id"] == "visual-acceptance")
        self.assertEqual(check["status"], "passed")

        screenshot_required = evaluate_development_quality(
            project, [state], result, requirement_text="修改完成后提供页面截图，以截图为准"
        )
        self.assertEqual(screenshot_required["status"], "blocked")
        self.assertTrue(any("截图验收" in blocker for blocker in screenshot_required["blockers"]))

    def test_artifact_policy_rejects_xml_as_standalone_app_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Mapper.xml").write_text("<mapper/>\n", encoding="utf-8")
            service = ArtifactService(recorder=lambda *_args: 1)
            with self.assertRaisesRegex(RuntimeError, "禁止把 .xml 文件作为独立交付物"):
                service.collect_packages(
                    "app-policy",
                    root,
                    ["*.xml"],
                    artifact_policy={
                        "require_packages": True,
                        "forbidden_standalone_extensions": [".xml"],
                    },
                )
        service = ArtifactService(
            detail_loader=lambda _request_id: {
                "delivery_mode": "sichuan_review_local_package",
                "delivery_options": [],
                "artifacts": [
                    {"kind": "package", "name": "ddyxzhyy.zip"},
                    {"kind": "sql", "name": "V3.1__app.sql"},
                    {"kind": "delivery_manifest", "name": "delivery-validation-manifest.json"},
                ],
            }
        )
        blockers = service.validate_artifact_policy(
            "app-policy",
            {
                "artifact_policy": {
                    "require_packages": True,
                    "allowed_user_facing_kinds": ["package", "sql"],
                    "forbidden_standalone_extensions": [".xml"],
                }
            },
        )
        self.assertIn("delivery_manifest", "；".join(blockers))


if __name__ == "__main__":
    unittest.main()
