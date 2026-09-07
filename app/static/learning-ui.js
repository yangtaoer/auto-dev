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
    return `<div class="learning-rounds">${list(rounds).map((round,index)=>`<details class="learning-round"><summary><b>验收记录 ${list(rounds).length-index}</b><span>${H(round.actor_name||'验收人')} · ${H(fmt(round.created_at))}</span></summary><div>${fact('被测版本 / 环境',[round.tested_version||'未注明版本',round.environment||'未注明环境'].join(' / '))}${round.raw_feedback?fact('原始反馈',round.raw_feedback):''}${list(round.items).map(item=>`<p class="learning-round-item"><b>${H(item.id)} · ${H(humanLabels[item.status]||item.status)}</b>${item.actual?`<span>实际：${H(item.actual)}</span>`:''}${item.expected?`<span>预期：${H(item.expected)}</span>`:''}${item.note?`<span>${H(item.note)}</span>`:''}</p>`).join('')}${round.repair_request_id?`<button type="button" class="text-button" data-learning-request="${H(round.repair_request_id)}">查看关联返修任务 ↗</button>`:''}</div></details>`).join('')}</div>`;
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
    document.querySelector('#detail-content .detail-head')?.after(session.element);
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
        session.draft={raw_feedback:'',tested_version:acceptance.tested_version||'',environment:'',items:{},expected_latest_feedback_id:Number(acceptance.latest_feedback_id)||0,idempotency_key:key(),preview:null};
        list(acceptance.items).forEach(item=>session.draft.items[item.id]={id:item.id,status:'unverified',actual:'',expected:'',note:''});
      }
      drawAcceptance(session);
    } catch(error) {
      session.element.innerHTML=renderSimilar(session.request)+empty('验收账本暂时无法读取',error.message)+'<button type="button" class="btn btn-secondary acceptance-reload">重新读取</button>';
      session.element.querySelector('.acceptance-reload').onclick=()=>loadAcceptance(session);
    } finally {session.loading=false;}
  }

  function renderAcceptanceItem(item,draft,editable) {
    const entry=draft.items[item.id]||(draft.items[item.id]={id:item.id,status:'unverified',actual:'',expected:item.criterion||'',note:''});
    if(!entry.expected)entry.expected=item.criterion||'';
    const rawTestStatus=item.automatic_test_status||item.automated_test_status||item.test_status;
    const testStatus=rawTestStatus==='deferred'?'暂未接入':rawTestStatus;
    return `<article class="acceptance-item" data-ac-id="${H(item.id)}" data-status="${H(entry.status)}"><header><code>${H(item.id)}</code><b>${H(item.criterion)}</b></header><div class="learning-mini-facts"><span>研发：${H(developmentLabel(item.development_status))}</span><span>自动测试：${H(testStatus?({passed:'通过',failed:'未通过',unverified:'未验证',not_run:'未执行'}[testStatus]||testStatus):'未记录自动测试结论')}</span><span>已提交验收：${H(humanLabels[item.human_status]||'未验证')}</span></div>${item.feedback?.tested_version?`<p class="learning-basis">上次验收版本：${H(item.feedback.tested_version)}</p>`:''}<details class="acceptance-evidence"><summary>实现与自检依据</summary>${bullets([...list(item.tests),...list(item.evidence)])}</details>${editable?`<div class="acceptance-choice" role="group" aria-label="${H(item.id)} 本轮验收结论">${Object.entries(humanLabels).map(([status,label])=>`<button type="button" data-status="${status}" aria-pressed="${entry.status===status}">${label}</button>`).join('')}</div><div class="acceptance-failure" ${entry.status==='failed'?'':'hidden'}><label><span>实际表现</span><textarea data-item-field="actual" rows="2" maxlength="4000" placeholder="哪个角色、单位、步骤出现了什么问题">${H(entry.actual)}</textarea></label><label><span>预期表现</span><textarea data-item-field="expected" rows="2" maxlength="4000" placeholder="这一项应该如何表现">${H(entry.expected)}</textarea></label></div><label class="acceptance-item-note"><span>补充说明（可选）</span><input data-item-field="note" maxlength="2000" value="${H(entry.note)}" placeholder="验证范围、样例或复现条件"></label>`:''}</article>`;
  }

  function drawAcceptance(session) {
    const request=session.request,bundle=session.bundle,acceptance=bundle.acceptance||{},draft=session.draft;
    const items=list(acceptance.items),editable=Boolean(bundle.can_submit??bundle.can_accept),rounds=list(acceptance.rounds);
    const latestRound=rounds.find(round=>Number(round.id)===Number(acceptance.latest_feedback_id))||rounds[0];
    session.element.innerHTML=`${renderSimilar(request)}<section class="acceptance-panel"><header class="acceptance-heading"><div><p class="eyebrow">提出人验收 / HUMAN ACCEPTANCE</p><h3>${H(acceptanceLabels[acceptance.status]||'等待验收')}</h3></div><span class="learning-count">${items.length} <small>项</small></span></header><p class="learning-note">研发自检、自动测试和人工验收分别记录。每轮填写本次实际验证结果；未验证项不计为通过。</p>${acceptance.parent_request_id?`<p class="learning-repair-scope">返修第 ${Number(acceptance.repair_round)||1} 轮 · 处理 ${H(list(acceptance.failed_item_ids).join('、')||'反馈项')}<br>保护范围：${H(list(acceptance.protected_item_ids).join('、')||'以原验收账本为准')}</p>`:''}${bundle.can_assign?`<form class="acceptance-assignee"><label><span>指定验收人</span>${selectMarkup('user_id','选择验收人')}</label><button type="submit" class="btn btn-secondary">保存</button><p class="form-error" hidden></p></form>`:''}<form class="acceptance-form"><div class="acceptance-items">${items.map(item=>renderAcceptanceItem(item,draft,editable)).join('')||empty('尚未形成可逐项验收的账本','完成需求拆解后将在这里显示固定编号的验收项。')}</div>${editable&&items.length?`<div class="acceptance-compose"><div class="section-heading compact"><b>本轮验证结果</b><button type="button" class="text-button acceptance-pass-all">本轮全部验证通过</button></div><label><span>用一句话描述反馈</span><textarea name="raw_feedback" rows="3" maxlength="12000" placeholder="例如：共 10 项，3、6 未完成，其余均验证通过。第 3 项实际…，预期…">${H(draft.raw_feedback)}</textarea><small>先预览识别结果，确认后填入上面的逐项结论。输入草稿在任务自动刷新时保留。</small></label><button type="button" class="btn btn-secondary acceptance-preview">预览逐项结论</button><div class="acceptance-preview-result" aria-live="polite" ${draft.preview?'':'hidden'}></div><div class="acceptance-test-context"><label><span>实际验证的版本（可选）</span><input name="tested_version" maxlength="300" value="${H(draft.tested_version)}" placeholder="例如 Build 12345 / 交付包版本"></label><label><span>验证环境（可选）</span><input name="environment" maxlength="300" value="${H(draft.environment)}" placeholder="例如阿坝测试环境 / 现场版本"></label></div><p class="learning-note">此处记录你已完成的验证，不会连接环境或启动自动测试。</p><p class="form-error acceptance-error" hidden></p><button type="submit" class="btn btn-primary acceptance-submit">提交本轮验收反馈 ↗</button></div>`:request.status!=='delivered'?'<p class="learning-note">完成交付后，提出人或指定验收人可提交逐项验证结果。</p>':'<p class="learning-note">由需求提出人、指定验收人或管理员提交验收反馈。</p>'}</form><section class="learning-section"><h4>已提交的验收记录</h4>${renderRounds(rounds)}</section>${editable&&latestRound&&list(latestRound.items).some(item=>item.status==='failed')?`<div class="acceptance-repair-action"><div><b>基于已提交的未通过项继续修复</b><p>创建关联原需求的返修轮次，已通过项作为保护范围。提交反馈本身不会启动研发。</p></div><button type="button" class="btn btn-primary acceptance-start-repair" ${latestRound.repair_request_id?'disabled':''}>${latestRound.repair_request_id?'该轮已发起返修':'发起本轮局部返修 ↗'}</button><p class="form-error acceptance-repair-error" hidden></p></div>`:''}</section>`;
    if(Number(acceptance.latest_feedback_id)!==draft.expected_latest_feedback_id){
      const notice=document.createElement('div');notice.className='acceptance-stale-notice';
      notice.innerHTML='<b>已有更新的验收反馈</b><p>本轮草稿已保留。请查看下方最新记录，再确认是否按当前草稿提交。</p><button type="button" class="btn btn-secondary">已核对最新反馈，继续填写当前草稿</button>';
      session.element.querySelector('.acceptance-compose')?.prepend(notice);
      notice.querySelector('button').onclick=()=>{draft.expected_latest_feedback_id=Number(acceptance.latest_feedback_id)||0;draft.idempotency_key=key();notice.remove();};
    }
    const savedSummary=acceptance.summary||{},summaryLine=document.createElement('div');
    summaryLine.className='learning-mini-facts acceptance-saved-summary';
    summaryLine.innerHTML=`<span>已提交：通过 ${Number(savedSummary.passed)||0}</span><span>未通过 ${Number(savedSummary.failed)||0}</span><span>未验证 ${Number(savedSummary.unverified)||0}</span>${acceptance.requirement_revision!=null?`<span>需求修订 ${H(acceptance.requirement_revision)}</span>`:''}${acceptance.tested_version?`<span>验收版本 ${H(acceptance.tested_version)}</span>`:''}`;
    session.element.querySelector('.acceptance-heading')?.after(summaryLine);
    bindRequestLinks(session.element);
    const versionField=session.element.querySelector('[name="tested_version"]');
    if(versionField){versionField.required=true;versionField.closest('label').querySelector('span').textContent='实际验证的版本 / 构建号（必填）';}
    bindAcceptance(session,latestRound);
    if(draft.preview)drawPreview(session);
  }

  function bindAcceptance(session,latestRound) {
    const root=session.element,draft=session.draft,bundle=session.bundle;
    root.querySelectorAll('[data-ac-id]').forEach(row=>{
      const entry=draft.items[row.dataset.acId];
      row.querySelectorAll('.acceptance-choice button').forEach(button=>button.onclick=()=>{
        entry.status=button.dataset.status;row.dataset.status=entry.status;
        row.querySelectorAll('.acceptance-choice button').forEach(choice=>choice.setAttribute('aria-pressed',String(choice===button)));
        row.querySelector('.acceptance-failure').hidden=entry.status!=='failed';
      });
      row.querySelectorAll('[data-item-field]').forEach(input=>input.oninput=()=>{entry[input.dataset.itemField]=input.value;});
    });
    const form=root.querySelector('.acceptance-form');
    ['raw_feedback','tested_version','environment'].forEach(name=>{
      const input=form.elements.namedItem(name);if(input)input.oninput=()=>{draft[name]=input.value;if(name==='raw_feedback'){draft.preview=null;const preview=root.querySelector('.acceptance-preview-result');preview.hidden=true;preview.innerHTML='';}};
    });
    root.querySelector('.acceptance-pass-all')?.addEventListener('click',()=>{
      Object.values(draft.items).forEach(item=>item.status='passed');drawAcceptance(session);
    });
    root.querySelector('.acceptance-preview')?.addEventListener('click',async event=>{
      const error=root.querySelector('.acceptance-error'),button=event.currentTarget;error.hidden=true;
      if(!draft.raw_feedback.trim()){error.textContent='请先填写需要识别的反馈，或直接选择每项结论。';error.hidden=false;return;}
      button.disabled=true;session.previewLoading=true;const originalText=draft.raw_feedback;
      try {const preview=await api(`/api/requests/${encodeURIComponent(session.request.id)}/acceptance/preview`,{method:'POST',body:JSON.stringify({text:originalText})});if(draft.raw_feedback!==originalText){toast('反馈内容已修改，请重新预览');return;}draft.preview=preview;drawPreview(session);}
      catch(err){error.textContent=err.message;error.hidden=false;}finally{button.disabled=false;session.previewLoading=false;}
    });
    form.onsubmit=event=>{event.preventDefault();submitFeedback(session);};
    root.querySelector('.acceptance-start-repair')?.addEventListener('click',async event=>{
      const button=event.currentTarget,error=root.querySelector('.acceptance-repair-error');button.disabled=true;error.hidden=true;
      session.repairKey=session.repairKey||key();
      try {const result=await api(`/api/requests/${encodeURIComponent(session.request.id)}/acceptance/repair`,{method:'POST',body:JSON.stringify({feedback_id:latestRound.id,idempotency_key:session.repairKey})});toast('局部返修已进入队列');await refresh();await openDetail(result.request.id);}
      catch(err){error.textContent=err.message;error.hidden=false;button.disabled=false;}
    });
    const assignee=root.querySelector('.acceptance-assignee');
    if(assignee){
      const users=list(bundle.users),requesterId=session.request.requester_id;
      const options=users.map(user=>[String(user.id),`${user.display_name} · ${user.username}`]);
      if(requesterId&&!users.some(user=>Number(user.id)===Number(requesterId)))options.unshift([String(requesterId),'由需求提出人验收']);
      wireSelect(assignee.querySelector('.ledger-select'),options,bundle.acceptance?.acceptance_owner_id||requesterId||'');
      assignee.onsubmit=async event=>{
        event.preventDefault();const button=assignee.querySelector('[type="submit"]'),error=assignee.querySelector('.form-error');button.disabled=true;error.hidden=true;
        try{await api(`/api/requests/${encodeURIComponent(session.request.id)}/acceptance/assignee`,{method:'PUT',body:JSON.stringify({user_id:Number(assignee.elements.user_id.value)||Number(requesterId)})});toast('验收人已更新');await loadAcceptance(session);}
        catch(err){error.textContent=err.message;error.hidden=false;button.disabled=false;}
      };
    }
  }

  function drawPreview(session) {
    const panel=session.element.querySelector('.acceptance-preview-result'),preview=session.draft.preview;
    if(!panel||!preview)return;
    const items=[...list(preview.items)].sort((a,b)=>String(a.id).localeCompare(String(b.id),undefined,{numeric:true}));
    panel.hidden=false;
    panel.innerHTML=`<b>识别结果，尚未提交</b>${list(preview.warnings).length?bullets(preview.warnings):''}<div class="acceptance-preview-chips">${items.map(item=>`<span>${H(item.id)} · ${H(humanLabels[item.status]||'未验证')}</span>`).join('')}</div><p>未提及且未明确包含在“其余通过”中的项目，保留为未验证。</p><button type="button" class="btn btn-secondary" ${items.length?'':'disabled'}>确认识别结果，填入逐项结论</button>`;
    panel.querySelector('button').onclick=()=>{
      items.forEach(item=>{const target=session.draft.items[item.id];if(target&&Object.hasOwn(humanLabels,item.status)){target.status=item.status;['actual','expected','note'].forEach(name=>{if(item[name])target[name]=String(item[name]);});}});
      session.draft.preview=null;drawAcceptance(session);toast('识别结果已填入，请核对后提交反馈');
    };
  }

  async function submitFeedback(session) {
    const form=session.element.querySelector('.acceptance-form'),error=form.querySelector('.acceptance-error'),button=form.querySelector('[type="submit"]');
    if(!error||!button)return;
    error.hidden=true;
    const draft=session.draft,items=Object.values(draft.items);
    if(session.previewLoading){error.textContent='正在识别反馈，请等待预览结果后再提交。';error.hidden=false;return;}
    if(Number(session.bundle.acceptance.latest_feedback_id)!==draft.expected_latest_feedback_id){error.textContent='请先核对更新的验收记录，再确认本轮草稿。';error.hidden=false;return;}
    if(!draft.tested_version.trim()){error.textContent='请填写本次实际验证的版本或构建号。';error.hidden=false;form.elements.tested_version.focus();return;}
    if(draft.preview){error.textContent='请先确认识别结果，再提交本轮反馈。';error.hidden=false;return;}
    if(!items.some(item=>item.status!=='unverified')){error.textContent='请至少填写一项已验证结果。';error.hidden=false;return;}
    const incomplete=items.find(item=>item.status==='failed'&&!item.actual.trim()&&!item.note.trim()&&!draft.raw_feedback.trim());
    if(incomplete){error.textContent=`请补充 ${incomplete.id} 的实际表现、说明或原始反馈，便于精确返修。`;error.hidden=false;session.element.querySelector(`[data-ac-id="${CSS.escape(incomplete.id)}"] .acceptance-failure textarea`)?.focus();return;}
    const payload={items,raw_feedback:draft.raw_feedback,tested_version:draft.tested_version,environment:draft.environment,expected_latest_feedback_id:draft.expected_latest_feedback_id};
    const serialized=JSON.stringify(payload);
    if(draft.lastPayload&&draft.lastPayload!==serialized)draft.idempotency_key=key();draft.lastPayload=serialized;
    const controls=[...form.querySelectorAll('input,textarea,button')].map(control=>[control,control.disabled]);
    controls.forEach(([control])=>control.disabled=true);
    try {
      const result=await api(`/api/requests/${encodeURIComponent(session.request.id)}/acceptance/feedback`,{method:'POST',body:JSON.stringify({...payload,idempotency_key:draft.idempotency_key})});
      session.draft=null;
      if(result.acceptance)session.bundle={...session.bundle,acceptance:result.acceptance};
      await loadAcceptance(session);toast('本轮验收反馈已保存；可单独发起未通过项返修');catalog.loaded=false;
    } catch(err){
      error.textContent=`${err.message}。你的输入草稿已保留。`;error.hidden=false;controls.forEach(([control,disabled])=>control.disabled=disabled);
      if(!form.querySelector('.acceptance-check-latest')){
        const reload=document.createElement('button');reload.type='button';reload.className='text-button acceptance-check-latest';reload.textContent='读取最新验收记录并保留草稿';reload.onclick=()=>loadAcceptance(session);error.after(reload);
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
    openDetail(requestedId).then(()=>{if(new URLSearchParams(location.search).has('acceptance'))document.querySelector('.request-learning-panel')?.scrollIntoView({block:'start'});}).catch(error=>toast(error.message));
  }
  return {renderRequest,syncProjects,willOpenRequest:id=>{const session=draftPanels.get(id);if(session)session.reload=true;},open:()=>{syncProjects();loadCatalog(catalog.page);}};
})();
