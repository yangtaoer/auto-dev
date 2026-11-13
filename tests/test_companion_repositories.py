from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


class RegionalFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.origin = self.root / "webservice-scczp.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)], check=True, capture_output=True)
        seed = self.root / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "dev_sd")
        git(seed, "config", "user.name", "AutoDev test")
        git(seed, "config", "user.email", "test@example.invalid")
        (seed / "region.txt").write_text("province baseline", encoding="utf-8")
        (seed / "pom.xml").write_text("<project/>", encoding="utf-8")
        git(seed, "add", "--", "region.txt", "pom.xml")
        git(seed, "commit", "-q", "-m", "province baseline")
        self.sd_commit = git(seed, "rev-parse", "HEAD")
        git(seed, "switch", "-q", "-c", "dev_cd")
        (seed / "region.txt").write_text("chengdu baseline", encoding="utf-8")
        git(seed, "add", "--", "region.txt")
        git(seed, "commit", "-q", "-m", "chengdu baseline")
        self.cd_commit = git(seed, "rev-parse", "HEAD")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "origin", "dev_sd", "dev_cd")
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/dev_sd")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.sd = self.workspace / "webservice-scczp-sd"
        self.cd = self.workspace / "webservice-scczp"
        for path, branch in [(self.sd, "dev_sd"), (self.cd, "dev_cd")]:
            subprocess.run(["git", "clone", "-q", "-b", branch, self.origin.as_posix(), str(path)], check=True, capture_output=True)
            git(path, "config", "user.name", "AutoDev test")
            git(path, "config", "user.email", "test@example.invalid")
        self.project = self.project_for(self.sd, "province", "dev_sd", "sd")

    def project_for(self, path, key, branch, profile):
        return {"project_key": key, "base_branch": "dev", "repository_paths": [str(path)],
                "repository_base_branches": {path.name: branch}, "repository_expectations": {path.name: "webservice-scczp"},
                "quality_profile": {"companion_repositories": [{"name": path.name, "repository": "webservice-scczp",
                    "target_branch": branch, "role": "interface", "module": key + "接口程序", "maven_profile": profile}]}}


class CompanionWorkspaceTests(RegionalFixture):
    def test_same_remote_same_story_uses_distinct_regional_branches_and_clean_baselines(self):
        from app.orchestrator import Worker
        (self.sd / "region.txt").write_text("province uncommitted work", encoding="utf-8")
        (self.cd / "region.txt").write_text("chengdu uncommitted work", encoding="utf-8")
        worker = Worker.__new__(Worker)
        worker._workspace_lock = threading.RLock()
        worker.store = SimpleNamespace(add_event=lambda *args, **kwargs: None)
        fake = SimpleNamespace(worktree_dir=self.root / "worktrees", tfs_pat="", tfs_user_alias="test")
        chengdu = self.project_for(self.cd, "chengdu", "dev_cd", "")
        with patch("app.orchestrator.settings", fake):
            sd_path, sd_states, sd_branch = worker._prepare_worktrees("province-task", {"id": 123}, self.project)
            cd_path, cd_states, cd_branch = worker._prepare_worktrees("chengdu-task", {"id": 123}, chengdu)
        self.assertNotEqual(sd_branch, cd_branch)
        self.assertEqual(sd_states[0]["base_branch"], "dev_sd")
        self.assertEqual(cd_states[0]["base_branch"], "dev_cd")
        self.assertEqual((sd_path / "region.txt").read_text(), "province baseline")
        self.assertEqual((cd_path / "region.txt").read_text(), "chengdu baseline")
        self.assertEqual((self.sd / "region.txt").read_text(), "province uncommitted work")
        self.assertEqual((self.cd / "region.txt").read_text(), "chengdu uncommitted work")

    def test_companion_target_branch_mismatch_is_rejected_before_preparing(self):
        from app.orchestrator import Worker
        self.project["repository_base_branches"][self.sd.name] = "dev_cd"
        with self.assertRaisesRegex(RuntimeError, "目标分支应为 dev_sd"):
            Worker._configured_repository_paths(self.project)

    def test_tfs_remote_with_trailing_slash_resolves_the_shared_repository(self):
        from app.services.tfs import TfsClient
        response = SimpleNamespace(returncode=0, stdout="http://dev.tellhowsoft.com/DefaultCollection/DCS/_git/webservice-scczp/\n")
        with patch("app.services.tfs.subprocess.run", return_value=response):
            self.assertEqual(TfsClient.parse_origin(str(self.sd)), ("DCS", "webservice-scczp"))

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell not available")
    def test_review_command_uses_interface_branch_and_profile(self):
        git(self.sd, "switch", "-q", "-c", "feature/test")
        (self.sd / "Feature.java").write_text("class Feature {}", encoding="utf-8")
        git(self.sd, "add", "--", "Feature.java")
        git(self.sd, "commit", "-q", "-m", "feature")
        tools = self.root / "tools"
        tools.mkdir()
        log = tools / "maven-arguments.txt"
        (tools / "mvn.cmd").write_text('@echo off\r\necho %* >> "%~dp0maven-arguments.txt"\r\nexit /b 0\r\n', encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "local-runner/project-scripts/network-command-review.ps1"
        env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
               "AUTODEV_CHANGED_REPOSITORIES": json.dumps([self.sd.name]),
               "AUTODEV_REPOSITORY_BRANCHES": json.dumps({self.sd.name: "dev_sd"}),
               "AUTODEV_REPOSITORY_PROFILES": json.dumps({self.sd.name: "sd"})}
        result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(script)], cwd=self.workspace,
                                env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-P sd", log.read_text())
        self.assertIn("clean package", log.read_text())


