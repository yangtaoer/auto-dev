import json
import unittest
from datetime import datetime
from unittest.mock import patch

from app.services.task_timings import intake_timing, stage_timings


def stamp(time):
    return f"2026-11-19T{time}+00:00"


def step(code, start, end=None, status="completed"):
    return {"step_code": code, "status": status, "started_at": stamp(start), "finished_at": stamp(end) if end else None}


def milestones(**entries):
    return {kind.replace("_", ".", 1): {"at": stamp(value)} for kind, value in entries.items()}


class TaskTimingTests(unittest.TestCase):
    def test_historical_delivery_excludes_waiting_for_pr_and_deployment(self):
        record = {
            "status": "delivered",
            "delivery_mode": "sichuan_auto_review",
            "steps": [step("validate", "07:48:13.570", "07:48:14.230"),
                      step("prepare", "07:48:14", "07:48:44"),
                      step("submit", "08:02:56", "08:03:39"),
                      step("release", "08:56:49", "09:05:05"),
                      step("deliver", "08:03:40", "09:05:11")],
            "repository_states": [{"pr_id": 1, "merge_commit": "first"}, {"pr_id": 2, "merge_commit": "second"}],
            "task_trace": {
                "milestones": milestones(pr_waiting_merge="08:03:40", pr_merged="08:56:45", release_started="08:56:54"),
                "release": {"build_id": 1, "at": stamp("09:05:05"), "deployment_status": "deployed",
                            "runtime_started_at": "2026-11-19T09:03:52.000000002Z"},
            },
        }
        result = stage_timings(record, now=datetime.fromisoformat(stamp("09:06:11")))
        elapsed = {key: timing["duration_seconds"] for key, timing in result.items()}
        self.assertEqual(elapsed, {"intake": 0, "requirement": 30, "submit": 43, "merge": 3185,
                                   "release": None, "deploy": 73, "delivery": 6, "acceptance": 60})
        self.assertEqual(result["delivery"]["started_at"], stamp("09:05:05"))
        self.assertTrue(result["acceptance"]["running"])

    def test_build_deployment_and_feedback_have_separate_recorded_durations(self):
        record = {
            "status": "delivered",
            "completed_at": stamp("09:05:13"),
            "task_trace": {
                "milestones": milestones(release_started="09:00:00", delivery_started="09:05:02", delivery_completed="09:05:13"),
                "release": {"build_id": 1, "at": stamp("09:05:00"), "deployment_status": "deployed",
                            "build_finished_at": stamp("09:02:00"), "deployment_started_at": stamp("09:02:10"),
                            "deployment_finished_at": stamp("09:04:59")},
            },
            "acceptance_progress": {"latest_feedback_at": stamp("09:15:13")},
        }
        result = stage_timings(record, now=datetime.fromisoformat(stamp("10:00:00")))
        self.assertEqual({key: value["duration_seconds"] for key, value in result.items()},
                         {"release": 120, "deploy": 169, "delivery": 11, "acceptance": 600})
        self.assertFalse(any(value["running"] for value in result.values()))

    def test_partial_merge_uses_now_until_all_prs_have_been_merged(self):
        result = stage_timings({
            "status": "waiting_merge",
            "repository_states": [{"pr_id": 1, "merge_commit": "done"}, {"pr_id": 2}],
            "steps": [step("deliver", "08:03:40", status="running")],
            "task_trace": {"milestones": milestones(pr_waiting_merge="08:03:40", pr_merged="08:04:40")},
        }, now=datetime.fromisoformat(stamp("08:08:40")))
        self.assertEqual(result["merge"]["duration_seconds"], 300)
        self.assertTrue(result["merge"]["running"])
        self.assertNotIn("delivery", result)

    def test_planning_receipt_splits_development_and_preserves_the_original_execution_span(self):
        result = stage_timings({
            "status": "developing",
            "steps": [step("prepare", "08:00:00", "08:00:20"), step("develop", "08:00:20", status="running")],
            "task_trace": {"milestones": milestones(workspace_planning="08:00:22", workspace_plan_recorded="08:01:52")},
        }, now=datetime.fromisoformat(stamp("08:03:00")))
        self.assertEqual(result["requirement"]["duration_seconds"], 90)
        self.assertEqual(result["development"]["started_at"], stamp("08:01:52"))
        self.assertEqual(result["development"]["duration_seconds"], 68)
        self.assertTrue(result["development"]["running"])

    def test_skipped_or_synthetic_terminal_starts_are_not_displayed(self):
        result = stage_timings({
            "status": "delivered",
            "steps": [step("validate", "08:00:00", "08:00:00"), step("submit", "08:00:10", "08:00:10", "skipped")],
        })
        self.assertFalse(result)
        self.assertFalse(intake_timing({"status": "queued"}))
        intake = intake_timing({"status": "routed", "claimed_at": stamp("08:00:01"), "completed_at": stamp("08:00:08")})
        self.assertEqual(intake["intake"]["duration_seconds"], 7)

    def test_historical_tfs_times_only_apply_to_the_matching_build(self):
        from app.services.task_trace import for_request

        identity = {"buildId": 8, "definitionId": 97, "tfsProject": "DCS"}
        receipt = {"event_type": "release.build_observed", "created_at": stamp("12:00:00"), "message": "",
                   "metadata": json.dumps(identity | {"buildStartedAt": stamp("08:56:51"), "buildFinishedAt": stamp("08:59:50")})}
        release = {"event_type": "release.completed", "created_at": stamp("09:05:05"), "message": "",
                   "metadata": json.dumps(identity | {"result": "succeeded"})}
        with patch("app.services.task_trace.rows", return_value=[receipt, release]):
            trace = for_request("task")
        timing = stage_timings({"status": "delivered", "task_trace": trace})
        self.assertEqual(timing["release"]["started_at"], stamp("08:56:51"))
        self.assertEqual(timing["release"]["duration_seconds"], 179)

        release["metadata"] = json.dumps(identity | {"buildId": 9, "result": "succeeded"})
        with patch("app.services.task_trace.rows", return_value=[receipt, release]):
            self.assertIsNone(for_request("task")["release"]["build_started_at"])
