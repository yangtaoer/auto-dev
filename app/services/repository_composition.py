"""Check the registered source scope against the fetched delivery entry point."""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from .delivery import git


def validate_starter_composition(project: dict, repositories: list[Path]) -> None:
    contract = (project.get("quality_profile") or {}).get("repository_composition") or {}
    if not contract:
        return
    starter_name = str(contract.get("starter_repository") or "dcsd-springboot-starter")
    profile_name = str(contract.get("maven_profile") or "").strip()
    starter = next((repo for repo in repositories if repo.name == starter_name), None)
    if starter is None or not profile_name:
        raise RuntimeError("项目仓库组成校验缺少 starter 仓库或 Maven profile，未创建研发工作区")
    branch = (project.get("repository_base_branches") or {}).get(starter_name) or project.get("base_branch") or "dev"
    source = git(starter, "show", f"origin/{branch}:pom.xml")
    try:
        pom = ET.fromstring(source)
    except ET.ParseError as exc:
        raise RuntimeError("最新 starter pom.xml 无法解析，未创建研发工作区") from exc
    namespace = pom.tag.partition("}")[0] + "}" if pom.tag.startswith("{") else ""
    tag = lambda name: namespace + name
    profiles = [node for node in pom.findall(f"{tag('profiles')}/{tag('profile')}")
                if node.findtext(tag("id")) == profile_name]
    if len(profiles) != 1:
        raise RuntimeError(f"最新 starter 必须存在唯一 Maven profile {profile_name}，未创建研发工作区")
    # Binary-only dependencies must be named explicitly in the project's contract.
    binary_only = set(contract.get("binary_only_dependencies") or [])
    required = {starter_name}
    for dependency in profiles[0].findall(f"{tag('dependencies')}/{tag('dependency')}"):
        group = dependency.findtext(tag("groupId"), "").strip()
        artifact = dependency.findtext(tag("artifactId"), "").strip()
        if not (group == "com.tellhow" or group.startswith("com.tellhow.")) or dependency.findtext(tag("scope")) == "test":
            continue
        if not artifact or "${" in artifact:
            raise RuntimeError(f"{profile_name} 存在无法确定的内部依赖，未创建研发工作区")
        if artifact not in binary_only:
            required.add(artifact)
    configured = {repo.name for repo in repositories}
    missing, extra = sorted(required - configured), sorted(configured - required)
    if missing or extra:
        details = []
        if missing:
            details.append("缺少仓库：" + "、".join(missing))
        if extra:
            details.append("未被该 profile 引用的仓库：" + "、".join(extra))
        raise RuntimeError(f"项目仓库清单与最新 starter profile {profile_name} 不一致；"
                           + "；".join(details) + "。请同步项目仓库配置；未开始研发或提交代码")


def unregistered_workspaces(repository_states: list[dict]) -> list[str]:
    """Never silently adopt a repository created by the model outside the frozen scope."""
    if not repository_states:
        return []
    roots = {Path(state["worktree_path"]).resolve() for state in repository_states}
    parents = {root.parent for root in roots}
    problems = []
    for parent in parents:
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.is_dir() and (child / ".git").exists() and child.resolve() not in roots:
                problems.append(f"工作区存在未登记仓库 {child.name}，其文件未纳入提交范围；"
                                "请修正项目仓库配置并保留现有修改，不得将其报告为零代码变更或已交付")
    return sorted(set(problems))
