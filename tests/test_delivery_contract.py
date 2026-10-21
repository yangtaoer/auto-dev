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


class DeliveryContractTests(unittest.TestCase):
    def test_all_twelve_presets_include_root_sql_config_and_global_policy(self):
        from app.project_experience import apply_project_experience
        paths = list((Path(__file__).resolve().parents[1] / "local-runner/project-presets").glob("*.json"))
        self.assertEqual(len(paths), 12)
        for path in paths:
            with self.subTest(project=path.stem):
                project = apply_project_experience(json.loads(path.read_text(encoding="utf-8")))
                self.assertIn("*.sql", project["sql_patterns"])
                self.assertIn("**/*.properties", project["config_patterns"])
                self.assertNotIn("**/*.xml", project["config_patterns"])
                self.assertEqual(set(project["artifact_policy"]["allowed_user_facing_kinds"]), BUSINESS_ARTIFACT_KINDS)
                self.assertTrue(project["artifact_policy"]["require_packages"])
                self.assertEqual(set(project["artifact_policy"]["allowed_changed_asset_kinds"]), {"sql", "config"})
                self.assertIn("必须先保存", project["development_instructions"])

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
        kinds = ["package", "sql", "config", "release_artifact", "delivery_manifest", "verification_report",
                 "package_note", "license_request", "merge_screenshot", "menu_link", "report", "email_preview"]
        artifacts = [{"kind": kind, "name": {"sql": "upgrade.sql", "config": "runtime.properties"}.get(kind, kind)} for kind in kinds]
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
                     "runtime-settings.xml": "<config><feature enabled='true'/></config>"}
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            artifacts = []
            def record(request_id, kind, name, local_path, external_url):
                artifacts.append(dict(id=len(artifacts)+1, kind=kind, name=name, local_path=local_path))
                return len(artifacts)
            service = ArtifactService(recorder=record, detail_loader=lambda _id: {"artifacts": artifacts})
            declared = [{"repository": "repo", "path": "runtime-settings.xml", "kind": "config"}]
            with patch("app.services.delivery.settings", SimpleNamespace(delivery_dir=Path(directory) / "delivery")), patch(
                "app.services.delivery.changed_files", return_value=list(files)[:-1]
            ):
                first = service.collect_changed_assets("task", root, "base", {}, repository_name="repo", declared_assets=declared)
                second = service.collect_changed_assets("task", root, "base", {}, repository_name="repo", declared_assets=declared)
            self.assertEqual(first, second)
            self.assertEqual(len(artifacts), 4)
            self.assertEqual({item["kind"] for item in artifacts}, {"sql", "config"})
            self.assertTrue(all(item["name"].startswith("repo/") for item in artifacts))
            self.assertNotIn("secret-for-test", Path(artifacts[2]["local_path"]).read_text(encoding="utf-8"))
            self.assertIn("secret-for-test", (root / "application.properties").read_text(encoding="utf-8"))

    def test_missing_declared_script_and_configuration_are_ordinary_repair_gaps(self):
        assets, blockers = reconcile_delivery_assets({}, [], {"sql_changes": ["建表"], "config_changes": ["新增开关"]})
        self.assertEqual(assets, [])
        self.assertTrue(any(".sql" in value for value in blockers))
        self.assertTrue(any("配置模板" in value for value in blockers))

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

    def test_mail_and_tfs_manifest_include_sql_config_and_package_only(self):
        from app.services.delivery import ArtifactService, Mailer
        artifacts = [{"id": 1, "kind": "sql", "name": "schema.sql"}, {"id": 2, "kind": "config", "name": "application.yml"},
                     {"id": 3, "kind": "release_artifact", "name": "Build #1", "external_url": "https://tfs.test/artifacts"},
                     {"id": 4, "kind": "delivery_manifest", "name": "delivery-validation-manifest.json"},
                     {"id": 5, "kind": "merge_screenshot", "name": "capture.png"}]
        detail = {"id": "task", "work_item_id": 1, "delivery_mode": "sichuan_auto_review", "artifacts": artifacts,
                  "status": "delivered", "delivery_options": [], "joint_children": [{"id": "child", "status": "delivered"}],
                  "created_at": "2026-10-21T01:00:00+00:00"}
        for rendered in [ArtifactService().delivery_manifest_html(detail), Mailer().delivery_html(detail)]:
            self.assertIn("schema.sql", rendered)
            self.assertIn("application.yml", rendered)
            self.assertIn("https://tfs.test/artifacts", rendered)
            self.assertNotIn("delivery-validation-manifest.json", rendered)
            self.assertNotIn("capture.png", rendered)


if __name__ == "__main__":
    unittest.main()
