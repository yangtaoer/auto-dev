from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.commit_policy import (
    assert_delivery_history, committable_changes, is_local_validation_file,
    preserve_validation_before_sync, stage_delivery_changes,
)


class CommitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="autodev-commit-policy-")
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Commit policy test")
        self.git("config", "user.email", "fixture@example.invalid")
        self.put("src/business.py", "original business\n")
        self.put("tests/test_existing.py", "original test\n")
        self.put("docs/existing.json", '{"original":true}\n')
        self.git("add", "--all")
        self.git("commit", "-m", "fixture baseline")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        from app.services.process_env import sanitized_process_env
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True,
                              encoding="utf-8", env=sanitized_process_env(), check=True).stdout

    def put(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_global_path_matching_does_not_exclude_business_code_or_sql(self):
        for path in ["tests/check.py", "test/test_1678793.py", "module/tests/helper.py", "test_menu.py",
                     "scripts/test_wtcz.py", "a/feature_test.py", "conftest.py", "Docs/验证 [1].JSON", "module/docs/sub/menu.json"]:
            self.assertTrue(is_local_validation_file(path), path)
        for path in ["src/business.py", "src/contest.py", "docs/sql-changelog.md", "sql/upgrade.sql", "config/menu.json",
                     "src/main/view.xml", "src/test/java/BusinessTest.java", "tests/ui.test.js", "docs.json"]:
            self.assertFalse(is_local_validation_file(path), path)

    def test_staged_added_modified_deleted_validation_files_are_not_committed(self):
        self.put("src/business.py", "updated business\n")
        self.put("tests/test_existing.py", "local test changes\n")
        self.put("docs/验证 [1].json", '{"local":true}\n')
        self.put("tests/local helper.py", "local helper\n")
        (self.repo / "docs/existing.json").unlink()
        self.put("sql/upgrade.sql", "select 1;\n")
        self.git("add", "--all")  # Also covers files an agent has already staged.
        included, excluded = stage_delivery_changes(self.repo)
        self.assertEqual(set(included), {"src/business.py", "sql/upgrade.sql"})
        self.assertEqual(len(excluded), 4)
        self.assertEqual((self.repo / "tests/test_existing.py").read_text(), "local test changes\n")
        self.assertFalse((self.repo / "docs/existing.json").exists())
        self.git("commit", "-m", "business only")
        self.assertEqual(set(self.git("diff", "--name-only", self.base, "HEAD").splitlines()), set(included))
        self.assertEqual(self.git("show", "HEAD:tests/test_existing.py"), "original test\n")
        self.assertEqual(self.git("show", "HEAD:docs/existing.json"), '{"original":true}\n')
        self.assertEqual(committable_changes(self.repo, self.base), sorted(included))
        assert_delivery_history(self.repo, self.base)

    def test_only_validation_changes_create_no_staged_business_change(self):
        self.put("test_temp.py", "assert True\n")
        self.put("docs/manifest.json", "{}\n")
        included, excluded = stage_delivery_changes(self.repo)
        self.assertEqual(included, [])
        self.assertEqual(len(excluded), 2)
        self.assertEqual(committable_changes(self.repo, self.base), [])
        self.assertTrue((self.repo / "test_temp.py").exists())

    def test_rename_pair_is_excluded_atomically(self):
        self.git("mv", "src/business.py", "docs/business.json")
        included, excluded = stage_delivery_changes(self.repo)
        self.assertEqual(included, [])
        self.assertEqual(set(excluded), {"src/business.py", "docs/business.json"})
        self.assertTrue((self.repo / "docs/business.json").exists())
        self.assertEqual(self.git("show", ":src/business.py"), "original business\n")

    def test_local_stash_preserves_validation_before_target_sync(self):
        self.put("tests/test_existing.py", "local test changes\n")
        self.put("docs/new.json", "{}\n")
        self.put("src/business.py", "business still local\n")
        stash = preserve_validation_before_sync(self.repo)
        self.assertEqual(len(stash), 40)
        self.assertEqual((self.repo / "tests/test_existing.py").read_text(), "original test\n")
        self.assertFalse((self.repo / "docs/new.json").exists())
        self.assertEqual((self.repo / "src/business.py").read_text(), "business still local\n")
        self.assertEqual(self.git("show", f"{stash}:tests/test_existing.py"), "local test changes\n")
        self.assertEqual(self.git("show", f"{stash}^3:docs/new.json"), "{}\n")
        self.assertEqual(preserve_validation_before_sync(self.repo), "")

    def test_intermediate_commits_cannot_smuggle_excluded_files(self):
        self.put("docs/manifest.json", "{}\n")
        self.git("add", "--all")
        self.git("commit", "-m", "agent wrong commit")
        self.git("rm", "docs/manifest.json")
        self.git("commit", "-m", "agent removes wrong file")
        with self.assertRaisesRegex(RuntimeError, "未推送"):
            assert_delivery_history(self.repo, self.base)

    def test_policy_is_injected_for_all_project_types(self):
        from app.project_experience import apply_project_experience
        from app.services.commit_policy import COMMIT_POLICY_INSTRUCTIONS
        for key in ["network-command-app", "bazhong-self-developed", "luzhou-network-command", "brand-new-project"]:
            result = apply_project_experience({"project_key": key, "development_instructions": "项目说明"})
            self.assertIn("docs/", result["development_instructions"])
            self.assertIn("不得提交", result["development_instructions"])
            self.assertIn("项目说明", result["development_instructions"])
            self.assertEqual(apply_project_experience(result)["development_instructions"].count(COMMIT_POLICY_INSTRUCTIONS), 1)

    def test_real_push_contains_business_code_but_no_new_validation_files(self):
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        from app.orchestrator import Worker
        remote = Path(self.directory.name) / "remote.git"
        self.git("init", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.put("src/business.py", "updated business\n")
        self.put("tests/test_new.py", "local assertion\n")
        self.put("docs/report.json", "{}\n")
        store = Mock()
        worker = Worker(store)
        with patch("app.orchestrator.settings", SimpleNamespace(tfs_pat="")):
            commit = worker._commit_and_push(self.repo, {"id": "fixture"}, {"id": 1, "title": "business change"},
                                                   {"name": "fixture"}, "main", base_commit=self.base)
        self.assertEqual(self.git("rev-parse", "origin/main").strip(), commit)
        self.assertEqual(self.git("diff", "--name-only", self.base, "origin/main").splitlines(), ["src/business.py"])
        self.assertTrue((self.repo / "tests/test_new.py").exists())
        self.assertTrue((self.repo / "docs/report.json").exists())
        self.assertTrue(any(call.args[1] == "git.validation_excluded" for call in store.add_event.call_args_list))


if __name__ == "__main__":
    unittest.main()
