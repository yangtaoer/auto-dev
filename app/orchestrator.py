from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import zipfile
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import settings
from .db import utc_now
from .domain import REVIEW_DELIVERY_MODES, DeliveryMode, RunStatus, visible_delivery_artifacts
from .services.codex_runner import CodexRunner
from .services.delivery import (
    ArtifactService,
    Mailer,
    changed_files,
    git,
    protected_changes,
    repository_short_name,
    run_command,
)
from .services.tfs import TfsClient
from .services.process_env import git_authenticated_env, sanitized_process_env
from .project_catalog import resolve_project_for_work_item
from .live_stream import LiveCodexPublisher
from .store import LocalStore


logger = logging.getLogger("autodev.worker")


class Cancelled(RuntimeError):
    pass


class Worker:
    _workspace_lock = threading.RLock()

    def __init__(self, store=None, *, max_concurrency: int | None = None) -> None:
        self.store = store or LocalStore()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.threads: list[threading.Thread] = []
        self.max_concurrency = min(5, max(1, max_concurrency or settings.runner_max_concurrency))
        self._active_lock = threading.RLock()
        self._active_request_ids: set[str] = set()
        public_base_url = settings.cloud_url or settings.public_base_url if self.store.remote else settings.public_base_url
        self.artifacts = ArtifactService(
            recorder=self.store.add_artifact,
            detail_loader=self.store.detail,
            public_base_url=public_base_url,
        )
        self.mailer = Mailer()

    def start(self) -> None:
        if any(thread.is_alive() for thread in self.threads):
            return
        self.threads = [
            threading.Thread(target=self._loop, name=f"autodev-worker-{slot + 1}", daemon=True)
            for slot in range(self.max_concurrency)
        ]
        self.thread = self.threads[0]
        for thread in self.threads:
            thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=5)

    def join(self, timeout: float | None = None) -> None:
        for thread in self.threads:
            thread.join(timeout=timeout)

    @property
    def current_request_ids(self) -> list[str]:
        with self._active_lock:
            return sorted(self._active_request_ids)

    @property
    def current_request_id(self) -> str | None:
        active = self.current_request_ids
        return active[0] if active else None

    def _set_active(self, request_id: str, active: bool) -> None:
        with self._active_lock:
            if active:
                self._active_request_ids.add(request_id)
            else:
                self._active_request_ids.discard(request_id)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            processed = False
            try:
                processed = self.process_once()
            except Exception:
                # 单个任务的异常会在 run_request/poll_merge 中落库；这里避免工作线程退出。
                logger.exception("任务轮询循环发生异常")
            if not processed:
                self.stop_event.wait(settings.poll_seconds)

    def process_once(self) -> bool:
        routed = False
        if self.store.remote:
            intake = self.store.claim_intake()
            if intake:
                routed = True
                self._route_intake(intake)
        queued = self.store.next_queued()
        if queued:
            logger.info("开始执行研发任务 request_id=%s", queued)
            self._set_active(queued, True)
            try:
                self.run_request(queued)
            finally:
                self._set_active(queued, False)
            return True
        waiting = self.store.next_waiting()
        if waiting:
            logger.info("检查 PR 合并状态 request_id=%s", waiting)
            self._set_active(waiting, True)
            try:
                self.poll_merge(waiting)
            finally:
                self._set_active(waiting, False)
            return True
        return routed

    def _route_intake(self, intake: dict) -> None:
        try:
            project, item = resolve_project_for_work_item(intake["work_item_id"])
            request_id = self.store.route_intake(intake["id"], project_key=project["project_key"])
            logger.info(
                "TFS 需求已自动匹配项目 intake_id=%s work_item_id=%s project=%s area_path=%s request_id=%s",
                intake["id"], intake["work_item_id"], project["project_key"], item.get("area_path", ""), request_id,
            )
        except Exception as exc:
            message = str(exc)[:2000]
            self.store.route_intake(intake["id"], error_message=message)
            logger.warning(
                "TFS 需求自动匹配项目失败 intake_id=%s work_item_id=%s error=%s",
                intake["id"], intake["work_item_id"], message,
            )

    def run_request(self, request_id: str) -> None:
        detail = self.store.detail(request_id)
        if not detail:
            return
        project = detail["policy_snapshot"]
        try:
            self.store.update_request(request_id, status=RunStatus.VALIDATING.value, started_at=utc_now(), progress=5)
            self.store.update_step(request_id, "validate", "running", "正在读取并校验 TFS 需求")
            work_item = self._validate(detail, project)
            self.store.update_request(
                request_id,
                title=work_item["title"],
                requirement_summary=self._plain_text(work_item.get("description", ""))[:4000],
                work_item_revision=work_item.get("revision"),
                progress=15,
            )
            self.store.update_step(
                request_id,
                "validate",
                "completed",
                f"需求准入通过：{work_item.get('work_item_type', '—')} / {work_item.get('state', '—')}，revision {work_item.get('revision', '—')}",
            )
            self.store.add_event(request_id, "tfs.validated", f"TFS #{work_item['id']} 准入校验通过")
            self._check_cancelled(request_id)

            self.store.update_request(request_id, current_step="prepare", progress=22)
            self.store.update_step(request_id, "prepare", "running", "正在创建隔离工作区")
            repository_states: list[dict] = []
            if project.get("simulation_mode"):
                worktree, base_commit, branch = None, "demo-base-commit", f"feature/{work_item['id']}-demo"
            elif detail.get("supplement_answers") and self._resumable_worktrees(detail):
                repository_states = detail["repository_states"]
                workspace_paths = [Path(state["worktree_path"]) for state in repository_states]
                worktree = workspace_paths[0] if len(workspace_paths) == 1 else workspace_paths[0].parent
                base_commit = repository_states[0]["base_commit"]
                branch = detail.get("branch_name") or repository_states[0]["branch"]
                self.store.add_event(
                    request_id,
                    "workspace.resumed",
                    f"复用待补充任务的 {len(repository_states)} 个隔离仓库继续研发",
                )
            else:
                worktree, repository_states, branch = self._prepare_worktrees(request_id, work_item, project)
                base_commit = repository_states[0]["base_commit"]
            self.store.update_request(
                request_id,
                branch_name=branch,
                base_commit=base_commit,
                repository_states=repository_states,
            )
            repository_count = len(repository_states) or 1
            self.store.update_step(
                request_id,
                "prepare",
                "completed",
                self._workspace_prepared_message(project, repository_states, branch, repository_count),
            )
            self._check_cancelled(request_id)

            self.store.update_request(request_id, status=RunStatus.DEVELOPING.value, current_step="develop", progress=32)
            self.store.update_step(request_id, "develop", "running", "DevCore 正在分析需求并修改代码")
            if project.get("simulation_mode"):
                result = self._simulate_development(request_id, work_item)
                codex_thread_id = "demo-thread"
            else:
                live_publisher = LiveCodexPublisher(request_id, self.store)
                try:
                    run = CodexRunner().run(
                        cwd=worktree,
                        work_item=work_item,
                        project=project,
                        on_event=lambda event_type, message: self.store.add_event(request_id, event_type, message),
                        on_live_event=live_publisher.emit,
                        resume_thread_id=detail.get("codex_thread_id") if detail.get("supplement_answers") else None,
                        supplement_requests=detail.get("supplement_requests") or [],
                        supplement_answers=detail.get("supplement_answers") or [],
                    )
                finally:
                    live_publisher.close()
                result, codex_thread_id = run.result, run.thread_id
                for state in repository_states:
                    state["changed_files"] = changed_files(Path(state["worktree_path"]), state["base_commit"])
                changed_states = [state for state in repository_states if state["changed_files"]]
                paths = [
                    f"{state['name']}/{relative}"
                    for state in changed_states
                    for relative in state["changed_files"]
                ]
                self.store.update_request(request_id, repository_states=repository_states)
                blocked = protected_changes(paths, project.get("protected_patterns", []))
                if blocked:
                    self.store.update_request(
                        request_id,
                        status=RunStatus.WAITING_APPROVAL.value,
                        current_step="develop",
                        progress=48,
                        codex_thread_id=codex_thread_id,
                        error_message="检测到受保护路径变更：" + "、".join(blocked),
                    )
                    self.store.update_step(request_id, "develop", "failed", "需要管理员确认共享/受保护代码影响")
                    self.store.add_event(request_id, "policy.protected_change", "检测到受保护路径变更，任务已暂停", level="warning", metadata={"paths": blocked})
                    return
            summary = result.get("summary", "")
            supplement_requests = self._normalize_supplement_requests(result.get("supplement_requests"))
            if result.get("decision") == "needs_input" or supplement_requests:
                if not supplement_requests:
                    supplement_requests = [{
                        "id": "missing-critical-information",
                        "question": "请补充 DevCore 结论中指出的关键业务信息。",
                        "reason": summary or "当前缺少继续可靠研发所必需的信息。",
                        "suggested_answer": "请给出明确的数据来源、业务口径或权限规则。",
                        "required": True,
                    }]
                requested_at = utc_now()
                self.store.update_request(
                    request_id,
                    status=RunStatus.WAITING_INPUT.value,
                    current_step="clarify",
                    progress=50,
                    codex_thread_id=codex_thread_id,
                    result_summary=summary,
                    supplement_requests=supplement_requests,
                    supplement_requested_at=requested_at,
                    error_message="",
                )
                self.store.update_step(request_id, "develop", "completed", summary or "需求分析完成，需要补充关键信息")
                self.store.update_step(
                    request_id,
                    "clarify",
                    "running",
                    f"等待补充 {len(supplement_requests)} 项关键信息",
                )
                self.store.add_event(
                    request_id,
                    "development.input_required",
                    f"DevCore 需要补充 {len(supplement_requests)} 项关键信息后继续研发",
                    level="warning",
                    metadata={"requests": supplement_requests},
                )
                self._send_status_email(request_id, action_required=True)
                return
            if not project.get("simulation_mode") and not paths:
                raise RuntimeError("DevCore 执行完成，但没有产生代码变更")
            mode = DeliveryMode(detail["delivery_mode"])
            if mode == DeliveryMode.SICHUAN_AUTO_REVIEW and result.get("risks"):
                risk_text = "；".join(str(item) for item in result["risks"])
                self.store.update_request(
                    request_id,
                    status=RunStatus.WAITING_APPROVAL.value,
                    current_step="develop",
                    progress=48,
                    codex_thread_id=codex_thread_id,
                    result_summary=summary,
                    error_message="自动审核发现风险：" + risk_text,
                )
                self.store.update_step(request_id, "develop", "failed", "自动审核发现风险，需要人工确认")
                self.store.add_event(request_id, "review.risk_found", risk_text, level="warning")
                return
            self.store.update_request(request_id, result_summary=summary, codex_thread_id=codex_thread_id, progress=55)
            self.store.update_step(request_id, "develop", "completed", summary or "代码修改完成")
            if not detail.get("supplement_answers"):
                self.store.update_step(request_id, "clarify", "skipped", "本轮研发无需补充信息")
            self.store.add_event(request_id, "development.completed", summary or "DevCore 自动研发完成", metadata=result)
            self._check_cancelled(request_id)

            self.store.update_request(request_id, status=RunStatus.SUBMITTING.value, current_step="submit", progress=62)
            submit_message = (
                "本地交付策略已锁定：正在提交代码并同步至最新目标分支"
                if mode == DeliveryMode.LOCAL_PACKAGE
                else "正在提交并推送代码"
            )
            self.store.update_step(request_id, "submit", "running", submit_message)
            if mode == DeliveryMode.LOCAL_PACKAGE:
                self.store.add_event(
                    request_id,
                    "delivery.policy_enforced",
                    "本地交付不按改动大小或接口影响跳过：必须先提交目标分支，再基于最新目标分支构建打包",
                )
            if project.get("simulation_mode"):
                commit_hash = "demo" + request_id.replace("-", "")[:8]
            else:
                commits: list[str] = []
                for state in changed_states:
                    state_worktree = Path(state["worktree_path"])
                    commit_hash = self._commit_and_push(
                        state_worktree, detail, work_item, project, state["branch"]
                    )
                    state["commit_hash"] = commit_hash
                    state["status"] = "pushed"
                    commits.append(commit_hash)
                    if mode == DeliveryMode.LOCAL_PACKAGE:
                        self.artifacts.collect_changed_assets(
                            request_id,
                            state_worktree,
                            state["base_commit"],
                            project,
                            repository_name=state["name"],
                        )
                    else:
                        self.artifacts.collect_menu_links(request_id, state_worktree, state["base_commit"])
                if mode == DeliveryMode.LOCAL_PACKAGE:
                    commits = self._merge_local_package_to_base(
                        request_id,
                        project,
                        repository_states,
                        changed_states,
                    )
                commit_hash = commits[0]
                self.store.update_request(request_id, repository_states=repository_states)
            self.store.update_request(request_id, commit_hash=commit_hash)

            if mode == DeliveryMode.LOCAL_PACKAGE:
                target_branches = "、".join(
                    dict.fromkeys(str(state.get("base_branch") or "dev") for state in repository_states)
                ) if repository_states else project.get("base_branch", "dev")
                self.store.update_step(
                    request_id,
                    "submit",
                    "completed",
                    f"代码已提交至 {target_branches}，构建基线 {commit_hash[:12]}",
                )
                self._deliver_local_package(request_id, detail, project, worktree, work_item)
                return

            verification_command = project.get("build_command", "").strip()
            if mode == DeliveryMode.SICHUAN_AUTO_REVIEW and not project.get("simulation_mode") and not verification_command:
                raise RuntimeError("四川自动审核交付必须配置构建/校验命令，校验通过后才允许服务账号批准")
            if not project.get("simulation_mode") and verification_command:
                output = run_command(verification_command, worktree)
                self.store.add_event(request_id, "verification.completed", "PR 前构建/校验通过", metadata={"output_tail": output[-1000:]})

            if project.get("simulation_mode"):
                pr = self._create_pr(request_id, detail, project, worktree, work_item, branch)
                pull_requests = [(None, pr)]
            else:
                pull_requests = []
                for state in changed_states:
                    pr = self._create_pr(
                        request_id,
                        detail,
                        project,
                        Path(state["worktree_path"]),
                        work_item,
                        state["branch"],
                        target_branch=state["base_branch"],
                    )
                    state.update({"pr_id": pr["id"], "pr_url": pr["url"], "status": "waiting_merge"})
                    pull_requests.append((state, pr))
                self.store.update_request(request_id, repository_states=repository_states)
            primary_pr = pull_requests[0][1]
            self.store.update_request(request_id, pr_id=primary_pr["id"], pr_url=primary_pr["url"], progress=76)
            pr_numbers = "、".join(f"#{item[1]['id']}" for item in pull_requests)
            self.store.update_step(request_id, "submit", "completed", f"PR {pr_numbers} 已创建并关联需求")

            if mode == DeliveryMode.SICHUAN_AUTO_REVIEW:
                if project.get("simulation_mode"):
                    self.store.add_event(request_id, "pr.approved", "演示模式：四川审核服务账号已批准 PR")
                else:
                    tfs = TfsClient(project["tfs_collection_url"])
                    for state, pr in pull_requests:
                        tfs.approve_pull_request(state["repository_path"], pr["id"])
                    self.store.add_event(
                        request_id,
                        "pr.approved",
                        f"四川审核人 {project.get('reviewer_name') or settings.tfs_reviewer_name} 已批准 {len(pull_requests)} 个 PR；操作已记录审计",
                    )
            else:
                self._send_status_email(request_id, action_required=True)
                self.store.add_event(request_id, "mail.action_required", "已邮件通知项目经理安排产品部审核并合并 PR")

            self.store.update_request(
                request_id,
                status=RunStatus.WAITING_MERGE.value,
                current_step="deliver",
                progress=82,
                next_poll_at=(datetime.now(UTC) + timedelta(seconds=settings.poll_seconds)).isoformat(),
            )
            self.store.update_step(request_id, "deliver", "running", "本机执行器正在循环检测 PR 合并状态")
            self.store.add_event(request_id, "pr.waiting_merge", f"开始监控 PR {pr_numbers} 合并状态")
        except Cancelled:
            self._cancel(request_id)
        except Exception as exc:
            self._fail(request_id, exc)

    def poll_merge(self, request_id: str) -> None:
        detail = self.store.detail(request_id)
        if not detail or detail["status"] != RunStatus.WAITING_MERGE.value:
            return
        project = detail["policy_snapshot"]
        try:
            if project.get("simulation_mode"):
                if not detail.get("merge_commit"):
                    self._schedule_next_poll(request_id)
                    return
                pr = {
                    "id": detail["pr_id"], "status": "completed", "title": detail["title"],
                    "repository": project.get("project_key", "demo-repo"),
                    "source_branch": detail.get("branch_name"), "target_branch": project.get("base_branch", "dev"),
                    "merge_commit": detail["merge_commit"], "closed_date": utc_now(),
                }
                if pr["status"] == "completed":
                    self._finish_merged_request(request_id, pr)
                else:
                    self._schedule_next_poll(request_id)
                return

            repository_states = [state for state in detail.get("repository_states", []) if state.get("pr_id")]
            if not repository_states:
                repository_states = [
                    {
                        "name": Path(project["repository_path"]).name,
                        "repository_path": project["repository_path"],
                        "pr_id": detail["pr_id"],
                        "pr_url": detail["pr_url"],
                    }
                ]
            tfs = TfsClient(project["tfs_collection_url"])
            completed: list[tuple[dict, dict]] = []
            pending: list[int] = []
            for state in repository_states:
                pr = tfs.get_pull_request(state["repository_path"], int(state["pr_id"]))
                if pr["status"] == "abandoned":
                    raise RuntimeError(f"{state['name']} 的 PR #{state['pr_id']} 已被放弃，无法完成交付")
                if pr["status"] == "completed":
                    completed.append((state, pr))
                else:
                    pending.append(int(state["pr_id"]))
            if pending:
                self.store.add_event(
                    request_id,
                    "pr.polled",
                    f"仍有 {len(pending)} 个 PR 尚未合并：" + "、".join(f"#{pr_id}" for pr_id in pending),
                )
                self._schedule_next_poll(request_id)
            else:
                self._finish_merged_repositories(request_id, repository_states, completed)
        except Exception as exc:
            if self.store.get_status(request_id) != RunStatus.WAITING_MERGE.value:
                self._fail(request_id, exc)
            else:
                self.store.add_event(request_id, "pr.poll_failed", str(exc), level="warning")
                self._schedule_next_poll(request_id, backoff=True)

    def _validate(self, detail: dict, project: dict) -> dict:
        if not project.get("enabled"):
            raise RuntimeError("项目已停用，不允许发起自动研发")
        if project.get("simulation_mode"):
            return {
                "id": detail["work_item_id"], "revision": 1,
                "title": f"演示需求 #{detail['work_item_id']} · 全自助业务能力优化",
                "state": "已评审", "work_item_type": "用户情景",
                "area_path": project.get("tfs_area_path", ""),
                "description": "演示模式需求说明，用于验证三种交付流程。",
                "acceptance_criteria": "功能完成；交付物可下载；邮件信息完整。",
            }
        item = TfsClient(project["tfs_collection_url"]).get_work_item(detail["work_item_id"])
        allowed_types = project.get("allowed_work_item_types") or ["用户情景"]
        allowed_states = project.get("allowed_states") or ["已评审"]
        if item["work_item_type"] not in allowed_types:
            raise RuntimeError(
                f"TFS #{item['id']} 类型为 {item['work_item_type']}，项目仅允许：{'、'.join(allowed_types)}"
            )
        if item["state"] not in allowed_states:
            raise RuntimeError(
                f"TFS #{item['id']} 状态为 {item['state']}，项目仅允许：{'、'.join(allowed_states)}"
            )
        configured_area = project.get("tfs_area_path", "")
        if configured_area and not item["area_path"].startswith(configured_area):
            raise RuntimeError(f"需求区域 {item['area_path']} 不在项目准入范围 {configured_area} 内")
        title_keywords = [str(value).strip() for value in project.get("routing_title_keywords", []) if str(value).strip()]
        if title_keywords and not any(keyword.casefold() in item["title"].casefold() for keyword in title_keywords):
            raise RuntimeError(
                f"需求标题“{item['title']}”未命中项目“{project.get('name', project.get('project_key', ''))}”"
                f"的路由关键字：{'、'.join(title_keywords)}"
            )
        if not item.get("description"):
            raise RuntimeError("需求描述为空，无法自动研发")
        return item

    @staticmethod
    def _configured_repository_paths(project: dict) -> list[Path]:
        configured = project.get("repository_paths") or [project.get("repository_path", "")]
        repositories: list[Path] = []
        seen: set[str] = set()
        for value in configured:
            if not str(value).strip():
                continue
            repo = Path(value).resolve()
            key = str(repo).casefold()
            if key in seen:
                continue
            seen.add(key)
            if not repo.is_dir() or not (repo / ".git").exists():
                raise RuntimeError(f"项目仓库路径无效：{repo}")
            repositories.append(repo)
        if not repositories:
            raise RuntimeError("项目未配置可用的 Git 仓库")
        names = [repo.name.casefold() for repo in repositories]
        if len(names) != len(set(names)):
            raise RuntimeError("多仓项目存在同名仓库目录，无法创建隔离工作区")
        return repositories

    @staticmethod
    def _repository_base_branch(project: dict, repository: Path) -> str:
        default_branch = str(project.get("base_branch") or "dev").strip() or "dev"
        overrides = project.get("repository_base_branches") or {}
        if not isinstance(overrides, dict):
            raise RuntimeError("项目 repository_base_branches 必须是仓库名到分支名的映射")
        candidates = {repository.name.casefold(), str(repository).casefold()}
        for key, value in overrides.items():
            if str(key).strip().casefold() not in candidates:
                continue
            branch = str(value).strip()
            if not branch:
                raise RuntimeError(f"仓库 {repository.name} 的基础分支配置为空")
            return branch
        return default_branch

    @staticmethod
    def _workspace_prepared_message(
        project: dict,
        repository_states: list[dict],
        branch: str,
        repository_count: int,
    ) -> str:
        if not repository_states:
            return (
                f"已从最新 origin/{project.get('base_branch', 'dev')} 创建分支 {branch}，"
                f"隔离仓库 {repository_count} 个"
            )
        branches = list(dict.fromkeys(str(state.get("base_branch") or "dev") for state in repository_states))
        source = "、".join(f"origin/{item}" for item in branches)
        return f"已按 {source} 创建分支 {branch}，隔离仓库 {repository_count} 个"

    def _prepare_worktrees(
        self,
        request_id: str,
        work_item: dict,
        project: dict,
    ) -> tuple[Path, list[dict], str]:
        repositories = self._configured_repository_paths(project)
        fetch_env = git_authenticated_env(settings.tfs_pat) if settings.tfs_pat else sanitized_process_env()
        base_branch_name = f"feature/{work_item['id']}-{settings.tfs_user_alias}"
        workspace_root = settings.worktree_dir / request_id
        states: list[dict] = []
        attempted: list[tuple[Path, Path]] = []
        # 同一仓库的 fetch/worktree 元数据共用 Git 锁；只串行化准备阶段，Codex 与构建仍可并行。
        with self._workspace_lock:
            current_action = "准备 Git 隔离工作区"
            branch = base_branch_name
            try:
                for repo in repositories:
                    base_branch = self._repository_base_branch(project, repo)
                    current_action = f"启用仓库长路径支持（{repo.name}）"
                    subprocess.run(
                        ["git", "-C", str(repo), "config", "core.longpaths", "true"],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=sanitized_process_env(),
                    )
                    current_action = f"更新基础分支（{repo.name}）"
                    subprocess.run(
                        ["git", "-C", str(repo), "fetch", "origin", base_branch],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=fetch_env,
                    )
                branch_exists = any(
                    subprocess.run(
                        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{base_branch_name}"],
                        env=sanitized_process_env(),
                    ).returncode == 0
                    for repo in repositories
                )
                if branch_exists:
                    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
                    branch = f"{branch}-{suffix}"
                workspace_root.mkdir(parents=True, exist_ok=True)
                for repo in repositories:
                    base_branch = self._repository_base_branch(project, repo)
                    base_commit = git(repo, "rev-parse", f"origin/{base_branch}")
                    worktree = workspace_root / repo.name
                    if worktree.exists():
                        raise RuntimeError(f"任务工作区已存在，需要人工确认后重试：{worktree}")
                    current_action = f"创建隔离工作区（{repo.name}）"
                    attempted.append((repo, worktree))
                    subprocess.run(
                        ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(worktree), f"origin/{base_branch}"],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=sanitized_process_env(),
                    )
                    states.append(
                        {
                            "name": repo.name,
                            "repository_short_name": repository_short_name(repo.name),
                            "repository_path": str(repo),
                            "worktree_path": str(worktree),
                            "base_branch": base_branch,
                            "base_commit": base_commit,
                            "branch": branch,
                            "changed_files": [],
                            "status": "prepared",
                        }
                    )
            except subprocess.CalledProcessError as exc:
                self._rollback_worktrees(attempted, workspace_root, branch)
                output = exc.stderr or exc.stdout or ""
                lines = [line.strip() for line in str(output).replace("\r", "\n").splitlines() if line.strip()]
                detail = "\n".join(lines[-8:]) or str(exc)
                raise RuntimeError(f"{current_action}失败：{detail}") from exc
            except Exception:
                self._rollback_worktrees(attempted, workspace_root, branch)
                raise
        codex_workspace = states[0]["worktree_path"] if len(states) == 1 else str(workspace_root)
        self.store.add_event(
            request_id,
            "workspace.prepared",
            f"已准备 {len(states)} 个隔离仓库：{'、'.join(state['name'] for state in states)}",
            metadata={"repositories": states},
        )
        return Path(codex_workspace), states, branch

    @staticmethod
    def _resumable_worktrees(detail: dict) -> bool:
        states = detail.get("repository_states") or []
        return bool(states) and all(
            isinstance(state, dict)
            and state.get("worktree_path")
            and Path(state["worktree_path"]).is_dir()
            for state in states
        )

    @staticmethod
    def _normalize_supplement_requests(value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        normalized: list[dict] = []
        seen: set[str] = set()
        for index, item in enumerate(value, 1):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            request_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(item.get("id") or f"item-{index}")).strip("-")
            request_id = request_id or f"item-{index}"
            if request_id in seen:
                request_id = f"{request_id}-{index}"
            seen.add(request_id)
            normalized.append(
                {
                    "id": request_id[:80],
                    "question": question[:1000],
                    "reason": str(item.get("reason") or "").strip()[:1500],
                    "suggested_answer": str(item.get("suggested_answer") or "").strip()[:1000],
                    "required": bool(item.get("required", True)),
                }
            )
        return normalized

    @staticmethod
    def _rollback_worktrees(attempted: list[tuple[Path, Path]], workspace_root: Path, branch: str) -> None:
        """Rollback only the worktrees and branches created by the current preparation attempt."""
        clean_env = sanitized_process_env()
        for repo, worktree in reversed(attempted):
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                capture_output=True,
                text=True,
                env=clean_env,
            )
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "prune"],
                capture_output=True,
                text=True,
                env=clean_env,
            )
            if subprocess.run(
                ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                capture_output=True,
                env=clean_env,
            ).returncode == 0:
                subprocess.run(
                    ["git", "-C", str(repo), "branch", "-D", branch],
                    capture_output=True,
                    text=True,
                    env=clean_env,
                )
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
        try:
            workspace_root.rmdir()
        except OSError:
            # 只移除本次创建后已为空的任务根目录，绝不清理预先存在的未知内容。
            pass

    def _commit_and_push(self, worktree: Path, detail: dict, work_item: dict, project: dict, branch: str) -> str:
        title = re.sub(r"[\r\n]+", " ", work_item["title"]).strip()[:72]
        area = work_item.get("area_path", "").split("\\")[-1] or project.get("name", "项目")
        subject = f"feat(#{work_item['id']}):{area}-{title}"
        git(worktree, "config", "user.name", os.getenv("AUTODEV_GIT_NAME", "AutoDev Codex"))
        git(worktree, "config", "user.email", os.getenv("AUTODEV_GIT_EMAIL", "autodev@localhost"))
        git(worktree, "add", "--all")
        result = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--cached", "--quiet"],
            env=sanitized_process_env(),
        )
        if result.returncode == 0:
            raise RuntimeError("没有可提交的代码变更")
        git(worktree, "commit", "--no-verify", "-m", subject)
        commit_hash = git(worktree, "rev-parse", "HEAD")
        push_env = git_authenticated_env(settings.tfs_pat) if settings.tfs_pat else sanitized_process_env()
        git(worktree, "push", "--no-verify", "-u", "origin", branch, env=push_env)
        self.store.add_event(detail["id"], "git.pushed", subject, metadata={"branch": branch, "commit": commit_hash})
        return commit_hash

    def _merge_local_package_to_base(
        self,
        request_id: str,
        project: dict,
        repository_states: list[dict],
        changed_states: list[dict],
    ) -> list[str]:
        """将本地打包任务的提交快进到最新目标分支，并返回最终构建提交。"""
        changed_names = {str(state["name"]) for state in changed_states}
        git_env = git_authenticated_env(settings.tfs_pat) if settings.tfs_pat else sanitized_process_env()
        final_commits: list[str] = []

        for state in repository_states:
            worktree = Path(state["worktree_path"])
            repository_name = str(state["name"])
            target_branch = str(state.get("base_branch") or project.get("base_branch") or "dev")
            feature_branch = str(state["branch"])
            changed = repository_name in changed_names

            final_commit = self._sync_local_package_repository(
                worktree,
                repository_name=repository_name,
                feature_branch=feature_branch,
                target_branch=target_branch,
                changed=changed,
                git_env=git_env,
            )
            state["commit_hash"] = final_commit
            state["base_branch_commit"] = final_commit
            state["status"] = "base_pushed" if changed else "base_synced"
            if changed:
                final_commits.append(final_commit)
                self.store.add_event(
                    request_id,
                    "git.base_pushed",
                    f"{repository_name} 已提交至 {target_branch}，将基于该分支最新代码构建",
                    metadata={
                        "repository": repository_name,
                        "feature_branch": feature_branch,
                        "target_branch": target_branch,
                        "commit": final_commit,
                    },
                )
            else:
                self.store.add_event(
                    request_id,
                    "git.base_synced",
                    f"{repository_name} 无本次改动，构建工作区已同步至最新 {target_branch}",
                    metadata={
                        "repository": repository_name,
                        "target_branch": target_branch,
                        "commit": final_commit,
                    },
                )

        if not final_commits:
            raise RuntimeError("本地打包任务没有可提交到目标分支的代码变更")
        return final_commits

    @staticmethod
    def _sync_local_package_repository(
        worktree: Path,
        *,
        repository_name: str,
        feature_branch: str,
        target_branch: str,
        changed: bool,
        git_env: dict[str, str],
    ) -> str:
        remote_target = f"origin/{target_branch}"
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            git(worktree, "fetch", "origin", target_branch, env=git_env)
            if changed:
                rebase = subprocess.run(
                    ["git", "-C", str(worktree), "rebase", remote_target],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=git_env,
                    check=False,
                )
                if rebase.returncode:
                    subprocess.run(
                        ["git", "-C", str(worktree), "rebase", "--abort"],
                        capture_output=True,
                        text=True,
                        env=git_env,
                        check=False,
                    )
                    output = (rebase.stderr or rebase.stdout or "").strip()
                    raise RuntimeError(
                        f"{repository_name} 无法合入最新 {target_branch}，请解决代码冲突后重新发起：{output[-1200:]}"
                    )

                # 功能分支此前已推送；rebase 可能改变提交号，只允许覆盖本任务自己的远端功能分支。
                git(worktree, "push", "--no-verify", "--force-with-lease", "origin", feature_branch, env=git_env)
                push = subprocess.run(
                    ["git", "-C", str(worktree), "push", "--no-verify", "origin", f"HEAD:refs/heads/{target_branch}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=git_env,
                    check=False,
                )
                if push.returncode:
                    output = (push.stderr or push.stdout or "").strip()
                    concurrent_update = any(
                        marker in output.casefold()
                        for marker in ("non-fast-forward", "fetch first")
                    )
                    if concurrent_update and attempt < max_attempts:
                        continue
                    raise RuntimeError(
                        f"{repository_name} 提交至 {target_branch} 失败；未执行打包：{output[-1600:]}"
                    )
            else:
                git(worktree, "merge", "--ff-only", remote_target, env=git_env)

            git(worktree, "fetch", "origin", target_branch, env=git_env)
            local_commit = git(worktree, "rev-parse", "HEAD", env=git_env)
            remote_commit = git(worktree, "rev-parse", remote_target, env=git_env)
            if local_commit == remote_commit:
                return local_commit
            if not changed:
                git(worktree, "merge", "--ff-only", remote_target, env=git_env)
                return git(worktree, "rev-parse", "HEAD", env=git_env)
            if attempt == max_attempts:
                raise RuntimeError(
                    f"{repository_name} 的 {target_branch} 在提交后再次更新，无法确认构建基线；未执行打包"
                )

        raise RuntimeError(f"{repository_name} 无法同步最新 {target_branch}；未执行打包")

    def _create_pr(
        self,
        request_id: str,
        detail: dict,
        project: dict,
        worktree: Path | None,
        work_item: dict,
        branch: str,
        *,
        target_branch: str | None = None,
    ) -> dict:
        if project.get("simulation_mode"):
            pr_id = 8000 + int(work_item["id"]) % 1000
            url = f"{project['tfs_collection_url']}/{project['tfs_project']}/_git/demo/pullrequest/{pr_id}"
            return {"id": pr_id, "url": url}
        title = f"feat(#{work_item['id']}):{work_item['title'][:80]}"
        description = f"自动研发任务：{request_id}\n需求：#{work_item['id']}\n\n{detail.get('result_summary','')}"
        result = TfsClient(project["tfs_collection_url"]).create_pull_request(
            str(worktree), branch, target_branch or project.get("base_branch", "dev"), title, work_item["id"], description,
        )
        return {"id": int(result["PullRequestId"]), "url": result["WebUrl"]}

    def _deliver_local_package(self, request_id: str, detail: dict, project: dict, worktree: Path | None, work_item: dict) -> None:
        self.store.update_request(request_id, status=RunStatus.BUILDING.value, current_step="deliver", progress=74)
        self.store.update_step(request_id, "deliver", "running", "代码已提交目标分支；正在基于最新目标分支强制构建并归集交付物")
        if project.get("simulation_mode"):
            self._create_demo_artifacts(request_id, include_package=True)
        else:
            build_command = project.get("build_command", "").strip()
            if not build_command:
                raise RuntimeError("本地打包交付方式必须配置 build_command")
            output = run_command(build_command, worktree)
            self.store.add_event(request_id, "build.completed", "本地构建命令执行成功", metadata={"output_tail": output[-1000:]})
            self.artifacts.collect_packages(request_id, worktree, project.get("package_patterns", []))
        self._complete_delivery(request_id)

    def _finish_merged_request(self, request_id: str, pr: dict) -> None:
        detail = self.store.detail(request_id)
        self.store.update_request(request_id, status=RunStatus.CAPTURING.value, progress=91, merge_commit=pr.get("merge_commit"))
        self.store.add_event(request_id, "pr.merged", f"PR #{pr['id']} 已合并", metadata=pr)
        screenshot_id = self.artifacts.create_merge_evidence(
            request_id,
            pr,
            detail["pr_url"],
            repository_name=str(pr.get("repository") or detail["policy_snapshot"].get("project_key") or "repository"),
        )
        self.store.add_event(
            request_id,
            "pr.screenshot_captured",
            f"PR #{pr['id']} 的真实浏览器页面截图已生成",
            metadata={"artifact_id": screenshot_id},
        )
        if detail["policy_snapshot"].get("simulation_mode"):
            self._create_demo_artifacts(request_id, include_package=False)
        self._complete_delivery(request_id)

    def _finish_merged_repositories(
        self,
        request_id: str,
        repository_states: list[dict],
        completed: list[tuple[dict, dict]],
    ) -> None:
        self.store.update_request(request_id, status=RunStatus.CAPTURING.value, progress=91)
        merge_commits: list[str] = []
        for state, pr in completed:
            state["status"] = "completed"
            state["merge_commit"] = pr.get("merge_commit")
            if pr.get("merge_commit"):
                merge_commits.append(pr["merge_commit"])
            self.store.add_event(
                request_id,
                "pr.merged",
                f"{state['name']} 的 PR #{pr['id']} 已合并",
                metadata=pr,
            )
            screenshot_id = self.artifacts.create_merge_evidence(
                request_id,
                pr,
                state.get("pr_url", ""),
                repository_name=state["name"],
            )
            state["merge_screenshot_artifact_id"] = screenshot_id
            self.store.add_event(
                request_id,
                "pr.screenshot_captured",
                f"{state.get('repository_short_name') or repository_short_name(state['name'])} · PR #{pr['id']} 的真实浏览器页面截图已生成",
                metadata={"artifact_id": screenshot_id, "repository": state["name"]},
            )
        self.store.update_request(
            request_id,
            repository_states=repository_states,
            merge_commit=merge_commits[0] if merge_commits else None,
        )
        self._complete_delivery(request_id)

    def _complete_delivery(self, request_id: str) -> None:
        self.store.update_request(request_id, status=RunStatus.DELIVERING.value, progress=96, completed_at=utc_now())
        detail = self.store.detail(request_id)
        project = detail["policy_snapshot"]
        if detail["delivery_mode"] in REVIEW_DELIVERY_MODES:
            self._ensure_license_application(request_id, detail, project)
            detail = self.store.detail(request_id)
        if not project.get("simulation_mode"):
            manifest = self.artifacts.delivery_manifest_html(detail)
            tfs_result = TfsClient(project["tfs_collection_url"]).complete_delivery(
                detail["work_item_id"], manifest, actual_version="V1.0"
            )
            self.store.add_event(
                request_id,
                "tfs.delivery_completed",
                f"TFS 已更新为{tfs_result['state']}，实际交付版本 V1.0，交付产物路径已写入",
                metadata=tfs_result,
            )
        self._send_status_email(request_id, action_required=False)
        self.store.update_request(request_id, status=RunStatus.DELIVERED.value, current_step="deliver", progress=100, completed_at=utc_now())
        self.store.update_step(request_id, "deliver", "completed", "交付产物与通知邮件已生成，TFS 状态及实际交付版本已更新")
        self.store.add_event(request_id, "delivery.completed", "需求研发交付完成")

    def _ensure_license_application(self, request_id: str, detail: dict, project: dict) -> None:
        existing = next(
            (item for item in detail.get("artifacts", []) if item.get("kind") == "license_request"),
            None,
        )
        if existing:
            return

        if project.get("simulation_mode"):
            license_id = 900000 + int(detail["work_item_id"]) % 100000
            license_url = (
                f"{project['tfs_collection_url']}/{settings.tfs_license_project}"
                f"/_workitems/edit/{license_id}"
            )
            self.store.add_artifact(
                request_id,
                "license_request",
                f"License 授权申请 #{license_id}",
                external_url=license_url,
            )
            self.store.add_event(
                request_id,
                "license.created",
                f"演示模式：License 授权申请 #{license_id} 已生成",
                metadata={"license_id": license_id, "url": license_url},
            )
            return

        screenshots = [
            item
            for item in visible_delivery_artifacts(detail["delivery_mode"], detail.get("artifacts", []))
            if item.get("kind") == "merge_screenshot"
        ]
        if not screenshots:
            raise RuntimeError("截图交付缺少 PR 合并截图，无法创建 License 授权申请")

        local_candidates = sorted(self.artifacts.request_dir(request_id).glob("pr-*-merged-*.png"))
        sources: list[tuple[str, str]] = []
        for index, artifact in enumerate(screenshots):
            local_path = str(artifact.get("local_path") or "")
            if local_path and Path(local_path).is_file():
                source = local_path
            else:
                pr_number = re.search(r"PR #(\d+)", str(artifact.get("name") or ""))
                matching = (
                    list(self.artifacts.request_dir(request_id).glob(f"pr-{pr_number.group(1)}-merged-*.png"))
                    if pr_number
                    else []
                )
                if matching:
                    source = str(matching[0])
                elif index < len(local_candidates):
                    source = str(local_candidates[index])
                else:
                    source = self.artifacts.artifact_url(artifact)
            sources.append((str(artifact.get("name") or f"PR 合并截图 {index + 1}"), source))

        license_item = TfsClient(project["tfs_collection_url"]).create_license_application(
            request_id=request_id,
            source_work_item_id=int(detail["work_item_id"]),
            delivery_project_name=str(project.get("name") or detail.get("project_name") or "网络发令"),
            screenshot_sources=sources,
        )
        self.store.add_artifact(
            request_id,
            "license_request",
            f"License 授权申请 #{license_item['id']} · {license_item['title']}",
            external_url=license_item["url"],
        )
        self.store.add_event(
            request_id,
            "license.created" if license_item.get("created") else "license.reused",
            (
                f"License 授权申请 #{license_item['id']} 已创建并指派给周丹平"
                if license_item.get("created")
                else f"已复用现有 License 授权申请 #{license_item['id']}"
            ),
            metadata=license_item,
        )

    def _send_status_email(self, request_id: str, *, action_required: bool, terminal: bool = False) -> None:
        if self.store.remote:
            self.store.notify(request_id, action_required=action_required, terminal=terminal)
            return
        detail = self.store.detail(request_id)
        if terminal and detail.get("email_sent_at"):
            return
        project = detail["policy_snapshot"]
        recipients = list(detail.get("notification_emails") or [detail["requester_email"]])
        recipients.extend(email.strip() for email in project.get("notification_cc", "").split(",") if email.strip())
        recipients = list(dict.fromkeys(email.strip() for email in recipients if email and email.strip()))
        terminal_status = detail["status"] if terminal else None
        subject = self.mailer.delivery_subject(
            detail,
            action_required=action_required,
            terminal_status=terminal_status,
        )
        html_body = self.mailer.delivery_html(
            detail,
            action_required=action_required,
            terminal_status=terminal_status,
        )
        if not self.mailer.configured():
            name = (
                "terminal-email-preview.html"
                if terminal
                else ("review-email-preview.html" if action_required else "delivery-email-preview.html")
            )
            path = self.artifacts.request_dir(request_id) / name
            path.write_text(html_body, encoding="utf-8")
            self.store.add_artifact(request_id, "email_preview", path.name, str(path))
            self.store.add_event(request_id, "mail.preview", "SMTP 尚未配置，已生成邮件预览", level="warning")
            if terminal:
                self.store.update_request(request_id, email_sent_at=utc_now())
            return
        attachments = [Path(item["local_path"]) for item in detail["artifacts"] if item["kind"] == "merge_screenshot" and item["local_path"]]
        self.mailer.send(to=recipients, subject=subject, html_body=html_body, attachments=attachments)
        if terminal or not action_required:
            self.store.update_request(request_id, email_sent_at=utc_now())

    def _send_terminal_email(self, request_id: str) -> None:
        try:
            self._send_status_email(request_id, action_required=False, terminal=True)
        except Exception as exc:
            logger.exception("任务终态通知邮件发送失败 request_id=%s", request_id)
            self.store.add_event(
                request_id,
                "mail.terminal_failed",
                f"任务终态通知邮件发送失败：{str(exc)[:1000]}",
                level="error",
            )

    def _simulate_development(self, request_id: str, work_item: dict) -> dict:
        self.store.add_event(request_id, "devcore.thread", "演示模式：DevCore 研发线程已启动")
        self.store.add_event(request_id, "devcore.event", "演示模式：完成需求分析、代码修改与风险检查")
        return {
            "decision": "completed",
            "summary": f"已完成“{work_item['title']}”的演示开发，并验证交付流程。",
            "changed_files": ["src/demo/FeatureService.java", "config/application-demo.yml", "sql/upgrade.sql"],
            "acceptance_mapping": ["自动研发入口可用", "交付产物可追踪"],
            "risks": [], "sql_changes": ["sql/upgrade.sql"], "config_changes": ["config/application-demo.yml"],
            "database_operations": [], "supplement_requests": [],
        }

    def _create_demo_artifacts(self, request_id: str, *, include_package: bool) -> None:
        target = self.artifacts.request_dir(request_id)
        if include_package:
            sql = target / "sql" / "upgrade.sql"
            config = target / "config" / "application-demo.yml"
            sql.parent.mkdir(parents=True, exist_ok=True)
            config.parent.mkdir(parents=True, exist_ok=True)
            sql.write_text("-- 演示 SQL，正式任务将交付真实变更\nSELECT 1;\n", encoding="utf-8")
            config.write_text("autodev:\n  enabled: true\n", encoding="utf-8")
            self.store.add_artifact(request_id, "sql", "upgrade.sql", str(sql))
            self.store.add_artifact(request_id, "config", "application-demo.yml", str(config))
            archive = target / "package" / "demo-delivery.zip"
            archive.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
                package.writestr("README.txt", "全自助研发演示交付包")
            self.store.add_artifact(request_id, "package", archive.name, str(archive))

    def _schedule_next_poll(self, request_id: str, *, backoff: bool = False) -> None:
        delay = settings.poll_seconds * (3 if backoff else 1)
        self.store.update_request(request_id, next_poll_at=(datetime.now(UTC) + timedelta(seconds=delay)).isoformat())

    @staticmethod
    def _plain_text(value: str) -> str:
        return re.sub(r"<[^>]+>", " ", value or "").replace("&nbsp;", " ").strip()

    def _check_cancelled(self, request_id: str) -> None:
        if self.store.get_status(request_id) == RunStatus.CANCELLED.value:
            raise Cancelled()

    def _cancel(self, request_id: str) -> None:
        self.store.update_request(request_id, status=RunStatus.CANCELLED.value, completed_at=utc_now())
        self.store.add_event(request_id, "request.cancelled", "任务已取消", level="warning")
        self._send_terminal_email(request_id)

    def _fail(self, request_id: str, exc: Exception) -> None:
        message = str(exc)[:3000]
        logger.exception("研发任务失败 request_id=%s: %s", request_id, message)
        detail = self.store.detail(request_id)
        self.store.update_request(request_id, status=RunStatus.FAILED.value, error_message=message, completed_at=utc_now())
        if detail and detail.get("current_step"):
            self.store.update_step(request_id, detail["current_step"], "failed", message[:1000])
        self.store.add_event(request_id, "request.failed", message, level="error")
        self._send_terminal_email(request_id)


worker = Worker()
