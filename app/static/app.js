const USER = window.__USER__;
const state = { projects: [], users: [], dashboard: {active:[],recent:[],counts:{},runners:[],stats:{},capacity:{limit:5,active:0,queued:0,available:5}}, filter:'all', selectedRequest:null, selectedTerminal:false, selectedIntake:null, routingGeneration:0, live:{requestId:null,watcherId:null,cursor:0,generation:0,timer:null,lastGroup:'',lastKind:'',lastBubble:null} };
const MODE = {
  routing:['自动识别中','正在读取 TFS 需求并识别项目与交付策略。'],
  local_package:['本地打包交付','推送代码后在本机执行构建，交付安装包、SQL、配置和说明。'],
  sichuan_auto_review:['四川审核后交付','创建 PR，由专用审核服务账号在门禁通过后批准；检测合并后交付截图。'],
  product_manual_review:['产品审核后交付','先邮件发送 PR 给项目经理；系统循环检测合并，完成后交付截图。']
};
const STATUS = {routing:'项目识别中',queued:'等待执行',validating:'准入校验',developing:'Codex 研发中',submitting:'提交代码',building:'本地构建',waiting_merge:'等待 PR 合并',capturing:'生成合并凭证',delivering:'发送交付邮件',delivered:'已交付',waiting_approval:'等待人工确认',rejected:'准入驳回',failed:'执行失败',cancelled:'已取消'};
const TERMINAL = new Set(['delivered','failed','rejected','cancelled']);
const escapeHtml = (value='') => String(value).replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const fmt = value => value ? new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '—';
const fmtDuration = value => {const seconds=Number(value);if(!Number.isFinite(seconds)||seconds<0)return '—';if(seconds<60)return `${Math.max(1,Math.round(seconds))} 秒`;const minutes=Math.floor(seconds/60),hours=Math.floor(minutes/60),days=Math.floor(hours/24);if(days)return `${days} 天 ${hours%24} 小时`;if(hours)return `${hours} 小时 ${minutes%60} 分`;return `${minutes} 分`;};
const artifactLinks = artifacts => (artifacts||[]).map(a=>`<a class="artifact-link" href="${escapeHtml(a.external_url||`/api/artifacts/${a.id}`)}" target="_blank" title="${escapeHtml(a.name)}" onclick="event.stopPropagation()">${escapeHtml(a.name)} ↗</a>`).join('')||'<span class="muted">—</span>';
const delay = milliseconds => new Promise(resolve=>setTimeout(resolve,milliseconds));

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
  const titles={dashboard:['控制室 / CONTROL ROOM / 01','任务总览'],requests:['交付台账 / DELIVERY LEDGER / 02','交付记录'],projects:['支持项目 / SUPPORTED PROJECTS / 03','自助项目'],users:['账号管理 / ACCESS REGISTRY / 04','账号管理']};
  document.querySelector('#view-code').textContent=titles[name][0];document.querySelector('#view-title').textContent=titles[name][1];
}

