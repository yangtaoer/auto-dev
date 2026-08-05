from __future__ import annotations

import os
import re
import subprocess
import threading
import zipfile
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import settings
from .db import utc_now
from .domain import DeliveryMode, RunStatus
from .services.codex_runner import CodexRunner
from .services.delivery import ArtifactService, Mailer, changed_files, git, protected_changes, run_command
from .services.tfs import TfsClient
from .services.process_env import git_authenticated_env, sanitized_process_env
from .store import LocalStore


logger = logging.getLogger("autodev.worker")


class Cancelled(RuntimeError):
    pass


class Worker:
    def __init__(self, store=None) -> None:
        self.store = store or LocalStore()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.current_request_id: str | None = None
        public_base_url = settings.cloud_url or settings.public_base_url if self.store.remote else settings.public_base_url
        self.artifacts = ArtifactService(
            recorder=self.store.add_artifact,
            detail_loader=self.store.detail,
            public_base_url=public_base_url,
        )
        self.mailer = Mailer()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="autodev-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.process_once()
            except Exception:
                # 单个任务的异常会在 run_request/poll_merge 中落库；这里避免工作线程退出。
                logger.exception("任务轮询循环发生异常")
            self.stop_event.wait(settings.poll_seconds)

    def process_once(self) -> bool:
        queued = self.store.next_queued()
        if queued:
            logger.info("开始执行研发任务 request_id=%s", queued)
            self.current_request_id = queued
            try:
                self.run_request(queued)
            finally:
                self.current_request_id = None
            return True
        waiting = self.store.next_waiting()
        if waiting:
            logger.info("检查 PR 合并状态 request_id=%s", waiting)
            self.current_request_id = waiting
            try:
                self.poll_merge(waiting)
            finally:
                self.current_request_id = None
            return True
        return False

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
            if project.get("simulation_mode"):
                worktree, base_commit, branch = None, "demo-base-commit", f"feature/{work_item['id']}-demo"
            else:
                worktree, base_commit, branch = self._prepare_worktree(request_id, work_item, project)
            self.store.update_request(request_id, branch_name=branch, base_commit=base_commit)
            self.store.update_step(request_id, "prepare", "completed", f"分支 {branch} 已从最新 origin/{project.get('base_branch','dev')} 创建")
            self._check_cancelled(request_id)

            self.store.update_request(request_id, status=RunStatus.DEVELOPING.value, current_step="develop", progress=32)
            self.store.update_step(request_id, "develop", "running", "Codex 正在分析需求并修改代码")
            if project.get("simulation_mode"):
                result = self._simulate_development(request_id, work_item)
                codex_thread_id = "demo-thread"
            else:
                run = CodexRunner().run(
                    cwd=worktree,
                    work_item=work_item,
                    project=project,
                    on_event=lambda event_type, message: self.store.add_event(request_id, event_type, message),
                )
                result, codex_thread_id = run.result, run.thread_id
                paths = changed_files(worktree, base_commit)
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
                if not paths:
                    raise RuntimeError("Codex 执行完成，但没有产生代码变更")
            summary = result.get("summary", "")
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
            self.store.add_event(request_id, "development.completed", summary or "Codex 自动研发完成", metadata=result)
            self._check_cancelled(request_id)

            self.store.update_request(request_id, status=RunStatus.SUBMITTING.value, current_step="submit", progress=62)
            self.store.update_step(request_id, "submit", "running", "正在提交并推送代码")
            if project.get("simulation_mode"):
                commit_hash = "demo" + request_id.replace("-", "")[:8]
            else:
                commit_hash = self._commit_and_push(worktree, detail, work_item, project, branch)
                self.artifacts.collect_changed_assets(request_id, worktree, base_commit, project)
            self.store.update_request(request_id, commit_hash=commit_hash)

            if mode == DeliveryMode.LOCAL_PACKAGE:
                self.store.update_step(request_id, "submit", "completed", f"代码已推送，commit {commit_hash[:12]}")
                self._deliver_local_package(request_id, detail, project, worktree, work_item)
                return

            verification_command = project.get("build_command", "").strip()
            if mode == DeliveryMode.SICHUAN_AUTO_REVIEW and not project.get("simulation_mode") and not verification_command:
                raise RuntimeError("四川自动审核交付必须配置构建/校验命令，校验通过后才允许服务账号批准")
            if not project.get("simulation_mode") and verification_command:
                output = run_command(verification_command, worktree)
                self.store.add_event(request_id, "verification.completed", "PR 前构建/校验通过", metadata={"output_tail": output[-1000:]})

            pr = self._create_pr(request_id, detail, project, worktree, work_item, branch)
            self.store.update_request(request_id, pr_id=pr["id"], pr_url=pr["url"], progress=76)
            self.store.update_step(request_id, "submit", "completed", f"PR #{pr['id']} 已创建并关联需求")
            self.store.add_artifact(request_id, "pull_request", f"PR #{pr['id']}", external_url=pr["url"])

            if mode == DeliveryMode.SICHUAN_AUTO_REVIEW:
                if project.get("simulation_mode"):
                    self.store.add_event(request_id, "pr.approved", "演示模式：四川审核服务账号已批准 PR")
                else:
                    TfsClient(project["tfs_collection_url"]).approve_pull_request(project["repository_path"], pr["id"])
                    self.store.add_event(request_id, "pr.approved", "四川审核服务账号已批准 PR；操作已记录审计")
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
            self.store.add_event(request_id, "pr.waiting_merge", f"开始监控 PR #{pr['id']} 合并状态")
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
            else:
                pr = TfsClient(project["tfs_collection_url"]).get_pull_request(project["repository_path"], detail["pr_id"])
            if pr["status"] == "completed":
                self._finish_merged_request(request_id, pr)
            elif pr["status"] == "abandoned":
                raise RuntimeError(f"PR #{detail['pr_id']} 已被放弃，无法完成交付")
            else:
                self.store.add_event(request_id, "pr.polled", f"PR #{detail['pr_id']} 尚未合并")
                self._schedule_next_poll(request_id)
        except Exception as exc:
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
        if not item.get("description"):
            raise RuntimeError("需求描述为空，无法自动研发")
        return item

    def _prepare_worktree(self, request_id: str, work_item: dict, project: dict) -> tuple[Path, str, str]:
        repo = Path(project.get("repository_path", "")).resolve()
        if not repo.is_dir() or not (repo / ".git").exists():
            raise RuntimeError(f"项目仓库路径无效：{repo}")
        base_branch = project.get("base_branch", "dev")
        fetch_env = git_authenticated_env(settings.tfs_pat) if settings.tfs_pat else sanitized_process_env()
        subprocess.run(
            ["git", "-C", str(repo), "fetch", "origin", base_branch],
            check=True,
            capture_output=True,
            env=fetch_env,
        )
        base_commit = git(repo, "rev-parse", f"origin/{base_branch}")
        branch = f"feature/{work_item['id']}-{settings.tfs_user_alias}"
        branch_check = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            env=sanitized_process_env(),
        )
        if branch_check.returncode == 0:
            suffix = datetime.now().strftime("%Y%m%d-%H%M")
            branch = f"{branch}-{suffix}"
        worktree = settings.worktree_dir / request_id / repo.name
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if worktree.exists():
            raise RuntimeError(f"任务工作区已存在，需要人工确认后重试：{worktree}")
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(worktree), f"origin/{base_branch}"],
            check=True,
            capture_output=True,
            env=sanitized_process_env(),
        )
        self.store.add_event(request_id, "workspace.prepared", f"隔离工作区：{worktree}", metadata={"base_commit": base_commit})
        return worktree, base_commit, branch

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

    def _create_pr(self, request_id: str, detail: dict, project: dict, worktree: Path | None, work_item: dict, branch: str) -> dict:
        if project.get("simulation_mode"):
            pr_id = 8000 + int(work_item["id"]) % 1000
            url = f"{project['tfs_collection_url']}/{project['tfs_project']}/_git/demo/pullrequest/{pr_id}"
            return {"id": pr_id, "url": url}
        title = f"feat(#{work_item['id']}):{work_item['title'][:80]}"
        description = f"自动研发任务：{request_id}\n需求：#{work_item['id']}\n\n{detail.get('result_summary','')}"
        result = TfsClient(project["tfs_collection_url"]).create_pull_request(
            str(worktree), branch, project.get("base_branch", "dev"), title, work_item["id"], description,
        )
        return {"id": int(result["PullRequestId"]), "url": result["WebUrl"]}

    def _deliver_local_package(self, request_id: str, detail: dict, project: dict, worktree: Path | None, work_item: dict) -> None:
        self.store.update_request(request_id, status=RunStatus.BUILDING.value, current_step="deliver", progress=74)
        self.store.update_step(request_id, "deliver", "running", "正在本机打包并归集交付物")
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
        self.artifacts.create_merge_evidence(request_id, pr, detail["pr_url"])
        if detail["policy_snapshot"].get("simulation_mode"):
            self._create_demo_artifacts(request_id, include_package=False)
        self._complete_delivery(request_id)

    def _complete_delivery(self, request_id: str) -> None:
        self.store.update_request(request_id, status=RunStatus.DELIVERING.value, progress=96, completed_at=utc_now())
        self.artifacts.create_report(request_id)
        self._send_status_email(request_id, action_required=False)
        self.store.update_request(request_id, status=RunStatus.DELIVERED.value, current_step="deliver", progress=100, completed_at=utc_now())
        self.store.update_step(request_id, "deliver", "completed", "交付邮件和产物已生成")
        self.store.add_event(request_id, "delivery.completed", "需求研发交付完成")

    def _send_status_email(self, request_id: str, *, action_required: bool) -> None:
        if self.store.remote:
            self.store.notify(request_id, action_required=action_required)
            return
        detail = self.store.detail(request_id)
        project = detail["policy_snapshot"]
        recipients = [detail["requester_email"]]
        recipients.extend(email.strip() for email in project.get("notification_cc", "").split(",") if email.strip())
        subject_prefix = "[待审核]" if action_required else "[已交付]"
        subject = f"{subject_prefix} #{detail['work_item_id']} {detail.get('title','')}"
        html_body = self.mailer.delivery_html(detail, action_required=action_required)
        if not self.mailer.configured():
            path = self.artifacts.request_dir(request_id) / ("review-email-preview.html" if action_required else "delivery-email-preview.html")
            path.write_text(html_body, encoding="utf-8")
            self.store.add_artifact(request_id, "email_preview", path.name, str(path))
            self.store.add_event(request_id, "mail.preview", "SMTP 尚未配置，已生成邮件预览", level="warning")
            return
        attachments = [Path(item["local_path"]) for item in detail["artifacts"] if item["kind"] == "merge_screenshot" and item["local_path"]]
        self.mailer.send(to=recipients, subject=subject, html_body=html_body, attachments=attachments)
        self.store.update_request(request_id, email_sent_at=utc_now())

    def _simulate_development(self, request_id: str, work_item: dict) -> dict:
        self.store.add_event(request_id, "codex.thread", "演示模式：Codex 研发线程已启动")
        self.store.add_event(request_id, "codex.event", "演示模式：完成需求分析、代码修改与风险检查")
        return {
            "summary": f"已完成“{work_item['title']}”的演示开发，并验证交付流程。",
            "changed_files": ["src/demo/FeatureService.java", "config/application-demo.yml", "sql/upgrade.sql"],
            "acceptance_mapping": ["自动研发入口可用", "交付产物可追踪"],
            "risks": [], "sql_changes": ["sql/upgrade.sql"], "config_changes": ["config/application-demo.yml"],
        }

    def _create_demo_artifacts(self, request_id: str, *, include_package: bool) -> None:
        target = self.artifacts.request_dir(request_id)
        sql = target / "sql" / "upgrade.sql"
        config = target / "config" / "application-demo.yml"
        sql.parent.mkdir(parents=True, exist_ok=True)
        config.parent.mkdir(parents=True, exist_ok=True)
        sql.write_text("-- 演示 SQL，正式任务将交付真实变更\nSELECT 1;\n", encoding="utf-8")
        config.write_text("autodev:\n  enabled: true\n", encoding="utf-8")
        self.store.add_artifact(request_id, "sql", "upgrade.sql", str(sql))
        self.store.add_artifact(request_id, "config", "application-demo.yml", str(config))
        if include_package:
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

    def _fail(self, request_id: str, exc: Exception) -> None:
        message = str(exc)[:3000]
        logger.exception("研发任务失败 request_id=%s: %s", request_id, message)
        detail = self.store.detail(request_id)
        self.store.update_request(request_id, status=RunStatus.FAILED.value, error_message=message, completed_at=utc_now())
        if detail and detail.get("current_step"):
            self.store.update_step(request_id, detail["current_step"], "failed", message[:1000])
        self.store.add_event(request_id, "request.failed", message, level="error")


worker = Worker()
