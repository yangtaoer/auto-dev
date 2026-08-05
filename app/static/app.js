const USER = window.__USER__;
const state = { projects: [], users: [], dashboard: {active:[],recent:[],counts:{},runners:[]}, filter:'all', selectedRequest:null };
const MODE = {
  local_package:['本地打包交付','推送代码后在本机执行构建，交付安装包、SQL、配置和说明。'],
  sichuan_auto_review:['四川审核后交付','创建 PR，由专用审核服务账号在门禁通过后批准；检测合并后交付截图。'],
  product_manual_review:['产品审核后交付','先邮件发送 PR 给项目经理；系统循环检测合并，完成后交付截图。']
};
const STATUS = {queued:'等待执行',validating:'准入校验',developing:'Codex 研发中',submitting:'提交代码',building:'本地构建',waiting_merge:'等待 PR 合并',capturing:'生成合并凭证',delivering:'发送交付邮件',delivered:'已交付',waiting_approval:'等待人工确认',rejected:'准入驳回',failed:'执行失败',cancelled:'已取消'};
const escapeHtml = (value='') => String(value).replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const fmt = value => value ? new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '—';

async function api(url, options={}) {
  const response = await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});
  if (response.status===401) { location.href='/login'; throw new Error('登录已过期'); }
  const data = await response.json().catch(()=>({}));
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}
function toast(message){const el=document.querySelector('#toast');el.textContent=message;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2600)}

function switchView(name){
  document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===`view-${name}`));
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.view===name));
  const titles={dashboard:['CONTROL ROOM / 01','任务总览'],requests:['DELIVERY LEDGER / 02','交付记录'],projects:['POLICY REGISTRY / 03','项目策略'],users:['ACCESS REGISTRY / 04','账号管理']};
  document.querySelector('#view-code').textContent=titles[name][0];document.querySelector('#view-title').textContent=titles[name][1];
}