async function refresh(){
  const userRequest=USER.role==='admin'?api('/api/users'):Promise.resolve({users:[]});
  const projectRequest=USER.role==='admin'?api('/api/projects'):Promise.resolve({projects:[]});
  const [dashboard,projects,me,users]=await Promise.all([api('/api/dashboard'),projectRequest,api('/api/me'),userRequest]);
  Object.assign(USER,me.user);state.dashboard=dashboard;state.projects=projects.projects;state.users=users.users||[];renderDashboard();renderProjects();renderUsers();renderRequestEmailOptions(false);
  if(state.selectedRequest&&!state.selectedTerminal) refreshDetail(state.selectedRequest,true);
}
function renderDashboard(){
  const c=state.dashboard.counts;
  const delivered=c.delivered||0, waiting=c.waiting_merge||0, failed=c.failed||0;
  const active=Object.entries(c).filter(([k])=>!['delivered','failed','rejected','cancelled'].includes(k)).reduce((a,[,v])=>a+v,0);
  const stats=state.dashboard.stats||{};
  const metricData=USER.role==='admin'?[['当日任务',stats.today_total||0,'accent'],['任务总量',stats.total||0,''],['成功交付',stats.success||0,''],['失败 / 驳回',stats.failed||0,'danger'],['运行中',stats.running||0,'accent'],['等待合并',stats.waiting_merge||0,'warn']]:[['运行中',active,'accent'],['等待合并',waiting,'warn'],['已交付',delivered,''],['需要关注',failed+(c.waiting_approval||0),'']];
  const metrics=document.querySelector('#metrics');metrics.classList.toggle('admin-metrics',USER.role==='admin');metrics.innerHTML=metricData.map(([l,v,k])=>`<div class="metric ${k} ${l==='运行中'&&Number(v)>0?'live':''}"><span>${l}</span><b>${String(v).padStart(2,'0')}</b></div>`).join('');
  renderCodexQuota();
  const activeEl=document.querySelector('#active-runs'),activePipeline=document.querySelector('#active-pipeline');
  activePipeline.hidden=state.dashboard.active.length===0;
  activeEl.innerHTML=state.dashboard.active.map(runCard).join('');
  const capacity=state.dashboard.capacity||{limit:5,active:0,queued:0};
  const capacityStatus=document.querySelector('#capacity-status');
  if(capacityStatus)capacityStatus.innerHTML=`<b>${Number(capacity.active)||0} / ${Number(capacity.limit)||5}</b><span>并发槽位${capacity.queued?` · ${Number(capacity.queued)} 个排队`:''}</span>`;
  document.body.classList.toggle('has-active-runs',state.dashboard.active.length>0);
  document.querySelector('#recent-table').innerHTML=state.dashboard.recent.slice(0,8).map(recentRow).join('')||'<tr><td colspan="6" class="muted">暂无记录</td></tr>';
  renderAllTable();bindRows();
  const runners=state.dashboard.runners||[],online=runners.filter(r=>r.online),status=document.querySelector('#runner-status');
  status.classList.toggle('offline',online.length===0);status.querySelector('span').textContent=online.length?'执行器在线':'执行器离线';
}
function renderCodexQuota(){const el=document.querySelector('#codex-quota');if(!el)return;const runners=state.dashboard.runners||[];const runner=runners.find(r=>r.online&&r.codex_usage?.available)||runners.find(r=>r.codex_usage?.available);if(!runner){el.innerHTML='<div><p class="eyebrow">套餐容量 / CODEX CAPACITY</p><h2>套餐信息暂不可用</h2></div><span class="muted">执行器上线后自动同步</span>';return}const usage=runner.codex_usage,primary=usage.primary||{},remaining=primary.remaining_percent,used=primary.used_percent??0,credits=usage.credits||{},plan=String(usage.plan_type||'unknown').toUpperCase();const reset=primary.resets_at?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(primary.resets_at*1000)):'—';const balance=credits.unlimited?'无限':(credits.balance??'0');el.innerHTML=`<div class="quota-copy"><p class="eyebrow">套餐容量 / CODEX CAPACITY / ${escapeHtml(runner.runner_id)}</p><h2>${escapeHtml(plan)} 套餐</h2><span>周期重置 ${escapeHtml(reset)} · 额外余额 ${escapeHtml(balance)}</span></div><div class="quota-meter"><div class="quota-number"><b>${remaining??'—'}%</b><span>套餐剩余</span></div><div class="quota-track"><i style="width:${Math.max(0,Math.min(100,100-used))}%"></i></div><small>数据更新时间 ${fmt(usage.updated_at)}</small></div>`}
function runCard(r){const intake=r.record_type==='intake'||r.intake_id;return `<article class="run-card clickable-row ${intake?'routing-card':''}" ${intake?`data-intake-id="${r.intake_id||r.id}" data-work-item-id="${r.work_item_id}"`:`data-id="${r.id}"`}><div class="run-top"><span class="work-id">TFS #${r.work_item_id}</span><span class="status-tag">${STATUS[r.status]||r.status}</span></div><h3>${escapeHtml(r.title||'正在读取需求…')}</h3><div class="project">${escapeHtml(r.project_name)} · ${escapeHtml(r.requester_name)}</div><div class="live-activity"><i></i><div><span>当前输出</span><b>${escapeHtml(r.current_activity||STATUS[r.status]||'等待执行器反馈')}</b></div></div><div class="run-mode">${MODE[r.delivery_mode]?.[0]||r.delivery_mode}</div></article>`}
function recentRow(r){const intake=r.record_type==='intake'||r.intake_id;return `<tr class="clickable-row" ${intake?`data-intake-id="${r.intake_id||r.id}" data-work-item-id="${r.work_item_id}"`:`data-id="${r.id}"`}><td class="demand-cell"><b>#${r.work_item_id}</b><span>${escapeHtml(r.title||'等待读取')}</span></td><td>${escapeHtml(r.project_name)}</td><td>${MODE[r.delivery_mode]?.[0]||r.delivery_mode}</td><td><span class="status-dot" data-status="${r.status}">${STATUS[r.status]||r.status}</span></td><td><span class="activity-cell">${escapeHtml(r.current_activity||'—')}</span></td><td>${fmt(r.updated_at)}</td></tr>`}
function renderAllTable(){
  const data=state.filter==='all'?state.dashboard.recent:state.dashboard.recent.filter(x=>x.status===state.filter);
  const el=document.querySelector('#all-table');if(!el)return;
  el.innerHTML=data.map(r=>{const intake=r.record_type==='intake'||r.intake_id;return `<tr class="clickable-row" ${intake?`data-intake-id="${r.intake_id||r.id}" data-work-item-id="${r.work_item_id}"`:`data-id="${r.id}"`}><td class="demand-cell"><b>#${r.work_item_id} · ${escapeHtml(r.project_name)}</b><span>${escapeHtml(r.title||'等待读取')}</span><small>${MODE[r.delivery_mode]?.[0]||r.delivery_mode}</small></td><td><span class="status-dot" data-status="${r.status}">${STATUS[r.status]||r.status}</span></td><td>${escapeHtml(r.requester_name)}</td><td>${fmt(r.created_at)}</td><td>${fmt(r.completed_at)}</td><td class="duration-cell">${fmtDuration(r.duration_seconds)}</td><td><div class="artifact-links">${artifactLinks(r.artifacts)}</div></td></tr>`}).join('')||'<tr><td colspan="7" class="muted">没有符合条件的记录</td></tr>';bindRows();
}
function bindRows(){document.querySelectorAll('.clickable-row').forEach(el=>el.onclick=()=>el.dataset.intakeId?openRoutingDetail(el.dataset.intakeId,el.dataset.workItemId):openDetail(el.dataset.id))}

