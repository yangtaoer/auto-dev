from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


class PullRequestValidationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import pr_validation_recovery

        self.recovery = pr_validation_recovery
        self.original, self.preview, self.fixed = "a" * 40, "b" * 40, "c" * 40
        self.branch = "feature/5-review"
        self.path = "src/Feature.java"
        self.state = {
            "name": "repo", "repository_path": "main-repo", "worktree_path": "worktree",
            "pr_id": 12, "commit_hash": self.original, "branch": self.branch, "base_branch": "dev",
            "changed_files": [self.path],
        }
        self.detail = {
            "id": "job", "work_item_id": 5, "commit_hash": self.original,
            "policy_snapshot": {"verification_command": "verify"},
            "recovery_context": {"verified_commits": {"repo": self.original}},
            "repository_states": [self.state],
        }
        self.pr = {
            "id": 12, "status": "active", "project": "DCS", "project_id": "project-id", "repository": "repo",
            "source_commit": self.original, "merge_commit": self.preview,
            "source_branch": self.branch, "target_branch": "dev",
            "reviewer_approved": True, "auto_complete_enabled": True,
        }
        self.policy = {
            "status": "rejected", "evaluationId": "evaluation-id",
            "configuration": {"isEnabled": True, "isBlocking": True, "settings": {"buildDefinitionId": 17}},
            "context": {"buildId": 21, "lastMergeSourceCommitId": self.original, "lastMergeCommitId": self.preview},
        }
        self.worker = Mock()
        self.worker.stop_event.is_set.return_value = False
        self.worker.store.get_status.return_value = "waiting_merge"
        self.tfs = Mock()
        self.tfs.pull_request_policies.return_value = [self.policy]
        self.tfs.get_pull_request.return_value = self.pr
        self.tfs.branch_head.return_value = self.original

    def maintain(self) -> str:
        return self.recovery.maintain(self.worker, self.detail, [self.state], self.state, self.pr, self.tfs)

    def test_only_current_required_build_failure_is_eligible(self) -> None:
        matching = self.policy
        optional = {**matching, "configuration": {**matching["configuration"], "isBlocking": False}}
        stale = {**matching, "context": {**matching["context"], "lastMergeCommitId": "f" * 40}}
        running = {**matching, "status": "running"}
        self.tfs.pull_request_policies.return_value = [optional, stale, running, matching]
        self.assertEqual([check.build_id for check in self.recovery.failed_checks(self.tfs, self.pr)], [21])

    def test_source_failure_is_repaired_verified_and_pushed_to_the_same_branch(self) -> None:
        self.tfs.failed_pull_request_build_log.return_value = [
            "[ERROR] COMPILATION ERROR", "[ERROR] src/Feature.java:[12] cannot find symbol",
        ]
        worktree = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        source = worktree / self.path
        source.parent.mkdir()
        source.write_text("return 7\n", encoding="utf-8")
        actions = []
        state = {"head": self.original}

        def git(_root, *args, **kwargs):
            if args == ("rev-parse", "HEAD"):
                return state["head"]
            if args == ("rev-parse", "HEAD^"):
                return self.original
            if args == ("branch", "--show-current"):
                return self.branch
            if args[-2:] == ("--cached", "--name-only"):
                return self.path
            if args[0] == "commit":
                state["head"] = self.fixed
                actions.append("commit")
            if args[0] == "push":
                self.assertIn(f"{self.fixed}:refs/heads/{self.branch}", args)
                actions.append("push")
            return ""

        def repair(**kwargs):
            self.assertIn("cannot find symbol", kwargs["diagnostics"])
            self.assertEqual(kwargs["sources"], {self.path: "return 7\n"})
            actions.append("repair")
            return {"fixed": True, "summary": "修复编译错误",
                    "replacements": [{"path": self.path, "old": "return 7", "new": "return 7;"}]}

        with patch.object(self.recovery, "_checked_worktree", return_value=worktree), patch.object(
            self.recovery, "git", side_effect=git
        ), patch.object(self.recovery, "committable_changes", return_value=[self.path]), patch.object(
            self.recovery.CodexRunner, "repair_pr_validation", side_effect=repair
        ), patch.object(self.recovery, "run_command", side_effect=lambda *_a, **_k: actions.append("verify")), patch.object(
            self.recovery, "git_authenticated_env", return_value={}
        ):
            note = self.maintain()
        self.assertEqual(actions, ["repair", "commit", "verify", "push"])
        self.assertEqual(source.read_text(encoding="utf-8"), "return 7;\n")
        self.assertIn("等待 TFS 重新验证", note)
        self.assertEqual(self.state["commit_hash"], self.fixed)
        self.assertEqual(self.detail["recovery_context"]["verified_commits"]["repo"], self.fixed)
        event_types = [call.args[1] for call in self.worker.store.add_event.call_args_list]
        self.assertIn("pr.validation_repair_pushed", event_types)
        self.assertNotIn("pr.merged", event_types)

    def test_ambiguous_or_unapproved_text_replacements_are_never_written(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        source = root / "Feature.java"
        original = b"before\r\nrepeat\r\nrepeat\r\n"
        source.write_bytes(original)
        files = self.recovery._read_repair_sources(root, {source.name, "../outside.java"})
        self.assertEqual(list(files), [source.name])
        for path, old in ((source.name, "repeat"), ("outside.java", "before")):
            with self.subTest(path=path):
                edits = [{"path": source.name, "old": "before", "new": "first"},
                         {"path": path, "old": old, "new": "second"}]
                with self.assertRaises(RuntimeError):
                    self.recovery._apply_replacements(files, edits)
                self.assertEqual(source.read_bytes(), original)
        self.recovery._apply_replacements(files, [{"path": source.name, "old": "before", "new": "a\nb"}])
        self.assertEqual(source.read_bytes(), b"a\r\nb\r\nrepeat\r\nrepeat\r\n")

    def test_transient_failure_is_requeued_once_for_this_source(self) -> None:
        self.tfs.failed_pull_request_build_log.return_value = ["[ERROR] Read timed out"]
        self.assertIn("已重新排队", self.maintain())
        self.assertIn("已重新排队", self.maintain())
        self.policy["context"]["buildId"] += 1
        self.assertIn("继续监控", self.maintain())
        self.tfs.requeue_pull_request_policy.assert_called_once_with(self.pr, "evaluation-id")

    def test_reset_vote_is_reapplied_on_the_verified_source(self) -> None:
        self.pr["reviewer_approved"] = False
        self.maintain()
        self.maintain()
        self.tfs.approve_pull_request.assert_called_once_with("main-repo", 12, expected_source=self.original)
        self.tfs.pull_request_policies.assert_not_called()

    def test_changed_remote_source_stays_read_only(self) -> None:
        self.pr["source_commit"] = "d" * 40
        self.assertIn("核对", self.maintain())
        self.tfs.pull_request_policies.assert_not_called()
        self.tfs.approve_pull_request.assert_not_called()

    def test_interrupted_push_is_reconciled_only_when_tfs_confirms_the_commit(self) -> None:
        self.state["pr_validation"] = {"pending_commit": self.fixed, "build_id": 21}
        self.assertFalse(self.recovery.reconcile_pushed_repair(
            self.worker, self.detail, [self.state], self.state, self.original,
        ))
        self.assertTrue(self.recovery.reconcile_pushed_repair(
            self.worker, self.detail, [self.state], self.state, self.fixed,
        ))
        self.assertEqual(self.state["commit_hash"], self.fixed)
        self.assertEqual(self.state["pr_validation"]["phase"], "pushed")

    def test_sonar_failure_uses_findings_for_this_pr(self) -> None:
        with patch.object(self.recovery, "_sonar_details", return_value="src/Feature.java:12 java:S1192") as details:
            kind, evidence = self.recovery._diagnose(self.pr, ["[ERROR] QUALITY GATE STATUS: FAILED"])
        self.assertEqual(kind, "source")
        self.assertIn("java:S1192", evidence)
        self.assertEqual(details.call_args.args[0], 12)

    def test_sonar_log_cannot_direct_requests_to_another_host(self) -> None:
        with patch.object(self.recovery.httpx, "Client") as http:
            result = self.recovery._sonar_details(12, "http://example.test/dashboard?id=repo&pullRequest=12")
        self.assertEqual(result, "")
        http.assert_not_called()
