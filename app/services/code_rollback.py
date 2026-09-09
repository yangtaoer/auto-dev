"""Prepare inverse commits in fresh worktrees; never reset a target or force-push it."""
from __future__ import annotations

import re
from pathlib import Path

from .delivery import git, run_command
from .commit_policy import stage_delivery_changes
from .process_env import git_authenticated_env, sanitized_process_env
from .tfs import TfsClient
from ..config import settings


def prepare(repository: Path, target: str, commit: str | list[str], destination: Path, branch: str, subject: str, *, env=None) -> dict:
    commits = [commit] if isinstance(commit, str) else commit
    if not commits or len(set(commits)) != len(commits) or any(not re.fullmatch(r'[0-9a-fA-F]{40}', c) for c in commits):
        raise RuntimeError('缺少可信的完整提交号，拒绝猜测回退范围')
    git(repository, 'check-ref-format', '--branch', target)
    git(repository, 'fetch', 'origin', target, env=env)
    tip = git(repository, 'rev-parse', f'origin/{target}')
    parent_counts = {}
    for index, item in enumerate(commits):
        git(repository, 'merge-base', '--is-ancestor', item, tip)
        if index:
            git(repository, 'merge-base', '--is-ancestor', commits[index - 1], item)
        parents = git(repository, 'rev-list', '--parents', '-n', '1', item).split()[1:]
        if len(parents) not in {1, 2}:
            raise RuntimeError('不支持根提交或多父级合并的自动回退')
        parent_counts[item] = len(parents)
    if destination.exists():
        raise RuntimeError('回退工作区已存在，需核查上次执行结果，不能覆盖现场')
    destination.parent.mkdir(parents=True, exist_ok=True)
    git(repository, 'worktree', 'add', '-b', branch, str(destination), tip)
    git(destination, 'config', 'user.name', 'AutoDev Codex')
    git(destination, 'config', 'user.email', 'autodev@localhost')
    try:
        for item in reversed(commits):
            git(destination, 'revert', '--no-commit', *(['-m', '1'] if parent_counts[item] == 2 else []), item)
    except RuntimeError as exc:
        # Retain this isolated conflict workspace, without touching any user's checkout.
        raise RuntimeError(f'本次改动与后续代码冲突，未推送；现场保留在 {destination}: {exc}') from exc
    included, excluded = stage_delivery_changes(destination)
    if not included:
        raise RuntimeError('没有可回退的业务代码变更（可能已被其他提交撤销）')
    git(destination, 'diff', '--cached', '--check')
    git(destination, 'commit', '--no-verify', '-m', subject)
    return {'repository_path': str(repository), 'worktree_path': str(destination), 'base_branch': target,
            'original_commit': commits[-1], 'original_commits': commits, 'target_before': tip, 'branch': branch,
            'revert_commit': git(destination, 'rev-parse', 'HEAD'), 'changed_files': included,
            'excluded_validation_files': excluded, 'status': 'prepared'}


