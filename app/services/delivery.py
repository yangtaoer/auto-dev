from __future__ import annotations

import fnmatch
import html
import mimetypes
import shutil
import smtplib
import ssl
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime as format_rfc_datetime, make_msgid
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import settings
from ..db import add_artifact, request_detail
from ..domain import DELIVERY_MODE_LABELS, DeliveryMode
from .process_env import sanitized_process_env


BRAND_MARK_CID = "autodev-brand-mark"
BRAND_MARK_PATH = Path(__file__).resolve().parents[1] / "static" / "brand" / "autodev-mark.png"


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

    def sender_address(self) -> Address:
        return Address(display_name=settings.smtp_from_name or "AutoDev 全自助研发交付", addr_spec=settings.smtp_from)

    def delivery_subject(self, detail: dict, *, action_required: bool = False) -> str:
        status = "待审核" if action_required else "已交付"
        title = str(detail.get("title") or "研发任务").strip()[:80]
        return f"【AutoDev · {status}】TFS #{detail['work_item_id']}｜{title}"

    def build_message(
        self, *, to: list[str], subject: str, html_body: str, attachments: list[Path] | None = None
    ) -> EmailMessage:
        recipients = [email.strip() for email in to if email and email.strip()]
        if not recipients:
            raise RuntimeError("邮件没有收件人")
        message = EmailMessage()
        message["From"] = self.sender_address()
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message["Date"] = format_rfc_datetime(datetime.now(timezone(timedelta(hours=8))))
        message["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1])
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(
            "AutoDev 全自助研发交付通知。请使用支持 HTML 的邮件客户端查看需求说明、代码信息和交付产物。"
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

    def delivery_html(self, detail: dict, *, action_required: bool = False) -> str:
        mode = DELIVERY_MODE_LABELS[DeliveryMode(detail["delivery_mode"])]
        started_at = detail.get("started_at") or detail["created_at"]
        end_at = detail.get("completed_at") or datetime.now(UTC).isoformat()

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
            "merge_evidence": "合并凭证",
        }
        artifact_lines = []
        for item in detail.get("artifacts", []):
            artifact_url = item.get("external_url") or f"{settings.public_base_url}/api/artifacts/{item['id']}"
            kind = kind_labels.get(item.get("kind"), item.get("kind") or "交付文件")
            artifact_lines.append(
                f"""<tr><td style="padding:0 0 10px 0">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #d8e5dd;background:#f8fbf9">
                  <tr>
                    <td width="48" align="center" style="padding:15px 8px;color:#087a49;font:700 18px Consolas,'Courier New',monospace">↘</td>
                    <td style="padding:13px 8px">
                      <div style="font-size:14px;font-weight:700;color:#123428;word-break:break-all">{safe_text(item.get('name'))}</div>
                      <div style="margin-top:3px;color:#6c8479;font-size:11px;letter-spacing:.04em">{safe_text(kind)}</div>
                    </td>
                    <td width="100" align="right" style="padding:13px 16px 13px 8px">
                      <a href="{html.escape(artifact_url, quote=True)}" style="display:inline-block;padding:8px 12px;background:#0d2b20;color:#79f2ad;text-decoration:none;font-size:12px;font-weight:700;white-space:nowrap">下载产物</a>
                    </td>
                  </tr>
                </table></td></tr>"""
            )
        artifacts = "".join(artifact_lines) or """<tr><td style="padding:16px;border:1px dashed #c7d8ce;color:#71877c;text-align:center;font-size:13px">当前阶段暂无可下载产物</td></tr>"""
        expiry_note = (
            f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px;background:#fff8e5;border-left:4px solid #e8a318">
              <tr><td style="padding:12px 14px;color:#7a5712;font-size:12px;line-height:1.65"><b>下载有效期</b>　OSS 链接自生成起 {settings.oss_retention_days} 天内有效，请及时下载并归档。</td></tr>
            </table>"""
            if any(item.get("external_url") and "aliyuncs.com" in item["external_url"] for item in detail.get("artifacts", []))
            else ""
        )
        pr_url = str(detail.get("pr_url") or "")
        pr_value = (
            f"<a href=\"{html.escape(pr_url, quote=True)}\" style=\"color:#087a49;text-decoration:underline;word-break:break-all\">{html.escape(pr_url)}</a>"
            if pr_url else "无需 PR"
        )
        action = ""
        if action_required:
            action_button = (
                f"<a href=\"{html.escape(pr_url, quote=True)}\" style=\"display:inline-block;margin-top:10px;padding:9px 14px;background:#e8a318;color:#13251d;text-decoration:none;font-weight:700;font-size:12px\">打开 PR 并安排合并 →</a>"
                if pr_url else ""
            )
            action = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 22px;background:#fff7df;border-left:4px solid #e8a318">
              <tr><td style="padding:15px 17px;color:#694b0d;font-size:13px;line-height:1.65"><b style="display:block;color:#392a0c;font-size:14px">需要项目经理协同处理</b>请联系有权限的同事审核并合并 PR；AutoDev 会持续检测合并状态，完成后自动发送最终交付邮件。{action_button}</td></tr>
            </table>"""

        status_label = "等待代码合并" if action_required else "研发交付完成"
        status_color = "#ffc857" if action_required else "#79f2ad"
        completed_text = format_datetime(detail.get("completed_at"), "进行中")
        console_url = html.escape(settings.public_base_url, quote=True)
        return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_text(status_label)}</title></head>
<body style="margin:0;padding:0;background:#edf3ef;color:#163026;font-family:'Microsoft YaHei UI','Microsoft YaHei',sans-serif">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">TFS #{detail['work_item_id']} · {safe_text(detail.get('title'))} · {status_label}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#edf3ef"><tr><td align="center" style="padding:30px 12px">
  <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:100%;max-width:680px;background:#ffffff;border:1px solid #d4e1d9;box-shadow:0 10px 35px rgba(18,52,40,.08)">
    <tr><td style="padding:27px 30px;background:#0c1a14;border-bottom:4px solid {status_color}">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td width="62" valign="top"><div style="width:48px;height:48px;background:#eef6f0;text-align:center"><img src="cid:{BRAND_MARK_CID}" width="48" height="48" alt="AutoDev" style="display:block;width:48px;height:48px;border:0;object-fit:contain"></div></td>
        <td valign="top"><div style="color:#79f2ad;font:700 10px Consolas,'Courier New',monospace;letter-spacing:.16em">AUTODEV · DELIVERY SIGNAL</div><div style="margin-top:7px;color:#f0f7f2;font-size:22px;font-weight:700">{status_label}</div></td>
        <td width="110" align="right" valign="top"><span style="display:inline-block;padding:6px 9px;border:1px solid {status_color};color:{status_color};font:700 10px Consolas,'Courier New',monospace">TFS #{detail['work_item_id']}</span></td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:30px">
      <div style="color:#698176;font:700 10px Consolas,'Courier New',monospace;letter-spacing:.13em">需求 / REQUIREMENT</div>
      <h1 style="margin:8px 0 22px;color:#102d22;font-size:24px;line-height:1.45">{safe_text(detail.get('title'))}</h1>
      {action}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:22px;border:1px solid #dce7e0;background:#f7faf8">
        <tr>
          <td width="50%" style="padding:14px 16px;border-right:1px solid #dce7e0;border-bottom:1px solid #dce7e0"><div style="color:#72887d;font-size:10px">项目</div><div style="margin-top:4px;color:#17392c;font-size:13px;font-weight:700">{safe_text(detail.get('project_name'))}</div></td>
          <td width="50%" style="padding:14px 16px;border-bottom:1px solid #dce7e0"><div style="color:#72887d;font-size:10px">交付方式</div><div style="margin-top:4px;color:#17392c;font-size:13px;font-weight:700">{safe_text(mode)}</div></td>
        </tr><tr>
          <td width="50%" style="padding:14px 16px;border-right:1px solid #dce7e0"><div style="color:#72887d;font-size:10px">提交人</div><div style="margin-top:4px;color:#17392c;font-size:13px;font-weight:700">{safe_text(detail.get('requester_name'))}</div></td>
          <td width="50%" style="padding:14px 16px"><div style="color:#72887d;font-size:10px">{'当前耗时' if action_required else '开发耗时'}</div><div style="margin-top:4px;color:#087a49;font-size:13px;font-weight:700">{safe_text(duration_text)}</div></td>
        </tr>
      </table>

      <div style="margin-bottom:20px"><div style="margin-bottom:7px;color:#698176;font:700 10px Consolas,'Courier New',monospace;letter-spacing:.1em">需求说明 / BRIEF</div><div style="padding:15px 17px;background:#f7faf8;border-left:3px solid #6ea98a;color:#29493d;font-size:13px;line-height:1.8">{safe_text(detail.get('requirement_summary'), limit=2000)}</div></div>
      <div style="margin-bottom:22px"><div style="margin-bottom:7px;color:#698176;font:700 10px Consolas,'Courier New',monospace;letter-spacing:.1em">开发说明 / DEVELOPMENT NOTES</div><div style="padding:15px 17px;background:#f7faf8;border-left:3px solid #79f2ad;color:#29493d;font-size:13px;line-height:1.8">{safe_text(detail.get('result_summary'))}</div></div>

      <div style="margin-bottom:7px;color:#698176;font:700 10px Consolas,'Courier New',monospace;letter-spacing:.1em">代码信息 / CODE DELIVERY</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:22px;border:1px solid #dce7e0">
        <tr><td width="74" style="padding:11px 13px;background:#f2f7f4;color:#6c8277;font-size:11px">分支</td><td style="padding:11px 13px;color:#17392c;font:12px Consolas,'Courier New',monospace;word-break:break-all">{safe_text(detail.get('branch_name'))}</td></tr>
        <tr><td width="74" style="padding:11px 13px;background:#f2f7f4;border-top:1px solid #dce7e0;color:#6c8277;font-size:11px">提交</td><td style="padding:11px 13px;border-top:1px solid #dce7e0;color:#17392c;font:12px Consolas,'Courier New',monospace;word-break:break-all">{safe_text(detail.get('commit_hash'))}</td></tr>
        <tr><td width="74" style="padding:11px 13px;background:#f2f7f4;border-top:1px solid #dce7e0;color:#6c8277;font-size:11px">PR</td><td style="padding:11px 13px;border-top:1px solid #dce7e0;color:#17392c;font-size:12px;word-break:break-all">{pr_value}</td></tr>
      </table>

      <div style="margin-bottom:7px;color:#698176;font:700 10px Consolas,'Courier New',monospace;letter-spacing:.1em">时间记录 / TIMELINE</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:25px;background:#0f211a;color:#dce9e1">
        <tr><td style="padding:13px 16px;border-bottom:1px solid #294338"><span style="display:inline-block;width:82px;color:#789589;font-size:10px">提交时间</span><b style="font-size:12px">{format_datetime(detail.get('created_at'))}</b></td></tr>
        <tr><td style="padding:13px 16px;border-bottom:1px solid #294338"><span style="display:inline-block;width:82px;color:#789589;font-size:10px">开始时间</span><b style="font-size:12px">{format_datetime(started_at)}</b></td></tr>
        <tr><td style="padding:13px 16px"><span style="display:inline-block;width:82px;color:#789589;font-size:10px">完成时间</span><b style="font-size:12px;color:{status_color}">{completed_text}</b></td></tr>
      </table>

      <div style="margin-bottom:8px;color:#698176;font:700 10px Consolas,'Courier New',monospace;letter-spacing:.1em">交付产物 / DELIVERABLES</div>
      {expiry_note}<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{artifacts}</table>
    </td></tr>
    <tr><td style="padding:19px 30px;background:#f3f7f4;border-top:1px solid #d9e5de;color:#70857a;font-size:11px;line-height:1.7">
      此邮件由 <b style="color:#315847">AutoDev 全自助研发交付</b> 自动发送，研发与交付操作均保留审计记录。<br>
      <a href="{console_url}" style="color:#087a49;text-decoration:none">打开研发控制台 →</a>
    </td></tr>
  </table>
</td></tr></table></body></html>"""