class CompanionPolicyTests(unittest.TestCase):
    def setUp(self):
        from app.services import companion_repositories
        from app.services.quality_gates import evaluate_development_quality
        self.companions = companion_repositories
        self.evaluate = evaluate_development_quality
        self.spec = {"name": "regional-interface", "repository": "shared-interface", "target_branch": "dev_region",
                     "module": "区域接口程序", "role": "interface", "maven_profile": ""}
        self.project = {"quality_profile": {"companion_repositories": [self.spec]}}
        self.states = [{"name": "main", "changed_files": ["src/Feature.java"]}, {"name": "regional-interface", "changed_files": []}]
        self.review = {"repository": "regional-interface", "target_branch": "dev_region", "inspected": True,
                       "requires_changes": False, "reason": "已核对当前接口参数和调用边界", "evidence": ["Controller.java / endpoint"]}

    def test_main_requirement_requires_inspection_of_configured_interface(self):
        gate = self.evaluate(self.project, self.states, {"decision": "completed"})
        self.assertTrue(any("检索关联接口程序" in value for value in gate["blockers"]))
        gate = self.evaluate(self.project, self.states, {"decision": "completed", "companion_reviews": [self.review]})
        self.assertFalse(gate["blockers"])
        self.assertEqual(gate["companion_reviews"], [self.review])

    def test_declared_interface_change_must_have_code_and_correct_region(self):
        review = {**self.review, "requires_changes": True, "target_branch": "dev_other"}
        errors = self.companions.review_problems(self.project, self.states, {"companion_reviews": [review]})
        self.assertTrue(any("检索分支" in value for value in errors))
        self.assertTrue(any("实际代码变更不一致" in value for value in errors))

    def test_provincial_title_routes_to_one_project_with_interface_in_scope(self):
        from app.project_catalog import resolve_projects_for_work_item, load_project_presets
        from app.project_experience import apply_project_experience
        root = Path(__file__).resolve().parents[1]
        presets = [json.loads((root / f"local-runner/project-presets/{key}.json").read_text("utf-8"))
                   for key in ["sichuan-dispatch-network-command", "chengdu-network-command"]]
        item = {"id": 123, "title": "【四川省调】操作票功能优化", "description": "同步外部接口", "area_path": "XiNanArea-New\\四川省区团队"}
        projects, _, _ = resolve_projects_for_work_item(123, presets, work_item=item)
        self.assertEqual([p["project_key"] for p in projects], ["sichuan-dispatch-network-command"])
        self.assertIn("webservice-scczp-sd", [Path(p).name for p in projects[0]["repository_paths"]])
        self.assertIn("关联接口程序", apply_project_experience(projects[0])["development_instructions"])
        self.assertEqual(projects[0]["repository_base_branches"]["webservice-scczp-sd"], "dev_sd")

    def test_partial_expectations_preserve_existing_repository_checks(self):
        from app.project_catalog import load_project_presets
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = {"project_key": "example", "repository_paths": ["C:/main", "C:/interface-sd"],
                       "repository_expectations": {"interface-sd": "interface"}}
            (root / "example.json").write_text(json.dumps(project), encoding="utf-8")
            with patch("app.project_catalog.settings", SimpleNamespace(project_preset_dir=root, runner_id="test")), \
                 patch("app.project_catalog.repository_tfs_paths", return_value={"main": "https://tfs/main", "interface-sd": "https://tfs/interface"}):
                result = load_project_presets()[0]
            self.assertEqual(result["repository_expectations"], {"main": "main", "interface-sd": "interface"})

    def test_maven_outputs_are_local_and_business_java_stays_committable(self):
        from app.services.commit_policy import is_local_validation_file
        self.assertTrue(is_local_validation_file("target/classes/Feature.class"))
        self.assertTrue(is_local_validation_file("target/webservice.jar"))
        self.assertFalse(is_local_validation_file("src/main/java/com/webservice/Feature.java"))

    def test_companion_commits_are_delivered_as_packages_separately_from_main_pipeline_sources(self):
        from app.services.release_policy import commits_for_release
        detail = {"policy_snapshot": self.project, "repository_states": [
            {"name": "main", "commit_hash": "a" * 40},
            {"name": "regional-interface", "commit_hash": "b" * 40}]}
        self.assertEqual(commits_for_release([detail]), {"main": ["a" * 40]})

    def test_simulation_keeps_its_existing_demonstration_flow(self):
        project = {**self.project, "simulation_mode": True}
        gate = self.evaluate(project, self.states, {"decision": "completed"})
        self.assertFalse(gate["blockers"])

    def test_cancelled_batch_member_does_not_cancel_the_release_owner(self):
        from app.orchestrator import Worker, Cancelled
        worker = Worker.__new__(Worker)
        detail = {"artifacts": []}
        worker.store = SimpleNamespace(
            release_claim=lambda rid: {"action": "run", "batch_id": "batch", "members": ["owner", "other"]},
            detail=lambda rid: {"status": "waiting_release"}, update_request=lambda *args, **kwargs: None,
            update_step=lambda *args, **kwargs: None, add_event=lambda *args, **kwargs: None,
            release_finish=lambda *args, **kwargs: None)
        worker._check_cancelled = lambda rid: None
        worker._ensure_auto_release = lambda *args, **kwargs: {"artifactsUrl": "https://tfs.invalid/artifact"}
        def package(target, rid):
            if rid == "other":
                raise Cancelled()
        with patch.object(self.companions, "deliver_packages", side_effect=package):
            self.assertTrue(worker._coordinated_release("owner", detail, self.project))