function renderProjects(){
  const el=document.querySelector('#project-grid');if(!el)return;
  el.innerHTML=state.projects.length?state.projects.map(p=>`<article class="project-card"><div class="project-head"><div><span class="project-key">${escapeHtml(p.project_key)}</span><h3>${escapeHtml(p.name)}</h3></div><span class="mode-badge">${MODE[p.delivery_mode]?.[0]||p.delivery_mode}</span></div><div class="project-facts"><div class="fact"><span>TFS 项目</span><b>${escapeHtml(p.tfs_project)}</b></div><div class="fact"><span>本机执行器</span><b>${escapeHtml(p.runner_id)}</b></div><div class="fact"><span>基础分支</span><b>${escapeHtml(p.base_branch)}</b></div><div class="fact"><span>服务状态</span><b>支持自助开发</b></div></div><div class="project-origin"><i></i><span>策略由本机项目目录同步</span></div></article>`).join(''):'<div class="empty-state catalog-empty"><b>项目目录正在等待本机同步</b><span>请确认本机执行器在线；项目预设同步后会自动出现在这里。</span></div>';
}
function renderUsers(){
  const el=document.querySelector('#users-table');if(!el)return;
  el.innerHTML=state.users.map(u=>{const emails=(u.emails||[u.email]).map((email,index)=>`<span class="email-badge ${index===0?'primary':''}">${escapeHtml(email)}</span>`).join('');const self=u.id===USER.id;return `<tr><td><code>${escapeHtml(u.username)}</code>${self?'<small class="self-mark">当前账号</small>':''}</td><td>${escapeHtml(u.display_name)}</td><td><div class="email-badges">${emails}</div></td><td>${u.role==='admin'?'管理员':'项目经理'}</td><td><span class="status-dot" data-status="${u.active?'delivered':'cancelled'}">${u.active?'启用':'停用'}</span></td><td><div class="row-actions"><button class="text-action edit-user" data-id="${u.id}">编辑</button><button class="text-action ${u.active?'danger':'success'} toggle-user" data-id="${u.id}" ${self?'disabled title="不能停用当前账号"':''}>${u.active?'禁用':'启用'}</button></div></td></tr>`}).join('');
  document.querySelectorAll('.edit-user').forEach(btn=>btn.onclick=()=>openUser(state.users.find(u=>u.id===Number(btn.dataset.id))));
  document.querySelectorAll('.toggle-user').forEach(btn=>btn.onclick=()=>toggleUser(state.users.find(u=>u.id===Number(btn.dataset.id))));
}
function renderRequestEmailOptions(reset=true){const el=document.querySelector('#request-email-options');if(!el)return;const checked=reset?new Set(USER.emails||[]):new Set([...el.querySelectorAll('input:checked')].map(x=>x.value));const emails=USER.emails||[USER.email].filter(Boolean);el.innerHTML=emails.length?emails.map((email,index)=>`<label class="mail-choice"><input type="checkbox" name="notification_emails" value="${escapeHtml(email)}" ${reset||checked.has(email)?'checked':''}><span><b>${escapeHtml(email)}</b><small>${index===0?'主邮箱':'备用邮箱'}</small></span><i>✓</i></label>`).join(''):'<div class="mail-empty">当前账号尚未配置通知邮箱，请联系管理员。</div>'}

