from __future__ import annotations

import fnmatch
import base64
import hashlib
import html
import json
import mimetypes
import re
import shutil
import smtplib
import ssl
import subprocess
import os
import signal
import time
from datetime import UTC, datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime as format_rfc_datetime, make_msgid
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from ..config import settings
from ..db import add_artifact, request_detail
from ..domain import DELIVERY_MODE_LABELS, STATUS_LABELS, DeliveryMode, RunStatus, TaskType, visible_delivery_artifacts
from .blocker_summary import summarize_blocker
from .process_env import sanitized_process_env
from .command_logs import clean_log, decode_output, command_failure


BRAND_MARK_CID = "autodev-brand-mark"
BRAND_MARK_PATH = Path(__file__).resolve().parents[1] / "static" / "brand" / "autodev-email-mark.png"


def run_command(
    command: str,
    cwd: Path,
    timeout_minutes: int = 60,
    *,
    env_overrides: dict[str, str] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> str:
    process_env = sanitized_process_env()
    if env_overrides:
        process_env.update({str(key): str(value) for key, value in env_overrides.items()})
    if cancel_check:
        cancel_check()
        with subprocess.Popen(command, cwd=cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=process_env,
                              start_new_session=os.name != 'nt',
                              creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0) as process:
            deadline = time.monotonic() + timeout_minutes * 60
            try:
                while True:
                    cancel_check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('构建命令执行超时')
                    try:
                        stdout, stderr = process.communicate(timeout=2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                if process.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name != 'nt':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.communicate(timeout=10)
                raise
            cancel_check()
            if process.returncode:
                raise command_failure(process.returncode, stdout, stderr, process_env.get('AUTODEV_REQUEST_ID', ''))
            return clean_log(decode_output(stdout) + '\n' + decode_output(stderr))[-5000:]
    result = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        timeout=timeout_minutes * 60,
        env=process_env,
        check=False,
    )
    output = clean_log(decode_output(result.stdout) + "\n" + decode_output(result.stderr))
    if result.returncode:
        raise command_failure(result.returncode, result.stdout, result.stderr, process_env.get('AUTODEV_REQUEST_ID', ''))
    return output[-5000:]


def git(worktree: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or sanitized_process_env(),
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} 失败: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def changed_files(worktree: Path, base_commit: str) -> list[str]:
    tracked = git(worktree, "diff", "--name-only", base_commit, "--")
    untracked = git(worktree, "ls-files", "--others", "--exclude-standard")
    paths = [line.replace("\\", "/") for output in (tracked, untracked) for line in output.splitlines() if line.strip()]
    return list(dict.fromkeys(paths))


def added_files(worktree: Path, base_commit: str) -> list[str]:
    tracked = git(worktree, "diff", "--name-only", "--diff-filter=A", base_commit, "--")
    untracked = git(worktree, "ls-files", "--others", "--exclude-standard")
    paths = [line.replace("\\", "/") for output in (tracked, untracked) for line in output.splitlines() if line.strip()]
    return list(dict.fromkeys(paths))


def menu_link_from_view_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    match = re.search(
        r"(?:^|/)tbp_config/runtime/module/(?P<module>[^/]+)/views/(?P<view>[^/]+)\.view\.xml$",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    return f"/{match.group('module')}/views/{match.group('view')}"


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def protected_changes(paths: list[str], patterns: list[str]) -> list[str]:
    return [path for path in paths if matches_any(path, patterns)]


def repository_short_name(repository_name: str) -> str:
    """Build the compact repository label used by screenshot deliverables and the UI."""
    value = str(repository_name or "repository").strip().strip("/\\")
    value = Path(value).name or "repository"
    for prefix in ("dcsd-", "th-dc-biz-"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
            break
    for suffix in ("-sichuancd-dm", "-sichuancd", "-dm"):
        if value.lower().endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value or "repository"


class ArtifactService:
    def __init__(
        self,
        *,
        recorder: Callable[[str, str, str, str, str], int] | None = None,
        detail_loader: Callable[[str], dict[str, Any] | None] | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self.recorder = recorder or add_artifact
        self.detail_loader = detail_loader or request_detail
        self.public_base_url = public_base_url or settings.public_base_url

    def record(self, request_id: str, kind: str, name: str, local_path: str = "", external_url: str = "") -> int:
        return self.recorder(request_id, kind, name, local_path, external_url)

    def request_dir(self, request_id: str) -> Path:
        path = settings.delivery_dir / request_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def collect_changed_assets(
        self,
        request_id: str,
        worktree: Path,
        base_commit: str,
        project: dict,
        *,
        repository_name: str = "",
    ) -> list[int]:
        ids: list[int] = []
        paths = changed_files(worktree, base_commit)
        groups = {
            "sql": project.get("sql_patterns", []),
            "config": project.get("config_patterns", []),
        }
        artifact_policy = project.get("artifact_policy") or {}
        allowed_changed_kinds = set(artifact_policy.get("allowed_changed_asset_kinds") or groups)
        for kind, patterns in groups.items():
            if kind not in allowed_changed_kinds:
                continue
            for relative in paths:
                source = worktree / relative
                if source.is_file() and matches_any(relative, patterns):
                    delivery_relative = Path(repository_name) / relative if repository_name else Path(relative)
                    target = self.request_dir(request_id) / kind / delivery_relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    ids.append(self.record(request_id, kind, delivery_relative.as_posix(), str(target)))
        return ids

    def collect_menu_links(self, request_id: str, worktree: Path, base_commit: str) -> list[int]:
        ids: list[int] = []
        existing = {
            str(item.get("name") or "")
            for item in (self.detail_loader(request_id) or {}).get("artifacts", [])
            if item.get("kind") == "menu_link"
        }
        for relative in added_files(worktree, base_commit):
            menu_link = menu_link_from_view_path(relative)
            if not menu_link or menu_link in existing:
                continue
            ids.append(self.record(request_id, "menu_link", menu_link, external_url=menu_link))
            existing.add(menu_link)
        return ids

    def artifact_url(self, artifact: dict) -> str:
        return str(artifact.get("external_url") or f"{self.public_base_url}/api/artifacts/{artifact['id']}")

    def delivery_manifest_html(self, detail: dict) -> str:
        kind_labels = {
            "package": "安装包",
            "package_note": "构建说明",
            "sql": "SQL 脚本",
            "config": "配置文件",
            "merge_screenshot": "PR 合并截图",
            "menu_link": "新增视图菜单链接",
            "license_request": "License 授权申请",
            "release_artifact": "自动发版产物",
            "analysis_report": "问题分析报告",
            "verification_report": "历史实现验证报告",
            "delivery_manifest": "交付校验清单",
        }
        lines: list[str] = []
        items = (
            list(detail.get("artifacts", []))
            if detail.get("joint_children")
            else visible_delivery_artifacts(
                str(detail.get("delivery_mode") or ""),
                detail.get("artifacts", []),
                detail.get("delivery_options"),
            )
        )
        for item in items:
            safe_name = html.escape(str(item.get("name") or "交付产物"))
            label = html.escape(kind_labels.get(str(item.get("kind") or ""), str(item.get("kind") or "交付产物")))
            if item.get("kind") == "menu_link":
                value = f"<code>{safe_name}</code>"
            else:
                url = html.escape(self.artifact_url(item), quote=True)
                value = f'<a href="{url}">{safe_name}</a>'
            lines.append(f"<li><strong>{label}：</strong>{value}</li>")
        if not lines:
            lines.append("<li>本次交付无独立下载产物，请查看 AutoDev 任务记录。</li>")
        console_url = html.escape(f"{self.public_base_url}/", quote=True)
        return (
            "<div><p><strong>AutoDev 自动研发交付</strong></p><ul>"
            + "".join(lines)
            + f'</ul><p><a href="{console_url}">打开 AutoDev 研发控制台</a></p></div>'
        )

    def collect_packages(
        self,
        request_id: str,
        worktree: Path,
        patterns: list[str],
        *,
        artifact_policy: dict[str, Any] | None = None,
    ) -> list[int]:
        ids: list[int] = []
        policy = artifact_policy or {}
        allowed_extensions = {str(item).casefold() for item in policy.get("allowed_package_extensions") or []}
        forbidden_extensions = {str(item).casefold() for item in policy.get("forbidden_standalone_extensions") or []}
        for pattern in patterns:
            for source in worktree.glob(pattern):
                if not source.is_file():
                    continue
                suffix = source.suffix.casefold()
                if allowed_extensions and suffix not in allowed_extensions:
                    raise RuntimeError(f"交付包 {source.name} 的扩展名不在项目白名单：{', '.join(sorted(allowed_extensions))}")
                if suffix in forbidden_extensions:
                    raise RuntimeError(f"项目禁止把 {suffix} 文件作为独立交付物：{source.name}")
                relative = source.relative_to(worktree)
                target = self.request_dir(request_id) / "package" / relative.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                ids.append(self.record(request_id, "package", relative.name, str(target)))
        if not ids:
            if policy.get("require_packages"):
                raise RuntimeError("构建成功，但项目要求的 package_patterns 未匹配到任何正式交付包")
            marker = self.request_dir(request_id) / "package" / "README.txt"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("构建成功，但配置的 package_patterns 未匹配到文件。\n", encoding="utf-8")
            ids.append(self.record(request_id, "package_note", marker.name, str(marker)))
        return ids

    def validate_artifact_policy(self, request_id: str, project: dict[str, Any]) -> list[str]:
        policy = project.get("artifact_policy") or {}
        if not policy:
            return []
        detail = self.detail_loader(request_id) or {}
        visible = visible_delivery_artifacts(
            str(detail.get("delivery_mode") or ""),
            detail.get("artifacts", []),
            detail.get("delivery_options"),
        )
        blockers: list[str] = []
        allowed_kinds = set(policy.get("allowed_user_facing_kinds") or [])
        if allowed_kinds:
            unexpected = sorted({str(item.get("kind") or "") for item in visible if item.get("kind") not in allowed_kinds})
            if unexpected:
                blockers.append("存在项目交付物白名单之外的类型：" + "、".join(unexpected))
        forbidden_extensions = {str(item).casefold() for item in policy.get("forbidden_standalone_extensions") or []}
        forbidden = [
            str(item.get("name") or "")
            for item in visible
            if Path(str(item.get("name") or "")).suffix.casefold() in forbidden_extensions
        ]
        if forbidden:
            blockers.append("存在禁止独立交付的文件：" + "、".join(forbidden))
        if policy.get("require_packages") and not any(item.get("kind") == "package" for item in visible):
            blockers.append("项目要求正式前后端交付包，但当前没有 package 产物")
        return blockers

    def create_delivery_validation_manifest(self, request_id: str, detail: dict[str, Any]) -> int:
        existing = next(
            (item for item in detail.get("artifacts", []) if item.get("kind") == "delivery_manifest"),
            None,
        )
        if existing:
            return int(existing["id"])
        request_dir = self.request_dir(request_id)
        checksums = []
        for path in sorted(request_dir.rglob("*")):
            if not path.is_file() or path.name == "delivery-validation-manifest.json":
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            checksums.append(
                {
                    "path": path.relative_to(request_dir).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": digest,
                }
            )
        manifest = {
            "request_id": request_id,
            "work_item_id": detail.get("work_item_id"),
            "project": detail.get("project_name") or (detail.get("policy_snapshot") or {}).get("name"),
            "generated_at": datetime.now(UTC).isoformat(),
            "repositories": [
                {
                    "name": state.get("name"),
                    "target_branch": state.get("base_branch"),
                    "merge_commit": state.get("merge_commit"),
                    "build_commit": state.get("build_commit") or state.get("commit_hash"),
                }
                for state in detail.get("repository_states") or []
            ],
            "artifact_policy": (detail.get("policy_snapshot") or {}).get("artifact_policy") or {},
            "quality_gate": detail.get("quality_gate_result") or {},
            "files": checksums,
        }
        target = request_dir / "delivery-validation-manifest.json"
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.record(request_id, "delivery_manifest", target.name, str(target))

    def create_merge_evidence(
        self,
        request_id: str,
        pr: dict,
        pr_url: str,
        *,
        repository_name: str = "",
    ) -> int:
        target_dir = self.request_dir(request_id)
        repository_label = repository_short_name(repository_name or str(pr.get("repository") or "repository"))
        pr_id = str(pr.get("id") or "unknown")
        artifact_name = f"{repository_label} · PR #{pr_id} · 合并截图.png"
        detail = self.detail_loader(request_id) or {}
        for artifact in detail.get("artifacts", []):
            if artifact.get("kind") == "merge_screenshot" and artifact.get("name") == artifact_name:
                return int(artifact["id"])
        slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", repository_label).strip("-") or "repository"
        image_path = target_dir / f"pr-{pr_id}-merged-{slug}.png"
        if "/demo/" in pr_url:
            self._capture_demo_merge_page(request_id, pr, pr_url, repository_label, image_path)
        else:
            self._capture_real_pr_page(pr, pr_url, image_path)
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise RuntimeError(f"PR #{pr_id} 浏览器截图未生成")
        return self.record(request_id, "merge_screenshot", artifact_name, str(image_path))

    @staticmethod
    def _capture_real_pr_page(pr: dict, pr_url: str, image_path: Path) -> None:
        if not settings.tfs_pat:
            raise RuntimeError("未配置 TFS_PAT，无法打开真实 PR 页面截图")
        parsed_url = urlsplit(pr_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise RuntimeError(f"PR 地址无效：{pr_url}")
        authorization = base64.b64encode(f":{settings.tfs_pat}".encode("ascii")).decode("ascii")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    device_scale_factor=1,
                    ignore_https_errors=True,
                    locale="zh-CN",
                )
                origin = f"{parsed_url.scheme}://{parsed_url.netloc}"

                def authorize(route, request) -> None:
                    headers = dict(request.headers)
                    headers["authorization"] = f"Basic {authorization}"
                    route.continue_(headers=headers)

                context.route(f"{origin}/**", authorize)
                page = context.new_page()
                response = page.goto(pr_url, wait_until="domcontentloaded", timeout=60_000)
                if response and response.status >= 400:
                    raise RuntimeError(f"TFS PR 页面返回 HTTP {response.status}")
                pr_marker = str(pr.get("id") or "")
                page.wait_for_function(
                    """prId => {
                      const text = document.body?.innerText || '';
                      const merged = text.includes('已完成') || text.includes('完成此拉取请求') || text.includes('Completed');
                      return text.includes(prId) && merged;
                    }""",
                    arg=pr_marker,
                    timeout=45_000,
                )
                page.wait_for_timeout(1_500)
                visible_text = page.locator("body").inner_text(timeout=10_000)
                if pr_marker not in visible_text:
                    raise RuntimeError(f"TFS 页面未显示 PR #{pr_marker}")
                if not any(marker in visible_text for marker in ("已完成", "完成此拉取请求", "Completed")):
                    raise RuntimeError(f"TFS 页面未显示 PR #{pr_marker} 已合并")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(image_path), full_page=False)
                context.close()
                browser.close()
        except Exception as exc:
            image_path.unlink(missing_ok=True)
            raise RuntimeError(f"无法截取真实 TFS PR #{pr.get('id', '')} 页面：{exc}") from exc

    @staticmethod
    def _capture_demo_merge_page(
        request_id: str,
        pr: dict,
        pr_url: str,
        repository_label: str,
        image_path: Path,
    ) -> None:
        completed = pr.get("closed_date") or datetime.now(UTC).isoformat()
        page = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><style>
        *{{box-sizing:border-box}}body{{margin:0;width:1440px;height:900px;background:#07110e;color:#eef9f2;font-family:'Microsoft YaHei',sans-serif;padding:72px}}
        .seal{{color:#76f7ae;font:700 18px Consolas;letter-spacing:.22em}}h1{{font-size:56px;margin:32px 0 12px}}.sub{{color:#9fb6aa;font-size:20px}}
        .card{{margin-top:56px;border:1px solid #28493b;background:#0b1b15;padding:38px 44px;box-shadow:16px 16px 0 #102e22}}
        .row{{display:grid;grid-template-columns:220px 1fr;padding:17px 0;border-bottom:1px solid #1d382d;font-size:20px}}.row:last-child{{border:0}}
        .key{{color:#87aa99}}.ok{{display:inline-block;color:#06150e;background:#76f7ae;padding:7px 14px;font-weight:700}}
        .foot{{position:absolute;bottom:64px;color:#6f8f80;font:16px Consolas}}
        </style></head><body><div class='seal'>AutoDev / MERGE SCREENSHOT</div><h1>代码合并完成</h1><div class='sub'>演示环境中的 Pull Request 已完成</div>
        <div class='card'><div class='row'><div class='key'>状态</div><div><span class='ok'>COMPLETED</span></div></div>
        <div class='row'><div class='key'>PR</div><div>#{html.escape(str(pr.get('id','')))} · {html.escape(pr.get('title',''))}</div></div>
        <div class='row'><div class='key'>代码仓库</div><div>{html.escape(repository_label)}</div></div>
        <div class='row'><div class='key'>合并方向</div><div>{html.escape(pr.get('source_branch',''))} → {html.escape(pr.get('target_branch',''))}</div></div>
        <div class='row'><div class='key'>Merge commit</div><div>{html.escape(pr.get('merge_commit') or '—')}</div></div>
        <div class='row'><div class='key'>完成时间</div><div>{html.escape(completed)}</div></div></div>
        <div class='foot'>{html.escape(pr_url)} · 任务 {html.escape(request_id)}</div></body></html>"""
        html_path = image_path.with_suffix(".html")
        html_path.write_text(page, encoding="utf-8")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                page_obj = browser.new_page(viewport={"width": 1440, "height": 900})
                page_obj.goto(html_path.as_uri())
                page_obj.screenshot(path=str(image_path), full_page=True)
                browser.close()
        except Exception as exc:
            image_path.unlink(missing_ok=True)
            raise RuntimeError(f"无法生成演示 PR #{pr.get('id', '')} 合并截图：{exc}") from exc


class Mailer:
    def configured(self) -> bool:
        return bool(settings.smtp_host and settings.smtp_from)

    def sender_address(self) -> Address:
        return Address(display_name=settings.smtp_from_name or "AutoDev · 自主研发交付", addr_spec=settings.smtp_from)

    @staticmethod
    def _deduplicate_emails(values: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            email = str(value or "").strip()
            key = email.casefold()
            if email and key not in seen:
                result.append(email)
                seen.add(key)
        return result

    def admin_copy_recipients(self, primary_recipients: Iterable[str]) -> list[str]:
        primary = {str(value).strip().casefold() for value in primary_recipients if str(value).strip()}
        configured = re.split(r"[,;]", settings.task_admin_email)
        return [email for email in self._deduplicate_emails(configured) if email.casefold() not in primary]

    def delivery_subject(
        self,
        detail: dict,
        *,
        action_required: bool = False,
        terminal_status: str | None = None,
    ) -> str:
        analysis_task = detail.get("task_type") == TaskType.ANALYSIS.value
        terminal_labels = {
            "failed": "分析失败" if analysis_task else "执行失败",
            "cancelled": "任务已取消",
            "rejected": "准入驳回",
        }
        waiting_input = action_required and detail.get("status") == RunStatus.WAITING_INPUT.value
        waiting_approval = action_required and detail.get("status") == RunStatus.WAITING_APPROVAL.value
        status = terminal_labels.get(terminal_status) or (
            "待补充" if waiting_input else (
                "待确认" if waiting_approval else (
                    "待审核" if action_required else ("分析完成" if analysis_task else "已交付")
                )
            )
        )
        title = str(detail.get("title") or "研发任务").strip()[:80]
        return f"【AutoDev · {status}】TFS #{detail['work_item_id']}｜{title}"

    def build_message(
        self, *, to: list[str], subject: str, html_body: str, attachments: list[Path] | None = None
    ) -> EmailMessage:
        recipients = self._deduplicate_emails(to)
        if not recipients:
            raise RuntimeError("邮件没有收件人")
        admin_copies = self.admin_copy_recipients(recipients)
        message = EmailMessage()
        message["From"] = self.sender_address()
        message["To"] = ", ".join(recipients)
        if admin_copies:
            message["Cc"] = ", ".join(admin_copies)
        message["Subject"] = subject
        message["Date"] = format_rfc_datetime(datetime.now(timezone(timedelta(hours=8))))
        message["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1])
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(
            "AutoDev 自主研发交付通知。请使用支持 HTML 的邮件客户端查看需求说明、代码信息和交付产物。"
        )
        message.add_alternative(html_body, subtype="html")
        if f"cid:{BRAND_MARK_CID}" in html_body and BRAND_MARK_PATH.is_file():
            html_part = message.get_payload()[-1]
            html_part.add_related(
                BRAND_MARK_PATH.read_bytes(),
                maintype="image",
                subtype="png",
                cid=f"<{BRAND_MARK_CID}>",
                filename=BRAND_MARK_PATH.name,
                disposition="inline",
            )
        for path in attachments or []:
            if not path.exists() or path.stat().st_size > 10 * 1024 * 1024:
                continue
            mime, _ = mimetypes.guess_type(path.name)
            maintype, subtype = (mime or "application/octet-stream").split("/", 1)
            message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        return message

    def send(self, *, to: list[str], subject: str, html_body: str, attachments: list[Path] | None = None) -> None:
        if not self.configured():
            raise RuntimeError("未配置 SMTP_HOST/SMTP_FROM")
        message = self.build_message(to=to, subject=subject, html_body=html_body, attachments=attachments)
        use_smtps = settings.smtp_protocol == "smtps" or settings.smtp_port == 465
        if use_smtps:
            smtp_client = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=30,
                context=ssl.create_default_context(),
            )
        else:
            smtp_client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        with smtp_client as smtp:
            if settings.smtp_starttls and not use_smtps:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)

    def delivery_html(
        self,
        detail: dict,
        *,
        action_required: bool = False,
        terminal_status: str | None = None,
    ) -> str:
        analysis_task = detail.get("task_type") == TaskType.ANALYSIS.value
        mode = (
            "问题分析"
            if analysis_task
            else (
            f"多项目联合交付（{len(detail.get('joint_children') or [])} 个项目）"
            if detail.get("joint_children")
            else DELIVERY_MODE_LABELS[DeliveryMode(detail["delivery_mode"])]
            )
        )
        started_at = detail.get("started_at") or detail["created_at"]
        end_at = detail.get("completed_at") or datetime.now(UTC).isoformat()
        terminal_labels = {
            "failed": "问题分析失败" if analysis_task else "研发执行失败",
            "cancelled": "分析任务已取消" if analysis_task else "研发任务已取消",
            "rejected": "问题准入驳回" if analysis_task else "需求准入驳回",
        }
        terminal_colors = {"failed": "#bf3e34", "cancelled": "#607d85", "rejected": "#b37418"}
        waiting_input = action_required and detail.get("status") == RunStatus.WAITING_INPUT.value
        waiting_approval = action_required and detail.get("status") == RunStatus.WAITING_APPROVAL.value
        status_label = terminal_labels.get(terminal_status) or (
            ("等待补充分析信息" if analysis_task else "等待补充研发信息")
            if waiting_input
            else ("等待风险确认" if waiting_approval else (
                "等待代码合并" if action_required else ("问题分析完成" if analysis_task else "研发交付完成")
            ))
        )
        status_color = terminal_colors.get(terminal_status) or (
            "#b37418" if waiting_input or waiting_approval else ("#257e65" if action_required else "#ff582c")
        )
        terminal = terminal_status in terminal_labels

        def parse_datetime(value: str | None) -> datetime | None:
            if not value:
                return None
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed

        def format_datetime(value: str | None, fallback: str = "—") -> str:
            try:
                parsed = parse_datetime(value)
            except (TypeError, ValueError):
                parsed = None
            if not parsed:
                return fallback
            china_time = parsed.astimezone(timezone(timedelta(hours=8)))
            return china_time.strftime("%Y-%m-%d %H:%M:%S") + "（UTC+8）"

        def safe_text(value: object, fallback: str = "—", limit: int = 4000) -> str:
            text = str(value or fallback)[:limit]
            return html.escape(text).replace("\n", "<br>")

        try:
            duration_seconds = max(0, int((parse_datetime(end_at) - parse_datetime(started_at)).total_seconds()))
            duration_parts = []
            days, remainder = divmod(duration_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            if days:
                duration_parts.append(f"{days} 天")
            if hours:
                duration_parts.append(f"{hours} 小时")
            if minutes:
                duration_parts.append(f"{minutes} 分")
            if seconds or not duration_parts:
                duration_parts.append(f"{seconds} 秒")
            duration_text = " ".join(duration_parts)
        except (ValueError, TypeError):
            duration_text = "—"

        kind_labels = {
            "package": "安装包",
            "package_note": "构建说明",
            "sql": "SQL 脚本",
            "config": "配置文件",
            "merge_screenshot": "合并截图",
            "menu_link": "新增视图菜单链接",
            "license_request": "License 授权申请",
            "release_artifact": "自动发版产物",
            "analysis_report": "问题分析报告",
        }
        artifact_lines = []
        deliverable_items = (
            list(detail.get("artifacts", []))
            if detail.get("joint_children")
            else visible_delivery_artifacts(
                str(detail.get("delivery_mode") or ""),
                detail.get("artifacts", []),
                detail.get("delivery_options"),
            )
        )
        for item in deliverable_items:
            artifact_url = item.get("external_url") or f"{settings.public_base_url}/api/artifacts/{item['id']}"
            kind = kind_labels.get(item.get("kind"), item.get("kind") or "交付文件")
            action_label = (
                "下载截图"
                if item.get("kind") == "merge_screenshot"
                else (
                    "打开申请"
                    if item.get("kind") == "license_request"
                    else (
                        "查看发版产物"
                        if item.get("kind") == "release_artifact"
                        else ("下载报告" if item.get("kind") == "analysis_report" else "下载产物")
                    )
                )
            )
            if item.get("kind") == "menu_link":
                artifact_lines.append(
                    f"""<tr><td style="padding:0 0 7px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #c9d8d2;background:#f5f8f1"><tr><td width="42" align="center" style="padding:10px 6px;color:#b37418;font:700 16px Consolas,'Courier New',monospace">↗</td><td style="padding:9px 12px"><div style="font-size:13px;font-weight:700;color:#082c30;word-break:break-all">{safe_text(item.get('name'))}</div><div style="margin-top:2px;color:#607d85;font-size:11px;letter-spacing:.04em">{safe_text(kind)}</div></td></tr></table></td></tr>"""
                )
                continue
            artifact_lines.append(
                f"""<tr><td style="padding:0 0 7px 0">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #c9d8d2;background:#f5f8f1">
                  <tr>
                    <td width="42" align="center" style="padding:10px 6px;color:#257e65;font:700 16px Consolas,'Courier New',monospace">↘</td>
                    <td style="padding:9px 7px">
                      <div style="font-size:13px;font-weight:700;color:#082c30;word-break:break-all">{safe_text(item.get('name'))}</div>
                      <div style="margin-top:2px;color:#607d85;font-size:11px;letter-spacing:.04em">{safe_text(kind)}</div>
                    </td>
                    <td class="email-file-action" width="94" align="right" style="padding:9px 12px 9px 7px">
                      <a href="{html.escape(artifact_url, quote=True)}" style="display:inline-block;padding:8px 11px;border-radius:6px;background:#257e65;color:#ffffff;text-decoration:none;font-size:12px;font-weight:700;white-space:nowrap">{action_label}</a>
                    </td>
                  </tr>
                </table></td></tr>"""
            )
        artifacts = "".join(artifact_lines) or """<tr><td style="padding:11px;border:1px dashed #c9d8d2;color:#607d85;text-align:center;font-size:13px">当前阶段暂无可下载产物</td></tr>"""
        expiry_note = (
            f"<span style=\"color:#d74419;font-size:11px;font-weight:400;letter-spacing:0\">OSS 下载链接 {settings.oss_retention_days} 天内有效</span>"
            if any(item.get("external_url") and "aliyuncs.com" in item["external_url"] for item in deliverable_items)
            else ""
        )
        pr_url = str(detail.get("pr_url") or "")
        pr_value = (
            f"<a href=\"{html.escape(pr_url, quote=True)}\" style=\"color:#257e65;text-decoration:underline;word-break:break-all\">{html.escape(pr_url)}</a>"
            if pr_url else ("尚未提交 PR" if waiting_approval or waiting_input else "无需 PR")
        )
        console_url = html.escape(settings.public_base_url, quote=True)
        action = ""
        if waiting_input:
            request_rows = []
            for index, item in enumerate(detail.get("supplement_requests") or [], 1):
                if not isinstance(item, dict):
                    continue
                required = "必填" if item.get("required", True) else "可选"
                reason = safe_text(item.get("reason"), "继续可靠研发所需")
                suggestion = safe_text(item.get("suggested_answer"), "请按实际业务口径说明")
                request_rows.append(
                    f"""<tr><td style="padding:10px 12px;border-bottom:1px solid #d3dec7">
                    <div style="color:#082c30;font-size:13px;font-weight:700">{index}. {safe_text(item.get('question'))}</div>
                    <div style="margin-top:4px;color:#607d85;font-size:12px;line-height:1.55">原因：{reason}</div>
                    <div style="margin-top:2px;color:#906516;font-size:12px;line-height:1.55">填写提示：{suggestion} · {required}</div>
                    </td></tr>"""
                )
            questions = "".join(request_rows) or """<tr><td style="padding:10px 12px;color:#607d85;font-size:13px">请登录平台查看待补充事项。</td></tr>"""
            action = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px;background:#fff5df;border-left:4px solid #b37418">
              <tr><td style="padding:12px 13px 7px;color:#607d85;font-size:13px;line-height:1.55"><b style="display:block;color:#082c30;font-size:14px">需要补充关键信息后继续研发</b>DevCore 已完成当前分析，任务已安全暂停，不计入本机并发占用。</td></tr>
              <tr><td style="padding:0 13px 11px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #d3dec7;background:#fffefb">{questions}</table></td></tr>
              <tr><td style="padding:0 13px 13px"><a href="{console_url}" style="display:inline-block;padding:8px 12px;background:#ff582c;border-radius:6px;color:#ffffff;text-decoration:none;font-size:13px;font-weight:700">登录 AutoDev 补充并继续 →</a></td></tr>
            </table>"""
        elif waiting_approval:
            blocker = summarize_blocker(detail)
            reason = safe_text(blocker["reason"], limit=3000)
            decision_required = safe_text(blocker["decision_required"], limit=3000)
            decision_row = (f'<tr><td style="padding:12px 14px">需要你判断</td><td style="padding:12px 14px;line-height:1.7">{decision_required}</td></tr>' if blocker["decision_required"] else '')
            blocked_title = "任务已暂停，等待你的判断" if blocker["decision_required"] else "普通技术问题无需人工审批"
            action = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px;border:1px solid #082c30;background:#fffefb">
              <tr><td colspan="2" style="padding:12px 14px;background:#e3f1e7;color:#082c30"><span style="display:block;color:#ff582c;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.16em">RECOVERY / RISK SUMMARY</span><b style="display:block;margin-top:4px;font-size:15px">{blocked_title}</b></td></tr>
              <tr><td width="118" valign="top" style="padding:12px 10px 12px 14px;border-bottom:1px solid #d3dec7;color:#d74419;font-size:12px;font-weight:700">为什么阻塞</td><td valign="top" style="padding:12px 14px 12px 10px;border-bottom:1px solid #d3dec7;color:#35565a;font-size:13px;line-height:1.7">{reason}</td></tr>
              {decision_row}
              <tr><td colspan="2" style="padding:0 14px 14px"><a href="{console_url}" style="display:inline-block;padding:8px 12px;background:#ff582c;border-radius:6px;color:#ffffff;text-decoration:none;font-size:13px;font-weight:700">打开 AutoDev 继续执行 →</a></td></tr>
            </table>"""
        elif action_required:
            review_items = [
                {
                    "repository": state.get("repository_short_name") or repository_short_name(state.get("name", "")),
                    "pr_id": state.get("pr_id"),
                    "pr_url": state.get("pr_url"),
                }
                for state in detail.get("repository_states", [])
                if state.get("pr_url") and state.get("status") != "completed"
            ]
            if not review_items and pr_url:
                review_items = [{"repository": "主仓库", "pr_id": detail.get("pr_id"), "pr_url": pr_url}]
            review_links = "".join(
                f"<a href=\"{html.escape(str(item['pr_url']), quote=True)}\" style=\"display:inline-block;margin:7px 7px 0 0;padding:7px 10px;background:#257e65;color:#ffffff;text-decoration:none;font-weight:700;font-size:12px;white-space:nowrap\">{safe_text(item['repository'])} · PR #{safe_text(item['pr_id'])} →</a>"
                for item in review_items
            )
            action = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px;background:#e3f1e7;border-left:4px solid #257e65">
              <tr><td style="padding:11px 13px;color:#355e56;font-size:13px;line-height:1.55"><b style="display:block;color:#082c30;font-size:13px">需要项目经理协同处理：</b> 请逐个联系有权限的同事审核并合并以下 PR；AutoDev 将持续检测。<div>{review_links}</div></td></tr>
            </table>"""
        elif terminal:
            reason = safe_text(detail.get("error_message"), "任务已终止，暂无更多错误说明。", limit=3000)
            action = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px;background:#fff0eb;border-left:4px solid {status_color}">
              <tr><td style="padding:11px 13px;color:#763f3c;font-size:13px;line-height:1.65"><b style="display:block;margin-bottom:3px;color:#082c30;font-size:13px">终止原因 / TERMINATION REASON</b>{reason}</td></tr>
            </table>"""

        if not action_required and not terminal:
            acceptance_url = html.escape(
                f"{settings.public_base_url.rstrip('/')}/?request={detail.get('id', '')}&acceptance=1", quote=True
            )
            action += f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px;background:#fff5df;border-left:4px solid #b37418">
              <tr><td style="padding:13px 14px;color:#35565a;font-size:13px;line-height:1.7"><b style="display:block;color:#082c30;font-size:14px">交付完成后，请提出人确认验证结果</b>
              只需选择验证通过或不通过；不通过时可选填具体需求项或说明。系统自动关联本次交付，无需填写版本、环境，也无需提供真实截图。反馈会保留在项目经验中；需要时可继续发起关联返修。
              <div style="margin-top:10px"><a href="{acceptance_url}" style="display:inline-block;padding:9px 13px;background:#ff582c;border-radius:6px;color:#ffffff;text-decoration:none;font-weight:700">确认验收结果 →</a></div>
              </td></tr></table>"""

        completed_text = format_datetime(detail.get("completed_at"), "进行中")
        signal_label = "TERMINAL SIGNAL" if terminal else (
            "INPUT SIGNAL" if waiting_input else ("DECISION SIGNAL" if waiting_approval else "DELIVERY SIGNAL")
        )
        duration_label = "任务耗时" if terminal else (
            "当前耗时" if action_required else ("分析耗时" if analysis_task else "开发耗时")
        )
        notes_label = "执行摘要 / EXECUTION NOTES" if terminal else (
            ("当前分析结论 / CURRENT CONCLUSION" if analysis_task else "当前研发结论 / CURRENT CONCLUSION")
            if waiting_input else ("分析结论 / ANALYSIS CONCLUSION" if analysis_task else "开发说明 / DEVELOPMENT NOTES")
        )
        notes_value = detail.get("result_summary") or ("任务在完成前终止，请查看上方终止原因及研发控制台中的执行记录。" if terminal else "—")
        artifact_heading = "已有产物 / AVAILABLE FILES" if terminal else (
            "当前产物 / CURRENT FILES" if waiting_input else (
                "分析报告 / ANALYSIS REPORT" if analysis_task else "交付产物 / DELIVERABLES"
            )
        )
        code_section = ""
        if not action_required and not analysis_task:
            if detail.get("joint_children"):
                child_rows = "".join(
                    f"""<tr><td style="padding:8px 10px;border-bottom:1px solid #c9d8d2;color:#082c30;font-size:12px;font-weight:700">{safe_text(child.get('project_name'))}</td><td style="padding:8px 10px;border-bottom:1px solid #c9d8d2;color:#607d85;font-size:12px">{safe_text(STATUS_LABELS.get(RunStatus(child.get('status')), child.get('status')))}</td><td style="padding:8px 10px;border-bottom:1px solid #c9d8d2;color:#607d85;font-size:12px">{len([state for state in child.get('repository_states', []) if state.get('pr_id')])} 个 PR</td></tr>"""
                    for child in detail["joint_children"]
                )
                code_section = f"""<div style="margin-bottom:5px;color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.1em">联合项目 / JOINT DELIVERY</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px;border:1px solid #c9d8d2;background:#f5f8f1">{child_rows}</table>"""
            else:
                code_section = f"""<div style="margin-bottom:5px;color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.1em">代码信息 / CODE DELIVERY</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px;border:1px solid #c9d8d2;background:#f5f8f1">
        <tr>
          <td width="38%" valign="top" style="padding:9px 11px;border-right:1px solid #c9d8d2"><div style="color:#607d85;font-size:10px">分支</div><div style="margin-top:3px;color:#082c30;font:11px Consolas,'Courier New',monospace;word-break:break-all">{safe_text(detail.get('branch_name'))}</div></td>
          <td width="38%" valign="top" style="padding:9px 11px;border-right:1px solid #c9d8d2"><div style="color:#607d85;font-size:10px">提交</div><div style="margin-top:3px;color:#082c30;font:11px Consolas,'Courier New',monospace;word-break:break-all">{safe_text(detail.get('commit_hash'))}</div></td>
          <td width="24%" valign="top" style="padding:9px 11px"><div style="color:#607d85;font-size:10px">PR</div><div style="margin-top:3px;color:#082c30;font-size:12px;word-break:break-all">{pr_value}</div></td>
        </tr>
      </table>"""
        return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_text(status_label)}</title>
<style>
@media only screen and (max-width:600px) {{
  .email-shell {{width:100%!important;}}
  .email-body {{padding:20px 16px!important;}}
  .email-header {{padding:18px 16px!important;}}
  .email-mark {{display:none!important;}}
  .email-meta-cell,.email-time-cell {{display:block!important;width:auto!important;border-right:0!important;}}
  .email-copy-cell {{display:block!important;width:100%!important;padding:0 0 14px!important;}}
  .email-file-name {{overflow-wrap:anywhere;word-break:break-all;}}
  .email-file-action {{padding-left:6px!important;padding-right:6px!important;}}
}}
</style></head>
<body data-mail-theme="mint-editorial" style="margin:0;padding:0;background:#e6eee5;color:#082c30;font-family:'Microsoft YaHei UI','Microsoft YaHei',sans-serif">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">TFS #{detail['work_item_id']} · {safe_text(detail.get('title'))} · {status_label}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e6eee5"><tr><td align="center" style="padding:16px 10px">
  <table role="presentation" class="email-shell" width="720" cellpadding="0" cellspacing="0" style="width:100%;max-width:720px;background:#fffefb;border:1px solid #c9d8d2;border-radius:16px;overflow:hidden;box-shadow:0 12px 32px rgba(24,61,48,.08)">
    <tr><td class="email-header" style="padding:22px 24px;background:#d6e9dc;border-radius:16px 16px 0 0;border-top:4px solid {status_color};border-bottom:1px solid #c9d8d2">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td class="email-mark" width="136" valign="middle"><img src="cid:{BRAND_MARK_CID}" width="116" height="47" alt="AutoDev" style="display:block;width:116px;height:47px;border:0;object-fit:contain"></td>
        <td valign="middle"><div style="color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.16em">AutoDev · {signal_label}</div><div style="margin-top:6px;color:#082c30;font-size:22px;font-weight:700">{status_label}</div></td>
        <td width="110" align="right" valign="top"><span style="display:inline-block;padding:7px 10px;border:1px solid #bdd3c6;background:#fffefb;border-radius:20px;color:#355e56;font:700 10px Consolas,'Courier New',monospace">TFS #{detail['work_item_id']}</span></td>
      </tr></table>
    </td></tr>
    <tr><td class="email-body" style="padding:24px 26px 22px">
      <div style="color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.13em">需求 / REQUIREMENT</div>
      <h1 style="margin:7px 0 20px;color:#082c30;font-size:23px;line-height:1.5;overflow-wrap:anywhere">{safe_text(detail.get('title'))}</h1>
      {action}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px;border:1px solid #c9d8d2;background:#edf3ee">
        <tr>
          <td class="email-meta-cell" width="25%" style="padding:10px 12px;border-right:1px solid #c9d8d2"><div style="color:#607d85;font-size:10px">项目</div><div style="margin-top:3px;color:#082c30;font-size:13px;font-weight:700">{safe_text(detail.get('project_name'))}</div></td>
          <td class="email-meta-cell" width="25%" style="padding:10px 12px;border-right:1px solid #c9d8d2"><div style="color:#607d85;font-size:10px">交付方式</div><div style="margin-top:3px;color:#082c30;font-size:13px;font-weight:700">{safe_text(mode)}</div></td>
          <td class="email-meta-cell" width="25%" style="padding:10px 12px;border-right:1px solid #c9d8d2"><div style="color:#607d85;font-size:10px">提交人</div><div style="margin-top:3px;color:#082c30;font-size:13px;font-weight:700">{safe_text(detail.get('requester_name'))}</div></td>
          <td class="email-meta-cell" width="25%" style="padding:10px 12px"><div style="color:#607d85;font-size:10px">{duration_label}</div><div style="margin-top:3px;color:#257e65;font-size:13px;font-weight:700">{safe_text(duration_text)}</div></td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px"><tr>
        <td class="email-copy-cell" width="50%" valign="top" style="padding-right:6px"><div style="margin-bottom:5px;color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.1em">需求说明 / BRIEF</div><div style="padding:10px 12px;background:#edf3ee;border-left:3px solid #257e65;color:#35565a;font-size:13px;line-height:1.6">{safe_text(detail.get('requirement_summary'), limit=2000)}</div></td>
        <td class="email-copy-cell" width="50%" valign="top" style="padding-left:6px"><div style="margin-bottom:5px;color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.1em">{notes_label}</div><div style="padding:10px 12px;background:#edf3ee;border-left:3px solid {status_color};color:#35565a;font-size:13px;line-height:1.6">{safe_text(notes_value)}</div></td>
      </tr></table>

      {code_section}

      <div style="margin-bottom:5px;color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.1em">时间记录 / TIMELINE</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:18px;border:1px solid #c9d8d2;border-radius:8px;background:#edf3ee;color:#082c30">
        <tr>
          <td class="email-time-cell" width="33.33%" style="padding:10px 12px;border-right:1px solid #c9d8d2"><div style="color:#607d85;font-size:10px">提交时间</div><b style="display:block;margin-top:3px;font-size:12px;overflow-wrap:anywhere">{format_datetime(detail.get('created_at'))}</b></td>
          <td class="email-time-cell" width="33.33%" style="padding:10px 12px;border-right:1px solid #c9d8d2"><div style="color:#607d85;font-size:10px">开始时间</div><b style="display:block;margin-top:3px;font-size:12px;overflow-wrap:anywhere">{format_datetime(started_at)}</b></td>
          <td class="email-time-cell" width="33.33%" style="padding:10px 12px"><div style="color:#607d85;font-size:10px">完成时间</div><b style="display:block;margin-top:3px;font-size:12px;color:{status_color};overflow-wrap:anywhere">{completed_text}</b></td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:5px"><tr><td style="color:#607d85;font:700 9px Consolas,'Courier New',monospace;letter-spacing:.1em">{artifact_heading}</td><td align="right">{expiry_note}</td></tr></table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{artifacts}</table>
    </td></tr>
    <tr><td style="padding:16px 26px;border-radius:0 0 16px 16px;background:#edf3ee;border-top:1px solid #c9d8d2;color:#607d85;font-size:11px;line-height:1.5">
      此邮件由 <b style="color:#ff582c">AutoDev · 自主研发交付</b> 自动发送，研发与交付操作均保留审计记录。
      <a href="{console_url}" style="margin-left:10px;color:#257e65;text-decoration:none;white-space:nowrap">打开研发控制台 →</a>
    </td></tr>
  </table>
</td></tr></table></body></html>"""
