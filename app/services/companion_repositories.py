"""Regional companion programs participate in the same development and delivery."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import re
import shutil
from urllib.parse import unquote
import zipfile

from ..config import ROOT, settings
from .delivery import git, run_command
from .process_env import git_authenticated_env, sanitized_process_env


def specifications(project: dict) -> list[dict]:
    return (project.get("quality_profile") or {}).get("companion_repositories") or []


def validate_configuration(project: dict, repositories: list[Path]) -> None:
    by_name = {path.name: path for path in repositories}
    seen = set()
    for spec in specifications(project):
        name = spec["name"]
        if name in seen or name not in by_name:
            raise RuntimeError(f"关联接口仓库配置重复或缺失 {name}")
        seen.add(name)
        branch = str(spec.get("target_branch") or "")
        candidates = {name.casefold(), str(by_name[name]).casefold()}
        configured = next((value for key, value in (project.get("repository_base_branches") or {}).items()
                           if str(key).casefold() in candidates), None)
        if not branch or branch != configured:
            raise RuntimeError(f"接口仓库 {name} 的目标分支应为 {branch}，请核对地区分支配置")
        remote = git(by_name[name], "remote", "get-url", "origin").rstrip("/").removesuffix(".git")
        if unquote(remote.rsplit("/", 1)[-1]).casefold() != spec["repository"].casefold():
            raise RuntimeError(f"接口仓库 {name} 的 origin 与项目配置不一致")
        git(by_name[name], "check-ref-format", "--branch", branch)
        profile = str(spec.get("maven_profile") or "")
        if profile and not re.fullmatch(r"[a-zA-Z0-9_.-]+", profile):
            raise RuntimeError(f"接口仓库 {name} 的 Maven profile 不合法")


def feature_branch(project: dict, default: str) -> str:
    if not specifications(project):
        return default
    key = str(project.get("project_key") or "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
        raise RuntimeError("关联接口程序的项目需要有效项目标识")
    return default + "-" + key


def review_problems(project: dict, states: list[dict], result: dict) -> list[str]:
    if project.get("simulation_mode"):
        return []
    reviews = {review.get("repository"): review for review in result.get("companion_reviews") or []}
    by_name = {state["name"]: state for state in states}
    problems = []
    for spec in specifications(project):
        name = spec["name"]
        review = reviews.get(name) or {}
        if (review.get("inspected") is not True or not review.get("evidence")
                or not str(review.get("reason") or "").strip()):
            problems.append(f"请检索关联接口程序 {name}（{spec['target_branch']}）并记录功能位置、影响范围及联动修改判断")
            continue
        if review.get("target_branch") != spec["target_branch"]:
            problems.append(f"接口程序 {name} 的检索分支应为 {spec['target_branch']}")
        changed = bool((by_name.get(name) or {}).get("changed_files"))
        if review.get("requires_changes") is not changed:
            problems.append(f"接口程序 {name} 的联动修改判断与实际代码变更不一致，请补齐实现或修正判断依据")
    return problems


def deliver_packages(worker, request_id: str) -> None:
    detail = worker.store.detail(request_id)
    project = detail["policy_snapshot"]
    specs = {spec["name"]: spec for spec in specifications(project)}
    if not specs or project.get("simulation_mode"):
        return
    states = detail.get("repository_states") or []
    artifacts = {item["id"]: item for item in detail.get("artifacts") or []}
    for state in states:
        if state["name"] not in specs or not state.get("changed_files"):
            continue
        spec = specs[state["name"]]
        branch = state["base_branch"]
        if branch != spec["target_branch"]:
            raise RuntimeError(f"接口程序 {state['name']} 的打包分支与项目地区不一致")
        receipt = state.get("companion_delivery") or {}
        recorded = receipt.get("artifact_ids") or []
        if (receipt.get("target_branch") == branch and recorded
                and all(artifacts.get(artifact_id, {}).get("kind") == "package" for artifact_id in recorded)):
            continue
        worker._check_cancelled(request_id)
        worktree = Path(state["worktree_path"])
        if git(worktree, "diff", "HEAD", "--name-only"):
            raise RuntimeError(f"接口程序 {state['name']} 在合并后仍有源码修改，请保留现场并核对")
        env = git_authenticated_env(settings.tfs_pat) if settings.tfs_pat else sanitized_process_env()
        git(worktree, "fetch", "origin", branch, env=env)
        merged = str(state.get("merge_commit") or state.get("base_branch_commit") or "")
        if not merged:
            raise RuntimeError(f"接口程序 {state['name']} 缺少 PR 合并记录")
        git(worktree, "merge-base", "--is-ancestor", merged, f"origin/{branch}", env=env)
        git(worktree, "checkout", "--detach", f"origin/{branch}", env=env)
        build_commit = git(worktree, "rev-parse", "HEAD")
        script = ROOT / "local-runner/project-scripts/interface-package.ps1"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = "& " + quote(script) + " -RepositoryPath " + quote(worktree) + " -MavenProfile " + quote(spec.get("maven_profile") or "")
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        worker.store.add_event(request_id, "companion.build_started", f"正在构建 {spec['module']}，目标分支 {branch}",
                               metadata={"repository": state["name"], "branch": branch, "commit": build_commit})
        run_command("pwsh.exe -NoProfile -EncodedCommand " + encoded, worktree,
                    cancel_check=lambda: worker._check_cancelled(request_id))
        worker._check_cancelled(request_id)
        packages = [path for path in (worktree / "target").glob("*.jar")
                    if not path.name.endswith(("-sources.jar", "-javadoc.jar", "-tests.jar"))]
        if len(packages) != 1:
            raise RuntimeError(f"接口程序 {state['name']} 必须构建出唯一的业务 JAR")
        package = packages[0]
        with zipfile.ZipFile(package) as archive:
            if not any(name.startswith("BOOT-INF/") for name in archive.namelist()):
                raise RuntimeError(f"接口程序 {state['name']} 未生成完整 Spring Boot 系统包")
        label = re.sub(r'[<>:"/\\|?*]', '-', spec['module'])[:40]
        branch_label = branch.replace('/', '-')
        name = f"{label}-{branch_label}-{build_commit[:8]}-{package.name}"
        target = worker.artifacts.request_dir(request_id) / "package" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package, target)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        worker._check_cancelled(request_id)
        artifact_id = worker.artifacts.record(request_id, "package", name, str(target))
        state["companion_delivery"] = {"artifact_ids": [artifact_id], "build_commit": build_commit,
                                       "target_branch": branch, "sha256": digest}
        state["build_commit"] = build_commit
        worker.store.update_request(request_id, repository_states=states)
        worker.store.add_event(request_id, "companion.build_completed", f"{spec['module']} 系统包已生成", metadata={
            "repository": state["name"], **state["companion_delivery"]})
