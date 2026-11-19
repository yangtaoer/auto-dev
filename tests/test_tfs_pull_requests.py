from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx


class TfsPullRequestTests(unittest.TestCase):
    collection = "/DefaultCollection/DCS/_apis/git/repositories/repo-id/pullRequests"
    pr_path = f"{collection}/123"
    reviewer_path = f"{pr_path}/reviewers/reviewer-id"
    active = {"pullRequestId": 123, "status": "active", "createdBy": {"id": "creator-id"}}
    completed = {"pullRequestId": 123, "status": "completed"}
    not_editable = (400, {"message": "TF401181: PR state does not allow editing."})

    def setUp(self) -> None:
        from app.services import tfs

        self.tfs_module = tfs
        self.tfs = tfs.TfsClient("https://tfs.test/DefaultCollection", pat="author-pat")
        self.repo = tfs.RepositoryInfo("DCS", "repo", "repo-id", "project-id")
        self.enterContext(patch.object(self.tfs, "repository_info", return_value=self.repo))
        self.link = self.enterContext(patch.object(self.tfs, "_ensure_work_item_link", return_value=True))
        self.client = self.enterContext(patch.object(self.tfs, "_client"))
        self.enterContext(patch(
            "app.services.tfs.settings",
            SimpleNamespace(tfs_reviewer_id="reviewer-id", tfs_reviewer_pat="reviewer-pat"),
        ))

    def respond(self, *responses) -> None:
        self.requests = []
        replies = iter(responses)

        def send(request):
            self.requests.append(request)
            status, payload = next(replies)
            return httpx.Response(status, json=payload)

        self.client.side_effect = lambda _pat: httpx.Client(transport=httpx.MockTransport(send))

    def assert_requests(self, *expected) -> None:
        self.assertEqual([(r.method, r.url.path) for r in self.requests], list(expected))

    def test_auto_review_links_and_votes_before_auto_complete(self) -> None:
        self.respond((200, {"value": []}), (201, self.active), (200, self.active), (200, {"vote": 10}),
                     (200, self.completed))
        pr = self.tfs.create_pull_request("repo", "feature/123", "dev", "Title", 123, "Description",
                                          auto_complete=False)
        self.assertFalse(pr["AutoCompleteEnabled"])
        self.link.assert_called_once_with(self.repo, 123, 123)
        self.assertTrue(self.tfs.approve_pull_request("repo", 123))
        self.assert_requests(
            ("GET", self.collection), ("POST", self.collection), ("GET", self.pr_path),
            ("PUT", self.reviewer_path), ("PATCH", self.pr_path),
        )
        self.assertEqual(json.loads(self.requests[3].content), {"vote": 10})
        self.assertEqual(self.client.call_args_list[3].args, ("reviewer-pat",))
        self.assertEqual(json.loads(self.requests[4].content), {
            "autoCompleteSetBy": {"id": "creator-id"},
            "completionOptions": {"deleteSourceBranch": True},
        })

    def test_default_creation_still_enables_auto_complete(self) -> None:
        enabled = {
            **self.active, "autoCompleteSetBy": {"id": "creator-id"},
            "completionOptions": {"deleteSourceBranch": True},
        }
        self.respond((200, {"value": []}), (201, self.active), (200, enabled))
        pr = self.tfs.create_pull_request("repo", "feature/123", "dev", "Title", 123, "Description")
        self.assertTrue(pr["AutoCompleteEnabled"])
        self.assertTrue(pr["DeleteSourceBranch"])
        self.assert_requests(("GET", self.collection), ("POST", self.collection), ("PATCH", self.pr_path))

    def test_already_merged_pr_needs_no_vote(self) -> None:
        self.respond((200, self.completed))
        self.assertFalse(self.tfs.approve_pull_request("repo", 123))
        self.assert_requests(("GET", self.pr_path))

    def test_existing_vote_restores_auto_complete_without_recasting(self) -> None:
        source = "a" * 40
        approved = {**self.active, "reviewers": [{"id": "reviewer-id", "vote": 10}],
                    "lastMergeSourceCommit": {"commitId": source}}
        enabled = {**approved, "autoCompleteSetBy": {"id": "creator-id"}}
        self.respond((200, approved), (200, enabled))
        self.assertTrue(self.tfs.approve_pull_request("repo", 123, expected_source=source))
        self.assert_requests(("GET", self.pr_path), ("PATCH", self.pr_path))

    def test_approval_stops_if_the_reviewed_source_has_changed(self) -> None:
        self.respond((200, {**self.active, "lastMergeSourceCommit": {"commitId": "b" * 40}}))
        with self.assertRaisesRegex(self.tfs_module.TfsError, "源提交已变化"):
            self.tfs.approve_pull_request("repo", 123, expected_source="a" * 40)
        self.assert_requests(("GET", self.pr_path))

    def test_validation_diagnostics_require_the_exact_pr_merge_preview(self) -> None:
        build = {"status": "completed", "result": "failed", "sourceBranch": "refs/pull/123/merge",
                 "sourceVersion": "b" * 40, "definition": {"id": 17}}
        pr = {"project": "DCS", "id": 123, "merge_commit": "a" * 40}
        self.respond((200, build))
        self.assertIsNone(self.tfs.failed_pull_request_build_log(pr, 21, 17))
        self.assertEqual(len(self.requests), 1)

        build["sourceVersion"] = pr["merge_commit"]
        timeline = {"records": [{"type": "Job", "result": "failed", "log": {"id": 8}},
                                {"type": "Task", "result": "failed", "name": "Maven", "log": {"id": 9}}]}
        self.respond((200, build), (200, timeline), (200, {"value": ["[ERROR] COMPILATION ERROR"]}))
        self.assertEqual(self.tfs.failed_pull_request_build_log(pr, 21, 17),
                         ["构建步骤 Maven", "[ERROR] COMPILATION ERROR"])
        self.assertTrue(self.requests[2].url.path.endswith("/builds/21/logs/9"))

    def test_vote_conflict_only_recovers_a_confirmed_merge(self) -> None:
        for status in ("completed", "abandoned", "active"):
            with self.subTest(status=status):
                self.respond((200, self.active), self.not_editable, (200, {**self.active, "status": status}))
                if status == "completed":
                    self.assertFalse(self.tfs.approve_pull_request("repo", 123))
                else:
                    with self.assertRaisesRegex(self.tfs_module.TfsPullRequestNotEditableError, "TF401181"):
                        self.tfs.approve_pull_request("repo", 123)
                self.assert_requests(("GET", self.pr_path), ("PUT", self.reviewer_path), ("GET", self.pr_path))

    def test_merge_after_vote_preserves_successful_vote(self) -> None:
        self.respond((200, self.active), (200, {"vote": 10}), self.not_editable, (200, self.completed))
        self.assertTrue(self.tfs.approve_pull_request("repo", 123))
        self.assert_requests(
            ("GET", self.pr_path), ("PUT", self.reviewer_path), ("PATCH", self.pr_path), ("GET", self.pr_path),
        )

    def test_unconfirmed_vote_or_permission_failure_never_enables_auto_complete(self) -> None:
        for response, error in (((200, {"vote": 0}), "未确认"), ((403, {"message": "Forbidden"}), "403")):
            with self.subTest(response=response):
                self.respond((200, self.active), response)
                with self.assertRaisesRegex(self.tfs_module.TfsError, error):
                    self.tfs.approve_pull_request("repo", 123)
                self.assert_requests(("GET", self.pr_path), ("PUT", self.reviewer_path))
