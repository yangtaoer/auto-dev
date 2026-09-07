/* Project memory and human acceptance share records, never browser test sessions. */
window.ProjectLearning = (() => {
  'use strict';
  const H = escapeHtml;
  const list = value => Array.isArray(value) ? value : [];
  const text = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
  const statusLabels = {candidate:'待验证',verified:'已验证',deprecated:'已废弃'};
  const humanLabels = {unverified:'未验证',passed:'验证通过',failed:'未通过'};
  const acceptanceLabels = {pending:'等待验收',partial:'部分验证通过',changes_requested:'需要返修',accepted:'验收通过'};
  const draftPanels = new Map();
  const catalog = {items:[],total:0,page:1,size:12,filters:{project_id:'',q:'',status:''},generation:0,loaded:false};
  let experienceGeneration = 0;
  const statusChip = status => `<span class="learning-status ${Object.hasOwn(statusLabels,status)?status:'candidate'}">${H(statusLabels[status]||'待验证')}</span>`;
  const empty = (title,copy='') => `<div class="learning-empty"><b>${H(title)}</b>${copy?`<p>${H(copy)}</p>`:''}</div>`;
  const fact = (label,value) => `<div class="learning-fact"><span>${H(label)}</span><p>${H(text(value)||'暂无记录')}</p></div>`;
  const bullets = values => list(values).length ? `<ul class="learning-evidence">${list(values).map(value=>`<li>${H(text(value))}</li>`).join('')}</ul>` : '<p class="muted">暂无记录</p>';
  const key = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const selectMarkup = (name,label) => `<div class="ledger-select" data-learning-select="${H(name)}"><input type="hidden" name="${H(name)}"><button type="button" aria-haspopup="listbox" aria-expanded="false"><b>${H(label)}</b><i></i></button><div class="ledger-select-menu" role="listbox" hidden></div></div>`;

  function wireSelect(control,options,value,onChange) {
    if(!control)return;
    setLedgerSelectOptions(control,options,String(value??''));
    control.querySelectorAll('[data-value]').forEach(button=>button.addEventListener('click',()=>onChange?.(button.dataset.value)));
    if(control.dataset.learningKeyboard)return;
    control.dataset.learningKeyboard='1';
    control.addEventListener('keydown',event=>{
      const options=[...control.querySelectorAll('[role="option"]')];
      if(event.key==='Escape'){closeLedgerPopovers();control.querySelector(':scope > button').focus();return;}
      if(!['ArrowDown','ArrowUp'].includes(event.key))return;
      event.preventDefault();
      if(!control.classList.contains('open'))control.querySelector(':scope > button').click();
      const current=options.indexOf(document.activeElement),next=current+(event.key==='ArrowDown'?1:-1);
      options[(next+options.length)%options.length]?.focus();
    });
  }

  function syncProjects() {
    if(USER.role!=='admin')return;
    const projectControl=document.querySelector('#experience-project-filter');
    const projectValue=projectControl?.querySelector('input')?.value??catalog.filters.project_id;
    wireSelect(projectControl,[['','全部项目'],...state.projects.map(project=>[String(project.id),project.name])],projectValue);
    const statusControl=document.querySelector('#experience-status-filter');
    wireSelect(statusControl,[['','全部状态'],...Object.entries(statusLabels)],statusControl?.querySelector('input')?.value??catalog.filters.status);
  }

  async function loadCatalog(page=1) {
    const container=document.querySelector('#experience-list');
    if(!container)return;
    const generation=++catalog.generation;
    catalog.page=Math.max(1,Number(page)||1);
    container.setAttribute('aria-busy','true');
    container.innerHTML=empty('正在读取项目经验…');
    const params=new URLSearchParams({limit:String(catalog.size),offset:String((catalog.page-1)*catalog.size)});
    Object.entries(catalog.filters).forEach(([name,value])=>{if(value)params.set(name,value);});
    try {
      const result=await api(`/api/admin/project-experiences?${params}`);
      if(generation!==catalog.generation)return;
      catalog.items=list(result.items);catalog.total=Number(result.total)||0;catalog.loaded=true;
      const maxPage=Math.max(1,Math.ceil(catalog.total/catalog.size));
      if(catalog.page>maxPage)return loadCatalog(maxPage);
      container.innerHTML=catalog.items.length?catalog.items.map(item=>{
        const summary=item.acceptance_summary||{};
        return `<article class="experience-card"><header><div><span class="learning-project">${H(item.project_name||item.project_key)}</span><h3><button type="button" data-experience-id="${H(item.id)}">${H(item.title||`需求 #${item.work_item_id}`)}</button></h3></div>${statusChip(item.status)}</header><p class="experience-scope">${H(item.scope_summary||'实现范围正在整理')}</p><footer><span>#${H(item.work_item_id)} · ${H(fmt(item.updated_at||item.created_at))}</span><span>验收 ${Number(summary.passed)||0}/${Number(summary.total)||0}${summary.failed?` · 未通过 ${Number(summary.failed)}`:''}</span><button type="button" class="text-button" data-experience-id="${H(item.id)}">查看积累 ↗</button></footer></article>`;
      }).join(''):empty('还没有匹配的项目经验','已完成需求将持续保留实现范围和验收反馈；可以调整项目、关键词或状态筛选。');
      container.querySelectorAll('[data-experience-id]').forEach(button=>button.onclick=()=>openExperience(button.dataset.experienceId));
      document.querySelector('#experience-total').textContent=`共 ${catalog.total} 条经验`;
      const pagination=document.querySelector('#experience-pagination');
      pagination.innerHTML=`<span>第 ${catalog.page} / ${maxPage} 页</span><div><button type="button" data-page="${catalog.page-1}" ${catalog.page<=1?'disabled':''}>← 上一页</button><button type="button" data-page="${catalog.page+1}" ${catalog.page>=maxPage?'disabled':''}>下一页 →</button></div>`;
      pagination.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>loadCatalog(button.dataset.page));
    } catch(error) {
      if(generation!==catalog.generation)return;
      container.innerHTML=empty('项目经验暂时无法读取',error.message)+'<button type="button" class="btn btn-secondary learning-reload">重新读取</button>';
      container.querySelector('.learning-reload').onclick=()=>loadCatalog(catalog.page);
    } finally {if(generation===catalog.generation)container.setAttribute('aria-busy','false');}
  }

  function renderRounds(rounds) {
    if(!list(rounds).length)return '<p class="muted">尚无人工验收反馈。未反馈不会计为通过。</p>';
    rounds=[...rounds].sort((a,b)=>Number(b.id)-Number(a.id));
    return `<div class="learning-rounds">${list(rounds).map((round,index)=>`<details class="learning-round"><summary><b>验收记录 ${list(rounds).length-index}</b><span>${H(round.actor_name||'验收人')} · ${H(fmt(round.created_at))}</span></summary><div>${round.overall_status?fact('整体验收',round.overall_status==='passed'?'验证通过':'验证不通过'):''}${fact('被测版本 / 环境',[round.tested_version||'未注明版本',round.environment||'未注明环境'].join(' / '))}${round.raw_feedback?fact('原始反馈',round.raw_feedback):''}${list(round.items).map(item=>`<p class="learning-round-item"><b>${H(item.id)} · ${H(humanLabels[item.status]||item.status)}</b>${item.actual?`<span>实际：${H(item.actual)}</span>`:''}${item.expected?`<span>预期：${H(item.expected)}</span>`:''}${item.note?`<span>${H(item.note)}</span>`:''}</p>`).join('')}${round.repair_request_id?`<button type="button" class="text-button" data-learning-request="${H(round.repair_request_id)}">查看关联返修任务 ↗</button>`:''}</div></details>`).join('')}</div>`;
  }

  function bindRequestLinks(root) {
    root.querySelectorAll('[data-learning-request]').forEach(button=>button.onclick=()=>{
      const modal=document.querySelector('#experience-modal');if(modal)modal.hidden=true;
      openDetail(button.dataset.learningRequest);
    });
  }

  async function openExperience(id) {
    const modal=document.querySelector('#experience-modal'),content=document.querySelector('#experience-detail-content');
    if(!modal||!content)return;
    const generation=++experienceGeneration;modal.hidden=false;
    content.innerHTML='<h2 id="experience-detail-title">项目经验</h2>'+empty('正在读取经验详情…');
    document.querySelector('#close-experience').focus();
    try {
      const result=await api(`/api/admin/project-experiences/${encodeURIComponent(id)}`);
      if(generation!==experienceGeneration||modal.hidden)return;
      const item=result.experience||{},summary=item.acceptance_summary||{},evidence=item.evidence||{};
      item.rounds=item.feedback_rounds||item.rounds||item.acceptance_rounds;
      item.lessons=list(item.lessons).map(lesson=>({...lesson,id:lesson.acceptance_id||lesson.id,feedback:lesson.feedback?[lesson.feedback.actual&&`实际：${lesson.feedback.actual}`,lesson.feedback.expected&&`预期：${lesson.feedback.expected}`,lesson.feedback.note&&`补充：${lesson.feedback.note}`,lesson.feedback.tested_version&&`被测版本：${lesson.feedback.tested_version}`].filter(Boolean).join('\n'):''}));
      item.revisions=list(item.revisions).map(revision=>typeof revision==='object'?`${fmt(revision.created_at)} · ${statusLabels[revision.status]||revision.status||''}\n${revision.reason||'经验内容更新'}`:revision);
      content.innerHTML=`<p class="eyebrow">项目经验 / PROJECT MEMORY</p><div class="experience-detail-heading"><div><span class="learning-project">${H(item.project_name||item.project_key)}</span><h2 id="experience-detail-title">${H(item.title)}</h2></div>${statusChip(item.status)}</div><p class="learning-note">${H(item.verification_label||'经验必须结合当前代码与适用范围复核。')}</p><div class="learning-summary"><b>${Number(summary.passed)||0}<small> / ${Number(summary.total)||0}</small></b><span>人工验证通过<br>未通过 ${Number(summary.failed)||0} · 未验证 ${Number(summary.unverified)||0}</span><button type="button" class="text-button" data-learning-request="${H(item.request_id)}">原需求 #${H(item.work_item_id)} ↗</button></div>${fact('修改了什么',item.scope_summary)}${fact('如何完成',item.implementation_summary)}<section class="learning-section"><h3>逐项实现与反馈</h3>${list(item.lessons).map(lesson=>`<article class="learning-lesson"><b>${H(lesson.id||'')} ${H(lesson.criterion||lesson.title||lesson.lesson||'经验条目')}</b><p>${H(lesson.lesson||lesson.implementation||'')}</p><div class="learning-mini-facts"><span>研发：${H(developmentLabel(lesson.development_status))}</span><span>人工：${H(humanLabels[lesson.human_status]||'未验证')}</span></div>${lesson.applies_to?fact('适用条件',lesson.applies_to):''}${lesson.limitations?fact('适用边界',lesson.limitations):''}${bullets(lesson.evidence)}${lesson.feedback?fact('验收反馈',lesson.feedback):''}</article>`).join('')||'<p class="muted">暂无结构化条目</p>'}</section><section class="learning-section"><h3>可复核来源</h3>${fact('PR / 提交',[evidence.pr_url,evidence.commit_hash,evidence.merge_commit].filter(Boolean).join('\n'))}${fact('仓库与变更',evidence.repositories)}${fact('被测版本',evidence.tested_versions)}${bullets(item.history_refs||item.source_refs)}</section><section class="learning-section"><h3>验收与返修历史</h3>${renderRounds(item.rounds||item.acceptance_rounds)}${list(item.repairs||item.repair_runs).map(repair=>`<p><button type="button" class="text-button" data-learning-request="${H(repair.id||repair.request_id)}">返修轮次 ${H(repair.repair_round||'')} · ${H(repair.status||'查看任务')} ↗</button></p>`).join('')}</section><section class="learning-section"><h3>经验版本记录</h3>${bullets(item.revisions||item.lifecycle_audit||item.history)}</section><form id="experience-status-form" class="learning-status-form"><label><span>管理经验状态</span>${selectMarkup('status','经验状态')}</label><label><span>依据 / 原因</span><textarea name="reason" rows="2" maxlength="2000" required placeholder="写明验证来源、适用条件或废弃原因"></textarea></label><p class="learning-note">状态调整会保留记录；已验证状态需要具备验收或其他可复核依据。</p><p class="form-error" hidden></p><button type="submit" class="btn btn-secondary">保存状态</button></form>`;
      const retrospective=item.retrospective||{};
      if(item.parent_request_id){
        const relation=document.createElement('p');relation.className='learning-repair-scope';
        relation.innerHTML=`返修第 ${Number(item.repair_round)||1} 轮 · <button type="button" class="text-button" data-learning-request="${H(item.parent_request_id)}">查看原任务 ↗</button><br>处理：${H(list(item.failed_item_ids).join('、')||'反馈项')}<br>保护：${H(list(item.protected_item_ids).join('、')||'原验收范围')}`;
        content.querySelector('.learning-summary')?.after(relation);
      }
      if(list(retrospective.lessons).length||list(retrospective.regression_suggestions).length){
        const section=document.createElement('section');section.className='learning-section';
        section.innerHTML=`<h3>研发复盘与适用边界</h3><p class="learning-note">复盘由研发过程形成，结论需结合人工验收及当前代码验证。</p>${list(retrospective.lessons).map(lesson=>`<article class="learning-lesson"><b>${H(lesson.title||'经验条目')}</b>${fact('做法与原因',lesson.lesson)}${fact('适用条件',lesson.applies_to)}${fact('适用边界',lesson.limitations)}${list(lesson.acceptance_ids).length?fact('关联验收项',lesson.acceptance_ids.join('、')):''}${bullets(lesson.evidence)}</article>`).join('')}${list(retrospective.regression_suggestions).length?`<h4>后续回归建议</h4>${bullets(retrospective.regression_suggestions)}`:''}`;
        content.querySelector('.learning-section')?.before(section);
      }
      bindRequestLinks(content);
      const form=content.querySelector('#experience-status-form');
      wireSelect(form.querySelector('.ledger-select'),Object.entries(statusLabels),item.status||'candidate');
      form.onsubmit=async event=>{
        event.preventDefault();const error=form.querySelector('.form-error'),button=form.querySelector('[type="submit"]');error.hidden=true;button.disabled=true;
        try {await api(`/api/admin/project-experiences/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({status:form.elements.status.value,reason:form.elements.reason.value.trim()})});toast('经验状态已更新');await openExperience(id);await loadCatalog(catalog.page);}
        catch(err){error.textContent=err.message;error.hidden=false;button.disabled=false;}
      };
    } catch(error) {if(generation===experienceGeneration)content.innerHTML='<h2 id="experience-detail-title">项目经验</h2>'+empty('无法读取经验详情',error.message);}
  }

  function developmentLabel(status) {
    return ({completed:'已实现',partial:'部分实现',blocked:'存在阻塞',not_applicable:'不适用',pending:'待实现',unreported:'未记录'})[status]||status||'未记录';
  }

  function renderSimilar(request) {
    const context=list(request.project_lesson_context),usage=list(request.lesson_usage);
    if(!context.length&&!usage.length)return '';
    const usageSummaries=usage.map(item=>typeof item==='object'?`经验 #${item.experience_id||''} · ${{adopted:'已采用',not_applicable:'本次不适用',conflict:'与当前实现存在差异'}[item.decision]||item.decision||'待复核'}\n${item.reason||''}${item.evidence?`\n当前代码依据：${item.evidence}`:''}`:item);
    return `<details class="learning-similar"><summary><b>本次参考的项目经验</b><span>${context.length} 条来源</span></summary><div>${context.map(item=>`<article><b>${H(item.title||item.criterion||'历史经验')}</b><p>${H(item.scope_summary||item.lesson||item.implementation_summary||'')}</p>${item.request_id?`<button type="button" class="text-button" data-learning-request="${H(item.request_id)}">来源需求 #${H(item.work_item_id||'')} ↗</button>`:''}${item.match_reason||item.reason||item.relevance?`<small>${H(item.match_reason||item.reason||item.relevance)}</small>`:''}${statusChip(item.status)}</article>`).join('')}${usage.length?`<h4>采用情况与当前代码复核</h4>${bullets(usageSummaries)}`:''}</div></details>`;
  }

  function renderRequest(request) {
    let session=draftPanels.get(request.id);
    if(!session){session={request,element:document.createElement('section'),bundle:null,draft:null,status:'',loading:false};session.element.className='request-learning-panel';session.element.dataset.requestId=request.id;draftPanels.set(request.id,session);}
    session.request=request;
    // Reattach the original node after task polling; typed text, focus state and preview survive.
    document.querySelector('#task-acceptance-mount')?.append(session.element);
    document.querySelector('#task-experience-context').innerHTML=renderSimilar(request);
    bindRequestLinks(document.querySelector('#task-experience-context'));
    renderRequirementPoints(session);
    if(!session.bundle&&!session.loading)loadAcceptance(session);
    else if((session.status!==request.status||session.reload)&&!session.loading){session.reload=false;loadAcceptance(session);}
    session.status=request.status;
  }

  async function loadAcceptance(session) {
    session.loading=true;
    if(!session.bundle)session.element.innerHTML=renderSimilar(session.request)+empty('正在读取验收账本…');
    try {
      const result=await api(`/api/requests/${encodeURIComponent(session.request.id)}/acceptance`);
      session.bundle=result;
      if(!session.draft){
        const acceptance=result.acceptance||{};
        session.draft={verdict:null,raw_feedback:'',failedIds:new Set(),expected_latest_feedback_id:Number(acceptance.latest_feedback_id)||0,idempotency_key:key(),optionalOpen:false};
      }
      renderRequirementPoints(session);
      drawAcceptance(session);
    } catch(error) {
      session.element.innerHTML=renderSimilar(session.request)+empty('验收账本暂时无法读取',error.message)+'<button type="button" class="btn btn-secondary acceptance-reload">重新读取</button>';
      session.element.querySelector('.acceptance-reload').onclick=()=>loadAcceptance(session);
    } finally {session.loading=false;}
  }

  function renderRequirementPoints(session) {
    if(state.selectedRequest!==session.request.id)return;
    const mount=document.querySelector('#requirement-points'),bundle=session.bundle;
    if(!mount)return;
    if(!bundle){mount.innerHTML=empty('正在读取需求拆解…');return;}
    mount.innerHTML=`<ol class="task-points">${list(bundle.acceptance?.items).map(item=>`<li>${H(item.criterion)}</li>`).join('')}</ol>`;
    if(bundle.can_assign){
      const owner=document.createElement('details');owner.className='task-context';
      owner.innerHTML=`<summary>指定验收人</summary><form class="acceptance-assignee"><label>${selectMarkup('user_id','选择验收人')}</label><button class="btn btn-secondary" type="submit">保存</button><p class="form-error" hidden></p></form>`;
      mount.append(owner);
      const form=owner.querySelector('form'),options=list(bundle.users).map(user=>[String(user.id),`${user.display_name} · ${user.username}`]);
      wireSelect(form.querySelector('.ledger-select'),options,bundle.acceptance?.acceptance_owner_id||session.request.requester_id||'');
      form.onsubmit=async event=>{
        event.preventDefault();const button=form.querySelector('button[type=submit]'),error=form.querySelector('.form-error');button.disabled=true;error.hidden=true;
        try{await api(`/api/requests/${session.request.id}/acceptance/assignee`,{method:'PUT',body:JSON.stringify({user_id:Number(form.elements.user_id.value)})});toast('验收人已更新');await loadAcceptance(session);}
        catch(err){error.textContent=err.message;error.hidden=false;button.disabled=false;}
      };
    }
  }

  function drawAcceptance(session) {
    const bundle=session.bundle,acceptance=bundle.acceptance||{},draft=session.draft;
    const items=list(acceptance.items),editable=Boolean(bundle.can_submit??bundle.can_accept),rounds=list(acceptance.rounds);
    const latestRound=rounds.find(round=>Number(round.id)===Number(acceptance.latest_feedback_id));
    const failed=latestRound&&(latestRound.overall_status==='failed'||list(latestRound.items).some(item=>item.status==='failed'));
    session.element.innerHTML=`<section class="acceptance-panel"><header class="acceptance-heading"><div><p class="eyebrow">提出人验收 / ACCEPTANCE</p><h3>${H(acceptanceLabels[acceptance.status]||'等待验收')}</h3></div></header>
      ${editable?`<form class="acceptance-form"><fieldset class="acceptance-verdict"><legend>这次需求验证结果如何？</legend>
        <button type="button" data-verdict="passed" aria-pressed="${draft.verdict==='passed'}"><span aria-hidden="true">✓</span>验证通过</button>
        <button type="button" data-verdict="failed" aria-pressed="${draft.verdict==='failed'}"><span aria-hidden="true">×</span>验证不通过</button></fieldset>
        <details class="acceptance-optional" ${draft.verdict==='failed'?'':'hidden'} ${draft.optionalOpen?'open':''}><summary>补充未通过项或说明（可选）</summary>
          <p class="learning-note">可留空直接提交；未选择的项目不会自动记为通过。</p>
          <div class="acceptance-failed-points">${items.map(item=>`<label><input type="checkbox" value="${H(item.id)}" ${draft.failedIds.has(item.id)?'checked':''}><span>${H(item.criterion)}</span></label>`).join('')}</div>
          <label class="acceptance-comment"><span>补充说明（可选）</span><textarea name="raw_feedback" rows="3" maxlength="12000" placeholder="例如：第 3 项仍未生效">${H(draft.raw_feedback)}</textarea></label>
        </details><p class="form-error acceptance-error" hidden></p>
        <div class="acceptance-submit-row"><button type="submit" class="btn btn-primary acceptance-submit">提交验收结果 ↗</button><small>自动关联本次交付，无需填写版本或环境。</small></div></form>`:`<p class="learning-note">${session.request.status==='delivered'?'由需求提出人、指定验收人或管理员提交验收结果。':'任务交付后，只需确认验证通过或不通过。'}</p>`}
      ${editable&&failed?`<div class="acceptance-repair-action"><button type="button" class="btn btn-secondary acceptance-start-repair" ${latestRound.repair_request_id?'disabled':''}>${latestRound.repair_request_id?'本轮已发起返修':'根据本轮反馈继续修复 ↗'}</button><p class="form-error acceptance-repair-error" hidden></p></div>`:''}
      ${rounds.length?`<details class="acceptance-history"><summary>历史反馈 · ${rounds.length} 次</summary>${renderRounds(rounds)}</details>`:''}
    </section>`;
    if(Number(acceptance.latest_feedback_id)!==draft.expected_latest_feedback_id){
      const notice=document.createElement('div');notice.className='acceptance-stale-notice';
      notice.innerHTML='<b>已有新的验收反馈，当前草稿已保留。</b><button type="button" class="btn btn-secondary">已核对历史反馈，继续提交</button>';
      session.element.querySelector('.acceptance-form')?.prepend(notice);
      notice.querySelector('button').onclick=()=>{draft.expected_latest_feedback_id=Number(acceptance.latest_feedback_id)||0;draft.idempotency_key=key();notice.remove();};
    }
    bindRequestLinks(session.element);
    const form=session.element.querySelector('.acceptance-form');
    if(form){
      form.querySelectorAll('[data-verdict]').forEach(button=>button.onclick=()=>{
        draft.verdict=button.dataset.verdict;
        form.querySelectorAll('[data-verdict]').forEach(choice=>choice.setAttribute('aria-pressed',String(choice===button)));
        form.querySelector('.acceptance-optional').hidden=draft.verdict!=='failed';
        form.querySelector('.acceptance-error').hidden=true;
      });
      form.querySelector('.acceptance-optional').ontoggle=event=>{draft.optionalOpen=event.currentTarget.open;};
      form.querySelectorAll('input[type=checkbox]').forEach(input=>input.onchange=()=>{input.checked?draft.failedIds.add(input.value):draft.failedIds.delete(input.value);});
      form.elements.raw_feedback.oninput=event=>{draft.raw_feedback=event.target.value;};
      form.onsubmit=event=>{event.preventDefault();submitFeedback(session);};
    }
    session.element.querySelector('.acceptance-start-repair')?.addEventListener('click',async event=>{
      const button=event.currentTarget,error=session.element.querySelector('.acceptance-repair-error');button.disabled=true;error.hidden=true;
      session.repairKey=session.repairKey||key();
      try{const result=await api(`/api/requests/${session.request.id}/acceptance/repair`,{method:'POST',body:JSON.stringify({feedback_id:latestRound.id,idempotency_key:session.repairKey})});toast('返修已进入队列');await refresh();await openDetail(result.request.id);}
      catch(err){error.textContent=err.message;error.hidden=false;button.disabled=false;}
    });
  }

  async function submitFeedback(session) {
    const form=session.element.querySelector('.acceptance-form'),error=form.querySelector('.acceptance-error');
    error.hidden=true;const draft=session.draft;
    if(!draft.verdict){error.textContent='请选择验证通过或不通过。';error.hidden=false;return;}
    if(Number(session.bundle.acceptance.latest_feedback_id)!==draft.expected_latest_feedback_id){error.textContent='已有新反馈，请核对历史记录后继续提交。';error.hidden=false;return;}
    const payload={overall_status:draft.verdict,failed_item_ids:draft.verdict==='failed'?[...draft.failedIds]:[],raw_feedback:draft.verdict==='failed'?draft.raw_feedback:'',expected_latest_feedback_id:draft.expected_latest_feedback_id};
    const serialized=JSON.stringify(payload);
    if(draft.lastPayload&&draft.lastPayload!==serialized)draft.idempotency_key=key();draft.lastPayload=serialized;
    const controls=[...form.querySelectorAll('input,textarea,button')].map(control=>[control,control.disabled]);
    controls.forEach(([control])=>control.disabled=true);
    try{
      const result=await api(`/api/requests/${session.request.id}/acceptance/feedback`,{method:'POST',body:JSON.stringify({...payload,idempotency_key:draft.idempotency_key})});
      session.draft=null;
      if(result.acceptance)session.bundle={...session.bundle,acceptance:result.acceptance};
      await loadAcceptance(session);toast('验收结果已保存');catalog.loaded=false;
    }catch(err){
      error.textContent=`${err.message}。草稿已保留。`;error.hidden=false;
      controls.forEach(([control,disabled])=>control.disabled=disabled);
      if(!form.querySelector('.acceptance-check-latest')){
        const reload=document.createElement('button');reload.type='button';reload.className='text-button acceptance-check-latest';reload.textContent='读取最新反馈并保留草稿';reload.onclick=()=>loadAcceptance(session);error.after(reload);
      }
    }
  }

  document.querySelector('#experience-filters')?.addEventListener('submit',event=>{
    event.preventDefault();const data=new FormData(event.currentTarget);catalog.filters={q:String(data.get('q')||'').trim(),project_id:String(data.get('project_id')||''),status:String(data.get('status')||'')};closeLedgerPopovers();loadCatalog(1);
  });
  document.querySelector('#experience-reset')?.addEventListener('click',()=>{
    document.querySelector('#experience-filters').reset();catalog.filters={q:'',project_id:'',status:''};
    ['#experience-project-filter','#experience-status-filter'].forEach(selector=>setLedgerSelectValue(document.querySelector(selector),''));loadCatalog(1);
  });
  document.querySelector('#close-experience')?.addEventListener('click',()=>{experienceGeneration++;document.querySelector('#experience-modal').hidden=true;});
  document.querySelector('#experience-modal')?.addEventListener('click',event=>{if(event.target===event.currentTarget){experienceGeneration++;event.currentTarget.hidden=true;}});
  window.addEventListener('keydown',event=>{if(event.key==='Escape'){const modal=document.querySelector('#experience-modal');if(modal&&!modal.hidden){experienceGeneration++;modal.hidden=true;}}});
  const requestedId=new URLSearchParams(location.search).get('request');
  if(requestedId&&/^[A-Za-z0-9-]{1,80}$/.test(requestedId)){
    openDetail(requestedId).then(()=>{if(new URLSearchParams(location.search).has('acceptance'))TaskDialog.select('acceptance');}).catch(error=>toast(error.message));
  }
  return {renderRequest,syncProjects,willOpenRequest:id=>{const session=draftPanels.get(id);if(session)session.reload=true;},open:()=>{syncProjects();loadCatalog(catalog.page);}};
})();
