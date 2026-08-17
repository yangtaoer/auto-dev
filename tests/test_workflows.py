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
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

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
from app.db import update_request, update_step  # noqa: E402
from app.orchestrator import Worker, worker  # noqa: E402
from app.project_catalog import (  # noqa: E402
    load_project_presets,
    resolve_project_for_work_item,
    update_project_routing_aliases,
)
from app.services.codex_runner import CodexRunner  # noqa: E402
from app.services.dm7_plugin import discover_dm7_plugin  # noqa: E402
from app.services.delivery import (  # noqa: E402
    ArtifactService,
    Mailer,
    changed_files,
    menu_link_from_view_path,
    repository_short_name,
)
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
        self.assertIn("background:#0b1020", rendered)
        self.assertIn("background:#eef1f7", rendered)
        self.assertIn("#7769ad", mailer.delivery_html(detail, action_required=True))
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

    def test_view_xml_path_becomes_menu_link_and_tfs_manifest_contains_artifacts(self) -> None:
        source = "src/main/resources/META-INF/resources/tbp_config/runtime/module/direct/views/operationTicketOverview.view.xml"
        self.assertEqual(menu_link_from_view_path(source), "/direct/views/operationTicketOverview")
        self.assertIsNone(menu_link_from_view_path(source.replace(".view.xml", ".form.xml")))
        service = ArtifactService(public_base_url="https://auto.example.test")
        detail = {
            "delivery_mode": "product_manual_review",
            "artifacts": [
                {"id": 7, "kind": "merge_screenshot", "name": "notice-srv · PR #202 · 合并截图.png", "external_url": "https://oss.test/pr-202.png"},
                {"id": 8, "kind": "menu_link", "name": "/direct/views/operationTicketOverview", "external_url": "/direct/views/operationTicketOverview"},
                {"id": 9, "kind": "config", "name": "source.yml", "external_url": "https://oss.test/source.yml"},
                {"id": 10, "kind": "license_request", "name": "License 授权申请 #1652475", "external_url": "https://tfs.test/_workitems/edit/1652475"},
            ],
        }
        manifest = service.delivery_manifest_html(detail)
        self.assertIn("https://oss.test/pr-202.png", manifest)
        self.assertIn("/direct/views/operationTicketOverview", manifest)
        self.assertIn("新增视图菜单链接", manifest)
        self.assertIn("License 授权申请", manifest)
        self.assertIn("https://tfs.test/_workitems/edit/1652475", manifest)
        self.assertNotIn("source.yml", manifest)

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

    def test_sichuan_review_then_merge(self) -> None:
        project_id = self.create_project("test-sichuan", "sichuan_auto_review")
        detail = self.submit_and_process(project_id, 910002)
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
        self.assertNotIn("pull_request", {item["kind"] for item in completed["artifacts"]})

    def test_product_review_emails_before_and_after_merge(self) -> None:
        project_id = self.create_project("test-product", "product_manual_review")
        detail = self.submit_and_process(project_id, 910003)
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
        self.assertNotIn("pull_request", {item["kind"] for item in completed["artifacts"]})

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
        self.assertEqual(page.text.count("SYSTEM V1.0-Alpha"), 1)
        self.assertIn("AutoDev", page.text)
        self.assertIn("/static/brand/autodev-sidebar-mark.png", page.text)
        self.assertNotIn("DELIVERY LOOP", page.text)
        self.assertNotIn("系统版本 / VERSION", page.text)
        self.assertIn("control-strip", page.text)
        self.assertIn('id="project-guide"', page.text)
        self.assertIn("支持项目与别名", page.text)
        self.assertIn("可自助研发项目", page.text)
        self.assertIn("project-guide", page.text)

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
        self.assertIn("renderProjectGuide", script)
        self.assertNotIn("project-guide-trigger')?.addEventListener('click'", script)

        login_template = Path("app/templates/login.html").read_text(encoding="utf-8")
        self.assertIn("login-logo-lockup", login_template)
        self.assertIn("autodev-mark.png", login_template)
        self.assertIn("Auto<span>Dev</span>", login_template)
        self.assertNotIn("brand-float", Path("app/static/brand-ui.css").read_text(encoding="utf-8"))
        self.assertNotIn("login-logo-plate", login_template)
        self.assertNotIn("CONTINUOUS CODE DELIVERY", login_template)
        brand_styles = Path("app/static/brand-ui.css").read_text(encoding="utf-8")
        self.assertIn("width: 255px", brand_styles)
        self.assertIn(".project-guide-panel", brand_styles)
        self.assertIn(".project-guide:hover .project-guide-panel", brand_styles)

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
        self.assertIn('id="project-guide"', pm_page.text)
        self.assertIn("支持项目与别名", pm_page.text)
        self.assertEqual(pm_page.text.count("SYSTEM V1.0-Alpha"), 1)
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

    def test_local_project_preset_catalog_contains_nanchong_product_review(self) -> None:
        projects = load_project_presets()
        nanchong = next(item for item in projects if item["project_key"] == "nanchong-network-command")
        self.assertEqual(nanchong["name"], "南充网络发令")
        self.assertEqual(nanchong["tfs_area_path"], "XiNanArea-New\\四川省区团队")
        self.assertEqual(nanchong["routing_title_keywords"], ["南充网络发令", "南充网络下令"])
        self.assertEqual(nanchong["delivery_mode"], "product_manual_review")
        self.assertEqual(nanchong["base_branch"], "dev")
        self.assertEqual(len(nanchong["repository_paths"]), 7)
        self.assertTrue(all(path.startswith("C:\\work\\workSpaceTellHow\\dcsd-springboot-sichuannc\\") for path in nanchong["repository_paths"]))

    def test_local_project_catalog_contains_new_network_command_projects(self) -> None:
        projects = {item["project_key"]: item for item in load_project_presets()}
        expected = {
            "sichuan-dispatch-network-command": ("四川省调网络发令", "product_manual_review", 9),
            "chongqing-dispatch-network-command": ("重庆市调网络发令", "product_manual_review", 10),
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
        self.assertEqual(project["delivery_mode"], "product_manual_review")
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
