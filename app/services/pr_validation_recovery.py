"""Repair a failed required PR validation using its original, scoped checkout."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from ..config import settings
from .codex_runner import CodexRunner
from .command_logs import bounded_log, clean_log
from .commit_policy import committable_changes
from .delivery import git, protected_changes, run_command
from .process_env import git_authenticated_env
from .task_cancellation import TaskCancelled
from .tfs import TfsClient
from . import java_code_checks, tfs_merge_checks


MAX_REPAIRS = 2
REPAIR_TIMEOUT = 12 * 60
SHA = re.compile(r"[0-9a-f]{40}")
ERROR_LINE = re.compile(r"\[ERROR\]|##\[error\]|\berror(?:\s+[A-Z]+\d+)?:|\bcompilation (?:error|failure)\b", re.I)
SOURCE_FAILURE = re.compile(
    r"quality gate status:\s*(?:failed|error)|compilation (?:error|failure)|cannot find symbol|"
    r"\berror\s+[A-Z]+\d+:|\.(?:java|cs|ts|tsx|js|cc|cpp|go|rs)\b.*\berror\b|"
    r"there are test failures|tests run:.*failures:\s*[1-9]",
    re.I,
)
TRANSIENT_FAILURE = re.compile(
    r"connection (?:reset|timed out|refused)|read timed out|temporarily unavailable|"
    r"status(?: code)?[: =]+(?:502|503|504)\b|could not resolve host|temporary failure in name resolution",
    re.I,
)
CHECK_SUPPRESSION = re.compile(r"(?im)^\+(?!\+\+).*\b(?:NOSONAR|sonar[._-](?:skip|exclusions))\b")
SECRET = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:basic\s+|bearer\s+)?|"
    r"(?:password|passwd|access_token|sonar\.token|sonar\.login|client_secret|api[_-]?key)\s*[:=]\s*)\S+"
)


@dataclass(frozen=True, slots=True)
class FailedCheck:
    name: str
    evaluation_id: str
    build_id: int
    definition_id: int


@dataclass(frozen=True, slots=True)
class RepairSource:
    path: Path
    original: bytes
    text: str
    encoding: str
    newline: str


def failed_checks(tfs: TfsClient, pr: dict) -> list[FailedCheck]:
    return [
        FailedCheck(item["name"], item["evaluation_id"], item["build_id"], item["definition_id"])
        for item in tfs_merge_checks.current_build_policies(tfs, pr)
        if item["status"] == "rejected"
    ]


def _safe_text(value: str, limit: int) -> str:
    value = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[redacted]@", clean_log(value))
    return bounded_log(SECRET.sub(r"\1[redacted]", value), limit)


def _sonar_details(pr_id: int, errors: str) -> str:
    try:
        service = urlsplit(settings.tfs_sonar_url)
        if service.scheme not in {"http", "https"} or not service.hostname or service.username is not None:
            return ""
        for match in re.finditer(r"https?://[^\s]+", errors):
            dashboard = urlsplit(match.group().rstrip(".,);]"))
            if (dashboard.scheme, dashboard.netloc) != (service.scheme, service.netloc):
                continue
            query = parse_qs(dashboard.query)
            if dashboard.path.endswith("/dashboard") and query.get("pullRequest") == [str(pr_id)] and query.get("id"):
                key = query["id"][0]
                break
        else:
            return ""
        with httpx.Client(base_url=settings.tfs_sonar_url, timeout=8, trust_env=False, follow_redirects=False) as client:
            response = client.get("api/qualitygates/project_status", params={"projectKey": key, "pullRequest": pr_id})
            response.raise_for_status()
            gate = response.json()["projectStatus"]
            response = client.get("api/issues/search", params={
                "componentKeys": key, "pullRequest": pr_id, "resolved": "false", "ps": 30,
            })
            response.raise_for_status()
            issues = response.json().get("issues") or []
        conditions = [
            f"{c.get('metricKey')}={c.get('actualValue')}，阈值 {c.get('errorThreshold')}"
            for c in gate.get("conditions") or [] if c.get("status") == "ERROR"
        ]
        findings = [
            f"{str(i.get('component') or '').removeprefix(key + ':')}:{i.get('line') or '?'} "
            f"{i.get('rule') or ''} {i.get('message') or ''}" for i in issues
        ]
        return _safe_text("\n".join(conditions + findings), 6800)
    except (httpx.HTTPError, ValueError, KeyError):
        return ""


def _diagnose(pr: dict, log: list[str]) -> tuple[str, str]:
    selected = [line for line in map(clean_log, log) if ERROR_LINE.search(line)]
    errors = _safe_text("\n".join(dict.fromkeys(selected[-80:])), 8500)
    if re.search(r"quality gate status:\s*(?:failed|error)", errors, re.I):
        details = _sonar_details(pr["id"], errors)
        return ("source", details + "\n" + errors) if details else ("unknown", errors)
    if SOURCE_FAILURE.search(errors):
        return "source", errors
    if TRANSIENT_FAILURE.search(errors):
        return "transient", errors
    return "unknown", errors


def reconcile_pushed_repair(worker, detail: dict, states: list[dict], state: dict, source: str | None) -> bool:
    repair = state.get("pr_validation") or {}
    candidate = repair.get("pending_commit")
    if not candidate or source != candidate:
        return False
    previous = state["commit_hash"]
    state["commit_hash"] = candidate
    repair.update(phase="pushed", pending_commit="", previous_source=previous,
                  message=f"PR #{state['pr_id']} 已推送修复提交 {candidate[:12]}，等待 TFS 重新验证")
    context = detail.get("recovery_context") or {}
    context.setdefault("verified_commits", {})[state["name"]] = candidate
    fields = {"repository_states": states, "recovery_context": context}
    if detail.get("commit_hash") == previous:
        fields["commit_hash"] = candidate
    worker.store.update_request(detail["id"], **fields)
    worker.store.add_event(
        detail["id"], "pr.validation_repair_pushed", repair["message"],
        metadata={"pr_id": state["pr_id"], "build_id": repair.get("build_id"), "commit": candidate},
    )
    return True


def _checked_worktree(detail: dict, state: dict, pr: dict, tfs: TfsClient, expected: str) -> Path:
    root = Path(state["worktree_path"]).resolve()
    workspace = settings.worktree_dir.resolve()
    task_root = (workspace / detail["id"]).resolve()
    if task_root.parent != workspace or root.parent != task_root or not root.is_dir():
        raise RuntimeError("找不到本任务的隔离仓库")
    if git(root, "rev-parse", "HEAD") != expected or git(root, "branch", "--show-current") != state["branch"]:
        raise RuntimeError("隔离工作区的分支或提交已变化")
    origin = urlsplit(git(root, "remote", "get-url", "origin"))
    collection = urlsplit(tfs.base_url)
    repository_path = f"{unquote(collection.path).rstrip('/')}/{pr['project']}/_git/{pr['repository']}"
    if ((origin.scheme, origin.netloc) != (collection.scheme, collection.netloc)
            or unquote(origin.path).rstrip("/").removesuffix(".git").casefold() != repository_path.casefold()):
        raise RuntimeError("隔离仓库的 origin 与 PR 不一致")
    if git(root, "diff", "HEAD", "--name-only") or committable_changes(root, expected):
        raise RuntimeError("隔离工作区已有未提交改动")
    return root


def _assert_current_pr(tfs: TfsClient, state: dict, pr: dict) -> None:
    current = tfs.get_pull_request(state["repository_path"], pr["id"])
    if (current["status"] != "active" or current["source_commit"] != state["commit_hash"]
            or current["source_branch"] != state["branch"] or current["target_branch"] != state["base_branch"]
            or current["merge_commit"] != pr["merge_commit"]):
        raise RuntimeError("TFS 上的 PR 或合并预检提交已变化")


def _read_repair_sources(root: Path, allowed: set[str]) -> dict[str, RepairSource]:
    sources = {}
    for name in sorted(allowed):
        path = root / name
        if (path.resolve() != path or not path.is_relative_to(root) or not path.is_file()
                or path.stat().st_size > 2_000_000):
            continue
        raw = path.read_bytes()
        encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            newline = "\r\n" if "\r\n" in text else "\n"
            sources[name] = RepairSource(path, raw, text, encoding, newline)
    if not sources:
        raise RuntimeError("授权范围内没有可修复的文本源码")
    return sources


def _source_context(sources: dict[str, RepairSource], diagnostics: str) -> dict[str, str]:
    result = {}
    remaining = 110_000
    # Start with files named in diagnostics. Include affected methods and the file header.
    ordered = sorted(sources.items(), key=lambda entry: entry[1].path.name not in diagnostics)
    for name, source in ordered:
        if remaining < 1000:
            break
        limit = min(36_000, remaining)
        text = source.text.replace("\r\n", "\n")
        if len(text) > limit:
            lines = text.splitlines(keepends=True)
            pattern = re.escape(source.path.name) + r"\s*[:(]\[?(\d+)"
            line_numbers = {int(value) for value in re.findall(pattern, diagnostics, re.I)}
            indices = set(range(min(60, len(lines))))
            for line in sorted(line_numbers)[:8]:
                indices.update(range(max(0, line - 85), min(len(lines), line + 150)))
            snippets = []
            previous = -1
            for index in sorted(indices):
                if index != previous + 1:
                    snippets.append(f"\n…（跳至第 {index + 1} 行）…\n")
                snippets.append(lines[index])
                previous = index
            text = "".join(snippets)[:limit]
        result[name] = text
        remaining -= len(text)
    return result


def _apply_replacements(sources: dict[str, RepairSource], replacements: list[dict]) -> None:
    if not 0 < len(replacements) <= 20:
        raise RuntimeError("DevCore 没有返回可核对的源码替换")
    updated: dict[str, str] = {}
    for replacement in replacements:
        name = replacement.get("path")
        if name not in sources or not replacement.get("old") or "new" not in replacement:
            raise RuntimeError("自动修复包含超出范围或缺少原文的替换")
        source = sources[name]
        text = updated.get(name, source.text)
        old, new = (
            replacement[field].replace("\r\n", "\n").replace("\n", source.newline)
            for field in ("old", "new")
        )
        offset = text.find(old)
        if offset == -1 or text.find(old, offset + 1) != -1:
            raise RuntimeError(f"{name} 的修复原文缺失或不唯一")
        updated[name] = text[:offset] + new + text[offset + len(old):]
    if any(sources[name].path.read_bytes() != sources[name].original for name in updated):
        raise RuntimeError("应用修复前工作区源码已变化")
    for name, text in updated.items():
        source = sources[name]
        source.path.write_bytes(text.encode(source.encoding))


def _push_repair(worker, tfs: TfsClient, detail: dict, states: list[dict], state: dict, pr: dict,
                 repair: dict) -> None:
    old = state["commit_hash"]
    candidate = repair["pending_commit"]
    branch = state["branch"]
    request_id = detail["id"]
    if tfs.branch_head(state["repository_path"], branch) == candidate:
        reconcile_pushed_repair(worker, detail, states, state, candidate)
        return
    if repair.get("push_attempts", 0) >= 2:
        raise RuntimeError("修复提交未获远端确认，已保留本地提交")
    root = _checked_worktree(detail, state, pr, tfs, candidate)
    if git(root, "rev-parse", "HEAD^") != old:
        raise RuntimeError("修复提交无法从已校验的原提交快进")
    worker._check_cancelled(request_id)
    _assert_current_pr(tfs, state, pr)
    repair["push_attempts"] = repair.get("push_attempts", 0) + 1
    worker.store.update_request(request_id, repository_states=states)
    # The repair is a single child of old. The lease also rejects a deleted PR branch.
    ref = f"refs/heads/{branch}"
    git(root, "push", f"--force-with-lease={ref}:{old}", "origin", f"{candidate}:{ref}",
        env=git_authenticated_env(settings.tfs_pat))
    reconcile_pushed_repair(worker, detail, states, state, candidate)


def maintain(worker, detail: dict, states: list[dict], state: dict, pr: dict, tfs: TfsClient) -> str:
    request_id = detail["id"]
    pr_id = pr["id"]
    current = str(pr.get("source_commit") or "")
    expected = str(state.get("commit_hash") or "")
    if (pr["status"] != "active" or not SHA.fullmatch(expected) or not state.get("branch") or not pr.get("project_id")
            or pr.get("source_branch") != state["branch"] or pr.get("target_branch") != state.get("base_branch")):
        return ""
    if current != expected:
        return f"PR #{pr_id} 正在核对 TFS 的源提交，继续监控合并"

    verified = (detail.get("recovery_context") or {}).get("verified_commits") or {}
    if verified.get(state["name"]) != expected:
        return f"PR #{pr_id} 当前源提交缺少已校验的任务记录，继续监控合并"
    worker._check_cancelled(request_id)
    repair = state.setdefault("pr_validation", {})

    if repair.get("pending_commit"):
        if repair.get("push_attempts", 0) < 2:
            try:
                _push_repair(worker, tfs, detail, states, state, pr, repair)
            except TaskCancelled:
                raise
            except Exception as exc:
                repair["message"] = f"PR #{pr_id} 的修复提交待远端确认：{_safe_text(str(exc), 500)}"
                worker.store.update_request(request_id, repository_states=states)
        return repair["message"]

    definitions = java_code_checks.required_merge_builds(detail.get("policy_snapshot") or {}, state)
    gate = record_java_merge_checks(worker, detail, state, pr, tfs, definitions)
    if definitions and gate["status"] == "pending":
        return tfs_merge_checks.waiting_message(gate)

    ready = not definitions or gate["status"] == "passed"
    if ready and (pr.get("reviewer_approved") is False or not pr.get("auto_complete_enabled")):
        approval_source = current + ":" + pr["merge_commit"] if definitions else current
        if repair.get("approval_source") != approval_source:
            repair.update(approval_source=approval_source, approval_attempts=0, approval_error="")
        attempts = int(repair.get("approval_attempts") or 0)
        if attempts < 2 and (not attempts or repair.get("approval_error")):
            repair["approval_attempts"] = attempts + 1
            worker.store.update_request(request_id, repository_states=states)
            try:
                expected = {"expected_source": current}
                if definitions:
                    expected["expected_merge"] = pr["merge_commit"]
                if tfs.approve_pull_request(state["repository_path"], pr_id, **expected):
                    repair["approval_error"] = ""
                    worker.store.update_request(request_id, repository_states=states)
                    worker.store.add_event(request_id, "pr.approved", f"PR #{pr_id} 已由审核账号确认，继续自动完成")
            except Exception as exc:
                repair["approval_error"] = _safe_text(str(exc), 500)
                worker.store.update_request(request_id, repository_states=states)
                worker.store.add_event(request_id, "pr.auto_approval_blocked", repair["approval_error"], level="warning")
        return repair.get("approval_error") or f"PR #{pr_id} 正在核对自动审核和自动完成"

    if not SHA.fullmatch(str(pr.get("merge_commit") or "")):
        return f"PR #{pr_id} 正在等待 TFS 生成合并预检提交"
    checks = failed_checks(tfs, pr)
    if not checks:
        return tfs_merge_checks.waiting_message(gate) if definitions and not ready else ""
    check = checks[0]
    label = f"PR #{pr_id} 的必需检查 {check.name}（构建 #{check.build_id}）失败"
    if repair.get("build_id") == check.build_id:
        return repair.get("message") or label
    logs = tfs.failed_pull_request_build_log(pr, check.build_id, check.definition_id)
    if logs is None:
        return label + "，正在核对当前提交的构建记录"
    kind, diagnostics = _diagnose(pr, logs)
    repair.update(build_id=check.build_id, phase="blocked", message=label)
    worker.store.update_request(request_id, repository_states=states)
    worker.store.add_event(
        request_id, "pr.validation_failed", label, level="warning",
        metadata={"pr_id": pr_id, "build_id": check.build_id, "diagnostics": _safe_text(diagnostics, 1500)},
    )
    try:
        if kind == "transient" and check.evaluation_id and repair.get("requeued_source") != current:
            _assert_current_pr(tfs, state, pr)
            if check not in failed_checks(tfs, pr):
                return label + "，TFS 已更新检查结果"
            repair.update(requeued_source=current, phase="requeued", message=label + "，已重新排队")
            worker.store.update_request(request_id, repository_states=states)
            tfs.requeue_pull_request_policy(pr, check.evaluation_id)
            worker.store.add_event(request_id, "pr.validation_requeued", repair["message"])
        elif kind == "source" and int(repair.get("attempts") or 0) < MAX_REPAIRS:
            _repair_source(worker, detail, states, state, pr, tfs, repair, diagnostics)
        else:
            repair["message"] = label + (
                "，已达到自动修复次数上限" if kind == "source" else "，已记录失败原因并继续监控"
            )
    except TaskCancelled:
        raise
    except Exception as exc:
        repair.update(phase="blocked", message=f"{label}，自动修复暂停：{_safe_text(str(exc), 600)}")
        worker.store.add_event(request_id, "pr.validation_repair_blocked", repair["message"], level="warning")
    worker.store.update_request(request_id, repository_states=states)
    return repair["message"]


def record_java_merge_checks(worker, detail: dict, state: dict, pr: dict, tfs: TfsClient,
                             definitions: tuple[int, ...] = ()) -> dict:
    if not definitions and not state.get("java_code_check"):
        return {}
    try:
        receipt = tfs_merge_checks.evaluate(tfs, pr, definitions)
    except Exception as exc:
        if definitions:
            raise
        receipt = {"pr_id": pr["id"], "source_commit": pr.get("source_commit"), "merge_commit": pr.get("merge_commit"),
                   "status": "unavailable", "error": _safe_text(str(exc), 500)}
    if receipt != state.get("java_merge_check"):
        state["java_merge_check"] = receipt
        if receipt.get("builds") or receipt["status"] == "passed":
            worker.store.add_event(detail["id"], "pr.java_merge_check",
                                   f"已核对 PR #{pr['id']} 当前合并预览的 TFS 构建检查", metadata=receipt)
    return receipt


def _repair_source(worker, detail: dict, states: list[dict], state: dict, pr: dict,
                   tfs: TfsClient, repair: dict, diagnostics: str) -> None:
    request_id = detail["id"]
    project = detail["policy_snapshot"]
    original = state["commit_hash"]
    allowed = set(state.get("changed_files") or [])
    command = str(project.get("verification_command") or project.get("build_command") or "").strip()
    if not allowed or not command:
        raise RuntimeError("缺少该 PR 的源码范围或构建校验命令")
    root = _checked_worktree(detail, state, pr, tfs, original)
    sources = _read_repair_sources(root, allowed)
    repair.update(phase="repairing", attempts=int(repair.get("attempts") or 0) + 1,
                  message=f"正在修复 PR #{pr['id']} 的必需构建检查")
    worker.store.update_request(
        request_id, repository_states=states,
        next_poll_at=(datetime.now(UTC) + timedelta(seconds=2 * REPAIR_TIMEOUT + 120)).isoformat(),
    )
    worker.store.update_step(request_id, "deliver", "running", repair["message"])
    deadline = time.monotonic() + REPAIR_TIMEOUT

    def is_cancelled():
        return (worker.stop_event.is_set() or time.monotonic() >= deadline
                or worker.store.get_status(request_id) != "waiting_merge")

    load_model = getattr(worker.store, "model_config", None)
    result = CodexRunner().repair_pr_validation(
        cwd=root, detail=detail, sources=_source_context(sources, diagnostics), diagnostics=diagnostics,
        model_config=load_model(request_id) if callable(load_model) else None,
        on_event=lambda kind, text: worker.store.add_event(request_id, kind, text), is_cancelled=is_cancelled,
    )
    worker._check_cancelled(request_id)
    if is_cancelled():
        raise RuntimeError("PR 修复已超时或执行器已停止")
    if not result.get("fixed"):
        raise RuntimeError(_safe_text(str(result.get("summary") or "DevCore 未解决检查异常"), 350))
    if git(root, "rev-parse", "HEAD") != original or git(root, "branch", "--show-current") != state["branch"]:
        raise RuntimeError("修复时工作区 Git 状态发生变化")
    _apply_replacements(sources, result.get("replacements") or [])
    changed = set(committable_changes(root, original))
    if (not changed or not changed <= allowed
            or protected_changes([f"{state['name']}/{path}" for path in changed], project.get("protected_patterns") or [])):
        raise RuntimeError("修复未产生授权范围内的源码变更")
    git(root, "--literal-pathspecs", "add", "--", *sorted(changed))
    git(root, "diff", "--cached", "--check")
    if set(git(root, "-c", "core.quotepath=false", "diff", "--cached", "--name-only").splitlines()) != changed:
        raise RuntimeError("暂存区存在修复范围之外的改动")
    if CHECK_SUPPRESSION.search(git(root, "diff", "--cached", "--unified=0")):
        raise RuntimeError("修复改动压制了 Sonar 检查规则")
    scan_state = {**state, "changed_files": committable_changes(root, state["base_commit"])} if state.get("base_commit") else {}
    if scan_state and (report := java_code_checks.scan(project, scan_state)):
        repair["java_code_check"] = report
        if report["mode"] == "enforce":
            java_code_checks.verify_staged(report, scan_state)
        worker.store.add_event(request_id, "pr.java_repair_check", java_code_checks.report_message(report),
                               metadata=report, level="info" if report["status"] == "passed" else "warning")
    git(root, "commit", "--no-verify", "-m", f"fix(#{detail['work_item_id']}): 修复 PR 必需构建检查")
    candidate = git(root, "rev-parse", "HEAD")
    def check_build():
        worker._check_cancelled(request_id)
        if worker.stop_event.is_set():
            raise RuntimeError("执行器已停止，已保留修复提交")

    run_command(command, root.parent if len(states) > 1 else root, timeout_minutes=REPAIR_TIMEOUT // 60,
                env_overrides=worker._build_environment(detail, [state]), cancel_check=check_build)
    _checked_worktree(detail, state, pr, tfs, candidate)
    repair.update(phase="pending_push", pending_commit=candidate, push_attempts=0,
                  message=f"PR #{pr['id']} 的修复已通过本地校验，正在核对远端提交")
    worker.store.update_request(request_id, repository_states=states)
    _push_repair(worker, tfs, detail, states, state, pr, repair)
