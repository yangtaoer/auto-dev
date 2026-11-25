from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.artifact_contract import (
    BUSINESS_ARTIFACT_KINDS, changed_asset_kind, configuration_template, enforce_artifact_policy,
)
from app.services.database_audit import DatabaseWriteJournal, database_write_receipt, sql_fingerprint
from app.services.delivery_assets import reconcile_delivery_assets
from app.services.delivery_sql import delivery_script_problem


class DeliveryContractTests(unittest.TestCase):
    def test_all_presets_include_root_sql_config_and_global_policy(self):
        from app.project_experience import apply_project_experience
        paths = list((Path(__file__).resolve().parents[1] / "local-runner/project-presets").glob("*.json"))
        self.assertIn("aba-self-developed.json", {path.name for path in paths})
        self.assertGreaterEqual(len(paths), 13)
        for path in paths:
            with self.subTest(project=path.stem):
                project = apply_project_experience(json.loads(path.read_text(encoding="utf-8")))
                self.assertIn("*.sql", project["sql_patterns"])
                self.assertIn("**/*.properties", project["config_patterns"])
                self.assertNotIn("**/*.xml", project["config_patterns"])
                self.assertEqual(set(project["artifact_policy"]["allowed_user_facing_kinds"]), BUSINESS_ARTIFACT_KINDS)
                self.assertTrue(project["artifact_policy"]["require_packages"])
                self.assertEqual(set(project["artifact_policy"]["allowed_changed_asset_kinds"]), {"sql", "config", "rollback_note"})
                self.assertIn("必须先保存", project["development_instructions"])
                self.assertIn("禁止", project["development_instructions"])
                self.assertIn("存储过程", project["development_instructions"])

    def test_stale_snapshot_cannot_disable_sql_or_configuration_delivery(self):
        project = enforce_artifact_policy({"artifact_policy": {"allowed_changed_asset_kinds": ["sql"],
                                           "allowed_user_facing_kinds": ["menu_link", "delivery_manifest"]},
                                           "config_patterns": []})
        self.assertIn("config", project["artifact_policy"]["allowed_changed_asset_kinds"])
        self.assertEqual(set(project["artifact_policy"]["allowed_user_facing_kinds"]), BUSINESS_ARTIFACT_KINDS)

    def test_asset_classifier_excludes_sources_test_data_and_local_secrets(self):
        for path in ["upgrade.sql", "SQL/V1__schema.SQL", "src/main/resources/schema.sql"]:
            self.assertEqual(changed_asset_kind(path), "sql")
        for path in ["application.yml", "conf/service.properties", "deploy/nginx.conf", "config/runtime.js",
                     "settings/application.xml", "server-config.xml", "deploy/changes.properties.template"]:
            self.assertEqual(changed_asset_kind(path), "config", path)
        for path in ["pom.xml", "UserMapper.xml", "direct.view.xml", "tests/seed.sql", "test/sample.sql",
                     "docs/validation.json", ".env.backend", "secrets/server.yml", "../outside.sql",
                     "C:/outside.sql", "target/classes/schema.sql", ".github/workflows/build.yml"]:
            self.assertEqual(changed_asset_kind(path, {"config_patterns": ["**/*.xml"]}, declared_kind="config"), "", path)

    def test_visibility_is_identical_for_all_modes_and_keeps_analysis_report_separate(self):
        from app.domain import visible_delivery_artifacts
        kinds = ["package", "sql", "config", "rollback_note", "release_artifact", "delivery_manifest", "verification_report",
                 "package_note", "license_request", "merge_screenshot", "menu_link", "report", "email_preview"]
        names = {"sql": "upgrade.sql", "config": "runtime.properties", "rollback_note": "rollback.txt"}
        artifacts = [{"kind": kind, "name": names.get(kind, kind)} for kind in kinds]
        for mode in ["local_package", "sichuan_auto_review", "sichuan_review_local_package", "product_manual_review"]:
            for options in [None, [], ["auto_release"], ["merge_screenshot", "license_request"]]:
                self.assertEqual({item["kind"] for item in visible_delivery_artifacts(mode, artifacts, options)}, BUSINESS_ARTIFACT_KINDS)
        self.assertEqual(visible_delivery_artifacts("local_package", [{"kind": "analysis_report", "name": "analysis.md"}]),
                         [{"kind": "analysis_report", "name": "analysis.md"}])

    def test_collector_copies_root_nested_and_explicit_files_once_for_reviewed_delivery(self):
        from app.services.delivery import ArtifactService
        from app.config import settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            files = {"upgrade.sql": "CREATE TABLE T (ID INT);", "sql/fix.sql": "UPDATE T SET ID=1;",
                     "application.properties": "feature.enabled=true\ndb.password=secret-for-test\n",
                     "web.view.xml": "<view/>", "pom.xml": "<project/>", "tests/seed.sql": "DELETE FROM T;",
                     "runtime-settings.xml": "<config><feature enabled='true'/></config>",
                     "rollback.txt": "从部署前快照恢复旧记录，并核对待恢复对象的状态。"}
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            artifacts = []
            def record(request_id, kind, name, local_path, external_url):
                artifacts.append(dict(id=len(artifacts)+1, kind=kind, name=name, local_path=local_path))
                return len(artifacts)
            service = ArtifactService(recorder=record, detail_loader=lambda _id: {"artifacts": artifacts})
            declared = [{"repository": "repo", "path": "runtime-settings.xml", "kind": "config"},
                        {"repository": "repo", "path": "rollback.txt", "kind": "rollback_note"}]
            with patch("app.services.delivery.settings", SimpleNamespace(delivery_dir=Path(directory) / "delivery")), patch(
                "app.services.delivery.changed_files", return_value=list(files)[:-1]
            ):
                first = service.collect_changed_assets("task", root, "base", {}, repository_name="repo", declared_assets=declared)
                second = service.collect_changed_assets("task", root, "base", {}, repository_name="repo", declared_assets=declared)
            self.assertEqual(first, second)
            self.assertEqual(len(artifacts), 5)
            self.assertEqual({item["kind"] for item in artifacts}, {"sql", "config", "rollback_note"})
            self.assertTrue(all(item["name"].startswith("repo/") for item in artifacts))
            self.assertNotIn("secret-for-test", Path(artifacts[2]["local_path"]).read_text(encoding="utf-8"))
            self.assertIn("secret-for-test", (root / "application.properties").read_text(encoding="utf-8"))

    def test_missing_declared_script_and_configuration_are_ordinary_repair_gaps(self):
        assets, blockers = reconcile_delivery_assets({}, [], {"sql_changes": ["建表"], "config_changes": ["新增开关"]})
        self.assertEqual(assets, [])
        self.assertTrue(any(".sql" in value for value in blockers))
        self.assertTrue(any("配置模板" in value for value in blockers))

    def test_procedure_statements_are_blocked_and_direct_sql_is_allowed(self):
        blocked = [
            "CREATE OR REPLACE\nPROCEDURE fix_data AS BEGIN NULL; END;",
            "IF OBJECT_ID('dbo.fix_data') IS NOT NULL DROP PROC dbo.fix_data;",
            "CREATE OR ALTER /* deploy */ PROC dbo.fix_data AS SELECT 1;",
            "ALTER PROCEDURE fix_data LANGUAGE SQL;",
            'CALL "Schema" . "fix_data"();',
            "{? = CALL fix_data(?)}",
            "EXEC @code = dbo.fix_data;",
            "/* ! ordinary comment */ EXECUTE IMMEDIATE 'UPDATE T SET ID=1';",
            "/*!50003 CREATE*/ /*!50017 DEFINER=u@h*/ /*!50003 PROCEDURE fix_data() SELECT 1 */;",
            "DO LANGUAGE plpgsql $$BEGIN NULL; END$$;",
            "DECLARE changed INT := 1;\nBEGIN fix_data(changed); END;\n/",
        ]
        allowed = [
            "BEGIN TRANSACTION;\nUPDATE T SET ID=1;\nCOMMIT;",
            "BEGIN;\nUPDATE T SET VALUE='EXEC fix_data;';\nROLLBACK;",
            "UPDATE T SET VALUE='CREATE PROCEDURE ''中文''';\n-- CALL fix_data();",
            "INSERT INTO T VALUES (q'[keep 'CREATE PROCEDURE note']');",
            "INSERT INTO T VALUES ($note$DROP PROCEDURE is written in the document$note$);",
            "UPDATE T SET [EXEC]=1;\nGRANT EXECUTE ON T TO role1;",
            "GRANT EXECUTE ANY PROCEDURE TO role1;",
            "CREATE TRIGGER t AFTER UPDATE ON items FOR EACH ROW EXECUTE FUNCTION audit_item();",
            "INSERT INTO T VALUES (1) ON CONFLICT (id)\nDO UPDATE SET id = 1;",
        ]
        for sql in blocked:
            with self.subTest(sql=sql):
                self.assertIn("存储过程", delivery_script_problem("sql", sql))
        for sql in allowed:
            with self.subTest(sql=sql):
                self.assertFalse(delivery_script_problem("sql", sql))

    def test_changed_procedure_cannot_be_delivered_or_excluded_as_temporary(self):
        from app.services.delivery import ArtifactService
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rollback.SQL").write_text("CALL restore_legacy();", encoding="utf-16")
            state = {"name": "repo", "worktree_path": directory, "changed_files": ["rollback.SQL"]}
            result = {"database_changes": [{"classification": "temporary_verification",
                                           "script_paths": ["repo/rollback.SQL"], "evidence": "清理完成"}]}
            assets, blockers = reconcile_delivery_assets({}, [state], result)
            self.assertFalse(assets)
            self.assertIn("repo/rollback.SQL", blockers[0])
            self.assertIn("存储过程", blockers[0])
            service = ArtifactService(recorder=lambda *_args: self.fail("Procedure should not be recorded"),
                                      detail_loader=lambda _id: {})
            with self.assertRaisesRegex(RuntimeError, "存储过程"):
                service.collect_changed_assets("task", root, "base", {}, repository_name="repo",
                                               changed_paths=["rollback.SQL"])

    def test_legacy_data_manual_rollback_requires_a_real_note_and_preserves_write_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "rollback.txt"
            note.write_text("原状态未留在业务库。恢复归档备份中的旧记录，再按原清单核对条数。", encoding="utf-8")
            state = {"name": "repo", "worktree_path": directory, "changed_files": ["rollback.txt"]}
            record = {"classification": "manual_rollback", "script_paths": ["repo/rollback.txt"],
                      "evidence": "业务库没有恢复原值所需的历史记录", "call_ids": []}
            result = {"database_changes": [record], "sql_changes": []}
            self.assertIn("人工回退", " ".join(reconcile_delivery_assets({}, [state], result)[1]))
            result["delivery_assets"] = [{"repository": "repo", "path": note.name, "kind": "rollback_note"}]
            assets, blockers = reconcile_delivery_assets({}, [state], result)
            self.assertEqual([(item["kind"], item["path"]) for item in assets], [("rollback_note", "rollback.txt")])
            self.assertFalse(blockers)
            record["call_ids"] = ["write-1"]
            result["database_write_calls"] = [{"call_id": "write-1", "purpose": "migration", "status": "completed"}]
            self.assertIn("write-1", " ".join(reconcile_delivery_assets({}, [state], result)[1]))
            note.write_text(" \n", encoding="utf-8")
            self.assertIn("回退说明为空", " ".join(reconcile_delivery_assets({}, [state], result)[1]))

    def test_resumed_delivery_rechecks_existing_oss_sql_before_release(self):
        from app.orchestrator import Worker
        detail = {"id": "task", "delivery_mode": "sichuan_auto_review", "status": "delivering", "policy_snapshot": {},
                  "artifacts": [{"kind": "sql", "name": "upgrade.sql",
                                 "external_url": "https://bucket.oss-cn-chengdu.aliyuncs.com/path/upgrade.sql"}]}
        worker = Worker(store=SimpleNamespace(remote=True, detail=lambda _id: detail, get_status=lambda _id: detail["status"],
                                              add_artifact=lambda *_args: None))
        with patch("app.services.delivery.read_oss_artifact", return_value=b"CALL fix_legacy();"), \
                patch.object(worker, "_coordinated_release") as release:
            with self.assertRaisesRegex(RuntimeError, "存储过程"):
                worker._complete_delivery("task")
            release.assert_not_called()

    def test_one_saved_sql_does_not_hide_another_missing_script(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "first.sql").write_text("CREATE TABLE T (ID INT);", encoding="utf-8")
            state = {"name": "repo", "worktree_path": directory, "changed_files": ["first.sql"]}
            _, blockers = reconcile_delivery_assets({}, [state], {"sql_changes": ["first.sql", "second.sql"]})
            self.assertTrue(any("second.sql" in item for item in blockers))

    def test_missing_business_package_never_creates_fake_readme_deliverable(self):
        from app.services.delivery import ArtifactService
        with tempfile.TemporaryDirectory() as directory:
            service = ArtifactService(recorder=lambda *_args: self.fail("Must not record a fake package"))
            with self.assertRaisesRegex(RuntimeError, "未匹配到任何正式交付包"):
                service.collect_packages("task", Path(directory), ["release/*.zip"])

    def test_remote_paths_are_not_checked_on_the_runner_filesystem(self):
        from app.services.delivery import ArtifactService
        service = ArtifactService(detail_loader=lambda _id: {"delivery_mode": "sichuan_auto_review", "artifacts": [
            {"kind": "release_artifact", "name": "Build #1", "external_url": "https://tfs.test/artifacts"},
            {"kind": "sql", "name": "upgrade.sql", "local_path": "/cloud-only/data/deliveries/upgrade.sql"},
        ]})
        self.assertFalse(service.validate_artifact_policy("task", {"artifact_policy": {"require_packages": True}}))

    def test_review_finalization_recovers_sql_and_config_before_pipeline_and_email(self):
        from app.config import settings
        from app.orchestrator import Worker
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            (root / "upgrade.sql").write_text("CREATE TABLE T (ID INT);", encoding="utf-8")
            (root / "application.yml").write_text("feature: true\n", encoding="utf-8")
            detail = {"id": "task", "work_item_id": 1, "delivery_mode": "sichuan_auto_review",
                      "delivery_options": ["auto_release"], "status": "delivering", "artifacts": [],
                      "policy_snapshot": {"project_key": "city", "tfs_collection_url": "http://tfs.test"},
                      "repository_states": [{"name": "repo", "worktree_path": str(root), "base_commit": "base",
                                             "changed_files": ["upgrade.sql", "application.yml"]}]}
            class Store:
                remote = False
                def detail(self, _id): return detail
                def get_status(self, _id): return detail["status"]
                def update_request(self, _id, **fields): detail.update(fields)
                def add_artifact(self, _id, kind, name, local_path="", external_url=""):
                    detail["artifacts"].append(dict(id=len(detail["artifacts"])+1, kind=kind, name=name,
                                                    local_path=local_path, external_url=external_url))
                    return len(detail["artifacts"])
                def update_step(self, *_args, **_kwargs): pass
                def add_event(self, *_args, **_kwargs): pass
            store = Store()
            worker = Worker(store=store)
            def release(*_args):
                self.assertEqual({item["kind"] for item in detail["artifacts"]}, {"sql", "config"})
                store.add_artifact("task", "release_artifact", "Build #1", external_url="https://tfs.test/artifacts")
                return True
            with patch("app.services.delivery.settings", SimpleNamespace(delivery_dir=Path(directory) / "delivery")), patch.object(
                worker, "_coordinated_release", side_effect=release
            ), patch.object(worker, "_send_status_email") as send_mail, patch("app.orchestrator.TfsClient") as tfs:
                tfs.return_value.complete_delivery.return_value = {"state": "已解决"}
                worker._complete_delivery("task")
                send_mail.assert_called_once_with("task", action_required=False)
                html = tfs.return_value.complete_delivery.call_args.args[1]
                self.assertIn("upgrade.sql", html)
                self.assertIn("application.yml", html)
            self.assertEqual(detail["status"], "delivered")

    def test_executed_create_table_requires_file_even_if_result_claims_no_sql_changes(self):
        call = database_write_receipt({"type": "mcpToolCall", "tool": "dm7_execute", "id": "write-1",
                                       "status": "completed", "arguments": {"sql": "CREATE TABLE T (ID INT)", "purpose": "migration"}})
        assets, blockers = reconcile_delivery_assets({}, [], {"sql_changes": [], "database_write_calls": [call]})
        self.assertEqual(assets, [])
        self.assertIn("write-1", " ".join(blockers))

    def test_persisted_write_receipts_survive_interrupted_turn_without_raw_sql(self):
        from app.services.codex_runner import CodexRunner
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json"
            journal = DatabaseWriteJournal(path)
            events = [SimpleNamespace(method="item/started", payload={"item": {
                "type": "mcpToolCall", "tool": "dm7_execute", "id": "id-1", "status": "inProgress",
                "arguments": {"sql": "INSERT INTO T VALUES ('private-business-value')"}}}),
                SimpleNamespace(method="turn/completed", payload={"turn": {"status": "failed"}})]
            with self.assertRaises(RuntimeError):
                CodexRunner()._collect_output(events, lambda *_args: None, on_database_write=journal.observe)
            saved = DatabaseWriteJournal(path)
            self.assertIn("id-1", saved.entries)
            self.assertNotIn("private-business-value", path.read_text(encoding="utf-8"))
            self.assertNotIn("INSERT", path.read_text(encoding="utf-8"))

    def test_read_only_queries_do_not_need_scripts(self):
        for sql in ["SELECT * FROM T", "SELECT 'delete from T' AS X FROM DUAL", "SELECT ID FROM T FOR UPDATE"]:
            self.assertIsNone(database_write_receipt({"type": "mcpToolCall", "tool": "dm7_execute", "arguments": {"sql": sql}}))

    def test_business_sql_file_fingerprint_or_explicit_mapping_satisfies_observed_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sql = "CREATE TABLE T (ID INT);"
            (root / "upgrade.sql").write_text(sql, encoding="utf-8")
            state = {"name": "repo", "worktree_path": directory, "changed_files": ["upgrade.sql"]}
            call = {"call_id": "write-1", "purpose": "migration", "status": "completed", "sql_sha256": sql_fingerprint(sql)}
            assets, blockers = reconcile_delivery_assets({}, [state], {"database_write_calls": [call]})
            self.assertFalse(blockers)
            self.assertEqual(assets[0]["path"], "upgrade.sql")
            call["sql_sha256"] = "different-statement"
            result = {"database_write_calls": [call], "database_changes": [{"classification": "business_change",
                      "call_ids": ["write-1"], "script_paths": ["repo/upgrade.sql"], "evidence": "迁移中包含幂等保护"}]}
            self.assertFalse(reconcile_delivery_assets({}, [state], result)[1])

    def test_temporary_verification_requires_cleanup_and_cannot_hide_migration(self):
        call = {"call_id": "seed-1", "purpose": "test", "status": "completed", "sql_sha256": "hash"}
        record = {"classification": "temporary_verification", "call_ids": ["seed-1"], "script_paths": [], "evidence": "事务已回滚"}
        self.assertFalse(reconcile_delivery_assets({}, [], {"database_write_calls": [call], "database_changes": [record]})[1])
        call["purpose"] = "migration"
        self.assertTrue(reconcile_delivery_assets({}, [], {"database_write_calls": [call], "database_changes": [record]})[1])
        call["purpose"] = "test"
        record["evidence"] = ""
        self.assertTrue(reconcile_delivery_assets({}, [], {"database_write_calls": [call], "database_changes": [record]})[1])

    def test_empty_sql_and_path_escape_cannot_satisfy_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "empty.sql").write_text("-- TODO\n", encoding="utf-8")
            state = {"name": "repo", "worktree_path": directory, "changed_files": ["empty.sql"]}
            result = {"delivery_assets": [{"repository": "repo", "path": "../escape.sql", "kind": "sql"}]}
            assets, blockers = reconcile_delivery_assets({}, [state], result)
            self.assertFalse(assets)
            self.assertEqual(len(blockers), 2)

    def test_existing_executed_migration_can_be_declared_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "existing.sql").write_text("CREATE TABLE T (ID INT);", encoding="utf-8")
            state = {"name": "repo", "worktree_path": directory, "changed_files": []}
            result = {"delivery_assets": [{"repository": "repo", "path": "existing.sql", "kind": "sql"}], "sql_changes": ["existing.sql"]}
            assets, blockers = reconcile_delivery_assets({}, [state], result)
            self.assertFalse(blockers)
            self.assertEqual(assets[0]["path"], "existing.sql")

    def test_json_xml_and_yaml_config_secret_values_are_templates(self):
        cases = ['{"database":{"password":"not-a-real-password","enabled":true}}',
                 '<beans><property name="password" value="not-a-real-password"/></beans>',
                 'database:\n  password: not-a-real-password\n  enabled: true\n']
        for content in cases:
            result = configuration_template(content)
            self.assertNotIn("not-a-real-password", result)
            self.assertIn("SET_ON_SERVER", result)
        self.assertTrue(json.loads(configuration_template(cases[0]))["database"]["enabled"])

    def test_mail_and_tfs_manifest_include_manual_rollback_alongside_other_deliverables(self):
        from app.services.delivery import ArtifactService, Mailer
        artifacts = [{"id": 1, "kind": "sql", "name": "schema.sql"}, {"id": 2, "kind": "config", "name": "application.yml"},
                     {"id": 3, "kind": "release_artifact", "name": "Build #1", "external_url": "https://tfs.test/artifacts"},
                     {"id": 4, "kind": "delivery_manifest", "name": "delivery-validation-manifest.json"},
                     {"id": 5, "kind": "merge_screenshot", "name": "capture.png"},
                     {"id": 6, "kind": "rollback_note", "name": "rollback.txt"}]
        detail = {"id": "task", "work_item_id": 1, "delivery_mode": "sichuan_auto_review", "artifacts": artifacts,
                  "status": "delivered", "delivery_options": [], "joint_children": [{"id": "child", "status": "delivered"}],
                  "created_at": "2026-10-21T01:00:00+00:00"}
        for rendered in [ArtifactService().delivery_manifest_html(detail), Mailer().delivery_html(detail)]:
            self.assertIn("schema.sql", rendered)
            self.assertIn("application.yml", rendered)
            self.assertIn("https://tfs.test/artifacts", rendered)
            self.assertIn("rollback.txt", rendered)
            self.assertIn("旧数据回退说明", rendered)
            self.assertNotIn("delivery-validation-manifest.json", rendered)
            self.assertNotIn("capture.png", rendered)


class ArtifactPreviewTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import main

        self.main = main
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "deliveries"
        self.root.mkdir()
        self.enterContext(patch.object(main, "settings", SimpleNamespace(delivery_dir=self.root, max_artifact_mb=1)))
        self.record = self.enterContext(patch.object(main, "row"))
        self.session = self.enterContext(patch.object(main, "get_session_user", return_value={"id": 2, "role": "pm"}))
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def artifact(self, kind, name, data=None, url=""):
        path = self.root / name
        if data is not None:
            path.write_bytes(data)
        item = {"id": 5, "kind": kind, "name": name, "local_path": str(path) if data is not None else "",
                "external_url": url, "requester_id": 1, "acceptance_owner_id": 2}
        self.record.return_value = item
        return item

    def test_sql_txt_yaml_preview_and_download_in_original_encoding(self):
        for kind, name, text, encoding in [
            ("sql", "upgrade.sql", "-- 数据迁移\nUPDATE T SET ID=1;\n", "utf-8-sig"),
            ("rollback_note", "rollback.txt", "步骤：从备份回退\n", "gb18030"),
            ("config", "settings.yml", "label: <b>中文</b>\n", "utf-16"),
        ]:
            with self.subTest(filename=name):
                data = text.encode(encoding)
                self.assertTrue(self.main.public_artifact(self.artifact(kind, name, data))["previewable"])
                preview = self.client.get("/api/artifacts/5?preview=true")
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.text, text)
                self.assertEqual(preview.headers["content-type"], "text/plain; charset=utf-8")
                self.assertEqual(preview.headers["x-content-type-options"], "nosniff")
                self.assertIn("no-store", preview.headers["cache-control"])
                download = self.client.get("/api/artifacts/5")
                self.assertIn("attachment", download.headers["content-disposition"])
                self.assertEqual(download.content, data)

    def test_preview_rejects_unauthorized_requests_binary_and_unsafe_paths(self):
        self.artifact("config", "test.yml", b"flag: true")
        self.session.return_value = None
        self.assertEqual(self.client.get("/api/artifacts/5?preview=true").status_code, 401)
        self.session.return_value = {"id": 3, "role": "pm"}
        self.assertEqual(self.client.get("/api/artifacts/5?preview=true").status_code, 403)
        self.session.return_value = {"id": 3, "role": "admin"}
        package = self.artifact("package", "server.jar", b"PK")
        self.assertFalse(self.main.public_artifact(package)["previewable"])
        self.assertEqual(self.client.get("/api/artifacts/5?preview=true").status_code, 415)
        self.assertEqual(self.client.get("/api/artifacts/5").content, b"PK")
        artifact = self.artifact("sql", "outside.sql")
        outside = self.root.parent / "outside.sql"
        outside.write_text("SELECT 1", encoding="utf-8")
        artifact["local_path"] = str(outside)
        self.assertEqual(self.client.get("/api/artifacts/5?preview=true").status_code, 404)

    def test_legacy_signed_oss_preview_is_bounded_and_keeps_the_download_url(self):
        url = "https://bucket.oss-cn-chengdu.aliyuncs.com/autodev/request/sql/upgrade.sql?Signature=test"
        self.artifact("sql", "upgrade.sql", url=url)
        with patch("app.services.artifact_content.PREVIEW_MAX_BYTES", 7), \
                patch("app.services.artifact_content.httpx.stream") as fetch:
            stream = fetch.return_value.__enter__.return_value
            stream.status_code = 200
            stream.iter_bytes.return_value = iter(["-- 数字\nSELECT 1;".encode("utf-8")])
            preview = self.client.get("/api/artifacts/5?preview=true")
            self.assertEqual(preview.text, "-- 数")
            self.assertEqual(preview.headers["x-autodev-preview-truncated"], "true")
            self.assertFalse(fetch.call_args.kwargs["follow_redirects"])
            stream.status_code = 403
            self.assertEqual(self.client.get("/api/artifacts/5?preview=true").status_code, 410)
            self.artifact("sql", "upgrade.sql", url="https://example.invalid/upgrade.sql")
            fetch.reset_mock()
            self.assertEqual(self.client.get("/api/artifacts/5?preview=true").status_code, 415)
            fetch.assert_not_called()
        self.artifact("sql", "upgrade.sql", url=url)
        self.assertEqual(self.client.get("/api/artifacts/5", follow_redirects=False).headers["location"], url)

    def test_runner_cannot_upload_procedural_or_disguised_sql(self):
        main = self.main
        def allowed():
            return None
        with patch.dict(main.app.dependency_overrides, {main.runner_auth: allowed}), \
                patch.object(main, "runner_request"), patch.object(main, "add_artifact", return_value=9) as add:
            url = "/api/runner/requests/task/artifacts"
            data = {"kind": "sql", "name": "rollback.sql"}
            files = {"file": ("rollback.sql", b"CALL restore_old();")}
            result = self.client.post(url, data=data, files=files)
            self.assertEqual(result.status_code, 422)
            self.assertIn("存储过程", result.json()["detail"])
            result = self.client.post(url, data={**data, "kind": "config"}, files=files)
            self.assertEqual(result.status_code, 422)
            self.assertFalse(list(self.root.rglob("*.sql")))
            add.assert_not_called()
            files = {"file": ("rollback.sql", b"UPDATE T SET ID=1;")}
            self.assertEqual(self.client.post(url, data=data, files=files).json(), {"artifact_id": 9})
            add.assert_called_once()


if __name__ == "__main__":
    unittest.main()