class CompanionPackagingTests(RegionalFixture):
    def setUp(self):
        super().setUp()
        from app.services import companion_repositories
        from app.services.delivery import ArtifactService
        self.companions = companion_repositories
        self.state = {"name": self.sd.name, "worktree_path": str(self.sd), "base_branch": "dev_sd",
                      "merge_commit": self.sd_commit, "changed_files": ["Feature.java"]}
        self.detail = {"policy_snapshot": self.project, "repository_states": [self.state], "artifacts": []}
        def record(rid, kind, name, path, url=""):
            artifact_id = len(self.detail["artifacts"]) + 1
            self.detail["artifacts"].append({"id": artifact_id, "kind": kind, "name": name, "local_path": path})
            return artifact_id
        def update(rid, **fields):
            self.detail.update(fields)
        self.store = SimpleNamespace(detail=lambda rid: self.detail, add_event=lambda *args, **kwargs: None, update_request=update)
        artifacts = ArtifactService(recorder=record, detail_loader=lambda rid: self.detail)
        artifacts.request_dir = lambda rid: self.root / "deliveries" / rid
        self.worker = SimpleNamespace(store=self.store, artifacts=artifacts, _check_cancelled=lambda rid: None)
        self.worker.artifacts.request_dir("task").mkdir(parents=True)

    def build(self, command, cwd, **kwargs):
        decoded = base64.b64decode(command.split()[-1]).decode("utf-16-le")
        self.assertIn("-MavenProfile 'sd'", decoded)
        target = cwd / "target"
        target.mkdir(exist_ok=True)
        with zipfile.ZipFile(target / "webservice-scczp-1.jar", "w") as archive:
            archive.writestr("BOOT-INF/classes/region.txt", (cwd / "region.txt").read_text())

    def test_delivery_uses_merged_regional_branch_and_records_package_once(self):
        git(self.sd, "switch", "-q", "-c", "wrong-start", "origin/dev_cd")
        with patch.object(self.companions, "settings", SimpleNamespace(tfs_pat="")), patch.object(self.companions, "run_command", side_effect=self.build) as build:
            self.companions.deliver_packages(self.worker, "task")
            self.companions.deliver_packages(self.worker, "task")
        self.assertEqual(build.call_count, 1)
        self.assertEqual(self.state["build_commit"], self.sd_commit)
        self.assertEqual(len(self.detail["artifacts"]), 1)
        self.assertIn("dev_sd", self.detail["artifacts"][0]["name"])
        with zipfile.ZipFile(self.detail["artifacts"][0]["local_path"]) as package:
            self.assertEqual(package.read("BOOT-INF/classes/region.txt"), b"province baseline")

    def test_unmerged_other_region_commit_is_rejected(self):
        self.state["merge_commit"] = self.cd_commit
        with patch.object(self.companions, "settings", SimpleNamespace(tfs_pat="")), patch.object(self.companions, "run_command") as build:
            with self.assertRaises(RuntimeError):
                self.companions.deliver_packages(self.worker, "task")
        build.assert_not_called()

    def test_dirty_source_is_retained_and_not_packaged(self):
        (self.sd / "region.txt").write_text("pending source change", encoding="utf-8")
        with patch.object(self.companions, "run_command") as build:
            with self.assertRaisesRegex(RuntimeError, "合并后仍有源码修改"):
                self.companions.deliver_packages(self.worker, "task")
        self.assertEqual((self.sd / "region.txt").read_text(), "pending source change")
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
