window.TaskActions = (() => {
  const pending = new Set();
  const status = {pending:'等待执行器',running:'正在处理',waiting_merge:'等待回退 PR 合并',completed:'已完成',failed:'未完成 · 请查看原因'};
  const names = {cancel:'取消任务',restart:'重新开始',rollback:'回退需求'};
  function render(d) {
    const controls=d.controls||[], busy=controls.some(c=>['pending','running','waiting_merge'].includes(c.status));
    const latest=controls[0], manager=d.can_manage_request!==false;
    const running=!['delivered','failed','rejected','cancelled'].includes(d.status);
    const rolled=controls.some(c=>c.action==='rollback');
    const canRollback=d.status==='delivered'&&!isAnalysisTask(d)&&!rolled&&(d.repository_states||[]).some(s=>s.changed_files?.length&&(s.merge_commit||s.commit_hash));
    const button=(action,label)=>`<button type="button" class="btn ${action==='rollback'?'btn-ghost rollback-action':'btn-secondary'}" data-task-action="${action}" ${busy||pending.has(d.id)?'disabled':''}>${label||names[action]}</button>`;
    const actions=manager?`${running?button('cancel',d.joint_group_id?'取消联合任务':'取消任务'):''}${running||d.status==='cancelled'?button('restart',d.joint_group_id?'重新开始当前项目':'重新开始'):''}${canRollback?button('rollback'):''}`:'';
    const records=controls.slice(0,4).map(c=>{
      const result=c.result||{};
      return `<article class="task-control-record" data-control-status="${escapeHtml(c.status)}"><header><b>${names[c.action]||'任务操作'}</b><span>${status[c.status]||c.status}</span></header><p>${escapeHtml(c.message||'')}</p>${result.new_request_id?`<button class="text-button" data-restarted-request="${escapeHtml(result.new_request_id)}">打开新任务 ↗</button>`:''}${(result.repositories||[]).map(r=>`<div class="rollback-repo"><b>${escapeHtml(r.name||'仓库')}</b><code>${escapeHtml((r.revert_commit||'').slice(0,12))}</code><span>${escapeHtml(r.status==='completed'?'目标分支已回退':r.status==='waiting_merge'?'等待合并':'已准备')}</span>${r.pr_url&&/^https?:\/\//i.test(r.pr_url)?`<a href="${escapeHtml(r.pr_url)}" target="_blank" rel="noopener">查看回退 PR ↗</a>`:''}</div>`).join('')}</article>`;
    }).join('');
    return actions||records?`<section class="task-controls"><div class="detail-actions">${actions}</div>${records}</section>`:'';
  }
  function bind(d) {
    document.querySelectorAll('[data-restarted-request]').forEach(b=>b.onclick=()=>openDetail(b.dataset.restartedRequest));
    document.querySelectorAll('[data-task-action]').forEach(b=>b.onclick=()=>confirmAction(d,b.dataset.taskAction));
  }
  function confirmAction(d,action) {
    let modal=document.querySelector('#task-action-confirm');
    if(!modal){modal=document.createElement('dialog');modal.id='task-action-confirm';modal.className='task-action-confirm';document.body.append(modal);}
    const copy={cancel:'将停止研发与后续交付，并关闭尚未合并的 PR。已合并代码和已经启动的外部发版不会自动撤销。',restart:`先等待当前${d.joint_group_id?'项目':''}任务停止，再从最新目标分支创建新会话和新工作区。原记录保留；已合并代码不会自动撤销。${d.joint_group_id?'联合任务中的其他项目不受影响。':''}`,rollback:'将针对本次任务的提交生成反向提交，按原项目的目标分支及审核策略执行。后续代码发生冲突时停止并保留现场，不强制覆盖。此操作仅回退代码，不回滚数据库，也不自动部署或重新发版。'};
    modal.innerHTML=`<form method="dialog"><p class="eyebrow">TASK CONTROL / 操作确认</p><h2>${names[action]}</h2><p class="action-source">#${d.work_item_id} · ${escapeHtml(d.project_name)}</p><p class="action-explanation">${copy[action]}</p><p class="action-error" role="alert" hidden></p><div class="detail-actions"><button class="btn btn-ghost" value="cancel">暂不操作</button><button type="button" class="btn btn-primary" id="confirm-task-action">确认${names[action]} ↗</button></div></form>`;
    modal.showModal();
    modal.querySelector('#confirm-task-action').onclick=async event=>{
      const button=event.currentTarget;button.disabled=true;pending.add(d.id);
      try{
        await api(`/api/requests/${d.id}/${action}`,{method:'POST'});
        modal.close();state.selectedTerminal=false;toast('操作已提交，执行进展将在任务首页显示');
        await refreshDetail(d.id);
      }catch(error){const box=modal.querySelector('.action-error');box.textContent=error.message;box.hidden=false;button.disabled=false;}
      finally{pending.delete(d.id);}
    };
  }
  return {render,bind};
})();