async function waitForRouting(intakeId){
  for(let attempt=0;attempt<120;attempt+=1){
    const intake=(await api(`/api/intakes/${intakeId}`)).intake;
    if(intake.status==='routed')return intake.result_request_id;
    if(intake.status==='failed')throw new Error(intake.error_message||'未能自动识别该需求所属项目');
    await delay(1000);
  }
  return null;
}

function addOptimisticIntake(intakeId,workItemId){
  const now=new Date().toISOString();
  const intake={id:intakeId,intake_id:intakeId,record_type:'intake',work_item_id:workItemId,title:'正在读取 TFS 需求并识别项目…',project_name:'项目识别中',requester_name:USER.display_name,delivery_mode:'routing',status:'routing',current_activity:'任务已提交，等待执行器扫描',created_at:now,updated_at:now,completed_at:null,duration_seconds:null,artifacts:[]};
  state.dashboard.active=[intake,...state.dashboard.active.filter(item=>item.id!==intakeId)].slice(0,12);
  state.dashboard.recent=[intake,...state.dashboard.recent.filter(item=>item.id!==intakeId)].slice(0,40);
  state.dashboard.counts.routing=(state.dashboard.counts.routing||0)+1;
  if(state.dashboard.stats){state.dashboard.stats.total=(state.dashboard.stats.total||0)+1;state.dashboard.stats.today_total=(state.dashboard.stats.today_total||0)+1;state.dashboard.stats.running=(state.dashboard.stats.running||0)+1}
  if(state.dashboard.capacity)state.dashboard.capacity.queued=(state.dashboard.capacity.queued||0)+1;
  switchView('dashboard');renderDashboard();
}

