"""Repository-scoped Java preflight using Checkstyle and Alibaba P3C."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from ..config import ROOT, settings
from .command_logs import bounded_log, decode_output
from .commit_policy import committable_changes
from .process_env import sanitized_process_env


TOOL_ROOT = ROOT / "local-runner" / "java-checks"
_TOOL_LOCK = threading.Lock()
_CONFIG_FILES = ("pom.xml", "settings.xml", "checkstyle.xml", "p3c.xml", "BusinessCodeCheck.java", "messages.properties")
_MAX_FINDINGS = 60
_SUPPRESSION = re.compile(r"\b(?:NOSONAR|sonar[._-](?:skip|exclusions)|(?:checkstyle|pmd)\.skip)\b", re.I)
JAVA_INSTRUCTIONS = (
    "后端 Java 修改遵守本仓库既有命名、分层与依赖版本。单方法最多 80 行有效代码，循环与判断合计最多嵌套 3 层。"
    "保留异常语义和日志证据，禁止空 catch、printStackTrace、明文敏感日志和 SQL 值拼接。"
    "变更核心业务逻辑时执行对应单测。不要在改动中新增扫描屏蔽。"
    "根据项目方言和现有框架处理数据库、事务及校验规则。"
)


def profile_for(project: dict, state: dict) -> dict | None:
    if project.get("simulation_mode"):
        return None
    quality = project.get("quality_profile") or {}
    profiles = quality.get("java_code_checks") or []
    if not isinstance(profiles, list):
        raise RuntimeError("Java 扫描配置应按仓库与分支登记")
    for profile in profiles:
        if (isinstance(profile, dict) and profile.get("repository") == state.get("name")
                and profile.get("target_branch") == state.get("base_branch")):
            if profile.get("mode") not in {"observe", "enforce"}:
                raise RuntimeError("Java 扫描配置的模式无效")
            return profile
    return None


def required_merge_builds(project: dict, state: dict) -> tuple[int, ...]:
    profile = profile_for(project, state) or {}
    # Observation can record official results without changing the review state.
    if profile.get("mode") != "enforce":
        return ()
    definitions = profile.get("required_merge_builds") or []
    if not isinstance(definitions, list) or any(type(item) is not int or item <= 0 for item in definitions):
        raise RuntimeError("Java 扫描配置中的 TFS 合并验证定义编号无效")
    return tuple(dict.fromkeys(definitions))


def _run(args: list[str], cwd: Path, timeout: int, *, accepted=(0,)) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL,
                            env=sanitized_process_env(), timeout=timeout)
    if accepted is not None and result.returncode not in accepted:
        output = bounded_log(decode_output(result.stderr + b"\n" + result.stdout), 1800)
        raise RuntimeError(output or f"检查命令返回退出码 {result.returncode}")
    return result


def _tools(cache: Path) -> tuple[str, str, str]:
    """Share downloaded dependencies and compiled checks between task slots."""
    java, javac = shutil.which("java"), shutil.which("javac")
    if not java or not javac:
        raise RuntimeError("Java 扫描需要执行器上的 JDK")
    key = hashlib.sha256(b"".join((TOOL_ROOT / name).read_bytes() for name in _CONFIG_FILES)).hexdigest()[:16]
    directory = cache / key
    classpath = directory / "classpath.txt"
    classes = directory / "classes"
    marker = classes / "autodev" / "javachecks" / "BusinessCodeCheck.class"
    with _TOOL_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        resolved = classpath.read_text(encoding="utf-8").strip() if classpath.is_file() else ""
        if not resolved or not all(Path(path).is_file() for path in resolved.split(os.pathsep)):
            maven = shutil.which("mvn.cmd" if os.name == "nt" else "mvn")
            if not maven:
                raise RuntimeError("Java 扫描需要执行器上的 Maven 来准备固定版本的工具")
            staged = directory / "classpath.pending"
            staged.unlink(missing_ok=True)
            _run([maven, "--batch-mode", "--quiet", "--no-transfer-progress", "--settings",
                  str(TOOL_ROOT / "settings.xml"), "--file", str(TOOL_ROOT / "pom.xml"),
                  f"-Dmaven.repo.local={cache / 'maven'}",
                  "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:build-classpath",
                  f"-Dmdep.outputFile={staged}"], directory, 240)
            resolved = decode_output(staged.read_bytes()).strip()
            if not resolved:
                raise RuntimeError("扫描工具依赖没有生成有效类路径")
            staged.write_text(resolved, encoding="utf-8")
            staged.replace(classpath)
        messages = marker.parent / "messages.properties"
        if not marker.is_file() or not messages.is_file():
            classes.mkdir(parents=True, exist_ok=True)
            _run([javac, "-encoding", "UTF-8", "-classpath", resolved, "-d", str(classes),
                  str(TOOL_ROOT / "BusinessCodeCheck.java")], directory, 45)
            shutil.copyfile(TOOL_ROOT / "messages.properties", messages)
    return java, resolved, key


def _tree_read(root: Path, revision: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "-C", str(root), "show", f"{revision}:{path}"],
                            capture_output=True, env=sanitized_process_env(), timeout=20)
    if result.returncode == 0:
        return result.stdout
    if not revision and (b"not in the index" in result.stderr or b"neither on disk nor in the index" in result.stderr):
        return None
    exists = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{revision}^{{tree}}"],
                            capture_output=True, env=sanitized_process_env(), timeout=20)
    if exists.returncode == 0 and (b"does not exist in" in result.stderr or b"exists on disk, but not in" in result.stderr):
        return None
    raise RuntimeError(f"无法读取扫描基线中的 {path}")


def _normal(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _sources(state: dict, *, staged=False) -> tuple[dict[str, bytes], dict[str, bytes], list[dict]]:
    root = Path(state["worktree_path"]).resolve()
    base = state["base_commit"]
    candidate, baseline, findings = {}, {}, []
    for name in dict.fromkeys(state.get("changed_files") or []):
        relative = PurePosixPath(str(name).replace("\\", "/"))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("扫描文件路径超出工作区")
        path = relative.as_posix()
        suffix = relative.suffix.lower()
        if suffix not in {".java", ".xml", ".properties", ".yml", ".yaml"}:
            continue
        source = (root / path).resolve()
        if not source.is_relative_to(root):
            raise RuntimeError("扫描文件路径超出工作区")
        if not staged and not source.is_file():
            continue
        raw = _tree_read(root, "", path) if staged else source.read_bytes()
        if raw is None:
            continue
        old = _tree_read(root, base, path)
        normalized = _normal(raw)
        if suffix == ".java":
            # Legacy Java projects can declare GBK on Windows. The scanners
            # both read the same UTF-8 snapshot, preserving original line numbers.
            candidate[path] = decode_output(normalized).encode("utf-8")
            if old is not None:
                baseline[path] = decode_output(_normal(old)).encode("utf-8")
        findings.extend(_suppression_changes(path, old or b"", normalized))
    return candidate, baseline, findings


def _suppression_changes(path: str, old: bytes, new: bytes) -> list[dict]:
    if not _SUPPRESSION.search(decode_output(new)):
        return []
    prev, curr = decode_output(old).splitlines(), decode_output(new).splitlines()
    matcher = SequenceMatcher(None, prev, curr, autojunk=False)
    return [{"tool": "AutoDev", "rule": "SCAN_SUPPRESSION", "path": path, "line": index + 1,
             "message": "新变更包含扫描跳过或排除设置", "severity": "error"}
            for tag, _, _, first, last in matcher.get_opcodes() if tag != "equal"
            for index in range(first, last) if _SUPPRESSION.search(curr[index])]


def _put_sources(folder: Path, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _xml_results(report: Path, root: Path, tool: str) -> list[dict]:
    tree = ElementTree.parse(report)
    findings = []
    expected = "checkstyle" if tool == "Checkstyle" else "pmd"
    local_name = lambda element: element.tag.rsplit("}", 1)[-1]
    if local_name(tree.getroot()) != expected:
        raise RuntimeError(f"{tool} 没有返回有效扫描报告")
    for item in tree.getroot():
        if local_name(item) in {"error", "configerror", "processingerror"}:
            raise RuntimeError(f"{tool} 无法分析源文件或规则配置")
        if local_name(item) != "file":
            continue
        relative = Path(item.attrib["name"]).resolve().relative_to(root.resolve()).as_posix()
        for entry in item:
            if local_name(entry) not in {"error", "violation"}:
                continue
            rule = entry.get("source" if tool == "Checkstyle" else "rule", "unknown").rsplit(".", 1)[-1]
            findings.append({
                "tool": tool, "rule": rule, "path": relative,
                "line": int(entry.get("line") or entry.get("beginline") or 0),
                "message": bounded_log(entry.get("message") or (entry.text or "").strip(), 400),
                "severity": entry.get("severity") or "warning",
            })
    return findings


def _scan_snapshot(cache: Path, folder: Path, files: dict[str, bytes], tooling: tuple[str, str, str]) -> list[dict]:
    if not files:
        return []
    java, classpath, key = tooling
    classpath += os.pathsep + str(cache / key / "classes")
    root = folder / "source"
    _put_sources(root, files)
    checkstyle = folder / "checkstyle.xml"
    p3c = folder / "p3c.xml"
    checked = _run([java, "-Duser.language=en", "-classpath", classpath, "com.puppycrawl.tools.checkstyle.Main",
                    "-c", str(TOOL_ROOT / "checkstyle.xml"), "-f", "xml", "-o", str(checkstyle), str(root)],
                   folder, 100, accepted=None)
    if not checkstyle.is_file():
        raise RuntimeError("Checkstyle 没有返回扫描报告")
    style_findings = _xml_results(checkstyle, root, "Checkstyle")
    errors = sum(item["severity"] == "error" for item in style_findings)
    if checked.returncode != 0 and (not errors or checked.returncode not in {1, errors, errors % 256}):
        raise RuntimeError("Checkstyle 命令未正常完成")
    _run([java, "-classpath", classpath, "net.sourceforge.pmd.PMD", "-d", str(root),
          "-R", str(TOOL_ROOT / "p3c.xml"), "-encoding", "UTF-8", "-f", "xml", "-reportfile", str(p3c)],
         folder, 100, accepted=(0, 4))
    return style_findings + _xml_results(p3c, root, "P3C")


def _introduced(findings: list[dict], baseline: list[dict], before: dict[str, bytes],
                after: dict[str, bytes]) -> list[dict]:
    previous = {(item["tool"], item["rule"], item["path"], item["line"], item["message"]) for item in baseline}
    corresponding = {}
    for path in before.keys() & after.keys():
        old, new = before[path].splitlines(), after[path].splitlines()
        for a, b, size in SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks():
            corresponding.update({(path, b + n + 1): a + n + 1 for n in range(size)})
    return [item for item in findings if (item["tool"], item["rule"], item["path"],
            corresponding.get((item["path"], item["line"])), item["message"]) not in previous]


def _fingerprint(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def _test_reports(root: Path) -> dict[Path, tuple[int, int]]:
    reports = {}
    for path in root.rglob("target/surefire-reports/TEST-*.xml"):
        if path.is_file() and path.resolve().is_relative_to(root):
            stat = path.stat()
            reports[path] = (stat.st_mtime_ns, stat.st_size)
    return reports


def _unit_tests(state: dict, config: dict) -> dict:
    root = Path(state["worktree_path"]).resolve()
    try:
        if not isinstance(config, dict):
            raise ValueError("单测配置无效")
        selectors = config["classes"]
        if not isinstance(selectors, list) or not selectors or any(
            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.$*?]+", value) for value in selectors
        ):
            raise ValueError("单测配置需要明确的测试类选择器")
        profile = config.get("maven_profile") or ""
        if profile and not re.fullmatch(r"[A-Za-z0-9_.-]+", profile):
            raise ValueError("单测 Maven profile 无效")
        pom = (root / str(config.get("pom") or "pom.xml")).resolve()
        if not pom.is_relative_to(root) or not pom.is_file():
            raise ValueError("单测 pom.xml 不在当前工作区内")
        maven = shutil.which("mvn.cmd" if os.name == "nt" else "mvn")
        if not maven:
            raise RuntimeError("执行器上缺少 Maven")
        args = [maven, "--batch-mode", "--file", str(pom), "-DskipTests=false", "-Dmaven.test.skip=false",
                "-DfailIfNoTests=true", "-Dsurefire.failIfNoSpecifiedTests=true", f"-Dtest={','.join(selectors)}"]
        if profile:
            args.extend(("-P", profile))
        before = _test_reports(pom.parent)
        _run([*args, "test"], root, 360)
        executed = 0
        for path, stamp in _test_reports(pom.parent).items():
            if stamp == before.get(path):
                continue
            suite = ElementTree.parse(path).getroot()
            if suite.tag != "testsuite" or int(suite.get("failures", "0")) or int(suite.get("errors", "0")):
                raise RuntimeError("本次 Surefire 报告包含测试失败或无效数据")
            executed += int(suite.get("tests", "0")) - int(suite.get("skipped", "0"))
        if executed <= 0:
            raise RuntimeError("本次 Maven 运行未生成实际执行用例的 Surefire 报告")
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.SubprocessError, ElementTree.ParseError) as exc:
        return {"status": "failed", "message": bounded_log(str(exc), 1200)}
    return {"status": "passed", "message": f"已通过 {executed} 个 Maven 单元测试", "classes": selectors}


def scan(project: dict, state: dict) -> dict | None:
    profile = profile_for(project, state)
    if not profile:
        return None
    report = {"repository": state["name"], "target_branch": state["base_branch"],
              "base_commit": state.get("base_commit"), "mode": profile["mode"]}
    try:
        after, before, suppressions = _sources(state)
        if not after and not suppressions:
            if profile["mode"] == "observe":
                return None
            return {**report, "status": "skipped", "files": {}}
        findings = suppressions
        existing = 0
        if after:
            cache = (settings.data_dir / "java-checks-tools").resolve()
            tooling = _tools(cache)
            with tempfile.TemporaryDirectory(prefix="autodev-java-checks-") as directory:
                folder = Path(directory).resolve()
                previous = _scan_snapshot(cache, folder / "baseline", before, tooling)
                current = _scan_snapshot(cache, folder / "candidate", after, tooling)
            new = _introduced(current, previous, before, after)
            existing = len(current) - len(new)
            findings = suppressions + new
            report["toolchain"] = tooling[2]
        p3c_blocking = set(profile.get("blocking_p3c_rules") or [])
        for item in findings:
            item["blocking"] = profile["mode"] == "enforce" and (
                (item["tool"] != "P3C" and item["severity"] == "error")
                or (item["tool"] == "P3C" and item["rule"] in p3c_blocking)
            )
        blocked = sum(item["blocking"] for item in findings)
        if after and (test_config := profile.get("unit_tests")):
            report["unit_tests"] = _unit_tests(state, test_config)
        return {**report, "status": "blocked" if blocked else ("warning" if findings else "passed"),
                "previous_findings": existing, "new_findings": len(findings), "blocking_count": blocked,
                "findings": sorted(findings, key=lambda item: not item["blocking"])[:_MAX_FINDINGS],
                "files": _fingerprint(after)}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ElementTree.ParseError) as exc:
        return {**report, "status": "unavailable", "error": bounded_log(str(exc), 1800)}


def verify_staged(report: dict, state: dict) -> None:
    if report.get("mode") != "enforce":
        return
    if (report.get("status") not in {"passed", "warning", "skipped"} or report["base_commit"] != state["base_commit"]
            or (report.get("unit_tests") or {}).get("status") == "failed"):
        raise RuntimeError("提交缺少当前基线的 Java 源码扫描通过记录")
    changed = committable_changes(Path(state["worktree_path"]), state["base_commit"], staged=True)
    files, _, suppressions = _sources({**state, "changed_files": changed}, staged=True)
    if _fingerprint(files) != report.get("files") or suppressions:
        raise RuntimeError("暂存的 Java 源码与扫描快照不一致，须重新校验")


def report_message(report: dict) -> str:
    prefix = f"{report['repository']} / {report['target_branch']}"
    if report["status"] == "unavailable":
        return f"{prefix} Java 扫描未完成：{report['error']}"
    if report["status"] == "skipped":
        return f"{prefix} 没有需要扫描的 Java 变更"
    return (f"{prefix} Java 扫描新增 {report['new_findings']} 项，"
            f"目标基线已有 {report['previous_findings']} 项")
