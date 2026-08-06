from __future__ import annotations

import fnmatch
import html
import mimetypes
import shutil
import smtplib
import ssl
import subprocess
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import settings
from ..db import add_artifact, request_detail
from ..domain import DELIVERY_MODE_LABELS, DeliveryMode
from .process_env import sanitized_process_env


def run_command(command: str, cwd: Path, timeout_minutes: int = 60) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_minutes * 60,
        env=sanitized_process_env(),
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode:
        raise RuntimeError(f"命令执行失败 ({result.returncode}): {command}\n{output[-3000:]}")
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
    output = git(worktree, "diff", "--name-only", base_commit, "--")
    return [line.replace("\\", "/") for line in output.splitlines() if line.strip()]


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def protected_changes(paths: list[str], patterns: list[str]) -> list[str]:
    return [path for path in paths if matches_any(path, patterns)]


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

    def collect_changed_assets(self, request_id: str, worktree: Path, base_commit: str, project: dict) -> list[int]:
        ids: list[int] = []
        paths = changed_files(worktree, base_commit)
        groups = {
            "sql": project.get("sql_patterns", []),
            "config": project.get("config_patterns", []),
        }
        for kind, patterns in groups.items():
            for relative in paths:
                source = worktree / relative
                if source.is_file() and matches_any(relative, patterns):
                    target = self.request_dir(request_id) / kind / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    ids.append(self.record(request_id, kind, relative, str(target)))
        return ids

    def collect_packages(self, request_id: str, worktree: Path, patterns: list[str]) -> list[int]:
        ids: list[int] = []
        for pattern in patterns:
            for source in worktree.glob(pattern):
                if not source.is_file():
                    continue
                relative = source.relative_to(worktree)
                target = self.request_dir(request_id) / "package" / relative.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                ids.append(self.record(request_id, "package", relative.name, str(target)))
        if not ids:
            marker = self.request_dir(request_id) / "package" / "README.txt"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("构建成功，但配置的 package_patterns 未匹配到文件。\n", encoding="utf-8")
            ids.append(self.record(request_id, "package_note", marker.name, str(marker)))
        return ids

    def create_merge_evidence(self, request_id: str, pr: dict, pr_url: str) -> int:
        target_dir = self.request_dir(request_id)
        html_path = target_dir / "merge-evidence.html"
        image_path = target_dir / "merge-evidence.png"
        tfs_image_path = target_dir / "tfs-pr-merged.png"
        completed = pr.get("closed_date") or datetime.now(UTC).isoformat()
        page = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><style>
        *{{box-sizing:border-box}}body{{margin:0;width:1440px;height:900px;background:#07110e;color:#eef9f2;font-family:'Microsoft YaHei',sans-serif;padding:72px}}
        .seal{{color:#76f7ae;font:700 18px Consolas;letter-spacing:.22em}}h1{{font-size:56px;margin:32px 0 12px}}.sub{{color:#9fb6aa;font-size:20px}}
        .card{{margin-top:56px;border:1px solid #28493b;background:#0b1b15;padding:38px 44px;box-shadow:16px 16px 0 #102e22}}
        .row{{display:grid;grid-template-columns:220px 1fr;padding:17px 0;border-bottom:1px solid #1d382d;font-size:20px}}.row:last-child{{border:0}}
        .key{{color:#87aa99}}.ok{{display:inline-block;color:#06150e;background:#76f7ae;padding:7px 14px;font-weight:700}}
        .foot{{position:absolute;bottom:64px;color:#6f8f80;font:16px Consolas}}
        </style></head><body><div class='seal'>AUTODEV / MERGE CERTIFICATE</div><h1>代码合并完成</h1><div class='sub'>系统通过 TFS API 检测到 Pull Request 已完成</div>
        <div class='card'><div class='row'><div class='key'>状态</div><div><span class='ok'>COMPLETED</span></div></div>
        <div class='row'><div class='key'>PR</div><div>#{html.escape(str(pr.get('id','')))} · {html.escape(pr.get('title',''))}</div></div>
        <div class='row'><div class='key'>代码仓库</div><div>{html.escape(pr.get('repository',''))}</div></div>
        <div class='row'><div class='key'>合并方向</div><div>{html.escape(pr.get('source_branch',''))} → {html.escape(pr.get('target_branch',''))}</div></div>
        <div class='row'><div class='key'>Merge commit</div><div>{html.escape(pr.get('merge_commit') or '—')}</div></div>
        <div class='row'><div class='key'>完成时间</div><div>{html.escape(completed)}</div></div></div>
        <div class='foot'>{html.escape(pr_url)} · 任务 {html.escape(request_id)}</div></body></html>"""
        html_path.write_text(page, encoding="utf-8")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                if settings.tfs_pat and "/demo/" not in pr_url:
                    try:
                        context = browser.new_context(http_credentials={"username": "", "password": settings.tfs_pat})
                        tfs_page = context.new_page(viewport={"width": 1440, "height": 1000})
                        tfs_page.goto(pr_url, wait_until="networkidle", timeout=30_000)
                        visible_text = tfs_page.locator("body").inner_text(timeout=5_000)
                        if str(pr.get("id", "")) in visible_text or pr.get("title", "") in visible_text:
                            tfs_page.screenshot(path=str(tfs_image_path), full_page=True)
                            browser.close()
                            return self.record(request_id, "merge_screenshot", "TFS-PR合并完成截图.png", str(tfs_image_path))
                        context.close()
                    except Exception:
                        # TFS Web 登录方式因部署而异；REST 已确认完成时退回系统生成凭证。
                        pass
                page_obj = browser.new_page(viewport={"width": 1440, "height": 900})
                page_obj.goto(html_path.as_uri())
                page_obj.screenshot(path=str(image_path), full_page=True)
                browser.close()
            return self.record(request_id, "merge_screenshot", "PR合并完成凭证.png", str(image_path))
        except Exception:
            return self.record(request_id, "merge_evidence", "PR合并完成凭证.html", str(html_path))


class Mailer:
    def configured(self) -> bool:
        return bool(settings.smtp_host and settings.smtp_from)

    def send(self, *, to: list[str], subject: str, html_body: str, attachments: list[Path] | None = None) -> None:
        recipients = [email.strip() for email in to if email and email.strip()]
        if not recipients:
            raise RuntimeError("邮件没有收件人")
        if not self.configured():
            raise RuntimeError("未配置 SMTP_HOST/SMTP_FROM")
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content("请使用支持 HTML 的邮件客户端查看交付说明。")
        message.add_alternative(html_body, subtype="html")
        for path in attachments or []:
            if not path.exists() or path.stat().st_size > 10 * 1024 * 1024:
                continue
            mime, _ = mimetypes.guess_type(path.name)
            maintype, subtype = (mime or "application/octet-stream").split("/", 1)
            message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
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

    def delivery_html(self, detail: dict, *, action_required: bool = False) -> str:
        mode = DELIVERY_MODE_LABELS[DeliveryMode(detail["delivery_mode"])]
        started_at = detail.get("started_at") or detail["created_at"]
        end_at = detail.get("completed_at") or datetime.now(UTC).isoformat()
        try:
            duration_seconds = max(0, int((datetime.fromisoformat(end_at) - datetime.fromisoformat(started_at)).total_seconds()))
            duration_text = f"{duration_seconds // 3600} 小时 {(duration_seconds % 3600) // 60} 分 {duration_seconds % 60} 秒"
        except (ValueError, TypeError):
            duration_text = "—"
        artifact_lines = []
        for item in detail.get("artifacts", []):
            artifact_url = item.get("external_url") or f"{settings.public_base_url}/api/artifacts/{item['id']}"
            artifact_lines.append(
                f"<li><a href='{html.escape(artifact_url, quote=True)}'>{html.escape(item['name'])}</a> · {html.escape(item['kind'])}</li>"
            )
        artifacts = "".join(artifact_lines) or "<li>暂无附件</li>"
        expiry_note = (
            f"<p style='color:#b26b00'><b>下载有效期：</b>OSS 交付链接自生成起 {settings.oss_retention_days} 天内有效，请及时下载归档。</p>"
            if any(item.get("external_url") and "aliyuncs.com" in item["external_url"] for item in detail.get("artifacts", []))
            else ""
        )
        action = "<p style='padding:14px;background:#fff5d6'><b>需要处理：</b>请安排有权限的同事审核并合并下方 PR，系统会持续检测合并状态。</p>" if action_required else ""
        pr = f"<p><b>PR：</b><a href='{html.escape(detail.get('pr_url') or '')}'>{html.escape(detail.get('pr_url') or '无')}</a></p>"
        return f"""<div style='font-family:Microsoft YaHei,sans-serif;color:#163026;line-height:1.7;max-width:760px'>
        <h2>需求研发交付 · #{detail['work_item_id']}</h2>{action}
        <p><b>需求：</b>{html.escape(detail.get('title') or '')}</p>
        <p><b>需求说明：</b>{html.escape((detail.get('requirement_summary') or '—')[:2000])}</p>
        <p><b>项目：</b>{html.escape(detail['project_name'])}</p><p><b>交付方式：</b>{mode}</p>{pr}
        <p><b>分支：</b>{html.escape(detail.get('branch_name') or '—')}</p>
        <p><b>提交：</b>{html.escape(detail.get('commit_hash') or '—')}</p>
        <p><b>开发说明：</b>{html.escape(detail.get('result_summary') or '—')}</p>
        <p><b>发起时间：</b>{html.escape(detail['created_at'])}</p><p><b>完成时间：</b>{html.escape(detail.get('completed_at') or '进行中')}</p>
        <p><b>{'当前耗时' if action_required else '开发耗时'}：</b>{duration_text}</p>
        <h3>交付产物</h3>{expiry_note}<ul>{artifacts}</ul>
        <p style='color:#71847b'>此邮件由全自助研发控制台自动发送，所有操作均保留审计记录。</p></div>"""