function showDetailDrawer(){document.querySelector('#detail-backdrop').hidden=false;document.querySelector('#detail-drawer').classList.add('open');document.querySelector('#detail-drawer').setAttribute('aria-hidden','false')}
async function openRoutingDetail(intakeId,workItemId){state.selectedIntake=intakeId;state.selectedRequest=null;state.selectedTerminal=false;const generation=++state.routingGeneration;showDetailDrawer();document.querySelector('#detail-content').innerHTML=`<div class="detail-head routing-head"><p class="eyebrow">项目识别 / ROUTING / ${escapeHtml(intakeId.slice(0,8))}</p><h2>#${workItemId} · 正在识别研发项目</h2><div class="detail-meta"><span>任务已成功发起</span><span>等待本机执行器</span></div></div><div class="routing-signal"><i></i><div><b>正在读取 TFS 需求</b><span>系统会自动识别所属项目与交付策略，识别完成后此处直接切换为任务详情。</span></div></div>`;try{const requestId=await waitForRouting(intakeId);if(generation!==state.routingGeneration||state.selectedIntake!==intakeId)return;if(requestId){state.selectedIntake=null;toast('项目已识别，研发任务进入队列');await refresh();await openDetail(requestId)}else{document.querySelector('#detail-content').innerHTML='<div class="error-box">项目识别仍在后台进行，请稍后从交付记录中查看。</div>'}}catch(err){if(generation!==state.routingGeneration)return;document.querySelector('#detail-content').innerHTML=`<div class="detail-head"><p class="eyebrow">识别失败 / ROUTING FAILED</p><h2>#${workItemId} · 项目识别失败</h2></div><div class="error-box">${escapeHtml(err.message)}</div><div class="detail-actions"><button class="btn btn-secondary" id="retry-new-run">重新发起</button></div>`;document.querySelector('#retry-new-run')?.addEventListener('click',()=>{closeDetail();document.querySelector('#open-request').click()})}}
async function openDetail(id){state.selectedIntake=null;state.selectedRequest=id;state.selectedTerminal=false;showDetailDrawer();await refreshDetail(id)}
async function refreshDetail(id,silent=false){
  const drawer=document.querySelector('#detail-drawer'),eventList=document.querySelector('#detail-content .event-list');
  const drawerScroll=silent?drawer.scrollTop:0,eventScroll=silent&&eventList?eventList.scrollTop:0;
  try{const d=(await api(`/api/requests/${id}`)).request;if(state.selectedRequest!==id)return;renderDetail(d);if(silent)requestAnimationFrame(()=>{drawer.scrollTop=drawerScroll;const refreshedEvents=document.querySelector('#detail-content .event-list');if(refreshedEvents)refreshedEvents.scrollTop=eventScroll})}catch(e){if(!silent)toast(e.message)}
}
function renderDetail(d){
  state.selectedTerminal=TERMINAL.has(d.status);
  const artifacts=d.artifacts.map(a=>`<a class="artifact" href="${escapeHtml(a.external_url||`/api/artifacts/${a.id}`)}" target="_blank"><span>${escapeHtml(a.name)}<small class="muted"> · ${escapeHtml(a.kind)}</small></span><i>下载 ↘</i></a>`).join('')||'<p class="muted">产物将在对应步骤完成后出现。</p>';
  const steps=d.steps.map(s=>`<div class="timeline-item ${s.status}"><div class="timeline-mark"></div><div class="timeline-copy"><b>${escapeHtml(s.name)}</b><span>${escapeHtml(s.message||s.status)}</span></div></div>`).join('');
  const events=d.events.slice(0,20).map(e=>`<div class="event"><b>${escapeHtml(e.event_type)} · ${fmt(e.created_at)}</b><p>${escapeHtml(e.message)}</p></div>`).join('');
  const simulate=USER.role==='admin'&&d.status==='waiting_merge'&&d.policy_snapshot.simulation_mode?`<button class="btn btn-secondary" id="simulate-merge">模拟 PR 已合并</button>`:'';
  const cancel=!['delivered','failed','rejected','cancelled'].includes(d.status)?`<button class="btn btn-ghost" id="cancel-run">取消任务</button>`:'';
  const live=d.status==='developing'?`<button class="btn btn-live" id="open-codex-stream"><i></i>查看研发过程</button>`:'';
  document.querySelector('#detail-content').innerHTML=`<div class="detail-head"><p class="eyebrow">任务 / RUN / ${d.id.slice(0,8)}</p><h2>#${d.work_item_id} · ${escapeHtml(d.title||'读取需求中')}</h2><div class="detail-meta"><span>${escapeHtml(d.project_name)}</span><span>${escapeHtml(d.delivery_mode_label)}</span><span>${escapeHtml(d.status_label)}</span></div><div class="notification-line">通知至 ${(d.notification_emails||[]).map(escapeHtml).join('、')||'—'}</div></div>${d.error_message?`<div class="error-box">${escapeHtml(d.error_message)}</div>`:''}<div class="detail-actions">${live}${d.pr_url?`<a class="btn btn-primary" href="${escapeHtml(d.pr_url)}" target="_blank">打开 PR ↗</a>`:''}${simulate}${cancel}</div><p class="eyebrow">研发流水线 / PIPELINE</p><div class="timeline">${steps}</div><p class="eyebrow">交付产物 / DELIVERABLES</p><div class="artifact-list">${artifacts}</div><div class="section-heading compact"><div><p class="eyebrow">事件流 / EVENT STREAM</p><h2>执行记录</h2></div></div><div class="event-list">${events}</div>`;
  document.querySelector('#open-codex-stream')?.addEventListener('click',()=>openCodexStream(d.id,d.work_item_id));
  document.querySelector('#simulate-merge')?.addEventListener('click',async()=>{try{await api(`/api/requests/${d.id}/simulate-merge`,{method:'POST'});toast('已模拟合并，正在生成交付物');await refresh()}catch(e){toast(e.message)}});
  document.querySelector('#cancel-run')?.addEventListener('click',async()=>{if(!confirm('确认取消这个任务？'))return;try{await api(`/api/requests/${d.id}/cancel`,{method:'POST'});toast('任务已取消');await refresh()}catch(e){toast(e.message)}});
}
async function openCodexStream(requestId,workItemId){closeCodexStream();const live=state.live,generation=++live.generation;live.requestId=requestId;live.cursor=0;live.lastGroup='';live.lastKind='';live.lastBubble=null;const panel=document.querySelector('#codex-stream-panel'),chat=document.querySelector('#codex-chat');panel.classList.add('open');panel.setAttribute('aria-hidden','false');document.querySelector('#codex-stream-title').textContent=`TFS #${workItemId} · 研发过程`;chat.innerHTML='<div class="chat-system"><i></i><span>正在连接本机 Codex 会话…</span></div>';try{const result=await api(`/api/requests/${requestId}/codex-watch/start`,{method:'POST'});if(generation!==live.generation)return;live.watcherId=result.watcher_id;live.cursor=result.cursor||0;chat.innerHTML='<div class="chat-system active"><i></i><span>实时通道已打开，等待新的 Codex 输出</span></div>';pollCodexStream(generation)}catch(err){if(generation!==live.generation)return;chat.innerHTML=`<div class="chat-system error"><i></i><span>${escapeHtml(err.message)}</span></div>`}}
async function pollCodexStream(generation){const live=state.live;if(generation!==live.generation||!live.watcherId)return;try{const result=await api(`/api/requests/${live.requestId}/codex-watch/${live.watcherId}?after=${live.cursor}`);if(generation!==live.generation)return;live.cursor=result.cursor||live.cursor;(result.events||[]).forEach(appendCodexEvent);live.timer=setTimeout(()=>pollCodexStream(generation),650)}catch(err){if(generation!==live.generation)return;appendCodexSystem(err.message,'error');live.watcherId=null}}
function renderCodexMarkdown(target,value){target.className='chat-rendered';let list=null;String(value||'').split(/\r?\n/).forEach(line=>{if(!line.trim()){list=null;return}if(line.startsWith('### ')){const heading=document.createElement('h3');heading.textContent=line.slice(4);target.appendChild(heading);list=null;return}if(line.startsWith('- ')){if(!list){list=document.createElement('ul');target.appendChild(list)}const item=document.createElement('li');item.textContent=line.slice(2);list.appendChild(item);return}const paragraph=document.createElement('p');paragraph.textContent=line;target.appendChild(paragraph);list=null})}
function appendCodexEvent(event){const live=state.live,chat=document.querySelector('#codex-chat'),kind=event.kind||'status';if(kind==='status')return;const group=event.group||`seq-${event.seq}`;let bubble=live.lastBubble;if(!event.delta||group!==live.lastGroup||kind!==live.lastKind||!bubble){bubble=document.createElement('article');bubble.className=`chat-message ${kind}`;const labels={assistant:'CODEX 研发结论',reasoning:'分析摘要',command:'终端执行',file:'文件变更',plan:'研发计划'};const label=document.createElement('div');label.className='chat-label';const name=document.createElement('span');name.textContent=labels[kind]||'研发过程';const time=document.createElement('time');time.textContent=new Date(event.at).toLocaleTimeString('zh-CN',{hour12:false});label.append(name,time);bubble.appendChild(label);const content=document.createElement(event.format==='markdown'?'div':'pre');bubble.appendChild(content);chat.appendChild(bubble);live.lastBubble=bubble}const content=bubble.lastElementChild;if(event.format==='markdown'&&!event.delta){renderCodexMarkdown(content,event.content)}else{content.textContent+=(event.content||'')}live.lastGroup=group;live.lastKind=kind;while(chat.children.length>140)chat.removeChild(chat.firstElementChild);chat.scrollTop=chat.scrollHeight}
function appendCodexSystem(message,type='status'){const chat=document.querySelector('#codex-chat'),el=document.createElement('div');el.className=`chat-system ${type}`;el.innerHTML=`<i></i><span>${escapeHtml(message)}</span>`;chat.appendChild(el);chat.scrollTop=chat.scrollHeight}
function closeCodexStream(){const live=state.live,requestId=live.requestId,watcherId=live.watcherId;live.generation+=1;if(live.timer)clearTimeout(live.timer);live.timer=null;live.watcherId=null;live.requestId=null;live.cursor=0;live.lastBubble=null;const panel=document.querySelector('#codex-stream-panel');panel.classList.remove('open');panel.setAttribute('aria-hidden','true');document.querySelector('#codex-chat').innerHTML='';if(requestId&&watcherId)fetch(`/api/requests/${requestId}/codex-watch/${watcherId}/stop`,{method:'POST',headers:{'Content-Type':'application/json'},keepalive:true}).catch(()=>{})}
function closeDetail(){state.routingGeneration+=1;state.selectedIntake=null;state.selectedRequest=null;state.selectedTerminal=false;closeCodexStream();document.querySelector('#detail-backdrop').hidden=true;document.querySelector('#detail-drawer').classList.remove('open');document.querySelector('#detail-drawer').setAttribute('aria-hidden','true')}

