from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class JavaCodeChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import java_code_checks

        self.checks = java_code_checks
        self.profile = {"repository": "api", "target_branch": "dev_sd", "mode": "observe", "required_merge_builds": [42]}
        self.project = {"quality_profile": {"java_code_checks": [self.profile]}}
        self.state = {"name": "api", "base_branch": "dev_sd"}

    def test_configuration_matches_only_the_registered_repository_and_branch(self) -> None:
        self.assertEqual(self.checks.profile_for(self.project, self.state), self.profile)
        self.assertEqual(self.checks.required_merge_builds(self.project, self.state), ())
        self.assertIsNone(self.checks.profile_for(self.project, {**self.state, "base_branch": "dev"}))
        self.assertIsNone(self.checks.profile_for(self.project, {**self.state, "name": "other-api"}))
        self.profile["mode"] = "enforce"
        self.assertEqual(self.checks.required_merge_builds(self.project, self.state), (42,))

    def test_baseline_issues_follow_unchanged_lines_and_changes_are_reported(self) -> None:
        file = "Job.java"
        existing = {"tool": "Checkstyle", "rule": "Rule", "path": file, "line": 2, "message": "existing"}
        moved = {**existing, "line": 3}
        new = {**moved, "line": 4, "message": "new"}
        before = {file: b"begin\nexisting();\nend\n"}
        after = {file: b"begin\n// note\nexisting();\nnew();\nend\n"}
        self.assertEqual(self.checks._introduced([moved, new], [existing], before, after), [new])
        self.assertEqual(self.checks._suppression_changes(file, b"// NOSONAR\n", b"\n// NOSONAR\n"), [])
        self.assertEqual(len(self.checks._suppression_changes(file, b"", b"// NOSONAR\n")), 1)

    def test_unavailable_tools_only_block_an_enforced_repository(self) -> None:
        from app.services.quality_gates import evaluate_development_quality

        for mode, expected in (("observe", "warning"), ("enforce", "blocked")):
            with self.subTest(mode=mode):
                self.profile["mode"] = mode
                with patch.object(self.checks, "_sources", return_value=({"Job.java": b"class Job {}"}, {}, [])), \
                     patch.object(self.checks, "_tools", side_effect=RuntimeError("JDK missing")):
                    report = self.checks.scan(self.project, self.state)
                gate = evaluate_development_quality(self.project, [], {"java_code_checks": [report]})
                self.assertEqual(report["status"], "unavailable")
                self.assertEqual(gate["status"], expected)
                java_gate = next(check for check in gate["checks"] if check["id"] == "java-code-api")
                self.assertIn("JDK missing", java_gate["detail"])

    def test_staged_check_rejects_material_added_after_the_scanner_ran(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(root), *args], check=True, capture_output=True, encoding="utf-8",
            ).stdout.strip()

        git("init", "-q")
        baseline = root / "Removed.java"
        baseline.write_bytes(b"class Removed {}\n")
        git("add", "--", baseline.name)
        git("-c", "user.name=Java Check Test", "-c", "user.email=test@example.test", "commit", "-qm", "Fixture")
        base = git("rev-parse", "HEAD")
        baseline.unlink()
        candidate = root / "新增文件.java"
        scanned = b"class Added {}\n"
        candidate.write_bytes(scanned)
        names = [candidate.name, baseline.name]
        state = {**self.state, "base_commit": base, "worktree_path": str(root), "changed_files": names}
        report = {"mode": "enforce", "status": "passed", "base_commit": base,
                  "files": self.checks._fingerprint({candidate.name: scanned})}
        git("add", "--all")
        self.checks.verify_staged(report, state)
        candidate.write_bytes(b"class Added { int modified; }\n")
        git("add", "--all")
        candidate.unlink()  # The index retains the unscanned content.
        with self.assertRaisesRegex(RuntimeError, "扫描快照不一致"):
            self.checks.verify_staged(report, state)

        self.profile["mode"] = "enforce"
        skipped = self.checks.scan(self.project, {**state, "changed_files": []})
        self.assertEqual(skipped["status"], "skipped")
        with self.assertRaisesRegex(RuntimeError, "扫描快照不一致"):
            self.checks.verify_staged(skipped, state)

    def test_maven_requires_a_fresh_passing_surefire_report(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (root / "pom.xml").write_text("<project/>", encoding="utf-8")
        report = root / "target" / "surefire-reports" / "TEST-JobTest.xml"
        report.parent.mkdir(parents=True)
        success = '<testsuite tests="2" failures="0" errors="0" skipped="1"/>'
        report.write_text(success, encoding="utf-8")
        state = {**self.state, "worktree_path": str(root)}
        config = {"classes": ["JobTest"]}
        with patch.object(self.checks.shutil, "which", return_value="maven"), \
             patch.object(self.checks, "_run") as maven:
            stale = self.checks._unit_tests(state, config)
            self.assertEqual(stale["status"], "failed")
            self.assertIn("未生成实际执行用例", stale["message"])
            maven.side_effect = lambda *_args, **_kwargs: report.write_text(
                '<testsuite tests="2" failures="1" errors="0" skipped="0"><testcase/></testsuite>', encoding="utf-8",
            )
            failed = self.checks._unit_tests(state, config)
            self.assertEqual(failed["status"], "failed")
            self.assertIn("包含测试失败", failed["message"])
            maven.side_effect = lambda *_args, **_kwargs: report.write_text(success, encoding="utf-8")
            passed = self.checks._unit_tests(state, config)
            self.assertIn("1 个", passed["message"])
            self.assertEqual(passed["status"], "passed")
            self.assertIn("-Dtest=JobTest", maven.call_args.args[0])
