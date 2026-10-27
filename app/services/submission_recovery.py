"""Resume only the reviewed-submission checkpoint. Never re-run development or push code."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from ..config import settings
from ..domain import SICHUAN_APPROVAL_DELIVERY_MODES
from .artifact_contract import enforce_artifact_policy
from .delivery import git, run_command
from .execution_recovery import submission_ready
from .tfs import TfsClient


def reconcile_pr(tfs: TfsClient, state: dict, work_item_id: int, description: str, *, dry_run: bool = False) -> dict:
    root=Path(state['worktree_path'])
    expected=state['commit_hash']
    if not root.is_dir() or git(root,'rev-parse','HEAD') != expected:
        raise RuntimeError(f"{state['name']} 隔离工作区与冻结提交不一致，未重复研发或推送")
    if git(root,'rev-parse','--abbrev-ref','HEAD') != state['branch']:
        raise RuntimeError(f"{state['name']} 当前分支已变化，已保留现场")
    if git(root,'diff','HEAD','--name-only') or git(root,'diff','--cached','--name-only'):
        raise RuntimeError(f"{state['name']} 存在未提交的已跟踪文件修改，不能静默覆盖后恢复")
    if tfs.parse_origin(str(root)) != tfs.parse_origin(state['repository_path']):
        raise RuntimeError(f"{state['name']} 仓库来源与项目快照不一致")
    prs=tfs.pull_requests_for_branch(str(root),state['branch'],state['base_branch'])
    active=[p for p in prs if p.get('status')=='active']
    complete=[p for p in prs if p.get('status')=='completed' and (p.get('lastMergeSourceCommit') or {}).get('commitId')==expected]
    if len(active)>1 or len(complete)>1:
        raise RuntimeError(f"{state['name']} 有多个无法唯一确认的 PR，不能重复创建")
    if complete and active:
        raise RuntimeError(f"{state['name']} 同时存在已完成与活跃 PR，需要核对远端状态")
    if state.get('pr_id') and not any(int(p['pullRequestId'])==int(state['pr_id']) for p in active+complete):
        raise RuntimeError(f"{state['name']} 已保存的 PR 与远端检查点不一致")
    head=tfs.branch_head(str(root),state['branch'])
    if head and head != expected:
        raise RuntimeError(f"{state['name']} 远端分支新增了其他提交，不能按旧检查点自动发布")
    if complete:
        info=tfs.repository_info(str(root));p=complete[0]
        return {'id':int(p['pullRequestId']),'status':'completed',
                'url':f"{tfs.base_url}/{quote(info.project,safe='')}/_git/{quote(info.name,safe='')}/pullrequest/{p['pullRequestId']}"}
    if head != expected:
        raise RuntimeError(f"{state['name']} 找不到原有远端提交或已完成 PR，未重新推送")
    if any(p.get('status')=='abandoned' and (p.get('lastMergeSourceCommit') or {}).get('commitId')==expected for p in prs):
        raise RuntimeError(f"{state['name']} 原 PR 已被放弃，不自动覆盖人工决定")
    subject=git(root,'log','-1','--format=%s')
    if f'(#{work_item_id})' not in subject:
        raise RuntimeError(f"{state['name']} 冻结提交不属于当前需求")
    if dry_run:
        return {'id':0,'status':'ready','url':''}
    result=tfs.create_pull_request(str(root),state['branch'],state['base_branch'],subject,work_item_id,description)
    return {'id':int(result['PullRequestId']),'url':result['WebUrl'],'status':'active'}


def resume(worker, request_id: str) -> None:
    worker._check_cancelled(request_id)
    detail=worker.store.detail(request_id)
    context=dict(detail.get('recovery_context') or {})
    if context.get('kind')!='submission' or not submission_ready(detail):
        raise RuntimeError('没有可核对的推送提交检查点，不能直接恢复提交')
    project=enforce_artifact_policy(detail['policy_snapshot'])
    states=detail['repository_states']
    changed=[state for state in states if state.get('commit_hash') or state.get('pr_id')]
    tfs=TfsClient(project['tfs_collection_url'])
    item=tfs.get_work_item(detail['work_item_id'])
    normalize=lambda value:' '.join(str(value or '').split())
    if normalize(item.get('title'))!=normalize(detail['title']) or normalize(worker._plain_text(item.get('description',''))) != normalize(detail.get('requirement_summary')):
        raise RuntimeError('中断后 TFS 需求标题或正文已变化，保留原实现，不能按旧范围继续交付')
    if item.get('state') in {'已删除','已移除','已拒绝','已废弃','已取消'}:
        raise RuntimeError('TFS 需求已取消或移除，不继续交付')
    fingerprints={state['name']:state['commit_hash'] for state in changed}
    # Validate every repository before invoking a build or creating the first PR.
    for state in changed:
        reconcile_pr(tfs,state,detail['work_item_id'],'',dry_run=True)
    context.update(state='running',resumed_at=datetime.now(UTC).isoformat())
    worker.store.update_request(request_id,status='submitting',current_step='submit',error_message='',recovery_context=context)
    worker.store.add_event(request_id,'execution.resuming','从已推送提交继续 PR 交付；不启动研发、不创建新提交、不重放业务 SQL',metadata={'commits':fingerprints})
    if context.get('verified_commits')!=fingerprints:
        command=str(project.get('verification_command') or project.get('build_command') or '').strip()
        if not command and detail['delivery_mode'] in SICHUAN_APPROVAL_DELIVERY_MODES:
            raise RuntimeError('缺少可复核的提交前校验命令，不能自动批准')
        if command:
            workspace=Path(states[0]['worktree_path']).parent if len(states)>1 else Path(states[0]['worktree_path'])
            run_command(command,workspace,env_overrides=worker._build_environment(detail,changed),
                        cancel_check=lambda:worker._check_cancelled(request_id))
        context['verified_commits']=fingerprints
        worker.store.update_request(request_id,recovery_context=context)
        worker.store.add_event(request_id,'verification.completed' if command else 'verification.product_review',
            '恢复检查点：原有提交构建/校验通过' if command else '该项目未配置本地校验，沿用产品审核策略，不自动批准',metadata={'commits':fingerprints})
    declared=(detail.get('quality_gate_result') or {}).get('delivery_assets') or []
    for state in changed:
        worker.artifacts.collect_changed_assets(request_id,Path(state['worktree_path']),state['base_commit'],project,
            repository_name=state['name'],declared_assets=declared,
            changed_paths=([asset['path'] for asset in declared if asset['repository']==state['name']]
                           if 'delivery_assets' in (detail.get('quality_gate_result') or {}) else state.get('changed_files') or []))
    prs=[]
    for state in changed:
        worker._check_cancelled(request_id)
        pr=reconcile_pr(tfs,state,detail['work_item_id'],f"自动研发任务 {request_id} 中断恢复\n{detail.get('result_summary','')}")
        state.update(pr_id=pr['id'],pr_url=pr['url'],status='waiting_merge')
        worker.store.update_request(request_id,repository_states=states)
        worker.store.add_event(request_id,'pr.recovered',f"{state['name']} 已核对/接续 PR #{pr['id']}",metadata={'pr_id':pr['id'],'url':pr['url']})
        prs.append((state,pr))
    worker._check_cancelled(request_id)
    if detail['delivery_mode'] in SICHUAN_APPROVAL_DELIVERY_MODES:
        for state,pr in prs:
            worker._check_cancelled(request_id)
            if pr['status']!='completed':
                tfs.approve_pull_request(state['repository_path'],pr['id'])
        worker.store.add_event(request_id,'pr.approved','恢复后的 PR 已按既定四川审核策略处理')
    elif not (detail.get('joint_group_id') and worker.store.remote):
        worker._send_status_email(request_id,action_required=True)
    context.update(state='resumed',completed_at=datetime.now(UTC).isoformat())
    worker.store.update_request(request_id,repository_states=states,pr_id=prs[0][1]['id'],pr_url=prs[0][1]['url'],
        commit_hash=changed[0]['commit_hash'],status='waiting_merge',current_step='deliver',progress=82,
        next_poll_at=(datetime.now(UTC)+timedelta(seconds=settings.poll_seconds)).isoformat(),recovery_context=context,error_message='')
    worker.store.update_step(request_id,'submit','completed','已核对并恢复原有 PR 交付，无重复研发或代码提交')
    worker.store.update_step(request_id,'deliver','running','恢复接续完成，继续检测 PR 合并与后续交付')
    worker.store.add_event(request_id,'execution.resumed','原任务已从提交检查点接续，继续既有合并发版流程')
    if detail.get('joint_group_id') and worker.store.remote and detail['delivery_mode']=='product_manual_review':
        worker.store.notify_joint_review(request_id)