function addEmailRow(value=''){const editor=document.querySelector('#user-email-editor'),row=document.createElement('div');row.className='email-row';row.innerHTML=`<span class="email-order">${String(editor.children.length+1).padStart(2,'0')}</span><input type="email" class="user-email-input" value="${escapeHtml(value)}" placeholder="name@example.com" required><button type="button" title="移除邮箱">×</button>`;row.querySelector('button').onclick=()=>{if(editor.children.length===1){row.querySelector('input').value='';return}row.remove();[...editor.children].forEach((item,index)=>item.querySelector('.email-order').textContent=String(index+1).padStart(2,'0'))};editor.appendChild(row)}
function openUser(user=null){const form=document.querySelector('#user-form');form.reset();form.elements.id.value=user?.id||'';form.elements.username.value=user?.username||'';form.elements.display_name.value=user?.display_name||'';form.elements.role.value=user?.role||'pm';form.elements.active.checked=user?.active??true;form.elements.password.required=!user;form.elements.password.value='';form.elements.role.disabled=user?.id===USER.id;form.elements.active.disabled=user?.id===USER.id;document.querySelector('#user-modal-title').textContent=user?'编辑账号':'新建内部账号';document.querySelector('#password-label').textContent=user?'重置密码（留空不修改）':'初始密码';document.querySelector('#save-user').textContent=user?'保存修改':'创建账号';document.querySelector('#user-email-editor').innerHTML='';(user?.emails||['']).forEach(addEmailRow);document.querySelector('#user-error').hidden=true;document.querySelector('#user-modal').hidden=false}
async function toggleUser(user){if(!user||user.id===USER.id)return;if(user.active&&!confirm(`确认禁用账号“${user.display_name}”？禁用后该账号会立即退出登录。`))return;try{await api(`/api/users/${user.id}`,{method:'PUT',body:JSON.stringify({username:user.username,display_name:user.display_name,emails:user.emails||[user.email],role:user.role,active:!user.active})});toast(user.active?'账号已禁用':'账号已启用');await refresh()}catch(err){toast(err.message)}}
function closeModals(){document.querySelectorAll('.modal-backdrop').forEach(x=>x.hidden=true)}

