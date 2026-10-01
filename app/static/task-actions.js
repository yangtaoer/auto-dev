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
    const repositories=(d.repository_states||[]).filter(repo=>repo.changed_files?.length&&(repo.merge_commit||repo.commit_hash));
    const facts=`<dl class="action-task-facts"><div><dt>所属项目</dt><dd>${escapeHtml(d.project_name)}</dd></div><div><dt>需求名称</dt><dd>${escapeHtml(d.title||`TFS #${d.work_item_id}`)}</dd></div><div><dt>TFS 编号</dt><dd>#${d.work_item_id}</dd></div><div><dt>当前状态</dt><dd><span class="status-dot" data-status="${escapeHtml(d.status)}">${escapeHtml(d.status_label||STATUS[d.status]||d.status)}</span></dd></div>${action==='cancel'?`<div><dt>最新输出</dt><dd>${escapeHtml(d.current_activity||d.result_summary||'暂无输出')}</dd></div>`:''}</dl>`;
    const detail=action==='restart'?`<div class="restart-transition"><section><small>当前</small>${editorialIcon('network')}<b>#${d.work_item_id}</b><span>原会话与工作区保留</span></section>${editorialIcon('arrow-right')}<section><small>新的</small>${editorialIcon('network')}<b>#${d.work_item_id}</b><span>基于最新目标分支重新开始</span></section></div>`:action==='rollback'?`<section class="action-repositories"><h3>涉及的代码仓库（${repositories.length}）</h3>${repositories.map(repo=>`<div>${editorialIcon('code')}<b>${escapeHtml(repo.name||repo.repository_name||'代码仓库')}</b><span>原提交 <code>${escapeHtml((repo.merge_commit||repo.commit_hash).slice(0,12))}</code></span></div>`).join('')}</section>`:'';
    modal.dataset.action=action;
    modal.innerHTML=`<form method="dialog"><button class="modal-close" value="cancel" aria-label="关闭操作确认">×</button><h2><span class="action-symbol">${editorialIcon(action==='rollback'?'refresh':'help')}</span>${names[action]}确认</h2><p class="action-explanation">${copy[action]}</p>${facts}${detail}<p class="action-error" role="alert" hidden></p><div class="detail-actions"><button class="btn btn-ghost" value="cancel">暂不操作</button><button type="button" class="btn btn-primary" id="confirm-task-action">确认${names[action]} ↗</button></div></form>`;
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
  function confirmRetry(d,reportSync=false) {
    let modal=document.querySelector('#task-action-confirm');
    if(!modal){modal=document.createElement('dialog');modal.id='task-action-confirm';modal.className='task-action-confirm';document.body.append(modal);}
    const title=reportSync?'重试报告同步':'重新发起任务';
    const reports=(d.artifacts||[]).filter(isAnalysisReport);
    modal.dataset.action='retry';
    modal.innerHTML=`<form method="dialog"><button class="modal-close" value="cancel" aria-label="关闭操作确认">×</button><h2><span class="action-symbol">${editorialIcon('refresh')}</span>${title}</h2><p class="action-explanation">${reportSync?'复用已生成报告，仅重试 TFS 状态同步与通知；不会重新分析或创建新任务。':'将保留原任务记录，并创建一条新的排队任务。新任务将按当前项目策略执行。'}</p><p class="action-source">#${d.work_item_id} · ${escapeHtml(d.project_name)}</p>${reportSync?`<div class="retry-report-files">${reports.map(file=>`<div>${editorialIcon('clipboard')}<span>${escapeHtml(file.name)}</span></div>`).join('')}</div>`:''}<div class="detail-actions"><button class="btn btn-ghost" value="cancel">暂不操作</button><button class="btn btn-primary" value="confirm">确认${reportSync?'重试':'重新发起'}</button></div></form>`;
    return new Promise(resolve=>{modal.addEventListener('close',()=>resolve(modal.returnValue==='confirm'),{once:true});modal.returnValue='';modal.showModal();});
  }
  return {render,bind,confirmRetry};
})();
