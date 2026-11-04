from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

class RepositoryCompositionTests(unittest.TestCase):
    def setUp(self):
        from app.services.repository_composition import validate_starter_composition, unregistered_workspaces
        from app.services.quality_gates import evaluate_development_quality
        self.validate = validate_starter_composition
        self.unregistered = unregistered_workspaces
        self.evaluate = evaluate_development_quality
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.starter = self.root / "dcsd-springboot-starter"
        self.starter.mkdir()
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.name", "AutoDev test")
        self.git("config", "user.email", "test@example.invalid")
        self.source = """<project xmlns="http://maven.apache.org/POM/4.0.0"><profiles>
          <profile><id>sichuanga</id><dependencies>
            <dependency><groupId>com.tellhow.dcsd</groupId><artifactId>dcsd-certify-ui-sichuanga</artifactId></dependency>
            <dependency><groupId>third.party</groupId><artifactId>external</artifactId></dependency>
            <dependency><groupId>com.tellhow.dcsd</groupId><artifactId>test-helper</artifactId><scope>test</scope></dependency>
          </dependencies></profile></profiles></project>"""
        (self.starter / "pom.xml").write_text(self.source, encoding="utf-8")
        self.git("add", "--", "pom.xml")
        self.git("commit", "-q", "-m", "delivery composition")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.project = {"base_branch": "dev", "quality_profile": {"repository_composition": {"maven_profile": "sichuanga"}}}
        self.correct = self.root / "dcsd-certify-ui-sichuanga"
        self.correct.mkdir()
        self.repositories = [self.starter, self.correct]

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.starter), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def test_guangan_old_shared_repository_is_rejected_before_development(self):
        old = self.root / "dcsd-certify-ui-sichuandd"
        old.mkdir()
        with self.assertRaisesRegex(RuntimeError, "缺少仓库：dcsd-certify-ui-sichuanga.*未被该 profile 引用的仓库：dcsd-certify-ui-sichuandd"):
            self.validate(self.project, [self.starter, old])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_uses_fetched_target_pom_instead_of_dirty_ide_pom(self):
        (self.starter / "pom.xml").write_text("invalid local edit", encoding="utf-8")
        self.validate(self.project, self.repositories)
        self.assertEqual((self.starter / "pom.xml").read_text(), "invalid local edit")

    def test_exact_profile_does_not_include_other_regions(self):
        other = "<profile><id>sichuancd</id><dependencies><dependency><groupId>com.tellhow</groupId><artifactId>chengdu-only</artifactId></dependency></dependencies></profile>"
        source = self.source.replace("</profiles>", other + "</profiles>")
        with patch("app.services.repository_composition.git", return_value=source):
            self.validate(self.project, self.repositories)

    def test_missing_or_duplicate_profile_is_not_silently_skipped(self):
        for source in (self.source.replace("sichuanga", "other"),
                       self.source.replace("</profiles>", "<profile><id>sichuanga</id></profile></profiles>")):
            with self.subTest(source=source), patch("app.services.repository_composition.git", return_value=source):
                with self.assertRaisesRegex(RuntimeError, "唯一 Maven profile"):
                    self.validate(self.project, self.repositories)

    def test_explicit_binary_dependency_does_not_require_source_checkout(self):
        source = self.source.replace("</dependencies>", "<dependency><groupId>com.tellhow</groupId><artifactId>binary-agent</artifactId></dependency></dependencies>")
        self.project["quality_profile"]["repository_composition"]["binary_only_dependencies"] = ["binary-agent"]
        with patch("app.services.repository_composition.git", return_value=source):
            self.validate(self.project, self.repositories)

    def test_unrelated_projects_keep_existing_workspace_behavior(self):
        with patch("app.services.repository_composition.git") as read:
            self.validate({}, self.repositories)
        read.assert_not_called()

    def test_guangan_preset_matches_real_regional_dependency(self):
        path = Path(__file__).resolve().parents[1] / "local-runner/project-presets/guangan-network-command.json"
        preset = json.loads(path.read_text("utf-8"))
        names = {Path(p).name for p in preset["repository_paths"]}
        self.assertIn("dcsd-certify-ui-sichuanga", names)
        self.assertNotIn("dcsd-certify-ui-sichuandd", names)
        self.assertEqual(Path(preset["repository_path"]).name, "dcsd-certify-ui-sichuanga")
        self.assertEqual(preset["quality_profile"]["repository_composition"]["maven_profile"], "sichuanga")

    def test_extra_repository_is_detected_without_deleting_its_changes(self):
        state = {"name": self.starter.name, "worktree_path": str(self.starter)}
        (self.correct / ".git").write_text("gitdir: original-checkout")
        change = self.correct / "production.js"
        change.write_text("retained repair", encoding="utf-8")
        errors = self.unregistered([state])
        self.assertTrue(any(self.correct.name in error for error in errors))
        self.assertEqual(change.read_text(), "retained repair")
        self.assertEqual(self.unregistered([state, {"name": self.correct.name, "worktree_path": str(self.correct)}]), [])

    def test_completed_development_cannot_pass_with_zero_observed_changes(self):
        gate = self.evaluate({}, [], {"decision": "completed", "changed_files": ["missing/repair.js"]})
        self.assertEqual(gate["status"], "blocked")
        self.assertTrue(any("没有可提交变更" in error for error in gate["blockers"]))

    def test_unknown_acceptance_repository_is_not_a_successful_mapping(self):
        result = {"decision": "completed", "acceptance_ledger": [{"id": "AC-01", "status": "completed",
                  "repositories": [self.correct.name], "files": ["repair.js"], "evidence": ["tested"]}]}
        state = {"name": self.starter.name, "worktree_path": str(self.starter), "changed_files": ["pom.xml"]}
        gate = self.evaluate({}, [state], result)
        self.assertTrue(any("未登记仓库" in error and self.correct.name in error for error in gate["blockers"]))

    def test_already_satisfied_and_simulation_may_have_no_changes(self):
        result = {"decision": "already_satisfied", "existing_implementation": {"verified": True, "evidence": ["target commit"]}}
        self.assertEqual(self.evaluate({}, [], result)["status"], "passed")
        self.assertEqual(self.evaluate({"simulation_mode": True}, [], {"decision": "completed"})["status"], "passed")


if __name__ == "__main__":
    unittest.main()