document.querySelectorAll('.nav-item').forEach(btn=>btn.onclick=()=>switchView(btn.dataset.view));
document.querySelectorAll('[data-view-link]').forEach(btn=>btn.onclick=()=>switchView(btn.dataset.viewLink));
document.querySelectorAll('[data-close]').forEach(btn=>btn.onclick=closeModals);
document.querySelector('#detail-backdrop').onclick=closeDetail;document.querySelector('#close-detail').onclick=closeDetail;
document.querySelector('#close-codex-stream').onclick=closeCodexStream;
document.querySelector('#open-request').onclick=()=>{renderRequestEmailOptions(true);document.querySelector('#request-error').hidden=true;document.querySelector('#request-modal').hidden=false;setTimeout(()=>document.querySelector('#request-form [name="work_item_id"]').focus(),0)};
document.querySelector('#logout').onclick=async()=>{await api('/api/auth/logout',{method:'POST'});location.href='/login'};
document.querySelectorAll('.chip').forEach(btn=>btn.onclick=()=>{document.querySelectorAll('.chip').forEach(x=>x.classList.remove('active'));btn.classList.add('active');state.filter=btn.dataset.filter;renderAllTable()});
document.querySelector('#new-user')?.addEventListener('click',()=>openUser());
document.querySelector('#add-user-email')?.addEventListener('click',()=>addEmailRow());

