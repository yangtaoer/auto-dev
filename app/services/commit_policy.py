"""Global source-delivery boundary: validation aids remain local, never in a PR."""
from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from .process_env import sanitized_process_env


COMMIT_POLICY_INSTRUCTIONS = """所有项目统一遵守提交边界：test/、tests/ 目录下的 Python 文件，以及 test*.py、*_test.py、conftest.py 测试脚本仅用于本地验证，不进入代码提交；任何层级 docs/ 目录下的 JSON 文件（含验证清单、菜单绑定清单）也不得提交。临时验证材料优先存放在仓库外的本地验证目录；可执行这些检查，但将结果写入结构化研发结果，不依赖提交这些文件作为交付依据。不要删除仓库已有的测试或 docs JSON，不将业务实现放进被排除路径，也不要通过改名规避规则。正常业务 Python、JS、Java、XML、SQL 及 Markdown 文档按需求提交。平台会在提交前再次过滤暂存区。"""


def is_local_validation_file(path: str) -> bool:
    parts = PurePosixPath(str(path).replace("\\", "/")).parts
    if not parts:
        return False
    name = parts[-1].casefold()
    directories = {part.casefold() for part in parts[:-1]}
    return (name.endswith(".json") and "docs" in directories) or (
        name.endswith(".py") and (bool(directories & {"test", "tests"}) or
                                 name.startswith("test") or name.endswith("_test.py") or name == "conftest.py")
    )


def _git(worktree: Path, *args: str, paths: list[str] | None = None) -> str:
    result = subprocess.run(["git", "-C", str(worktree), *args],
                            input="".join(f":(literal){path}\0" for path in paths) if paths is not None else None,
                            capture_output=True, text=True, encoding="utf-8", errors="strict",
                            env=sanitized_process_env(), check=False)
    if result.returncode:
        raise RuntimeError(f"提交文件检查失败：{result.stderr.strip()}")
    return result.stdout


def _paths(worktree: Path, *args: str) -> list[str]:
    return [path for path in _git(worktree, *args).split("\0") if path]


def committable_changes(worktree: Path, base_commit: str) -> list[str]:
    paths = _paths(worktree, "diff", "--no-renames", "--name-only", "-z", base_commit, "--")
    paths += _paths(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    return list(dict.fromkeys(path for path in paths if not is_local_validation_file(path)))


def stage_delivery_changes(worktree: Path) -> tuple[list[str], list[str]]:
    """Reset only excluded index entries to HEAD; keep every working file intact.

    Rename pairs are atomic: neither side is submitted when one side is excluded.
    NUL pathspecs support Chinese names, spaces and metacharacters without globbing.
    """
    _git(worktree, "add", "--all")
    status = iter(_paths(worktree, "diff", "--cached", "--name-status", "-z", "--find-renames"))
    excluded: list[str] = []
    for code in status:
        paths = [next(status)]
        if code.startswith(("R", "C")):
            paths.append(next(status))
        if any(is_local_validation_file(path) for path in paths):
            excluded.extend(paths)
    excluded = list(dict.fromkeys(excluded))
    if excluded:
        _git(worktree, "restore", "--source=HEAD", "--staged", "--pathspec-from-file=-", "--pathspec-file-nul", paths=excluded)
    included = _paths(worktree, "diff", "--cached", "--no-renames", "--name-only", "-z")
    if any(is_local_validation_file(path) for path in included):
        raise RuntimeError("暂存区仍包含本地验证材料，已停止提交")
    return included, excluded


def assert_delivery_history(worktree: Path, base_commit: str) -> None:
    """Do not push excluded files hidden inside agent-created intermediate commits."""
    _git(worktree, "merge-base", "--is-ancestor", base_commit, "HEAD")
    for commit in _git(worktree, "rev-list", f"{base_commit}..HEAD").splitlines():
        paths = _paths(worktree, "diff-tree", "--root", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", commit)
        forbidden = [path for path in paths if is_local_validation_file(path)]
        if forbidden:
            raise RuntimeError("工作区自行创建的提交包含禁止交付的验证文件，未推送：" + "、".join(forbidden[:20]))


def preserve_validation_before_sync(worktree: Path) -> str:
    """Keep excluded dirty files in a local Git stash before rebase/target sync.

    The stash is deliberately retained, not blindly applied over a newer target.
    Only the feature/target refs are pushed, so this recovery copy stays local.
    """
    dirty = _paths(worktree, "diff", "--no-renames", "--name-only", "-z", "HEAD", "--")
    dirty += _paths(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    excluded = list(dict.fromkeys(path for path in dirty if is_local_validation_file(path)))
    if not excluded:
        return ""
    _git(worktree, "stash", "push", "--include-untracked", "--message", "AutoDev local validation (not delivered)",
         "--pathspec-from-file=-", "--pathspec-file-nul", paths=excluded)
    return _git(worktree, "rev-parse", "refs/stash").strip()