def execute(control: dict, detail: dict, save) -> tuple[str, str, dict]:
    project = detail['policy_snapshot']
    result = control.get('result') or {}
    control['result'] = result
    states = result.setdefault('repositories', [])
    env = git_authenticated_env(settings.tfs_pat) if settings.tfs_pat else sanitized_process_env()
    tfs = TfsClient(project['tfs_collection_url'])
    if not states:
        sources = [s for s in detail.get('repository_states') or [] if s.get('changed_files') and (s.get('merge_commit') or s.get('commit_hash'))]
        if not sources:
            raise RuntimeError('没有可核验的本次提交')
        allowed = {str(Path(p).resolve()).casefold() for p in project.get('repository_paths') or [project['repository_path']]}
        for index, source in enumerate(sources):
            repository = Path(source['repository_path']).resolve()
            if str(repository).casefold() not in allowed:
                raise RuntimeError('历史记录中的仓库不属于本项目，拒绝回退')
            target = source.get('base_branch') or project.get('base_branch') or 'dev'
            expected = (project.get('repository_base_branches') or {}).get(source.get('name'), project.get('base_branch') or 'dev')
            if target != expected:
                raise RuntimeError('历史目标分支与项目配置不一致，需先确认回退目标')
            commit = source.get('merge_commit') or source.get('commit_hash')
            if source.get('pr_id'):
                pr = tfs.get_pull_request(str(repository), int(source['pr_id']))
                if pr['status'] != 'completed' or pr['target_branch'] != target or pr['merge_commit'] != commit:
                    raise RuntimeError('原 PR 合并记录与回退提交不一致，已停止')
            elif source.get('status') not in {'base_pushed', 'completed'} and not source.get('base_branch_commit'):
                raise RuntimeError('无法确认本次提交已进入目标分支')
            elif source.get('delivered_commits'):
                commit = source['delivered_commits']
            else:
                # Legacy local-package tasks only stored the final build tip. Never
                # mistake another requirement's newer commit for this task's change.
                subject = git(repository, 'show', '-s', '--format=%s', commit)
                paths = set(git(repository, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines())
                if f"(#{detail['work_item_id']})" not in subject or paths != set(source['changed_files']):
                    raise RuntimeError('历史本地交付缺少精确提交映射，构建提交与本次范围无法核对，未回退')
            branch = f"codex/rollback-{detail['work_item_id']}-{control['id'][:8]}-{index}"
            destination = settings.data_dir / 'rollback-workspaces' / control['id'] / repository.name
            state = prepare(repository, target, commit, destination, branch,
                            f"revert(#{detail['work_item_id']}):回退任务 {detail['id'][:8]} [{control['id']}]", env=env)
            state['name'] = source.get('name') or repository.name
            # Prepare ALL repositories before publishing any of them.
            states.append(state)
            save('running', '已准备 ' + state['name'] + '，尚未向目标分支提交', result)
        save('running', '全部仓库反向提交已准备完成，正在校验', result)
        verification = str(project.get('verification_command') or '').strip()
        if verification:
            # Match the ordinary development workspace layout and changed-side
            # contract (APP scripts locate child repositories by their real name).
            root = Path(states[0]['worktree_path'])
            if len(allowed) > 1:
                root = root.parent
            import json
            run_command(verification, root, env_overrides={
                'AUTODEV_CHANGED_REPOSITORIES': json.dumps([s['name'] for s in states]),
                'AUTODEV_WORK_ITEM_ID': str(detail['work_item_id']),
                'AUTODEV_REQUEST_ID': control['id'],
                'AUTODEV_APP_FRONTEND_SOURCE': next((s['repository_path'] for s in states if s['name'] == 'dcsd-app-ui'), ''),
            })
        result['prepared_all'] = True
        save('running', '反向提交校验完成，开始按原交付策略提交', result)
    if not result.get('prepared_all'):
        raise RuntimeError('回退准备不完整，未继续推送；请核查保留现场')
    for state in states:
        worktree = Path(state['worktree_path'])
        if state['status'] == 'prepared':
            git(worktree, 'push', '--no-verify', '-u', 'origin', state['branch'], env=env)
            if detail['delivery_mode'] == 'local_package':
                # Ordinary non-fast-forward protection prevents overwriting concurrent work.
                git(worktree, 'push', '--no-verify', 'origin', f"HEAD:refs/heads/{state['base_branch']}", env=env)
                state['status'] = 'completed'
                state['merge_commit'] = state['revert_commit']
            else:
                pr = tfs.create_pull_request(str(worktree), state['branch'], state['base_branch'],
                                            f"revert(#{detail['work_item_id']}):回退需求改动 {detail['id'][:8]}",
                                            detail['work_item_id'], f"原任务 {detail['id']}；反向提交 {state['revert_commit']}。仅代码回退，不执行数据库回滚或环境部署。")
                state.update(pr_id=pr['PullRequestId'], pr_url=pr['WebUrl'], status='waiting_merge')
                # Record the PR before approval (approval can merge immediately).
                save('running', '回退 PR 已创建', result)
                if detail['delivery_mode'] in {'sichuan_auto_review', 'sichuan_review_local_package'}:
                    tfs.approve_pull_request(str(worktree), int(state['pr_id']))
            save('running', '已提交 ' + state['name'] + ' 的回退改动', result)
        if state['status'] == 'waiting_merge':
            pr = tfs.get_pull_request(state['repository_path'], int(state['pr_id']))
            if pr['status'] == 'abandoned':
                raise RuntimeError('回退 PR 已被放弃，请检查操作记录')
            if pr['status'] == 'completed':
                state.update(status='completed', merge_commit=pr['merge_commit'])
    if all(s['status'] == 'completed' for s in states):
        return 'completed', '本次任务的代码已回退至目标分支；未改写历史，也未自动回滚数据库或重新部署', result
    return 'waiting_merge', '回退 PR 等待原项目审核/合并策略通过，尚未完成目标分支回退', result