async function refresh(){
  const requests=[api('/api/dashboard'),api('/api/projects')];if(USER.role==='admin')requests.push(api('/api/users'));
  const [dashboard,projects,users]=await Promise.all(requests);
  state.dashboard=dashboard;state.projects=projects.projects;state.users=users?.users||[];renderDashboard();renderProjects();renderUsers();fillProjectSelect();
  if(state.selectedRequest) refreshDetail(state.selectedRequest,true);
}
function renderDashboard(){
  const c=state.dashboard.counts;
  const delivered=c.delivered||0, waiting=c.waiting_merge||0, failed=c.failed||0;
  const active=Object.entries(c).filter(([k])=>!['delivered','failed','rejected','cancelled'].includes(k)).reduce((a,[,v])=>a+v,0);
  document.querySelector('#metrics').innerHTML=[['运行中',active,'accent'],['等待合并',waiting,'warn'],['已交付',delivered,''],['需要关注',failed+(c.waiting_approval||0),'']].map(([l,v,k])=>`<div class="metric ${k}"><span>${l}</span><b>${String(v).padStart(2,'0')}</b></div>`).join('');
  const activeEl=document.querySelector('#active-runs');
  activeEl.innerHTML=state.dashboard.active.length?state.dashboard.active.map(runCard).join(''):'<div class="empty-state">当前没有运行中的任务。输入一个 TFS 编号，让下一条流水线亮起来。</div>';
  document.querySelector('#recent-table').innerHTML=state.dashboard.recent.slice(0,8).map(recentRow).join('')||'<tr><td colspan="6" class="muted">暂无记录</td></tr>';
  renderAllTable();bindRows();
  const runners=state.dashboard.runners||[],online=runners.filter(r=>r.online),status=document.querySelector('#runner-status');
  status.classList.toggle('offline',online.length===0);status.querySelector('span').textContent=online.length?`本机执行器在线 · ${online.map(r=>r.runner_id).join('、')}`:'本机执行器未连接';
}
function runCard(r){return `<article class="run-card clickable-row" data-id="${r.id}"><div class="run-top"><span class="work-id">TFS #${r.work_item_id}</span><span class="status-tag">${STATUS[r.status]||r.status}</span></div><h3>${escapeHtml(r.title||'正在读取需求…')}</h3><div class="project">${escapeHtml(r.project_name)} · ${escapeHtml(r.requester_name)}</div><div class="progress-track"><i style="width:${r.progress}%"></i></div><div class="progress-meta"><span>${MODE[r.delivery_mode]?.[0]||r.delivery_mode}</span><b>${r.progress}%</b></div></article>`}
function recentRow(r){return `<tr class="clickable-row" data-id="${r.id}"><td class="demand-cell"><b>#${r.work_item_id}</b><span>${escapeHtml(r.title||'等待读取')}</span></td><td>${escapeHtml(r.project_name)}</td><td>${MODE[r.delivery_mode]?.[0]||r.delivery_mode}</td><td><span class="status-dot" data-status="${r.status}">${STATUS[r.status]||r.status}</span></td><td><div class="mini-progress"><i style="width:${r.progress}%"></i></div></td><td>${fmt(r.updated_at)}</td></tr>`}
function renderAllTable(){
  const data=state.filter==='all'?state.dashboard.recent:state.dashboard.recent.filter(x=>x.status===state.filter);
  const el=document.querySelector('#all-table');if(!el)return;
  el.innerHTML=data.map(r=>`<tr class="clickable-row" data-id="${r.id}"><td class="demand-cell"><b>#${r.work_item_id}</b><span>${escapeHtml(r.title||'等待读取')}</span></td><td>${escapeHtml(r.project_name)}<br><small class="muted">${escapeHtml(r.requester_name)}</small></td><td>${MODE[r.delivery_mode]?.[0]||r.delivery_mode}</td><td><span class="status-dot" data-status="${r.status}">${STATUS[r.status]||r.status}</span></td><td>${r.pr_url?`<a href="${escapeHtml(r.pr_url)}" target="_blank" onclick="event.stopPropagation()">#${r.pr_id} ↗</a>`:'—'}</td><td>${fmt(r.created_at)}</td></tr>`).join('')||'<tr><td colspan="6" class="muted">没有符合条件的记录</td></tr>';bindRows();
}
function bindRows(){document.querySelectorAll('.clickable-row').forEach(el=>el.onclick=()=>openDetail(el.dataset.id))}

function renderProjects(){
  const el=document.querySelector('#project-grid');if(!el)return;
  el.innerHTML=state.projects.map(p=>`<article class="project-card ${p.enabled?'':'off'}"><div class="project-head"><div><span class="project-key">${escapeHtml(p.project_key)}</span><h3>${escapeHtml(p.name)}</h3></div><span class="mode-badge">${MODE[p.delivery_mode][0]}</span></div><div class="project-facts"><div class="fact"><span>TFS 项目</span><b>${escapeHtml(p.tfs_project)}</b></div><div class="fact"><span>本机执行器</span><b>${escapeHtml(p.runner_id)}</b></div><div class="fact"><span>基础分支</span><b>${escapeHtml(p.base_branch)}</b></div><div class="fact"><span>状态</span><b>${p.enabled?'已启用':'已停用'}</b></div></div><div class="project-actions"><button class="btn btn-ghost edit-project" data-id="${p.id}">编辑策略</button></div></article>`).join('');
  document.querySelectorAll('.edit-project').forEach(btn=>btn.onclick=()=>openProject(state.projects.find(p=>p.id===Number(btn.dataset.id))));
}
function renderUsers(){const el=document.querySelector('#users-table');if(!el)return;el.innerHTML=state.users.map(u=>`<tr><td><code>${escapeHtml(u.username)}</code></td><td>${escapeHtml(u.display_name)}</td><td>${escapeHtml(u.email)}</td><td>${u.role==='admin'?'管理员':'项目经理'}</td><td><span class="status-dot" data-status="${u.active?'delivered':'cancelled'}">${u.active?'启用':'停用'}</span></td></tr>`).join('')}
function fillProjectSelect(){
  const select=document.querySelector('#request-project');const current=select.value;
  select.innerHTML=state.projects.filter(p=>p.enabled).map(p=>`<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
  if(current)select.value=current;updateStrategy();
}
function updateStrategy(){const id=Number(document.querySelector('#request-project').value);const p=state.projects.find(x=>x.id===id);if(!p)return;const modeRow=document.querySelector('#request-mode-row'),modeSelect=document.querySelector('#request-mode');const canOverride=USER.role==='admin'&&p.allow_requirement_override;modeRow.hidden=!canOverride;if(!canOverride)modeSelect.value='';const selected=canOverride&&modeSelect.value?modeSelect.value:p.delivery_mode;const [name,desc]=MODE[selected];document.querySelector('#strategy-preview').innerHTML=`<b>${name}${selected!==p.delivery_mode?' · 本需求覆盖':''}</b><span>${desc}</span>`}

async function openDetail(id){state.selectedRequest=id;document.querySelector('#detail-backdrop').hidden=false;document.querySelector('#detail-drawer').classList.add('open');document.querySelector('#detail-drawer').setAttribute('aria-hidden','false');await refreshDetail(id)}
async function refreshDetail(id,silent=false){
  try{const d=(await api(`/api/requests/${id}`)).request;renderDetail(d)}catch(e){if(!silent)toast(e.message)}
}
function renderDetail(d){
  const artifacts=d.artifacts.map(a=>`<a class="artifact" href="${escapeHtml(a.external_url||`/api/artifacts/${a.id}`)}" target="_blank"><span>${escapeHtml(a.name)}<small class="muted"> · ${escapeHtml(a.kind)}</small></span><i>下载 ↘</i></a>`).join('')||'<p class="muted">产物将在对应步骤完成后出现。</p>';
  const steps=d.steps.map(s=>`<div class="timeline-item ${s.status}"><div class="timeline-mark"></div><div class="timeline-copy"><b>${escapeHtml(s.name)}</b><span>${escapeHtml(s.message||s.status)}</span></div></div>`).join('');
  const events=d.events.slice(0,20).map(e=>`<div class="event"><b>${escapeHtml(e.event_type)} · ${fmt(e.created_at)}</b><p>${escapeHtml(e.message)}</p></div>`).join('');
  const simulate=USER.role==='admin'&&d.status==='waiting_merge'&&d.policy_snapshot.simulation_mode?`<button class="btn btn-secondary" id="simulate-merge">模拟 PR 已合并</button>`:'';
  const cancel=!['delivered','failed','rejected','cancelled'].includes(d.status)?`<button class="btn btn-ghost" id="cancel-run">取消任务</button>`:'';
  document.querySelector('#detail-content').innerHTML=`<div class="detail-head"><p class="eyebrow">RUN / ${d.id.slice(0,8)}</p><h2>#${d.work_item_id} · ${escapeHtml(d.title||'读取需求中')}</h2><div class="detail-meta"><span>${escapeHtml(d.project_name)}</span><span>${escapeHtml(d.delivery_mode_label)}</span><span>${escapeHtml(d.status_label)}</span><span>${d.progress}%</span></div></div>${d.error_message?`<div class="error-box">${escapeHtml(d.error_message)}</div>`:''}<div class="detail-actions">${d.pr_url?`<a class="btn btn-primary" href="${escapeHtml(d.pr_url)}" target="_blank">打开 PR ↗</a>`:''}${simulate}${cancel}</div><p class="eyebrow">PIPELINE</p><div class="timeline">${steps}</div><p class="eyebrow">DELIVERABLES</p><div class="artifact-list">${artifacts}</div><div class="section-heading compact"><div><p class="eyebrow">EVENT STREAM</p><h2>执行记录</h2></div></div><div class="event-list">${events}</div>`;
  document.querySelector('#simulate-merge')?.addEventListener('click',async()=>{try{await api(`/api/requests/${d.id}/simulate-merge`,{method:'POST'});toast('已模拟合并，正在生成交付物');await refresh()}catch(e){toast(e.message)}});
  document.querySelector('#cancel-run')?.addEventListener('click',async()=>{if(!confirm('确认取消这个任务？'))return;try{await api(`/api/requests/${d.id}/cancel`,{method:'POST'});toast('任务已取消');await refresh()}catch(e){toast(e.message)}});
}
function closeDetail(){state.selectedRequest=null;document.querySelector('#detail-backdrop').hidden=true;document.querySelector('#detail-drawer').classList.remove('open');document.querySelector('#detail-drawer').setAttribute('aria-hidden','true')}

function openProject(project=null){
  const modal=document.querySelector('#project-modal'),form=document.querySelector('#project-form');form.reset();document.querySelector('#project-modal-title').textContent=project?'编辑项目策略':'新建项目策略';
  if(project){Object.entries(project).forEach(([k,v])=>{const field=form.elements[k];if(!field)return;if(field.type==='checkbox')field.checked=Boolean(v);else if(Array.isArray(v))field.value=v.join('\n');else field.value=v??''})}
  else{form.elements.enabled.checked=true;form.elements.simulation_mode.checked=true;form.elements.runner_id.value='yangtao-pc';form.elements.base_branch.value='dev';form.elements.tfs_collection_url.value='http://dev.tellhowsoft.com/DefaultCollection';form.elements.tfs_project.value='XiNanArea-New';form.elements.allowed_work_item_types.value='用户情景';form.elements.allowed_states.value='已评审';form.elements.sql_patterns.value='**/*.sql';form.elements.config_patterns.value='**/*.yml\n**/*.yaml\n**/*.properties\n**/*.xml';form.elements.protected_patterns.value='**/common/**\n**/shared/**\n**/production/**'}
  modal.hidden=false;
}
function closeModals(){document.querySelectorAll('.modal-backdrop').forEach(x=>x.hidden=true)}

document.querySelectorAll('.nav-item').forEach(btn=>btn.onclick=()=>switchView(btn.dataset.view));
document.querySelectorAll('[data-view-link]').forEach(btn=>btn.onclick=()=>switchView(btn.dataset.viewLink));
document.querySelectorAll('[data-close]').forEach(btn=>btn.onclick=closeModals);
document.querySelector('#detail-backdrop').onclick=closeDetail;document.querySelector('#close-detail').onclick=closeDetail;
document.querySelector('#open-request').onclick=()=>{document.querySelector('#request-modal').hidden=false;updateStrategy()};
document.querySelector('#request-project').onchange=updateStrategy;
document.querySelector('#request-mode').onchange=updateStrategy;
document.querySelector('#logout').onclick=async()=>{await api('/api/auth/logout',{method:'POST'});location.href='/login'};
document.querySelectorAll('.chip').forEach(btn=>btn.onclick=()=>{document.querySelectorAll('.chip').forEach(x=>x.classList.remove('active'));btn.classList.add('active');state.filter=btn.dataset.filter;renderAllTable()});
document.querySelector('#new-project')?.addEventListener('click',()=>openProject());
document.querySelector('#new-user')?.addEventListener('click',()=>{document.querySelector('#user-form').reset();document.querySelector('#user-modal').hidden=false});

document.querySelector('#request-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,error=document.querySelector('#request-error');error.hidden=true;const data=Object.fromEntries(new FormData(form));data.project_id=Number(data.project_id);data.work_item_id=Number(data.work_item_id);if(!data.delivery_mode)delete data.delivery_mode;try{const result=await api('/api/requests',{method:'POST',body:JSON.stringify(data)});closeModals();form.reset();toast('研发任务已进入队列');await refresh();await openDetail(result.id)}catch(err){error.textContent=err.message;error.hidden=false}});
document.querySelector('#project-form')?.addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,error=document.querySelector('#project-error');error.hidden=true;const data=Object.fromEntries(new FormData(f));const id=data.id;delete data.id;['enabled','simulation_mode','allow_requirement_override'].forEach(k=>data[k]=f.elements[k].checked);['allowed_work_item_types','allowed_states','package_patterns','sql_patterns','config_patterns','protected_patterns'].forEach(k=>data[k]=(data[k]||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean));try{await api(id?`/api/projects/${id}`:'/api/projects',{method:id?'PUT':'POST',body:JSON.stringify(data)});closeModals();toast('项目策略已保存');await refresh()}catch(err){error.textContent=err.message;error.hidden=false}});
document.querySelector('#user-form')?.addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,error=document.querySelector('#user-error');error.hidden=true;const data=Object.fromEntries(new FormData(form));try{await api('/api/users',{method:'POST',body:JSON.stringify(data)});closeModals();form.reset();toast('账号已创建');await refresh()}catch(err){error.textContent=err.message;error.hidden=false}});

setInterval(()=>{document.querySelector('#clock').textContent=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'medium',hour12:false}).format(new Date())},1000);
refresh().catch(e=>toast(e.message));setInterval(()=>refresh().catch(()=>{}),5000);