document.querySelector('#request-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,error=document.querySelector('#request-error'),button=form.querySelector('button[type="submit"]');error.hidden=true;const data={work_item_id:Number(form.elements.work_item_id.value),notification_emails:[...form.querySelectorAll('[name="notification_emails"]:checked')].map(input=>input.value)};if(!data.notification_emails.length){error.textContent='请至少选择一个通知邮箱';error.hidden=false;return}button.disabled=true;try{const result=await api('/api/requests',{method:'POST',body:JSON.stringify(data)});closeModals();form.reset();if(result.routing){addOptimisticIntake(result.id,data.work_item_id);toast('提交成功，任务已进入运行看板');openRoutingDetail(result.id,data.work_item_id)}else{toast('研发任务已进入队列');await refresh();await openDetail(result.id)}}catch(err){form.elements.work_item_id.value=data.work_item_id;document.querySelector('#request-modal').hidden=false;error.textContent=err.message;error.hidden=false}finally{button.disabled=false}});
document.querySelector('#user-form')?.addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,error=document.querySelector('#user-error');error.hidden=true;const id=Number(form.elements.id.value)||null;const emails=[...form.querySelectorAll('.user-email-input')].map(input=>input.value.trim()).filter(Boolean);const data={username:form.elements.username.value.trim(),display_name:form.elements.display_name.value.trim(),emails,password:form.elements.password.value,role:form.elements.role.value,active:form.elements.active.checked};if(!data.password)delete data.password;try{const result=await api(id?`/api/users/${id}`:'/api/users',{method:id?'PUT':'POST',body:JSON.stringify(data)});if(result.user.id===USER.id)Object.assign(USER,result.user);closeModals();form.reset();toast(id?'账号已更新':'账号已创建');await refresh()}catch(err){error.textContent=err.message;error.hidden=false}});

setInterval(()=>{document.querySelector('#clock').textContent=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'medium',hour12:false}).format(new Date())},1000);
refresh().catch(e=>toast(e.message));setInterval(()=>refresh().catch(()=>{}),5000);
window.addEventListener('beforeunload',()=>{const live=state.live;if(live.requestId&&live.watcherId)fetch(`/api/requests/${live.requestId}/codex-watch/${live.watcherId}/stop`,{method:'POST',keepalive:true})});
