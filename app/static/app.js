const USER = window.__USER__;
const state = { projects: [], projectGuideSignature:'', users: [], notificationUsers:[], analytics:null, dashboard: {active:[],recent:[],counts:{},runners:[],stats:{},capacity:{limit:5,active:0,queued:0,available:5}}, records:{items:[],page:1,pageSize:10,total:0,totalPages:1,filters:{keyword:'',task_type:'',project_key:'',status:'',requester_id:'',date_from:'',date_to:''}}, selectedRequest:null, selectedTerminal:false, selectedIntake:null, routingGeneration:0, continuationDrafts:new Map(), orbExperience:{initialized:false,runs:new Map(),pending:[],detailSteps:new Map(),activitySignature:'',activityIndex:0,activityTimer:null}, live:{requestId:null,taskType:'development',watcherId:null,cursor:0,generation:0,timer:null,lastGroup:'',lastKind:'',lastBubble:null} };
const MODE = {
  routing:['自动识别中','正在读取 TFS 需求并识别项目与交付策略。'],
  local_package:['本地打包交付','提交到最新目标分支后在本机执行构建，交付安装包、SQL、配置和说明。'],
  sichuan_auto_review:['四川审核后交付','创建 PR 并自动审核；合并后按本次选择生成交付产物。'],
  sichuan_review_local_package:['四川审核后本地打包交付','创建 PR 并由四川审核自动合并；随后仅对本次修改的端进行本地打包。'],
  product_manual_review:['产品审核后交付','等待产品审核合并；完成后按本次选择生成交付产物。']
};
const STATUS = {routing:'项目识别中',joint_running:'联合研发中',waiting_runner:'等待执行器上线',queued:'等待执行',validating:'准入校验',developing:'DevCore 研发中',submitting:'提交代码',building:'本地构建',releasing:'自动发版',waiting_input:'待补充信息',waiting_merge:'等待 PR 合并',capturing:'截取 PR 页面',delivering:'汇总交付',delivered:'已交付',waiting_approval:'等待人工确认',rejected:'准入驳回',failed:'执行失败',cancelled:'已取消'};
const ANALYSIS_STATUS = {routing:'项目识别中',joint_running:'联合分析中',waiting_runner:'等待执行器上线',queued:'等待分析',validating:'问题准入校验',developing:'DevCore 分析中',waiting_input:'待补充分析信息',delivering:'生成分析报告',delivered:'分析完成',waiting_approval:'等待人工确认',rejected:'准入驳回',failed:'分析失败',cancelled:'已取消'};
const TERMINAL = new Set(['delivered','failed','rejected','cancelled']);
STATUS.waiting_release='等待同项目合并发版';
STATUS.waiting_retry='连接恢复后自动重试';
ANALYSIS_STATUS.waiting_retry=STATUS.waiting_retry;
STATUS.waiting_analysis_sync=ANALYSIS_STATUS.waiting_analysis_sync='分析完成，待同步交付';
const escapeHtml = (value='') => String(value).replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const editorialIcon = name => `<svg class="ui-icon" aria-hidden="true"><use href="/static/editorial-icons.svg#${name}"></use></svg>`;
const fmt = value => value ? new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '—';
const fmtStepTime = value => value ? new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) : '—';
const fmtDuration = value => {const seconds=Number(value);if(!Number.isFinite(seconds)||seconds<0)return '—';if(seconds<60)return `${Math.max(1,Math.round(seconds))} 秒`;const minutes=Math.floor(seconds/60),hours=Math.floor(minutes/60),days=Math.floor(hours/24);if(days)return `${days} 天 ${hours%24} 小时`;if(hours)return `${hours} 小时 ${minutes%60} 分`;return `${minutes} 分`;};
const stepTiming = step => {if(!step.started_at)return '<div class="timeline-times pending-time"><span>尚未开启</span></div>';const finished=step.finished_at?`<span>完成 <time>${escapeHtml(fmtStepTime(step.finished_at))}</time></span>`:'<span class="running-time">正在运行</span>';return `<div class="timeline-times"><span>开启 <time>${escapeHtml(fmtStepTime(step.started_at))}</time></span>${finished}<strong>耗时 ${escapeHtml(fmtDuration(step.duration_seconds))}</strong></div>`};
const REVIEW_MODES = new Set(['sichuan_auto_review','product_manual_review']);
const DELIVERY_OPTION_LABELS = {merge_screenshot:'代码合并截图',license_request:'License 申请',auto_release:'自动发版'};
const visibleArtifacts = (artifacts,deliveryMode='',deliveryOptions=null) => {const selected=deliveryOptions===null?new Set(['merge_screenshot','license_request']):new Set(deliveryOptions||[]),allowed=new Set(['menu_link','analysis_report','verification_report']);if(selected.has('merge_screenshot'))allowed.add('merge_screenshot');if(selected.has('license_request'))allowed.add('license_request');if(selected.has('auto_release'))allowed.add('release_artifact');return (artifacts||[]).filter(a=>!['pull_request','report','merge_evidence','email_preview','delivery_manifest'].includes(a.kind)&&String(a.name||'').split(/[\\/]/).pop().toLowerCase()!=='delivery-validation-manifest.json'&&!(a.kind==='merge_screenshot'&&String(a.name||'').includes('凭证'))&&(!REVIEW_MODES.has(deliveryMode)||allowed.has(a.kind)))};
const isMergeScreenshot = artifact => artifact?.kind==='merge_screenshot';
const isMenuLink = artifact => artifact?.kind==='menu_link';
const isLicenseRequest = artifact => artifact?.kind==='license_request';
const isReleaseArtifact = artifact => artifact?.kind==='release_artifact';
const isAnalysisReport = artifact => artifact?.kind==='analysis_report';
const isGovernanceReport = artifact => ['verification_report','delivery_manifest'].includes(artifact?.kind);
const artifactPreviewUrl = artifact => artifact.external_url||`/api/artifacts/${artifact.id}?preview=true`;
const artifactOpenUrl = artifact => artifact.external_url||`/api/artifacts/${artifact.id}`;
const artifactRepository = artifact => {const name=String(artifact?.name||'');const marker=name.indexOf(' · PR #');return marker>0?name.slice(0,marker):'代码仓库'};
const artifactPrNumber = artifact => String(artifact?.name||'').match(/PR #(\d+)/)?.[1]||'';
const artifactLinks = (artifacts,deliveryMode='',deliveryOptions=null) => visibleArtifacts(artifacts,deliveryMode,deliveryOptions).map(a=>isMergeScreenshot(a)?`<span class="artifact-action-pair"><button type="button" class="artifact-link artifact-preview-trigger" data-preview-url="${escapeHtml(artifactPreviewUrl(a))}" data-download-url="${escapeHtml(artifactOpenUrl(a))}" data-preview-title="${escapeHtml(a.name)}" data-preview-repository="${escapeHtml(artifactRepository(a))}">预览 ${escapeHtml(artifactRepository(a))} ↗</button><a class="artifact-link artifact-download-link" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener" download title="下载 ${escapeHtml(a.name)}" onclick="event.stopPropagation()">下载 ↘</a></span>`:isMenuLink(a)?`<button type="button" class="artifact-link menu-link-copy" data-menu-link="${escapeHtml(a.name)}" title="复制 ${escapeHtml(a.name)}">新增视图 · ${escapeHtml(a.name)}</button>`:isLicenseRequest(a)?`<a class="artifact-link license-link" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener" title="${escapeHtml(a.name)}" onclick="event.stopPropagation()">License 申请 ↗</a>`:isReleaseArtifact(a)?`<a class="artifact-link release-link" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener" title="${escapeHtml(a.name)}" onclick="event.stopPropagation()">发版产物 ↗</a>`:isAnalysisReport(a)?`<a class="artifact-link analysis-report-link" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener" title="${escapeHtml(a.name)}" onclick="event.stopPropagation()">问题分析报告 ↗</a>`:`<a class="artifact-link" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" title="${escapeHtml(a.name)}" onclick="event.stopPropagation()">${escapeHtml(a.name)} ↗</a>`).join('');
const requestDuration = record => {if(!record.created_at&&!record.started_at)return Number.isFinite(Number(record.duration_seconds))?Number(record.duration_seconds):null;const start=new Date(record.created_at||record.started_at).getTime(),end=new Date(record.completed_at||Date.now()).getTime();return Number.isFinite(start)&&Number.isFinite(end)?Math.max(0,(end-start)/1000):null};
const requestPullRequests = record => {const states=Array.isArray(record.repository_states)?record.repository_states:[];const items=states.filter(item=>item.pr_url).map(item=>({repository:item.repository_short_name||item.name||'代码仓库',id:item.pr_id,url:item.pr_url}));if(!items.length&&record.pr_url)items.push({repository:'主仓库',id:record.pr_id,url:record.pr_url});return items};
const pendingPrLinks = record => record.status==='waiting_merge'?requestPullRequests(record).map(item=>`<a class="artifact-link pr-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(item.repository)} · PR #${escapeHtml(item.id)} ↗</a>`).join(''):'';
const deliveryLinks = record => (pendingPrLinks(record)+artifactLinks(record.artifacts,record.delivery_mode,record.delivery_options))||'<span class="muted">—</span>';
const delay = milliseconds => new Promise(resolve=>setTimeout(resolve,milliseconds));
const engineText = value => String(value??'').replace(/codex/gi,match=>match===match.toUpperCase()?'DEVCORE':'DevCore');
const tfsBase = record => record?.tfs_collection_url||record?.policy_snapshot?.tfs_collection_url||state.projects.find(p=>p.id===record?.project_id)?.tfs_collection_url||state.projects[0]?.tfs_collection_url||'';
const tfsUrl = (record,workItemId=record?.work_item_id) => {const base=tfsBase(record);return base&&workItemId?`${base.replace(/\/$/,'')}/_workitems/edit/${encodeURIComponent(workItemId)}`:''};
const tfsLink = (record,label=`#${record?.work_item_id||''}`,className='tfs-work-link') => {const url=tfsUrl(record);return url?`<a class="${className}" href="${escapeHtml(url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(label)} ↗</a>`:`<span>${escapeHtml(label)}</span>`};
const jointBadge = record => Number(record?.joint_project_count)>1?`<span class="joint-badge">联合 ${Number(record.joint_project_index)||1}/${Number(record.joint_project_count)}</span>`:'';
const isAnalysisTask = record => record?.task_type==='analysis';
const taskTypeLabel = record => isAnalysisTask(record)?'问题分析':'自主研发';
const taskStatus = (record,status=runVisualStatus(record)) => (isAnalysisTask(record)?ANALYSIS_STATUS:STATUS)[status]||status;
const taskModeLabel = record => isAnalysisTask(record)?'问题分析':(MODE[record?.delivery_mode]?.[0]||record?.delivery_mode||'自主研发');

function bindArtifactPreviews(root=document){root.querySelectorAll('.artifact-preview-trigger').forEach(button=>{button.onclick=event=>{event.stopPropagation();openArtifactPreview(button.dataset.previewUrl,button.dataset.downloadUrl,button.dataset.previewTitle,button.dataset.previewRepository)}})}
function bindMenuLinks(root=document){root.querySelectorAll('.menu-link-copy').forEach(button=>{button.onclick=async event=>{event.stopPropagation();try{await navigator.clipboard.writeText(button.dataset.menuLink);toast('菜单链接已复制')}catch(_){toast(`菜单链接：${button.dataset.menuLink}`)}}})}
function openArtifactPreview(url,downloadUrl,title,repository){const overlay=document.querySelector('#artifact-preview'),image=document.querySelector('#artifact-preview-image');document.querySelector('#artifact-preview-title').textContent=title||'PR 合并截图';document.querySelector('#artifact-preview-repository').textContent=repository||'代码仓库';document.querySelector('#artifact-preview-download').href=downloadUrl||url;image.alt=title||'PR 合并截图';image.classList.remove('loaded');image.onload=()=>image.classList.add('loaded');image.src=url;overlay.hidden=false;document.body.classList.add('preview-open');document.querySelector('#close-artifact-preview').focus()}
function closeArtifactPreview(){const overlay=document.querySelector('#artifact-preview'),image=document.querySelector('#artifact-preview-image');if(overlay.hidden)return;overlay.hidden=true;image.onload=null;image.classList.remove('loaded');image.removeAttribute('src');document.body.classList.remove('preview-open')}

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
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.view===(name==='project-detail'?'projects':name)));
  document.querySelector('#project-breadcrumb').hidden=name!=='project-detail';
  document.body.classList.toggle('ledger-lock',name==='requests'||name==='experiences');
  const titles={dashboard:'任务总览',requests:'交付记录',projects:'自主项目',analytics:'统计看板',users:'账号管理',experiences:'项目经验',models:'研发设置','project-detail':'项目详情'};
  document.querySelector('#view-title').textContent=titles[name];
  document.title=`${titles[name]} · AutoDev`;
  document.body.dataset.view=name;
  closeSidebarTasks(false);
  if(name==='experiences')window.ProjectLearning?.open();
  if(name==='models')window.ModelSettings?.open();
}

async function refresh(){
  const userRequest=USER.role==='admin'?api('/api/users'):Promise.resolve({users:[]});
  const analyticsRequest=USER.role==='admin'?api('/api/admin/analytics'):Promise.resolve(null);
  const projectRequest=api('/api/projects');
  const recipientRequest=api('/api/notification-recipients');
  const recordsRequest=api(deliveryRecordsUrl());
  const [dashboard,projects,me,users,analytics,recipients,records]=await Promise.all([api('/api/dashboard'),projectRequest,api('/api/me'),userRequest,analyticsRequest,recipientRequest,recordsRequest]);
  Object.assign(USER,me.user);state.dashboard=dashboard;state.projects=projects.projects;state.users=users.users||[];state.notificationUsers=recipients.users||[];state.analytics=analytics;applyDeliveryRecords(records);renderDashboard();renderProjects();renderProjectGuide();renderAnalytics();renderUsers();renderRequestEmailOptions(false);renderRecordFilterOptions();renderAllTable();
  window.ProjectLearning?.syncProjects();
  if(state.selectedRequest&&!state.selectedTerminal) refreshDetail(state.selectedRequest,true);
}
const ORB_RUNNING_STATUSES=new Set(['routing','validating','developing','submitting','building','releasing','capturing','delivering']);
function renderSidebarOrbActivity(activeRuns){
  const panel=document.querySelector('#orb-activity-console'),task=document.querySelector('#orb-current-task'),output=document.querySelector('#orb-current-output'),counter=document.querySelector('#orb-task-index');if(!panel||!task||!output||!counter)return;
  const runs=(activeRuns||[]).filter(Boolean),experience=state.orbExperience,signature=runs.map(activeRunKey).join('|');
  const expanded=panel.getAttribute('aria-expanded')==='true';
  if(signature!==experience.activitySignature){experience.activitySignature=signature;experience.activityIndex=0}else if(runs.length>1&&!expanded&&!panel.matches(':hover,:focus-within'))experience.activityIndex=(experience.activityIndex+1)%runs.length;
  if(expanded)renderSidebarTaskList();
  panel.dataset.active=String(runs.length>0);
  if(!runs.length){counter.textContent='暂无任务';task.textContent='当前输出';output.textContent='等待新的研发或问题分析任务';return}
  const index=Math.min(experience.activityIndex,runs.length-1),run=runs[index],workItem=run.work_item_id?`TFS #${run.work_item_id}`:'运行任务',project=String(run.project_name||'项目识别中').trim();
  counter.textContent=`任务 ${index+1}/${runs.length}`;task.textContent=`${workItem} · ${project}`;output.textContent=engineText(String(run.current_activity||taskStatus(run,runVisualStatus(run))||'等待执行器反馈').trim());
  panel.classList.remove('is-switching');void panel.offsetWidth;panel.classList.add('is-switching');if(experience.activityTimer)clearTimeout(experience.activityTimer);experience.activityTimer=setTimeout(()=>panel.classList.remove('is-switching'),380);
}
function sidebarTaskRuns(){return state.dashboard.active.length?state.dashboard.active:state.dashboard.recent.slice(0,12)}
function renderSidebarTaskList(){
  const list=document.querySelector('#sidebar-task-list');if(!list)return;
  const runs=sidebarTaskRuns(),existing=new Map([...list.querySelectorAll('[data-task-key]')].map(button=>[button.dataset.taskKey,button]));
  document.querySelector('#sidebar-task-title').textContent=state.dashboard.active.length?'进行中的任务':'最近任务';
  document.querySelector('#sidebar-task-count').textContent=`${runs.length} 项`;
  list.querySelector('.sidebar-task-empty')?.remove();
  const keys=new Set();
  runs.forEach((run,index)=>{
    const key=activeRunKey(run),visualStatus=runVisualStatus(run);keys.add(key);
    let button=existing.get(key);
    if(!button){button=document.createElement('button');button.type='button';button.className='sidebar-task-option';button.dataset.taskKey=key;button.innerHTML='<span class="sidebar-task-meta"><b></b><small></small></span><strong></strong><span class="sidebar-task-output"></span><span class="sidebar-task-bottom"><i></i><span>打开任务 ↗</span></span>';}
    button.querySelector('.sidebar-task-meta b').textContent=run.project_name||'项目识别中';
    button.querySelector('.sidebar-task-meta small').textContent=run.work_item_id?`#${run.work_item_id}`:'待识别';
    button.querySelector('strong').textContent=run.title||'正在读取 TFS 需求并识别项目…';
    button.querySelector('.sidebar-task-output').textContent=engineText(run.current_activity||taskStatus(run,visualStatus)||'等待执行器反馈');
    button.querySelector('.sidebar-task-bottom i').textContent=taskStatus(run,visualStatus);
    button.dataset.status=visualStatus;
    button.setAttribute('aria-label',`打开任务 ${run.work_item_id||''} · ${run.project_name||'项目识别中'}`);
    button.onclick=()=>{closeSidebarTasks();const result=run.record_type==='intake'||run.intake_id?openRoutingDetail(run.intake_id||run.id,run.work_item_id,visualStatus,run.task_type):openDetail(run.id);Promise.resolve(result).catch(error=>toast(error.message));};
    const position=list.children[index];if(position!==button)list.insertBefore(button,position||null);
  });
  existing.forEach((button,key)=>{if(!keys.has(key)){const focused=button===document.activeElement;button.remove();if(focused)document.querySelector('#close-sidebar-tasks').focus();}});
  if(!runs.length){const empty=document.createElement('p');empty.className='sidebar-task-empty';empty.textContent='暂无任务。发起研发后，可在这里随时打开任务。';list.append(empty);}
}
function closeSidebarTasks(restoreFocus=true){
  const panel=document.querySelector('#sidebar-task-popover'),trigger=document.querySelector('#orb-activity-console');if(!panel||panel.hidden)return;
  panel.hidden=true;trigger?.setAttribute('aria-expanded','false');if(restoreFocus)trigger?.focus();
}
function toggleSidebarTasks(){
  const panel=document.querySelector('#sidebar-task-popover'),trigger=document.querySelector('#orb-activity-console');if(!panel)return;
  if(!panel.hidden){closeSidebarTasks();return}
  renderSidebarTaskList();panel.hidden=false;trigger.setAttribute('aria-expanded','true');
  (panel.querySelector('.sidebar-task-option')||document.querySelector('#close-sidebar-tasks')).focus();
}
function syncSidebarOrbState(activeRuns,runners){
  const character=window.sidebarCharacter;if(!character)return;
  const runs=activeRuns||[],statuses=runs.map(runVisualStatus);
  let characterState='curious';
  if(statuses.some(status=>['failed','rejected'].includes(status)))characterState='error';
  else if(statuses.includes('waiting_approval'))characterState='blocked';
  else if(statuses.some(status=>['waiting_input','waiting_merge','waiting_runner'].includes(status)))characterState='listening';
  else if(statuses.includes('routing'))characterState='reading';
  else if(statuses.some(status=>['validating','queued'].includes(status)))characterState='thinking';
  else if(statuses.some(status=>['submitting','capturing','delivering','releasing'].includes(status)))characterState='delivering';
  else if(statuses.includes('building'))characterState='building';
  else if(runs.length)characterState='working';
  else if(!(runners||[]).some(runner=>runner.online))characterState='sleeping';
  character.setRunning(statuses.some(status=>ORB_RUNNING_STATUSES.has(status)));
  character.setState(characterState);
  renderSidebarOrbActivity(runs);
}
function orbMotionReduced(){return window.matchMedia('(prefers-reduced-motion: reduce)').matches}
function orbSignalLayer(){let layer=document.querySelector('#orb-signal-layer');if(!layer){layer=document.createElement('div');layer.id='orb-signal-layer';layer.className='orb-signal-layer';layer.setAttribute('aria-hidden','true');document.body.appendChild(layer)}return layer}
function launchOrbSignal(target,tone='progress'){
  const source=document.querySelector('#sidebar-orb-character');if(orbMotionReduced()||!source||!target?.isConnected)return;
  const from=source.getBoundingClientRect(),to=target.getBoundingClientRect();if(!from.width||!to.width)return;
  const start={x:from.left+from.width/2,y:from.top+from.height/2},end={x:to.left+Math.min(to.width*.22,88),y:to.top+Math.min(to.height*.42,72)},dx=end.x-start.x,dy=end.y-start.y,angle=Math.atan2(dy,dx)*180/Math.PI,bend=Math.min(74,Math.max(24,Math.abs(dx)*.08));
  const signal=document.createElement('i');signal.className='orb-flight-signal';signal.dataset.tone=tone;signal.style.left=`${start.x}px`;signal.style.top=`${start.y}px`;orbSignalLayer().appendChild(signal);
  const transform=(x,y,scale)=>`translate3d(${x}px,${y}px,0) rotate(${angle}deg) scale(${scale})`;
  const animation=signal.animate([{transform:transform(0,0,.35),opacity:0},{transform:transform(dx*.12,dy*.05,.9),opacity:1,offset:.14},{transform:transform(dx*.58,dy*.42-bend,1),opacity:1,offset:.58},{transform:transform(dx,dy,1.35),opacity:0}],{duration:tone==='success'?920:760,easing:'cubic-bezier(.22,.82,.28,1)',fill:'forwards'});
  animation.onfinish=()=>{signal.remove();target.classList.remove('orb-impact');void target.offsetWidth;target.classList.add('orb-impact');setTimeout(()=>target.classList.remove('orb-impact'),900)};
}
function deliveryTokenLabels(record){const labels=[];const aliases={sql:'SQL',release_artifact:'RELEASE',merge_screenshot:'PR',analysis_report:'REPORT',license_request:'LICENSE',menu_link:'VIEW'};(record?.artifacts||[]).forEach(item=>{let label=aliases[item.kind];if(item.kind==='package')label=String(item.name||'').split('.').pop().toUpperCase()||'BUILD';if(label&&!labels.includes(label))labels.push(label)});return (labels.length?labels:['DONE']).slice(0,3)}
function releaseDeliveryTokens(target,record){if(orbMotionReduced()||!target?.isConnected)return;const rect=target.getBoundingClientRect(),layer=orbSignalLayer();deliveryTokenLabels(record).forEach((label,index)=>{const token=document.createElement('b');token.className='orb-delivery-token';token.textContent=label;token.style.left=`${rect.left+Math.min(rect.width*.24,106)+index*9}px`;token.style.top=`${rect.top+Math.min(rect.height*.48,42)}px`;layer.appendChild(token);const drift=(index-1)*34;const animation=token.animate([{transform:'translate3d(0,10px,0) scale(.6)',opacity:0},{transform:`translate3d(${drift*.35}px,-28px,0) scale(1)`,opacity:1,offset:.32},{transform:`translate3d(${drift}px,-7px,0) scale(.92)`,opacity:1,offset:.76},{transform:`translate3d(${drift}px,4px,0) scale(.82)`,opacity:0}],{duration:1250+index*90,delay:index*90,easing:'cubic-bezier(.2,.75,.24,1)',fill:'forwards'});animation.onfinish=()=>token.remove()})}
function collectOrbTransitions(runs){const experience=state.orbExperience,current=new Map();runs.forEach(run=>{const key=activeRunKey(run),status=runVisualStatus(run);current.set(key,{status,record:run});if(!experience.initialized)return;const previous=experience.runs.get(key);const target=document.querySelector(`[data-run-key="${CSS.escape(key)}"]`);if(!previous)experience.pending.push({type:'dispatch',target,record:run});else if(previous.status!==status)experience.pending.push({type:status==='waiting_approval'?'blocked':'progress',target,record:run})});if(experience.initialized)experience.runs.forEach((previous,key)=>{if(current.has(key))return;const record=(state.dashboard.recent||[]).find(item=>activeRunKey(item)===key),status=record?runVisualStatus(record):'';if(status==='delivered')experience.pending.push({type:'success',key,record});else if(['failed','rejected'].includes(status))experience.pending.push({type:'blocked',key,record})});experience.runs=current;experience.initialized=true}
function flushOrbTransitions(){const experience=state.orbExperience,character=window.sidebarCharacter;if(!character||!experience.pending.length)return;const priority={success:4,blocked:3,dispatch:2,progress:1},event=experience.pending.sort((a,b)=>priority[b.type]-priority[a.type])[0];experience.pending=[];requestAnimationFrame(()=>{const target=event.target?.isConnected?event.target:document.querySelector(`#recent-table [data-run-key="${CSS.escape(event.key||'')}"]`);if(event.type==='success'){character.celebrateOnce(target);launchOrbSignal(target,'success');releaseDeliveryTokens(target,event.record)}else if(event.type==='blocked'){character.alertOnce(target);launchOrbSignal(target,'blocked')}else if(event.type==='dispatch'){character.dispatchOnce(target);launchOrbSignal(target,'dispatch')}else{character.progressOnce(target);launchOrbSignal(target,'progress')}})}
function renderDashboard(){
  const c=state.dashboard.counts;
  const delivered=c.delivered||0, waiting=c.waiting_merge||0, failed=c.failed||0;
  const active=Object.entries(c).filter(([k])=>!['delivered','failed','rejected','cancelled','waiting_input'].includes(k)).reduce((a,[,v])=>a+v,0);
  const stats=state.dashboard.stats||{};
  const metricData=USER.role==='admin'?[['当日任务',stats.today_total||0,'accent'],['任务总量',stats.total||0,''],['成功交付',stats.success||0,''],['失败 / 驳回',stats.failed||0,'danger'],['运行中',stats.running||0,'accent'],['待补充',stats.waiting_input||0,'attention'],['等待合并',stats.waiting_merge||0,'warn']]:[['运行中',active,'accent'],['等待合并',waiting,'warn'],['已交付',delivered,''],['需要关注',failed+(c.waiting_approval||0)+(c.waiting_input||0),'attention']];
  const metrics=document.querySelector('#metrics');metrics.classList.toggle('admin-metrics',USER.role==='admin');metrics.innerHTML=metricData.map(([l,v,k])=>`<div class="metric ${k} ${l==='运行中'&&Number(v)>0?'live':''}"><span>${l}</span><div class="metric-value"><b>${String(v).padStart(2,'0')}</b><small>个</small></div></div>`).join('');
  renderDevCoreQuota();
  const activeEl=document.querySelector('#active-runs'),activePipeline=document.querySelector('#active-pipeline');
  activePipeline.hidden=state.dashboard.active.length===0;
  renderActiveRuns(activeEl,state.dashboard.active);
  const capacity=state.dashboard.capacity||{limit:5,active:0,queued:0};
  const capacityStatus=document.querySelector('#capacity-status');
  if(capacityStatus)capacityStatus.innerHTML=`<b>${Number(capacity.active)||0} / ${Number(capacity.limit)||5}</b><span>并发槽位${capacity.queued?` · ${Number(capacity.queued)} 个排队`:''}</span>`;
  document.body.classList.toggle('has-active-runs',state.dashboard.active.length>0);
  document.querySelector('#active-summary').textContent=`共 ${state.dashboard.active.length} 个任务${capacity.queued?`，其中 ${Number(capacity.queued)} 个排队`:''}`;
  const recent=state.dashboard.recent.slice(0,12);
  document.querySelector('#recent-summary').textContent=`共 ${recent.length} 条近期任务，按更新时间排序`;
  document.querySelector('#recent-table').innerHTML=recent.map(recentRow).join('')||'<tr><td colspan="9" class="muted">暂无记录</td></tr>';
  bindRows();
  const runners=state.dashboard.runners||[],online=runners.filter(r=>r.online),status=document.querySelector('#runner-status');
  status.classList.toggle('offline',online.length===0);status.querySelector('span').textContent=online.length?'执行器在线':'执行器离线';
  syncSidebarOrbState(state.dashboard.active,runners);
  flushOrbTransitions();
}
function renderDevCoreQuota(){const el=document.querySelector('#devcore-quota');if(!el)return;const runners=state.dashboard.runners||[];const runner=runners.find(r=>r.online&&r.devcore_usage?.available)||runners.find(r=>r.devcore_usage?.available);if(!runner){el.innerHTML='<div><p class="eyebrow">研发容量 / DEVCORE CAPACITY</p><h2>套餐信息暂不可用</h2></div><span class="muted">执行器上线后自动同步</span>';return}const usage=runner.devcore_usage,primary=usage.primary||{},remaining=primary.remaining_percent,used=primary.used_percent??0,credits=usage.credits||{},plan=String(usage.plan_type||'unknown').toUpperCase();const reset=primary.resets_at?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(primary.resets_at*1000)):'—';const balance=credits.unlimited?'无限':(credits.balance??'0');el.innerHTML=`<div class="quota-copy"><p class="eyebrow">研发容量 / DEVCORE CAPACITY / ${escapeHtml(runner.runner_id)}</p><h2>${escapeHtml(plan)} 套餐</h2><span>周期重置 ${escapeHtml(reset)} · 额外余额 ${escapeHtml(balance)}</span></div><div class="quota-meter"><div class="quota-number"><b>${remaining??'—'}%</b><span>套餐剩余</span></div><div class="quota-track"><i style="width:${Math.max(0,Math.min(100,100-used))}%"></i></div><small>数据更新时间 ${fmt(usage.updated_at)}</small></div>`}
function activeRunKey(r){return `${r.record_type==='intake'||r.intake_id?'intake':'request'}:${r.intake_id||r.id}`}
function runVisualStatus(r){return r.display_status||r.status}
function createRunCard(){
  const card=document.createElement('article');
  card.className='run-card clickable-row';card.tabIndex=0;
  card.innerHTML=`<div class="run-top"><span class="run-project-icon">${editorialIcon('code')}</span><h3 class="project"></h3><span class="work-id"></span>${editorialIcon('chevron-right')}</div><dl class="run-facts"><div><dt>${editorialIcon('clipboard')}需求名称</dt><dd class="run-title"></dd></div><div><dt>${editorialIcon('layers')}当前阶段</dt><dd><span class="status-tag"></span></dd></div><div class="live-activity"><dt>${editorialIcon('message')}最新输出</dt><dd><b></b></dd></div></dl><div class="run-mode"></div>`;
  card.addEventListener('keydown',event=>{if(event.target===card&&(event.key==='Enter'||event.key===' ')){event.preventDefault();card.click()}});
  card.addEventListener('pointerenter',()=>window.sidebarCharacter?.lookAt(card,0));
  card.addEventListener('pointerleave',()=>window.sidebarCharacter?.clearLook());
  return card;
}
function updateRunCard(card,r){
  const intake=r.record_type==='intake'||r.intake_id,key=activeRunKey(r),visualStatus=runVisualStatus(r),activity=engineText(r.current_activity||taskStatus(r,visualStatus)||'等待执行器反馈');
  card.dataset.runKey=key;card.classList.toggle('routing-card',intake);card.classList.toggle('analysis-run-card',isAnalysisTask(r));card.classList.toggle('waiting-runner-card',visualStatus==='waiting_runner');card.classList.toggle('joint-run-card',Number(r.joint_project_count)>1);card.classList.remove('run-leaving');card.dataset.status=visualStatus;
  delete card.dataset.id;delete card.dataset.intakeId;delete card.dataset.workItemId;
  if(intake){card.dataset.intakeId=r.intake_id||r.id;card.dataset.workItemId=r.work_item_id}else{card.dataset.id=r.id}
  card.querySelector('.work-id').innerHTML=tfsLink(r,`#${r.work_item_id}`)+jointBadge(r);
  const status=card.querySelector('.status-tag'),statusText=taskStatus(r,visualStatus);
  if(status.textContent!==statusText){status.textContent=statusText;status.classList.remove('status-updated');void status.offsetWidth;status.classList.add('status-updated')}
  const title=card.querySelector('.run-title');title.textContent=r.title||'正在读取需求…';title.title=title.textContent;
  const project=card.querySelector('.project');project.textContent=r.project_name||'项目识别中';project.title=`${project.textContent} · ${r.requester_name||''}`;
  const output=card.querySelector('.live-activity b');
  if(output.textContent!==activity){output.textContent=activity;output.classList.remove('activity-updated');void output.offsetWidth;output.classList.add('activity-updated')}
  output.title=activity;
  card.querySelector('.run-mode').textContent=taskModeLabel(r);
  card.onclick=()=>intake?openRoutingDetail(r.intake_id||r.id,r.work_item_id,visualStatus,r.task_type):openDetail(r.id);
}
function renderActiveRuns(container,runs){const existing=new Map([...container.children].map(card=>[card.dataset.runKey,card])),desired=new Set();runs.forEach((run,index)=>{const key=activeRunKey(run);desired.add(key);let card=existing.get(key);if(!card){card=createRunCard()}updateRunCard(card,run);const position=container.children[index];if(position!==card)container.insertBefore(card,position||null)});collectOrbTransitions(runs);existing.forEach((card,key)=>{if(desired.has(key))return;card.classList.add('run-leaving');setTimeout(()=>{if(!state.dashboard.active.some(run=>activeRunKey(run)===key))card.remove()},280)})}
function recentRow(r,index=0){
  const intake=r.record_type==='intake'||r.intake_id,visualStatus=runVisualStatus(r),title=r.title||'等待读取',activity=engineText(r.current_activity||'—');
  const number=String(index+1).padStart(2,'0'),url=tfsUrl(r),indexCell=url?`<a href="${escapeHtml(url)}" target="_blank" rel="noopener" title="打开 TFS #${r.work_item_id}" onclick="event.stopPropagation()">${number}</a>`:number;
  return `<tr class="clickable-row" data-run-key="${escapeHtml(activeRunKey(r))}" ${intake?`data-intake-id="${r.intake_id||r.id}" data-work-item-id="${r.work_item_id}" data-task-type="${escapeHtml(r.task_type||'development')}"`:`data-id="${r.id}"`}><td class="row-index">${indexCell}</td><td class="demand-cell" title="TFS #${r.work_item_id} · ${escapeHtml(title)}"><span>${escapeHtml(title)}</span>${jointBadge(r)}</td><td title="${escapeHtml(r.project_name)}">${escapeHtml(r.project_name)}</td><td title="${escapeHtml(taskModeLabel(r))}">${escapeHtml(taskModeLabel(r))}</td><td><span class="status-dot" data-status="${visualStatus}">${escapeHtml(taskStatus(r,visualStatus))}</span></td><td><span class="activity-cell" title="${escapeHtml(activity)}">${escapeHtml(activity)}</span></td><td class="duration-cell">${fmtDuration(requestDuration(r))}</td><td>${fmt(r.updated_at)}</td><td><button class="recent-open" type="button" aria-label="查看任务 ${r.work_item_id} 详情" title="查看任务详情">${editorialIcon('more')}</button></td></tr>`;
}
function renderAllTable(){
  const data=state.records.items||[];
  const el=document.querySelector('#all-table');if(!el)return;
  el.innerHTML=data.map((r,index)=>{const intake=r.record_type==='intake'||r.intake_id,visualStatus=runVisualStatus(r);return `<tr class="clickable-row" ${intake?`data-intake-id="${r.intake_id||r.id}" data-work-item-id="${r.work_item_id}" data-task-type="${escapeHtml(r.task_type||'development')}"`:`data-id="${r.id}"`}><td>${String((state.records.page-1)*state.records.pageSize+index+1).padStart(2,'0')}</td><td class="demand-cell" title="${escapeHtml(r.title||'等待读取')}"><span>${escapeHtml(r.title||'等待读取')}</span>${jointBadge(r)}</td><td title="${escapeHtml(r.project_name)}">${escapeHtml(r.project_name)}</td><td>${tfsLink(r,`#${r.work_item_id}`)}</td><td>${escapeHtml(taskTypeLabel(r))}</td><td><span class="status-dot" data-status="${visualStatus}">${escapeHtml(taskStatus(r,visualStatus))}</span></td><td>${escapeHtml(r.requester_name)}</td><td>${fmt(r.created_at)}</td><td>${fmt(r.completed_at)}</td><td class="duration-cell">${fmtDuration(requestDuration(r))}</td><td><div class="artifact-links">${deliveryLinks(r)}</div></td><td><button class="recent-open" type="button" aria-label="查看任务 ${r.work_item_id} 详情">${editorialIcon('more')}</button></td></tr>`}).join('')||'<tr><td colspan="12" class="muted">没有符合条件的记录</td></tr>';bindRows();bindArtifactPreviews(el);bindMenuLinks(el);
  renderRecordPagination();
}

function deliveryRecordsUrl(){const query=new URLSearchParams({page:String(state.records.page||1),page_size:String(state.records.pageSize||10)});Object.entries(state.records.filters||{}).forEach(([key,value])=>{if(String(value??'').trim())query.set(key,String(value).trim())});return `/api/delivery-records?${query}`}
function applyDeliveryRecords(result){const pagination=result?.pagination||{};state.records.items=result?.items||[];state.records.page=Number(pagination.page)||1;state.records.pageSize=Number(pagination.page_size)||10;state.records.total=Number(pagination.total)||0;state.records.totalPages=Number(pagination.total_pages)||1}
async function loadDeliveryRecords(page=state.records.page){state.records.page=Math.max(1,Number(page)||1);const result=await api(deliveryRecordsUrl());applyDeliveryRecords(result);renderAllTable()}
const RECORD_FILTER_OPTIONS={
  task_type:[['','全部类型'],['development','自主研发'],['analysis','问题分析']],
  status:[['','全部状态'],['waiting_merge','等待 PR 合并'],['waiting_input','待补充信息'],['developing','研发中'],['delivered','已交付'],['failed','执行失败'],['rejected','准入驳回'],['cancelled','已取消']]
};
function closeLedgerPopovers(except=null){document.querySelectorAll('.ledger-select.open,.ledger-date.open').forEach(control=>{if(control===except)return;control.classList.remove('open');const panel=control.querySelector('.ledger-select-menu,.ledger-calendar'),button=control.querySelector(':scope > button');if(panel)panel.hidden=true;if(button)button.setAttribute('aria-expanded','false')})}
function setLedgerSelectValue(control,value){const input=control.querySelector('input'),button=control.querySelector(':scope > button'),option=control.querySelector(`.ledger-select-menu [data-value="${CSS.escape(String(value??''))}"]`),fallback=control.querySelector('.ledger-select-menu [data-value=""]');input.value=String(value??'');button.querySelector('b').textContent=(option||fallback)?.textContent||'全部';control.querySelectorAll('[role="option"]').forEach(item=>item.setAttribute('aria-selected',String(item===option)));}
function setLedgerSelectOptions(control,options,value=''){if(!control)return;const signature=JSON.stringify(options);if(control.dataset.signature!==signature){control.dataset.signature=signature;const menu=control.querySelector('.ledger-select-menu');menu.innerHTML=options.map(([optionValue,label])=>`<button type="button" role="option" data-value="${escapeHtml(optionValue)}">${escapeHtml(label)}</button>`).join('');menu.querySelectorAll('[data-value]').forEach(option=>option.onclick=event=>{event.stopPropagation();setLedgerSelectValue(control,option.dataset.value);closeLedgerPopovers()})}setLedgerSelectValue(control,value);if(control.dataset.ready)return;control.dataset.ready='1';const button=control.querySelector(':scope > button'),menu=control.querySelector('.ledger-select-menu');button.onclick=event=>{event.stopPropagation();const opening=!control.classList.contains('open');closeLedgerPopovers(control);control.classList.toggle('open',opening);menu.hidden=!opening;button.setAttribute('aria-expanded',String(opening))};}
function localDate(value){const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value||''));return match?new Date(Number(match[1]),Number(match[2])-1,Number(match[3])):null}
function dateIso(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`}
function ledgerDateLabel(value){const date=localDate(value);return date?`${date.getFullYear()}年${date.getMonth()+1}月${date.getDate()}日`:'选择日期'}
function setLedgerDateValue(control,value){const input=control.querySelector('input'),button=control.querySelector(':scope > button');input.value=value||'';button.querySelector('b').textContent=ledgerDateLabel(input.value);const selected=localDate(input.value);if(selected)control._viewDate=new Date(selected.getFullYear(),selected.getMonth(),1);if(control.classList.contains('open'))renderLedgerCalendar(control)}
function positionLedgerCalendar(control){const panel=control.querySelector('.ledger-calendar'),button=control.querySelector(':scope > button');control.classList.remove('open-up');const availableBelow=window.innerHeight-button.getBoundingClientRect().bottom-12;if(panel.getBoundingClientRect().height>availableBelow&&button.getBoundingClientRect().top>panel.getBoundingClientRect().height+12)control.classList.add('open-up')}
function renderLedgerCalendar(control){const panel=control.querySelector('.ledger-calendar'),selected=control.querySelector('input').value,today=new Date(),view=control._viewDate||new Date(today.getFullYear(),today.getMonth(),1),first=new Date(view.getFullYear(),view.getMonth(),1),offset=(first.getDay()+6)%7,start=new Date(view.getFullYear(),view.getMonth(),1-offset),days=[];for(let index=0;index<42;index+=1){const day=new Date(start.getFullYear(),start.getMonth(),start.getDate()+index),value=dateIso(day);days.push(`<button type="button" class="${day.getMonth()!==view.getMonth()?'outside ':''}${value===selected?'selected ':''}${value===dateIso(today)?'today':''}" data-date="${value}" aria-label="${day.getFullYear()}年${day.getMonth()+1}月${day.getDate()}日"><span>${day.getDate()}</span></button>`)}panel.innerHTML=`<div class="ledger-calendar-head"><button type="button" data-month="-1" aria-label="上个月">←</button><b>${view.getFullYear()} / ${String(view.getMonth()+1).padStart(2,'0')}</b><button type="button" data-month="1" aria-label="下个月">→</button></div><div class="ledger-calendar-week"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div><div class="ledger-calendar-days">${days.join('')}</div><div class="ledger-calendar-foot"><button type="button" data-today>今天</button><button type="button" data-clear>清空</button></div>`;panel.querySelectorAll('[data-month]').forEach(button=>button.onclick=event=>{event.stopPropagation();control._viewDate=new Date(view.getFullYear(),view.getMonth()+Number(button.dataset.month),1);renderLedgerCalendar(control)});panel.querySelectorAll('[data-date]').forEach(button=>button.onclick=event=>{event.stopPropagation();setLedgerDateValue(control,button.dataset.date);closeLedgerPopovers()});panel.querySelector('[data-today]').onclick=event=>{event.stopPropagation();setLedgerDateValue(control,dateIso(new Date()));closeLedgerPopovers()};panel.querySelector('[data-clear]').onclick=event=>{event.stopPropagation();setLedgerDateValue(control,'');closeLedgerPopovers()};}
function initLedgerDate(control,value=''){if(!control)return;setLedgerDateValue(control,value);if(control.dataset.ready)return;control.dataset.ready='1';const button=control.querySelector(':scope > button'),panel=control.querySelector('.ledger-calendar');button.onclick=event=>{event.stopPropagation();const opening=!control.classList.contains('open');closeLedgerPopovers(control);control.classList.toggle('open',opening);panel.hidden=!opening;button.setAttribute('aria-expanded',String(opening));if(opening){renderLedgerCalendar(control);positionLedgerCalendar(control)}};panel.onclick=event=>event.stopPropagation();}
function renderRecordFilterOptions(){setLedgerSelectOptions(document.querySelector('[data-filter-select="task_type"]'),RECORD_FILTER_OPTIONS.task_type,state.records.filters.task_type);setLedgerSelectOptions(document.querySelector('[data-filter-select="status"]'),RECORD_FILTER_OPTIONS.status,state.records.filters.status);setLedgerSelectOptions(document.querySelector('#record-project-filter'),[['','全部项目'],...state.projects.map(project=>[project.project_key,project.name])],state.records.filters.project_key);setLedgerSelectOptions(document.querySelector('#record-requester-filter'),[['','全部提交人'],...state.users.map(user=>[String(user.id),user.display_name])],state.records.filters.requester_id);initLedgerDate(document.querySelector('[data-filter-date="date_from"]'),state.records.filters.date_from);initLedgerDate(document.querySelector('[data-filter-date="date_to"]'),state.records.filters.date_to)}
function resetRecordFilterControls(){document.querySelectorAll('[data-filter-select]').forEach(control=>setLedgerSelectValue(control,''));document.querySelectorAll('[data-filter-date]').forEach(control=>setLedgerDateValue(control,''));closeLedgerPopovers()}
function renderRecordPagination(){const pagination=document.querySelector('#record-pagination'),total=document.querySelector('#record-total');if(!pagination)return;const page=state.records.page,totalPages=state.records.totalPages;if(total)total.textContent=`共 ${state.records.total} 条`;const pages=[];for(let value=Math.max(1,page-2);value<=Math.min(totalPages,page+2);value+=1)pages.push(value);pagination.innerHTML=`<span>第 ${page} / ${totalPages} 页</span><div><button type="button" data-page="${page-1}" ${page<=1?'disabled':''}>← 上一页</button>${pages.map(value=>`<button type="button" data-page="${value}" class="${value===page?'active':''}">${value}</button>`).join('')}<button type="button" data-page="${page+1}" ${page>=totalPages?'disabled':''}>下一页 →</button></div>`;pagination.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>loadDeliveryRecords(button.dataset.page).catch(error=>toast(error.message)))}
function bindRows(){document.querySelectorAll('.clickable-row').forEach(el=>el.onclick=()=>el.dataset.intakeId?openRoutingDetail(el.dataset.intakeId,el.dataset.workItemId,'routing',el.dataset.taskType):openDetail(el.dataset.id))}

function safeRepositoryUrl(value){try{const url=new URL(String(value));return ['http:','https:'].includes(url.protocol)?url.href:'#'}catch{return '#'}}
function projectSymbol(project){const name=String(project.name||'');return /APP/i.test(name)?'phone':/巡航/.test(name)?'building':/省调/.test(name)?'layers':'network'}
function projectPolicies(project){const quality=project.quality_profile||{},artifact=project.artifact_policy||{};return [['历史复用',quality.history_reuse],['验收账本',quality.require_acceptance_ledger],['SQL / DM7',Boolean(quality.sql)],['自动验证',Boolean(quality.visual?.required_for_frontend)],['交付白名单',Boolean(artifact.allowed_user_facing_kinds?.length)]].filter(([,enabled])=>enabled).map(([label])=>label)}
function renderProjects(){
  const el=document.querySelector('#project-grid');if(!el)return;
  el.innerHTML=state.projects.length?state.projects.map((p,index)=>{const aliases=(p.routing_title_keywords||[]).map(value=>String(value).trim()).filter(Boolean),repositories=Object.entries(p.repository_tfs_paths||{}),gates=projectPolicies(p);return `<article class="project-card alias-project-card"><div class="project-head"><span class="project-symbol tone-${index%4}">${editorialIcon(projectSymbol(p))}</span><div class="project-identity"><h3>${escapeHtml(p.name)}</h3><p title="${escapeHtml(aliases.join('、'))}">别名：${escapeHtml(aliases.join('、')||'暂未配置')}</p></div><span class="project-configured"><i></i>已配置</span></div><div class="project-connections"><div><span>交付方式</span><b>${editorialIcon('cube')}${escapeHtml(MODE[p.delivery_mode]?.[0]||p.delivery_mode)}</b></div><div><span>代码仓库（TFS）· ${repositories.length} 个</span><button type="button" data-project-detail="${escapeHtml(p.project_key)}" title="查看全部仓库路径">${editorialIcon('network')}<span>${escapeHtml(repositories[0]?.[1]||'本机尚未解析到 TFS origin')}</span>${editorialIcon('chevron-right')}</button></div></div><div class="project-quality"><span>质量策略</span>${gates.length?gates.map(label=>`<b>${escapeHtml(label)}</b>`).join(''):'<span class="muted">采用项目默认规则</span>'}</div></article>`}).join(''):'<div class="empty-state catalog-empty"><b>项目目录正在等待本机同步</b><span>请确认本机执行器在线；项目预设同步后会自动出现在这里。</span></div>';
  el.querySelectorAll('[data-project-detail]').forEach(button=>button.onclick=()=>openProjectDetail(button.dataset.projectDetail));
}
function openProjectDetail(key){
  const project=state.projects.find(item=>item.project_key===key),content=document.querySelector('#project-detail-content');if(!project||!content)return;
  const repositories=Object.entries(project.repository_tfs_paths||{}),aliases=project.routing_title_keywords||[];
  switchView('project-detail');document.querySelector('#view-title').textContent=project.name;document.querySelector('#project-breadcrumb b').textContent=project.name;document.title=`${project.name} · AutoDev`;
  content.innerHTML=`<div class="project-detail-top"><section class="project-detail-card"><h3>${editorialIcon('clipboard')}项目信息</h3><dl><div><dt>项目标识</dt><dd>${escapeHtml(project.project_key)}</dd></div><div><dt>项目别名</dt><dd>${escapeHtml(aliases.join('、')||'暂未配置')}</dd></div><div><dt>交付方式</dt><dd>${escapeHtml(MODE[project.delivery_mode]?.[0]||project.delivery_mode)}</dd></div><div><dt>默认目标分支</dt><dd>${escapeHtml(project.base_branch||'未同步')}</dd></div></dl></section><section class="project-detail-card"><h3>${editorialIcon('cube')}项目治理</h3><div class="project-quality">${projectPolicies(project).map(label=>`<b>${escapeHtml(label)}</b>`).join('')||'<span>采用项目默认规则</span>'}</div><dl><div><dt>本地执行器</dt><dd>${escapeHtml(project.runner_id||'未同步')}</dd></div><div><dt>最近同步</dt><dd>${fmt(project.updated_at)}</dd></div><div><dt>配置来源</dt><dd>本机项目目录 · 只读</dd></div></dl></section></div><section class="project-detail-card"><h3>${editorialIcon('network')}仓库信息 <small>${repositories.length} 个</small></h3><div class="project-repository-list">${repositories.map(([name,url],index)=>`<article><span class="repository-number">${String(index+1).padStart(2,'0')}</span><div><b>${escapeHtml(name)}</b><a href="${escapeHtml(safeRepositoryUrl(url))}" target="_blank" rel="noopener">${escapeHtml(url)} ↗</a></div><span class="repository-branch" title="开发目标分支">${escapeHtml(project.repository_base_branches?.[name]||project.base_branch||'未同步分支')}</span></article>`).join('')||'<p class="muted">尚未同步仓库路径，请等待执行器上线。</p>'}</div></section>`;
}
function renderProjectGuide(){
  const guide=document.querySelector('#project-guide'),list=document.querySelector('#project-guide-list'),count=document.querySelector('#project-guide-count');
  if(!guide||!list||!count)return;
  const projects=state.projects||[];
  count.textContent=String(projects.length).padStart(2,'0');
  const signature=JSON.stringify(projects.map(p=>[p.project_key,p.name,p.routing_title_keywords]));
  if(signature===state.projectGuideSignature)return;
  state.projectGuideSignature=signature;
  list.innerHTML=projects.length?projects.map((p,index)=>{
    const aliases=(p.routing_title_keywords||[]).map(value=>String(value).trim()).filter(Boolean);
    return `<article class="project-guide-item"><span>${String(index+1).padStart(2,'0')}</span><div><strong>${escapeHtml(p.name)}</strong><div class="guide-aliases">${(aliases.length?aliases:[p.name]).map(alias=>`<code>【${escapeHtml(alias)}】</code>`).join('')}</div></div></article>`;
  }).join(''):'<div class="project-guide-empty">项目目录正在等待本机执行器同步</div>';
}
function renderAnalytics(){
  if(USER.role!=='admin'||!state.analytics)return;
  const a=state.analytics,o=a.overview||{},trend=a.daily_trend||[],sum=key=>trend.reduce((total,item)=>total+(Number(item[key])||0),0),waiting=(a.status_distribution||[]).find(item=>item.name==='waiting_merge')?.value||0;
  const kpis=[['近 14 天新建',sum('created'),'clipboard'],['近 14 天交付',sum('delivered'),'check'],['运行中',o.active||0,'code'],['等待合并',waiting,'layers']];
  document.querySelector('#analytics-kpis').innerHTML=kpis.map(([label,value,icon],index)=>`<article class="analytics-kpi"><span class="project-symbol tone-${index}">${editorialIcon(icon)}</span><div><span>${label}</span><b>${escapeHtml(value)}<small> 个</small></b></div></article>`).join('');
  renderAnalyticsColumns('#analytics-status',a.status_distribution||[],value=>STATUS[value]||value);
  renderAnalyticsDonut('#analytics-mode',a.mode_distribution||[],value=>MODE[value]?.[0]||value);
  renderAnalyticsDonut('#analytics-requesters',a.requester_distribution||[],value=>value);
  renderAnalyticsTrend(trend);
  document.querySelector('#analytics-projects').innerHTML=`<div class="analytics-project-head"><span>项目</span><span>任务</span><span>交付</span><span>失败</span><span>平均耗时</span></div>${(a.project_distribution||[]).map(item=>`<div class="analytics-project-row"><b>${escapeHtml(item.name)}</b><span>${item.total}</span><span>${item.delivered}</span><span>${item.failed}</span><span>${fmtDuration(item.avg_duration_seconds)}</span></div>`).join('')||'<p class="muted">暂无项目任务数据</p>'}`;
}
const CHART_COLORS=['#48b899','#ff7646','#f4bc59','#62a1bd','#829891','#b8cfa9','#c78e78'];
function renderAnalyticsColumns(selector,items,labeler){
  const el=document.querySelector(selector),max=Math.max(1,...items.map(item=>Number(item.value)||0));
  el.classList.add('status-columns');
  el.innerHTML=items.map((item,index)=>`<div class="status-column"><b>${Number(item.value)||0}</b><i style="height:${Math.max(0,Number(item.value)||0)/max*145}px;background:${CHART_COLORS[index%CHART_COLORS.length]}"></i><span>${escapeHtml(labeler(item.name))}</span></div>`).join('')||'<p class="muted">暂无状态数据</p>';
}
function renderAnalyticsDonut(selector,items,labeler){
  const el=document.querySelector(selector),total=items.reduce((n,item)=>n+(Number(item.value)||0),0);el.classList.add('donut-chart');
  if(!total){el.innerHTML='<p class="muted">暂无分布数据</p>';return}
  let offset=0;const stops=items.map((item,index)=>{const start=offset;offset+=(Number(item.value)||0)/total*100;return `${CHART_COLORS[index%CHART_COLORS.length]} ${start}% ${offset}%`});
  el.innerHTML=`<div class="donut-ring" aria-hidden="true" style="background:conic-gradient(${stops.join(',')})"><span><b>${total}</b><small>任务</small></span></div><ul>${items.map((item,index)=>`<li><i style="background:${CHART_COLORS[index%CHART_COLORS.length]}"></i><span>${escapeHtml(labeler(item.name))}</span><b>${Number(item.value)||0}</b></li>`).join('')}</ul>`;
}
function renderAnalyticsTrend(trend){
  const el=document.querySelector('#analytics-trend');if(!trend.length){el.innerHTML='<p class="muted">暂无近 14 天的任务数据</p>';return}
  const keys=['created','delivered','failed'],names=['新建任务','交付任务','失败任务'],colors=['#ff713d','#42b998','#8199a7'];
  const max=Math.max(1,...trend.flatMap(item=>keys.map(key=>Number(item[key])||0))),ceiling=Math.max(4,Math.ceil(max/4)*4),x=index=>42+index*680/Math.max(1,trend.length-1),y=value=>178-(Number(value)||0)/ceiling*144;
  const guides=Array.from({length:5},(_,i)=>`<line x1="42" y1="${34+i*36}" x2="728" y2="${34+i*36}" stroke="#e2e9e3"/><text x="28" y="${39+i*36}" text-anchor="end">${ceiling*(4-i)/4}</text>`).join('');
  const series=keys.map((key,index)=>`<polyline points="${trend.map((item,i)=>`${x(i)},${y(item[key])}`).join(' ')}" fill="none" stroke="${colors[index]}" stroke-width="2.5" stroke-linejoin="round"/>${trend.map((item,i)=>`<circle cx="${x(i)}" cy="${y(item[key])}" r="3" fill="${colors[index]}"><title>${escapeHtml(item.day)} · ${names[index]} ${Number(item[key])||0}</title></circle>`).join('')}`).join('');
  el.innerHTML=`<div class="trend-legend">${names.map((name,i)=>`<span><i style="background:${colors[i]}"></i>${name}</span>`).join('')}</div><svg class="trend-line-chart" viewBox="0 0 750 210" role="img" aria-label="近14天任务趋势；下方可展开逐日数据">${guides}${series}${trend.map((item,i)=>i%2===0||i===trend.length-1?`<text x="${x(i)}" y="203" text-anchor="middle">${escapeHtml(String(item.day).slice(5))}</text>`:'').join('')}</svg><details class="chart-source"><summary>查看逐日数据</summary><div>${trend.map(item=>`<p>${escapeHtml(item.day)}：新建 ${Number(item.created)||0} · 交付 ${Number(item.delivered)||0} · 失败 ${Number(item.failed)||0}</p>`).join('')}</div></details>`;
}
function renderAnalyticsBars(selector,items,labeler){const el=document.querySelector(selector);if(!el)return;const max=Math.max(1,...items.map(item=>Number(item.value)||0));el.innerHTML=items.map(item=>`<div class="analytics-bar"><div><span>${escapeHtml(labeler(item.name))}</span><b>${Number(item.value)||0}</b></div><i><em style="width:${(Number(item.value)||0)/max*100}%"></em></i></div>`).join('')||'<p class="muted">暂无数据</p>'}
function renderUsers(){
  const el=document.querySelector('#users-table');if(!el)return;
  el.innerHTML=state.users.map(u=>{const emails=(u.emails||[u.email]).map((email,index)=>`<span class="email-badge ${index===0?'primary':''}">${escapeHtml(email)}</span>`).join('');const self=u.id===USER.id;return `<tr><td><div class="account-name"><span class="account-avatar">${escapeHtml(String(u.display_name||u.username).slice(0,1))}</span><b>${escapeHtml(u.username)}</b>${self?'<small class="self-mark">当前账号</small>':''}</div></td><td>${escapeHtml(u.display_name)}</td><td><div class="email-badges">${emails}</div></td><td><span class="account-role ${u.role==='admin'?'admin':'pm'}">${u.role==='admin'?'管理员':'项目经理'}</span></td><td><span class="status-dot" data-status="${u.active?'delivered':'cancelled'}">${u.active?'启用':'停用'}</span></td><td><div class="row-actions"><button class="text-action edit-user" data-id="${u.id}">编辑</button><button class="text-action ${u.active?'danger':'success'} toggle-user" data-id="${u.id}" ${self?'disabled title="不能停用当前账号"':''}>${u.active?'禁用':'启用'}</button></div></td></tr>`}).join('');
  document.querySelectorAll('.edit-user').forEach(btn=>btn.onclick=()=>openUser(state.users.find(u=>u.id===Number(btn.dataset.id))));
  document.querySelectorAll('.toggle-user').forEach(btn=>btn.onclick=()=>toggleUser(state.users.find(u=>u.id===Number(btn.dataset.id))));
}
function renderRequestEmailOptions(reset=true){const el=document.querySelector('#request-email-options');if(!el)return;const checked=reset?new Set(USER.emails||[]):new Set([...el.querySelectorAll('input:checked')].map(x=>x.value)),seen=new Set();const groups=(state.notificationUsers||[]).map(user=>{const own=Number(user.id)===Number(USER.id),emails=(user.emails||[]).filter(email=>{const key=String(email).toLowerCase();if(seen.has(key))return false;seen.add(key);return true});if(!emails.length)return '';return `<section class="recipient-group ${own?'current':''}"><header><span>${own?'当前账号':'项目经理'}</span><b>${escapeHtml(user.display_name)}</b><small>@${escapeHtml(user.username)}</small></header><div class="recipient-emails">${emails.map((email,index)=>`<label class="mail-choice"><input type="checkbox" name="notification_emails" value="${escapeHtml(email)}" ${(reset?own:checked.has(email))?'checked':''}><span><b>${escapeHtml(email)}</b><small>${index===0?'主邮箱':'备用邮箱'}</small></span><i>✓</i></label>`).join('')}</div></section>`}).join('');el.innerHTML=groups||'<div class="mail-empty">当前没有可选择的通知邮箱，请联系管理员。</div>'}

async function waitForRouting(intakeId,onUpdate=null){
  for(let attempt=0;attempt<120;attempt+=1){
    const intake=(await api(`/api/intakes/${intakeId}`)).intake;
    if(onUpdate)onUpdate(intake);
    if(intake.status==='routed')return intake;
    if(intake.status==='failed')throw new Error(intake.error_message||'未能自动识别该需求所属项目');
    await delay(1000);
  }
  return null;
}

function addOptimisticIntake(intakeId,workItemId,runnerOnline=true,deliveryOptions=['auto_release'],taskType='development'){
  const now=new Date().toISOString();
  const analysis=taskType==='analysis',intake={id:intakeId,intake_id:intakeId,record_type:'intake',work_item_id:workItemId,title:analysis?'正在读取 TFS 问题并识别分析项目…':'正在读取 TFS 需求并识别项目…',project_name:'项目识别中',requester_name:USER.display_name,delivery_mode:'routing',task_type:taskType,delivery_options:deliveryOptions,status:runnerOnline?'routing':'waiting_runner',runner_online:runnerOnline,current_activity:runnerOnline?(analysis?'分析任务已提交，等待执行器扫描':'任务已提交，等待执行器扫描'):'执行器当前离线，任务已安全排队，待执行器上线后自动继续',created_at:now,updated_at:now,completed_at:null,duration_seconds:null,artifacts:[]};
  state.dashboard.active=[intake,...state.dashboard.active.filter(item=>item.id!==intakeId)].slice(0,12);
  state.dashboard.recent=[intake,...state.dashboard.recent.filter(item=>item.id!==intakeId)].slice(0,40);
  const countKey=runnerOnline?'routing':'waiting_runner';state.dashboard.counts[countKey]=(state.dashboard.counts[countKey]||0)+1;
  if(state.dashboard.stats){state.dashboard.stats.total=(state.dashboard.stats.total||0)+1;state.dashboard.stats.today_total=(state.dashboard.stats.today_total||0)+1;state.dashboard.stats.running=(state.dashboard.stats.running||0)+1}
  if(state.dashboard.capacity)state.dashboard.capacity.queued=(state.dashboard.capacity.queued||0)+1;
  switchView('dashboard');renderDashboard();
}

function showDetailDrawer(){TaskDialog.open();document.querySelector('#detail-backdrop').hidden=false;document.querySelector('#detail-drawer').classList.add('open');document.querySelector('#detail-drawer').setAttribute('aria-hidden','false')}
function renderRoutingState(intakeId,workItemId,status='routing',message='',taskType='development'){
  if(state.selectedIntake!==intakeId)return;
  const offline=status==='waiting_runner',analysis=taskType==='analysis';
  const record={work_item_id:workItemId};
  const head=`<div class="detail-head routing-head"><p class="detail-caption">任务详情 <small>RUN / ${escapeHtml(intakeId.slice(0,8))}</small></p><h2>${tfsLink(record,`#${workItemId}`)} <span>${offline?'等待执行器上线':`正在识别${analysis?'分析':'研发'}项目`}</span><i class="status-dot" data-status="${offline?'queued':'developing'}">${offline?'已进入安全队列':'识别中'}</i></h2><div class="detail-meta"><span>${editorialIcon('cube')}所属项目 <b>待识别</b></span><span>${analysis?'问题分析':'自主研发'}</span><span>任务已成功发起</span></div></div>`;
  const note=escapeHtml(message||(offline?`任务不会丢失，执行器上线后会自动领取并继续${analysis?'分析':'研发'}。`:`系统会自动识别所属项目与${analysis?'分析范围':'交付策略'}，识别完成后此处直接切换为任务详情。`));
  const overview=`<section class="routing-summary"><div class="routing-summary-icon">${editorialIcon(offline?'clock':'network')}</div><div><h3>${offline?'已进入安全队列':'正在识别项目'}</h3><p>${note}</p><ol class="routing-steps"><li data-state="done"><i>✓</i><b>已提交</b><small>任务已保存</small></li><li data-state="${offline?'active':'done'}"><i>${offline?'2':'✓'}</i><b>等待执行器</b><small>${offline?'上线后自动继续':'执行器已领取'}</small></li><li data-state="${offline?'pending':'active'}"><i>3</i><b>识别项目</b><small>${offline?'等待执行器上线':'读取需求与项目规则'}</small></li></ol></div></section><div class="routing-facts"><section><h3>任务信息</h3><p>TFS #${workItemId}</p><p>${analysis?'问题分析':'自主研发'} · 项目识别完成后显示交付策略</p></section><section><h3>项目识别依据</h3><p>使用需求标题、内容和项目别名确认范围，不会在识别前启动业务代码修改。</p></section></div>`;
  const pending='<div class="learning-empty"><b>项目识别完成后展示</b><p>当前已保存任务，无需重复提交。</p></div>';
  document.querySelector('#detail-content').innerHTML=TaskDialog.render(`intake-${intakeId}`,head,[['overview','交付概览',overview],['requirement','需求拆解',pending],['development',analysis?'分析过程':'研发过程',pending],['delivery',analysis?'报告交付':'合并发版',pending],['acceptance','验收反馈',pending]]);
  TaskDialog.bind();
}
async function openRoutingDetail(intakeId,workItemId,initialStatus='routing',taskType='development'){
  state.selectedIntake=intakeId;state.selectedRequest=null;state.selectedTerminal=false;
  const generation=++state.routingGeneration;
  showDetailDrawer();renderRoutingState(intakeId,workItemId,initialStatus,'',taskType);
  try{
    const routed=await waitForRouting(intakeId,intake=>renderRoutingState(intakeId,workItemId,intake.display_status,intake.display_message,intake.task_type||taskType));
    if(generation!==state.routingGeneration||state.selectedIntake!==intakeId)return;
    const requestId=routed?.result_request_id;
    if(requestId){state.selectedIntake=null;const analysis=(routed.task_type||taskType)==='analysis';toast(routed.joint?`已归类为 ${routed.children.length} 个项目，联合${analysis?'分析':'研发'}已启动`:`项目已识别，${analysis?'问题分析':'研发'}任务进入队列`);await refresh();await openDetail(requestId)}
    else document.querySelector('#detail-content').innerHTML='<div class="error-box">任务仍在后台排队，请稍后从任务总览中查看。</div>';
  }catch(err){
    if(generation!==state.routingGeneration)return;
    const record={work_item_id:workItemId};
    document.querySelector('#detail-content').innerHTML=`<div class="detail-head"><p class="eyebrow">识别失败 / ROUTING FAILED</p><h2>${tfsLink(record,`#${workItemId}`)} · 项目识别失败</h2></div><div class="error-box">${escapeHtml(err.message)}</div><div class="detail-actions"><button class="btn btn-secondary" id="retry-new-run">重新发起</button></div>`;
    document.querySelector('#retry-new-run')?.addEventListener('click',()=>{closeDetail();openRequestModal(taskType)});
  }
}
async function openDetail(id){window.ProjectLearning?.willOpenRequest(id);state.selectedIntake=null;state.selectedRequest=id;state.selectedTerminal=false;showDetailDrawer();await refreshDetail(id)}
async function refreshDetail(id,silent=false){
  const drawer=document.querySelector('#detail-drawer'),eventList=document.querySelector('#detail-content .event-list');
  const drawerScroll=silent?drawer.scrollTop:0,eventScroll=silent&&eventList?eventList.scrollTop:0;
  try{
    const d=(await api(`/api/requests/${id}`)).request;if(state.selectedRequest!==id)return;
    const active=document.activeElement,continuationFocused=silent&&active?.id==='continuation-prompt';
    const focusedTab=silent&&active?.dataset?.taskTab;
    const learningFocused=silent&&active?.closest?.('.request-learning-panel');
    const selectionStart=continuationFocused||learningFocused?active.selectionStart:null,selectionEnd=continuationFocused||learningFocused?active.selectionEnd:null,textareaScroll=continuationFocused||learningFocused?active.scrollTop:0;
    renderDetail(d);
    if(silent)requestAnimationFrame(()=>{
      drawer.scrollTop=drawerScroll;
      const refreshedEvents=document.querySelector('#detail-content .event-list');if(refreshedEvents)refreshedEvents.scrollTop=eventScroll;
      const prompt=continuationFocused?document.querySelector('#continuation-prompt'):learningFocused&&active.isConnected?active:null;
      if(prompt){prompt.focus({preventScroll:true});if(selectionStart!==null&&typeof prompt.setSelectionRange==='function')prompt.setSelectionRange(selectionStart,selectionEnd);prompt.scrollTop=textareaScroll;}
      else if(focusedTab)document.querySelector(`[data-task-tab="${focusedTab}"]`)?.focus({preventScroll:true});
    });
  }catch(e){if(!silent)toast(e.message)}
}
function renderAnalysisPanel(d){
  const result=d.analysis_result&&typeof d.analysis_result==='object'?d.analysis_result:{},hasResult=Object.keys(result).length>0;
  if(!isAnalysisTask(d)||!hasResult)return '';
  const confidence={high:['高可信','high'],medium:['中可信','medium'],low:['低可信','low']}[result.confidence]||['待确认','low'];
  const evidence=(result.evidence||[]).map((item,index)=>`<article class="analysis-evidence"><span>${String(index+1).padStart(2,'0')}</span><div><b>${escapeHtml(item.source||item.kind||'证据')}</b><small>${escapeHtml(({code:'代码',database:'数据库',log:'日志',tfs:'TFS',configuration:'配置',inference:'推断'})[item.kind]||item.kind||'证据')}</small><p>${escapeHtml(engineText(item.detail||'—'))}</p></div></article>`).join('')||'<p class="muted">暂无可复核证据。</p>';
  const list=(values,empty='无')=>(values||[]).length?`<ul>${values.map(item=>`<li>${escapeHtml(engineText(item))}</li>`).join('')}</ul>`:`<p class="muted">${empty}</p>`;
  return `<section class="analysis-report-panel"><header><div><p class="eyebrow">问题分析报告 / ROOT CAUSE REPORT</p><h3>${escapeHtml(engineText(result.summary||d.result_summary||'问题分析已完成'))}</h3></div><div class="analysis-verdicts"><span data-confidence="${escapeHtml(result.confidence||'low')}">${confidence[0]}</span><span>${result.is_data_issue?'数据问题':'非数据问题'}</span><span>${result.code_change_needed?'建议转研发':'无需代码改造'}</span></div></header><div class="root-cause-card"><span>ROOT CAUSE</span><b>根本原因</b><p>${escapeHtml(engineText(result.root_cause||'尚未形成唯一根因'))}</p></div><div class="analysis-grid"><div class="analysis-column evidence-column"><h4>证据链 <small>EVIDENCE</small></h4>${evidence}</div><div class="analysis-column"><h4>建议动作 <small>NEXT ACTIONS</small></h4>${list(result.recommended_actions,'暂无后续动作')}<h4>影响范围 <small>IMPACT</small></h4>${list(result.affected_scope,'暂无明确影响范围')}<h4>数据库核验 <small>DATABASE</small></h4>${list(result.database_operations,'未执行数据库核验')}</div></div>${(result.risks||[]).length?`<div class="analysis-risks"><b>分析限制与风险</b>${list(result.risks)}</div>`:''}</section>`;
}
function renderGovernancePanel(d){
  const history=Array.isArray(d.history_context)?d.history_context:[],ledger=Array.isArray(d.acceptance_ledger)?d.acceptance_ledger:[],gate=d.quality_gate_result&&typeof d.quality_gate_result==='object'?d.quality_gate_result:{},checks=Array.isArray(gate.checks)?gate.checks:[],analysis=d.analysis_result&&typeof d.analysis_result==='object'?d.analysis_result:{},invariants=Array.isArray(gate.business_invariants)?gate.business_invariants:(Array.isArray(analysis.business_invariants)?analysis.business_invariants:[]),conflicts=Array.isArray(analysis.historical_conflicts)?analysis.historical_conflicts:[];
  if(!history.length&&!ledger.length&&!checks.length&&!invariants.length&&!conflicts.length&&!analysis.environment)return '';
  const historyCards=history.slice(0,6).map((item,index)=>`<article class="governance-history"><span>${String(index+1).padStart(2,'0')}</span><div><b>${escapeHtml(item.task_type==='analysis'?'问题分析':'自主研发')} · ${escapeHtml(STATUS[item.status]||ANALYSIS_STATUS[item.status]||item.status||'—')}</b><p>${escapeHtml(engineText(item.result_summary||'暂无结论摘要'))}</p><small>${escapeHtml(item.id||'')} ${item.pr_url?` · PR #${escapeHtml(item.pr_id||'—')}`:''}</small></div></article>`).join('');
  const ledgerCards=ledger.map(item=>`<article class="acceptance-ledger-item" data-status="${escapeHtml(item.status||'partial')}"><header><span>${escapeHtml(item.id||'AC')}</span><b>${escapeHtml(item.status==='completed'?'已完成':item.status==='not_applicable'?'不适用':item.status==='blocked'?'阻塞':'部分完成')}</b></header><h4>${escapeHtml(engineText(item.criterion||'未命名验收项'))}</h4><p>${(item.files||[]).length?escapeHtml(item.files.join(' · ')):'未登记文件'}</p><small>${(item.tests||item.evidence||[]).slice(0,2).map(value=>escapeHtml(engineText(value))).join(' / ')||'未登记测试证据'}</small></article>`).join('');
  const gateRows=checks.map(item=>`<article class="quality-check" data-status="${escapeHtml(item.status||'warning')}"><i></i><div><b>${escapeHtml(item.name||item.id||'质量检查')}</b><p>${escapeHtml(engineText(item.detail||'—'))}</p></div><span>${escapeHtml(item.status==='passed'?'通过':item.status==='blocked'?'阻断':'提醒')}</span></article>`).join('');
  const invariantRows=invariants.map(item=>`<article class="invariant-row" data-status="${escapeHtml(item.status||'unverified')}"><b>${escapeHtml(item.name||'业务不变量')}</b><span>${escapeHtml(item.status==='verified'?'已验证':item.status==='not_applicable'?'不适用':'未验证')}</span><p>期望 ${escapeHtml(engineText(item.expected||'—'))} · 实际 ${escapeHtml(engineText(item.actual||'—'))}</p><small>${escapeHtml(engineText(item.evidence||item.source||'未提供证据'))}</small></article>`).join('');
  const environment=analysis.environment||{},environmentCard=Object.keys(environment).length?`<article class="analysis-environment"><span>ENVIRONMENT</span><b>${escapeHtml(environment.label||'未标注环境')}</b><p>${escapeHtml(engineText(environment.data_source||'未标注数据源'))}</p><small>${escapeHtml(environment.observed_at||'未标注观察时间')} · ${environment.verified?'已验证':'未验证'}</small></article>`:'';
  const conflictCards=conflicts.map(item=>`<article class="history-conflict"><b>历史结论差异 · ${escapeHtml(item.request_id||'未知任务')}</b><p>${escapeHtml(engineText(item.conflict||'—'))}</p><small>${escapeHtml(engineText(item.resolution||item.evidence||'尚未说明取舍依据'))}</small></article>`).join('');
  return `<section class="governance-panel"><header><div><p class="eyebrow">研发治理 / DELIVERY ASSURANCE</p><h3>经验门禁与可回滚证据</h3></div><span data-status="${escapeHtml(gate.status||'pending')}">${escapeHtml(gate.status==='passed'?'门禁通过':gate.status==='blocked'?'门禁阻断':gate.status==='warning'?'有提醒':'采集中')}</span></header>${environmentCard}${conflictCards?`<div class="governance-block"><h4>历史结论差异 <small>CONTRADICTIONS</small></h4>${conflictCards}</div>`:''}${historyCards?`<div class="governance-block"><h4>同需求历史 <small>HISTORY REUSE</small></h4><div class="governance-history-grid">${historyCards}</div></div>`:''}${ledgerCards?`<div class="governance-block"><h4>验收项账本 <small>ACCEPTANCE LEDGER</small></h4><div class="acceptance-ledger-grid">${ledgerCards}</div></div>`:''}${invariantRows?`<div class="governance-block"><h4>业务数据不变量 <small>DATA INVARIANTS</small></h4>${invariantRows}</div>`:''}${gateRows?`<div class="governance-block"><h4>自动质量门禁 <small>QUALITY GATES</small></h4>${gateRows}</div>`:''}</section>`;
}
function renderDetail(d){
  const jointChildren=Array.isArray(d.joint_children)?d.joint_children:[],jointStatus=d.joint_status||d.status,isJoint=jointChildren.length>1;
  if(d.status!=='waiting_approval')state.continuationDrafts.delete(d.id);
  const controlBusy=(d.controls||[]).some(c=>['pending','running','waiting_merge'].includes(c.status));
  state.selectedTerminal=!controlBusy&&(TERMINAL.has(jointStatus)||jointStatus==='waiting_input');
  const deliveredArtifacts=visibleArtifacts(d.artifacts,d.delivery_mode,d.delivery_options),screenshots=deliveredArtifacts.filter(isMergeScreenshot),files=deliveredArtifacts.filter(a=>!isMergeScreenshot(a));
  const screenshotGallery=screenshots.length?`<p class="eyebrow">PR 合并截图 / MERGE SCREENSHOTS</p><div class="merge-screenshot-grid">${screenshots.map(a=>`<article class="merge-screenshot-card"><button type="button" class="merge-screenshot-preview artifact-preview-trigger" data-preview-url="${escapeHtml(artifactPreviewUrl(a))}" data-download-url="${escapeHtml(artifactOpenUrl(a))}" data-preview-title="${escapeHtml(a.name)}" data-preview-repository="${escapeHtml(artifactRepository(a))}"><span class="screenshot-frame"><img src="${escapeHtml(artifactPreviewUrl(a))}" alt="${escapeHtml(a.name)}" loading="lazy"></span><span class="screenshot-meta"><i>${escapeHtml(artifactRepository(a))}</i><b>${artifactPrNumber(a)?`PR #${escapeHtml(artifactPrNumber(a))}`:'已合并 PR'}</b><small>点击画面预览真实浏览器截图 ↗</small></span></button><a class="merge-screenshot-download" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener" download onclick="event.stopPropagation()"><span>下载截图</span><i>↘</i></a></article>`).join('')}</div>`:'';
  const artifacts=files.map(a=>isMenuLink(a)?`<button type="button" class="artifact menu-artifact menu-link-copy" data-menu-link="${escapeHtml(a.name)}"><span><b>${escapeHtml(a.name)}</b><small class="muted">新增视图菜单链接</small></span><i>复制 ↗</i></button>`:isLicenseRequest(a)?`<a class="artifact license-artifact" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener"><span><b>${escapeHtml(a.name)}</b><small class="muted">合并截图已嵌入 TFS License 授权申请</small></span><i>打开申请 ↗</i></a>`:isReleaseArtifact(a)?`<a class="artifact release-artifact" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener"><span><b>${escapeHtml(a.name)}</b><small class="muted">TFS 流水线发布产物页面</small></span><i>查看产物 ↗</i></a>`:isAnalysisReport(a)?`<a class="artifact analysis-report-artifact" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener"><span><b>${escapeHtml(a.name)}</b><small class="muted">包含根因、证据链、影响范围与建议动作</small></span><i>下载报告 ↘</i></a>`:isGovernanceReport(a)?`<a class="artifact governance-report-artifact" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank" rel="noopener"><span><b>${escapeHtml(a.name)}</b><small class="muted">${a.kind==='delivery_manifest'?'构建提交、文件哈希与质量门禁':'最新目标分支既有实现复核'}</small></span><i>查看清单 ↘</i></a>`:`<a class="artifact" href="${escapeHtml(artifactOpenUrl(a))}" target="_blank"><span>${escapeHtml(a.name)}<small class="muted"> · ${escapeHtml(a.kind)}</small></span><i>下载 ↘</i></a>`).join('')||(!screenshots.length?`<p class="muted">${isAnalysisTask(d)?'分析报告将在结论形成后出现。':'产物将在对应步骤完成后出现。'}</p>`:'');
  const fileSection=d.status==='waiting_input'?'':(files.length||!screenshots.length?`<p class="eyebrow">${isAnalysisTask(d)?'分析报告 / ANALYSIS REPORT':'交付产物 / DELIVERABLES'}</p><div class="artifact-list">${artifacts}</div>`:'');
  const runningStepIndex=d.steps.findIndex(step=>step.status==='running');
  const steps=d.steps.map((s,index)=>`<div class="timeline-item ${s.status} ${index===runningStepIndex-1?'feeds-next':''}" data-step-code="${escapeHtml(s.step_code||'')}"><div class="timeline-mark"></div><div class="timeline-copy"><b>${escapeHtml(engineText(s.name))}</b><span>${escapeHtml(engineText(s.message||({pending:'等待执行',running:'正在执行',completed:'已完成',failed:'执行失败',skipped:'已跳过'}[s.status]||s.status)))}</span>${stepTiming(s)}</div></div>`).join('');
  const events=d.events.slice(0,20).map(e=>`<div class="event"><b>${escapeHtml(engineText(e.event_type))} · ${fmt(e.created_at)}</b><p>${escapeHtml(engineText(e.message))}</p></div>`).join('');
  const simulate=USER.role==='admin'&&d.status==='waiting_merge'&&d.policy_snapshot.simulation_mode?`<button class="btn btn-secondary" id="simulate-merge">模拟 PR 已合并</button>`:'';
  const cancel=TaskActions.render(d);
  const reportSync=isAnalysisTask(d)&&d.current_step==='deliver'&&Object.keys(d.analysis_result||{}).length>0&&files.some(isAnalysisReport)&&['failed','waiting_analysis_sync'].includes(d.status);
  const retry=d.can_manage_request!==false&&!isJoint&&(d.status==='failed'||reportSync)?`<button class="btn btn-secondary retry-run" id="retry-run">${reportSync?'重试报告同步':'重新发起'} ↻</button>`:'';
  const continuation=USER.role==='admin'&&(d.status==='waiting_approval'||d.can_continue_in_place)?renderContinuationPanel(d):'';
  const blockerBrief=d.status==='waiting_approval'?renderBlockerSummary(d):'';
  const live=d.status==='developing'?`<button class="btn btn-live" id="open-devcore-stream"><i></i>查看${isAnalysisTask(d)?'分析':'研发'}过程</button>`:'';
  const prSources=isJoint?jointChildren.flatMap(child=>requestPullRequests(child).map(item=>({...item,project:child.project_name}))):requestPullRequests(d).map(item=>({...item,project:''}));
  const prButtons=prSources.map(item=>`<a class="btn btn-primary" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">打开 ${escapeHtml(item.project?`${item.project} / ${item.repository}`:item.repository)} · PR #${escapeHtml(item.id)} ↗</a>`).join('');
  const supplement=d.can_manage_request!==false&&d.status==='waiting_input'?renderSupplementPanel(d):'';
  const optionLabels=isAnalysisTask(d)?[]:(d.delivery_options===null?['代码合并截图','License 申请（历史策略）']:(d.delivery_options||[]).map(value=>DELIVERY_OPTION_LABELS[value]||value));
  const classificationByProject=new Map((d.classification_summary||[]).map(item=>[item.project_key,item]));
  const jointPanel=isJoint?`<section class="joint-delivery-panel"><header><div><p class="eyebrow">${isAnalysisTask(d)?'联合分析 / JOINT ANALYSIS':'联合研发 / JOINT DELIVERY'}</p><h3>${jointChildren.length} 个项目协同${isAnalysisTask(d)?'分析':'交付'}</h3></div><span class="status-dot" data-status="${escapeHtml(jointStatus)}">${escapeHtml((isAnalysisTask(d)?ANALYSIS_STATUS:STATUS)[jointStatus]||jointStatus)}</span></header><div class="joint-child-grid">${jointChildren.map(child=>{const evidence=classificationByProject.get(child.project_key)||{},terms=(evidence.matched_terms||[]).join('、');return `<button type="button" class="joint-child ${child.id===d.id?'active':''}" data-joint-child-id="${escapeHtml(child.id)}"><span class="joint-child-index">${String(child.joint_project_index||1).padStart(2,'0')}</span><span class="joint-child-copy"><b>${escapeHtml(child.project_name)}</b><small>${escapeHtml(terms?`识别依据：${terms}`:'按需求内容自动归类')}</small><em>${escapeHtml(engineText(child.current_activity||child.status_label))}</em></span><i class="status-dot" data-status="${escapeHtml(child.display_status||child.status)}">${escapeHtml(child.status_label)}</i></button>`}).join('')}</div><p>${isAnalysisTask(d)?'各项目分别执行只读代码与开发库核验；全部完成后统一回填 TFS，并发送一封汇总分析邮件。':'各项目分别执行代码修改、提交、审核与构建；全部完成后统一更新 TFS，并发送一封汇总交付邮件。'}</p></section>`:'';
  const analysisPanel=renderAnalysisPanel(d),governancePanel=renderGovernancePanel(d),deliverySummary=isAnalysisTask(d)?`<div class="delivery-option-summary analysis-summary"><b>本次任务</b><span>只读代码分析</span><span>DM7 只读核验</span><span>结构化报告</span></div>`:`<div class="delivery-option-summary"><b>本次交付</b>${optionLabels.map(label=>`<span>${escapeHtml(label)}</span>`).join('')||'<span>按项目定义</span>'}</div>`;
  const head=`<div class="detail-head"><p class="detail-caption">任务详情 <small>RUN / ${d.id.slice(0,8)}${isJoint?' / JOINT':''}</small></p><h2>${tfsLink(d,`#${d.work_item_id}`)} <span>${escapeHtml(d.title||'读取需求中')}</span><i class="status-dot" data-status="${escapeHtml(d.status)}">${escapeHtml(d.status_label)}</i></h2><div class="detail-meta"><span>${editorialIcon('cube')}所属项目 <b>${escapeHtml(d.project_name)}</b></span><span>${editorialIcon('clock')}总耗时 <b>${fmtDuration(requestDuration(d))}</b></span><span>${editorialIcon('clipboard')}${d.completed_at?'完成时间':'创建时间'} <b>${fmt(d.completed_at||d.created_at)}</b></span><span>${escapeHtml(taskTypeLabel(d))} · ${escapeHtml(taskModeLabel(d))}</span></div></div>`;
  const summary=`<section class="task-summary" data-status="${escapeHtml(d.status)}"><h3>${escapeHtml(engineText(d.current_activity||d.status_label||''))}</h3><p>${escapeHtml(engineText(d.result_summary||'任务正在按流程推进，最新输出可在研发过程查看。'))}</p></section>`;
  const deliveryFiles=fileSection||screenshotGallery?`<section class="task-deliverables">${fileSection}${screenshotGallery}</section>`:'';
  const failure=d.error_message&&d.status!=='waiting_approval'?`<section class="task-failure-summary"><h3>${reportSync?'报告已生成，外部同步待重试':'任务未完成'}</h3><p>${escapeHtml(engineText(d.error_message).slice(0,180))}${d.error_message.length>180?'…':''}</p><details><summary>展开技术详情</summary><pre>${escapeHtml(engineText(d.error_message))}</pre></details></section>`:'';
  const codeSummary=d.branch_name||d.commit_hash?`<section class="task-code-summary"><h3>代码信息</h3><div><span>来源分支 <code>${escapeHtml(d.branch_name||'未登记')}</code></span><span>最终提交 <code>${escapeHtml(d.commit_hash||'未登记')}</code></span></div></section>`:'';
  const overview=`${files.length||screenshots.length?deliveryFiles:''}${blockerBrief}${failure}${['waiting_input','waiting_approval'].includes(d.status)?'':summary}${codeSummary}${jointPanel}${supplement}${continuation}${retry?`<div class="detail-actions">${retry}</div>`:''}${cancel}${!files.length&&!screenshots.length&&!['waiting_input','waiting_approval'].includes(d.status)?deliveryFiles:''}`;
  const requirements=`<div class="requirement-layout"><section class="requirement-primary"><h3>按需求描述验收</h3><p class="muted">保留需求原有分点；没有分点时按整体范围验证。</p><div id="requirement-points"></div><details class="task-context"><summary>查看需求描述</summary><div class="task-requirement-copy">${escapeHtml(d.requirement_summary||d.title||'需求描述读取中')}</div></details></section><aside class="requirement-context"><section><h3>${editorialIcon('layers')}验收范围</h3><p>以本次需求描述为准，不使用 TFS 验收条件字段作为拆解依据。</p><p>已完善的功能和其他项目代码不应受到本次修改影响。</p></section><section><h3>${editorialIcon('clipboard')}相关项目经验</h3><div id="task-experience-context"></div></section></aside></div>`;
  const development=isAnalysisTask(d)&&analysisPanel?`<div class="analysis-workspace">${analysisPanel}<details class="analysis-process-details"><summary>分析流水线与执行记录</summary><div class="development-layout"><aside class="development-steps"><div class="timeline">${steps}</div></aside><section class="task-event-console"><div class="detail-actions">${live}</div><div class="event-list">${events||'暂无执行记录'}</div></section></div></details>${governancePanel}</div>`:`<div class="development-layout"><aside class="development-steps"><h3>${isAnalysisTask(d)?'分析':'研发'}流水线</h3><div class="timeline">${steps}</div></aside><div class="development-workspace"><h3>当前${isAnalysisTask(d)?'分析':'研发'}进展</h3><div class="development-current">${summary}<div class="detail-actions">${live}</div></div><section class="task-event-console"><h3>最新执行记录</h3><div class="event-list">${events||'<p class="muted">暂无执行记录，新的输出将在这里展示。</p>'}</div></section>${governancePanel}</div></div>`;
  const repositories=Array.isArray(d.repository_states)?d.repository_states:[];
  const repositoryDelivery=repositories.length?`<section class="delivery-repositories"><h3>${editorialIcon('code')}代码提交与合并</h3><div class="delivery-repository-scroll"><table><thead><tr><th>代码仓库</th><th>目标分支</th><th>提交记录</th><th>合并记录</th><th>PR</th></tr></thead><tbody>${repositories.map(repo=>`<tr><td>${escapeHtml(repo.repository_short_name||repo.name||'代码仓库')}</td><td><code>${escapeHtml(repo.base_branch||'未登记')}</code></td><td><code title="${escapeHtml(repo.commit_hash||'')}">${escapeHtml((repo.commit_hash||'').slice(0,12)||'未登记')}</code></td><td><span class="${repo.merge_commit?'merge-recorded':'muted'}">${repo.merge_commit?'已登记合并提交':'尚无合并记录'}</span>${repo.merge_commit?`<code title="${escapeHtml(repo.merge_commit)}">${escapeHtml(repo.merge_commit.slice(0,12))}</code>`:''}</td><td>${repo.pr_url&&/^https?:\/\//i.test(repo.pr_url)?`<a href="${escapeHtml(repo.pr_url)}" target="_blank" rel="noopener">PR #${escapeHtml(repo.pr_id||'—')} ↗</a>`:'—'}</td></tr>`).join('')}</tbody></table></div></section>`:'';
  const syncStatus=reportSync?`<section class="report-sync-state"><div><h3>${editorialIcon('check')}分析报告已生成</h3><p>分析结论和已有报告保留，可在首页下载。</p><button type="button" class="btn btn-secondary" data-task-switch="overview">查看分析报告 →</button></div><div><h3>${editorialIcon('refresh')}外部同步待重试</h3><p>仅重试 TFS 状态同步与通知，不重新执行分析。</p>${retry.replace('id="retry-run"','id="retry-delivery"')}</div></section>${failure}`:'';
  const delivery=`${syncStatus}${deliverySummary}<h3 class="delivery-process-title">${isAnalysisTask(d)?'报告交付流程':'提交与交付流程'}</h3><div id="delivery-step-list" class="timeline"></div>${repositoryDelivery}<div class="detail-actions">${prButtons}${simulate}</div><div class="delivery-notice"><span>通知至 ${(d.notification_emails||[]).map(escapeHtml).join('、')||'—'}</span><button type="button" class="text-button" data-task-switch="overview">查看交付产物 →</button></div>`;
  document.querySelector('#detail-content').innerHTML=TaskDialog.render(d.id,head,[
    ['overview','交付概览',overview],['requirement','需求拆解',requirements],
    ['development',isAnalysisTask(d)?'分析过程':'研发过程',development],
    ['delivery',isAnalysisTask(d)?'报告交付':'合并发版',delivery],['acceptance','验收反馈','<div id="task-acceptance-mount"></div>']
  ]);
  document.querySelectorAll('#task-panel-development [data-step-code]').forEach(step=>{
    if(['submit','release','deliver'].includes(step.dataset.stepCode))document.querySelector('#delivery-step-list').append(step);
  });
  TaskDialog.bind();
  TaskActions.bind(d);
  document.querySelectorAll('[data-task-switch]').forEach(button=>button.onclick=()=>TaskDialog.select(button.dataset.taskSwitch,true));
  const stepSignature=`${d.status}:${d.current_step||''}:${d.steps.map(step=>step.status).join(',')}`,previousStepSignature=state.orbExperience.detailSteps.get(d.id);state.orbExperience.detailSteps.set(d.id,stepSignature);if(previousStepSignature&&previousStepSignature!==stepSignature){const runningStep=document.querySelector('#detail-content .timeline-item.running');if(runningStep){runningStep.classList.add('step-arrived');setTimeout(()=>runningStep.classList.remove('step-arrived'),1100)}}
  window.ProjectLearning?.renderRequest(d);
  bindArtifactPreviews(document.querySelector('#detail-content'));
  bindMenuLinks(document.querySelector('#detail-content'));
  document.querySelectorAll('[data-joint-child-id]').forEach(button=>button.onclick=()=>openDetail(button.dataset.jointChildId));
  document.querySelector('#open-devcore-stream')?.addEventListener('click',()=>openDevCoreStream(d.id,d.work_item_id,d));
  document.querySelector('#simulate-merge')?.addEventListener('click',async()=>{try{await api(`/api/requests/${d.id}/simulate-merge`,{method:'POST'});toast('已模拟合并，正在生成交付物');await refresh()}catch(e){toast(e.message)}});
  document.querySelectorAll('#retry-run,#retry-delivery').forEach(button=>button.addEventListener('click',async()=>{if(!await TaskActions.confirmRetry(d,reportSync))return;button.disabled=true;try{const result=await api(`/api/requests/${d.id}/${reportSync?'retry-analysis-sync':'retry'}`,{method:'POST'});toast(result.reuse_report?'已安排报告同步，不重新执行分析':'已重新发起，任务进入队列');await refresh();await openDetail(result.id)}catch(e){toast(e.message);button.disabled=false}}));
  document.querySelector('#continuation-prompt')?.addEventListener('input',event=>state.continuationDrafts.set(d.id,event.currentTarget.value));
  document.querySelector('#continuation-form')?.addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button[type="submit"]'),error=form.querySelector('.continuation-error'),prompt=form.elements.prompt.value.trim();error.hidden=true;button.disabled=true;try{await api(`/api/requests/${d.id}/continue`,{method:'POST',body:JSON.stringify({prompt})});state.continuationDrafts.delete(d.id);toast('已继续执行，将复用原工作区和 DevCore 会话');state.selectedTerminal=false;await refresh();await openDetail(d.id)}catch(e){error.textContent=e.message;error.hidden=false;button.disabled=false}});
  document.querySelector('#supplement-form')?.addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button[type="submit"]'),error=form.querySelector('.supplement-error');const answers=[...form.querySelectorAll('.supplement-answer')].map(input=>({id:input.dataset.id,answer:input.value.trim()})).filter(item=>item.answer);error.hidden=true;button.disabled=true;try{await api(`/api/requests/${d.id}/supplement`,{method:'POST',body:JSON.stringify({answers})});toast(`补充信息已提交，任务重新进入${isAnalysisTask(d)?'分析':'研发'}队列`);state.selectedTerminal=false;await refresh();await openDetail(d.id)}catch(e){error.textContent=e.message;error.hidden=false;button.disabled=false}});
}
function renderBlockerSummary(d){const summary=d.blocker_summary||{},reason=summary.reason||d.error_message||'研发遇到尚未解决的问题。',decision=summary.decision_required||'';return `<section class="blocker-brief" role="alert"><header><span>${decision?'BLOCKED / DECISION REQUIRED':'RECOVERY / AUTO POLICY'}</span><b>${decision?'任务已暂停，等待你的判断':'普通技术问题无需人工审批'}</b></header><div class="blocker-brief-grid" ${decision?'':'style="grid-template-columns:1fr"'}><article class="blocker-reason"><span>01 / 当前情况</span><p>${escapeHtml(engineText(reason))}</p></article>${decision?`<article class="blocker-decision"><span>02 / 需要你判断</span><p>${escapeHtml(engineText(decision))}</p></article>`:''}</div></section>`}
function renderContinuationPanel(d){const draft=state.continuationDrafts.get(d.id)||'';return `<section class="continuation-panel"><div class="continuation-marker"><span>ADMIN RECOVERY</span><b>继续尝试解决当前阻塞</b><p>不会新建任务。系统将采用最新项目门禁，复用当前隔离工作区与 DevCore 会话继续研发。</p></div><form id="continuation-form" class="continuation-form"><label for="continuation-prompt"><b>给 DevCore 的判断依据与补充提示</b><small>可选；可补充业务边界和排查线索，普通技术问题由 DevCore 自主判断。输入内容会在任务自动刷新期间保留，提交后再清除。</small></label><textarea id="continuation-prompt" name="prompt" maxlength="6000" placeholder="例如：请复用当前工作区继续排查，并以构建、测试、路由断言和部署检查作为验收证据。">${escapeHtml(draft)}</textarea><p class="form-error continuation-error" hidden></p><button type="submit" class="btn btn-primary">继续执行并保留现场 ↗</button></form></section>`}
function renderSupplementPanel(d){const analysis=isAnalysisTask(d),existing=new Map((d.supplement_answers||[]).map(item=>[String(item.id||''),String(item.answer||'')]));const requests=(d.supplement_requests||[]).filter(item=>item&&item.id&&item.question);const fields=requests.map((item,index)=>`<label class="supplement-item"><span class="supplement-index">${String(index+1).padStart(2,'0')}</span><span class="supplement-copy"><b>${escapeHtml(engineText(item.question))}${item.required===false?'<em>可选</em>':'<em>必填</em>'}</b>${item.reason?`<small>为什么需要：${escapeHtml(engineText(item.reason))}</small>`:''}${item.suggested_answer?`<i>填写提示：${escapeHtml(engineText(item.suggested_answer))}</i>`:''}<textarea class="supplement-answer" data-id="${escapeHtml(item.id)}" placeholder="请在这里填写明确的业务口径、数据来源或规则…" ${item.required===false?'':'required'}>${escapeHtml(existing.get(String(item.id))||'')}</textarea></span></label>`).join('');return `<section class="supplement-panel"><div class="supplement-signal"><span>INPUT REQUIRED</span><b>需要补充信息后继续${analysis?'分析':'研发'}</b><p>任务已暂停且不占用执行器并发槽位。提交后会继续原隔离工作区和 DevCore 会话。</p></div><div class="supplement-conclusion"><span>当前${analysis?'分析':'研发'}结论</span><p>${escapeHtml(engineText(d.result_summary||'DevCore 已完成当前分析，请根据下列问题补充信息。'))}</p></div><form id="supplement-form" class="supplement-form">${fields||'<p class="muted">待补充问题正在生成，请稍后刷新。</p>'}<p class="form-error supplement-error" hidden></p><button type="submit" class="btn btn-primary" ${fields?'':'disabled'}>提交补充并继续${analysis?'分析':'研发'} ↗</button></form></section>`}
async function openDevCoreStream(requestId,workItemId,record={}){closeDevCoreStream();const live=state.live,generation=++live.generation,analysis=isAnalysisTask(record);live.requestId=requestId;live.taskType=analysis?'analysis':'development';live.cursor=0;live.lastGroup='';live.lastKind='';live.lastBubble=null;const panel=document.querySelector('#devcore-stream-panel'),chat=document.querySelector('#devcore-chat');panel.classList.add('open');panel.setAttribute('aria-hidden','false');document.querySelector('#devcore-stream-eyebrow').textContent=analysis?'实时问题分析 / LIVE DEVCORE ANALYSIS':'实时研发会话 / LIVE DEVCORE SESSION';document.querySelector('#devcore-stream-title').innerHTML=`${tfsLink(record,`TFS #${workItemId}`)} · ${analysis?'分析':'研发'}过程`;chat.innerHTML='<div class="chat-system"><i></i><span>正在连接本机 DevCore 会话…</span></div>';try{const result=await api(`/api/requests/${requestId}/devcore-watch/start`,{method:'POST'});if(generation!==live.generation)return;live.watcherId=result.watcher_id;live.cursor=result.cursor||0;chat.innerHTML='<div class="chat-system active"><i></i><span>实时通道已打开，等待新的 DevCore 输出</span></div>';pollDevCoreStream(generation)}catch(err){if(generation!==live.generation)return;chat.innerHTML=`<div class="chat-system error"><i></i><span>${escapeHtml(engineText(err.message))}</span></div>`}}
async function pollDevCoreStream(generation){const live=state.live;if(generation!==live.generation||!live.watcherId)return;try{const result=await api(`/api/requests/${live.requestId}/devcore-watch/${live.watcherId}?after=${live.cursor}`);if(generation!==live.generation)return;live.cursor=result.cursor||live.cursor;(result.events||[]).forEach(appendDevCoreEvent);live.timer=setTimeout(()=>pollDevCoreStream(generation),650)}catch(err){if(generation!==live.generation)return;appendDevCoreSystem(engineText(err.message),'error');live.watcherId=null}}
function renderDevCoreMarkdown(target,value){target.className='chat-rendered';let list=null;engineText(value).split(/\r?\n/).forEach(line=>{if(!line.trim()){list=null;return}if(line.startsWith('### ')){const heading=document.createElement('h3');heading.textContent=line.slice(4);target.appendChild(heading);list=null;return}if(line.startsWith('- ')){if(!list){list=document.createElement('ul');target.appendChild(list)}const item=document.createElement('li');item.textContent=line.slice(2);list.appendChild(item);return}const paragraph=document.createElement('p');paragraph.textContent=line;target.appendChild(paragraph);list=null})}
function appendDevCoreEvent(event){const live=state.live,chat=document.querySelector('#devcore-chat'),kind=event.kind||'status',analysis=live.taskType==='analysis';if(kind==='status')return;const group=event.group||`seq-${event.seq}`;let bubble=live.lastBubble;if(!event.delta||group!==live.lastGroup||kind!==live.lastKind||!bubble){bubble=document.createElement('article');bubble.className=`chat-message ${kind}`;const labels={assistant:analysis?'DEVCORE 问题分析结论':'DEVCORE 研发结论',reasoning:'分析摘要',command:'终端执行',file:'文件变更',plan:analysis?'分析计划':'研发计划'};const label=document.createElement('div');label.className='chat-label';const name=document.createElement('span');name.textContent=labels[kind]||(analysis?'分析过程':'研发过程');const time=document.createElement('time');time.textContent=new Date(event.at).toLocaleTimeString('zh-CN',{hour12:false});label.append(name,time);bubble.appendChild(label);const content=document.createElement(event.format==='markdown'?'div':'pre');bubble.appendChild(content);chat.appendChild(bubble);live.lastBubble=bubble}const content=bubble.lastElementChild;if(event.format==='markdown'&&!event.delta){renderDevCoreMarkdown(content,event.content)}else{content.textContent+=engineText(event.content||'')}live.lastGroup=group;live.lastKind=kind;while(chat.children.length>140)chat.removeChild(chat.firstElementChild);chat.scrollTop=chat.scrollHeight}
function appendDevCoreSystem(message,type='status'){const chat=document.querySelector('#devcore-chat'),el=document.createElement('div');el.className=`chat-system ${type}`;el.innerHTML=`<i></i><span>${escapeHtml(engineText(message))}</span>`;chat.appendChild(el);chat.scrollTop=chat.scrollHeight}
function closeDevCoreStream(){const live=state.live,requestId=live.requestId,watcherId=live.watcherId;live.generation+=1;if(live.timer)clearTimeout(live.timer);live.timer=null;live.watcherId=null;live.requestId=null;live.cursor=0;live.lastBubble=null;const panel=document.querySelector('#devcore-stream-panel');panel.classList.remove('open');panel.setAttribute('aria-hidden','true');document.querySelector('#devcore-chat').innerHTML='';if(requestId&&watcherId)fetch(`/api/requests/${requestId}/devcore-watch/${watcherId}/stop`,{method:'POST',headers:{'Content-Type':'application/json'},keepalive:true}).catch(()=>{})}
function closeDetail(){state.routingGeneration+=1;state.selectedIntake=null;state.selectedRequest=null;state.selectedTerminal=false;closeDevCoreStream();document.querySelector('#detail-backdrop').hidden=true;document.querySelector('#detail-drawer').classList.remove('open');document.querySelector('#detail-drawer').setAttribute('aria-hidden','true');TaskDialog.close()}

function addEmailRow(value=''){const editor=document.querySelector('#user-email-editor'),row=document.createElement('div');row.className='email-row';row.innerHTML=`<span class="email-order">${String(editor.children.length+1).padStart(2,'0')}</span><input type="email" class="user-email-input" value="${escapeHtml(value)}" placeholder="name@example.com" required><button type="button" title="移除邮箱">×</button>`;row.querySelector('button').onclick=()=>{if(editor.children.length===1){row.querySelector('input').value='';return}row.remove();[...editor.children].forEach((item,index)=>item.querySelector('.email-order').textContent=String(index+1).padStart(2,'0'))};editor.appendChild(row)}
function openUser(user=null){const form=document.querySelector('#user-form');form.reset();form.elements.id.value=user?.id||'';form.elements.username.value=user?.username||'';form.elements.display_name.value=user?.display_name||'';form.elements.role.value=user?.role||'pm';form.elements.active.checked=user?.active??true;form.elements.password.required=!user;form.elements.password.value='';form.elements.role.disabled=user?.id===USER.id;form.elements.active.disabled=user?.id===USER.id;document.querySelector('#user-modal-title').textContent=user?'编辑账号':'新建内部账号';document.querySelector('#password-label').textContent=user?'重置密码（留空不修改）':'初始密码';document.querySelector('#save-user').textContent=user?'保存修改':'创建账号';document.querySelector('#user-email-editor').innerHTML='';(user?.emails||['']).forEach(addEmailRow);document.querySelector('#user-error').hidden=true;const modal=document.querySelector('#user-modal');modal.dataset.mode=user?'edit':'create';modal.hidden=false}
async function toggleUser(user){if(!user||user.id===USER.id)return;if(user.active&&!confirm(`确认禁用账号“${user.display_name}”？禁用后该账号会立即退出登录。`))return;try{await api(`/api/users/${user.id}`,{method:'PUT',body:JSON.stringify({username:user.username,display_name:user.display_name,emails:user.emails||[user.email],role:user.role,active:!user.active})});toast(user.active?'账号已禁用':'账号已启用');await refresh()}catch(err){toast(err.message)}}
function closeModals(){document.querySelectorAll('.modal-backdrop').forEach(x=>x.hidden=true)}

document.querySelectorAll('.nav-item').forEach(btn=>btn.onclick=()=>switchView(btn.dataset.view));
document.querySelectorAll('[data-view-link]').forEach(btn=>btn.onclick=()=>switchView(btn.dataset.viewLink));
document.querySelector('#orb-activity-console').addEventListener('click',toggleSidebarTasks);
document.querySelector('#close-sidebar-tasks').addEventListener('click',()=>closeSidebarTasks());
document.querySelector('#sidebar-task-popover').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeSidebarTasks();return}
  if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
  const buttons=[...event.currentTarget.querySelectorAll('.sidebar-task-option')];if(!buttons.length)return;
  event.preventDefault();const index=buttons.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();
});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#sidebar-task-popover,#orb-activity-console'))closeSidebarTasks(false)});
document.addEventListener('focusin',event=>{if(!event.target.closest('#sidebar-task-popover,#orb-activity-console'))closeSidebarTasks(false)});
window.addEventListener('resize',()=>{if(window.innerWidth<=900)closeSidebarTasks(false)});
document.querySelectorAll('[data-close]').forEach(btn=>btn.onclick=closeModals);
document.querySelector('#detail-backdrop').onclick=closeDetail;document.querySelector('#close-detail').onclick=closeDetail;
document.querySelector('#close-devcore-stream').onclick=closeDevCoreStream;
document.querySelector('#close-artifact-preview').onclick=closeArtifactPreview;
document.querySelector('#artifact-preview').onclick=event=>{if(event.target===event.currentTarget)closeArtifactPreview()};
function setRequestTaskType(taskType='development'){
  const form=document.querySelector('#request-form'),analysis=taskType==='analysis',radio=form.querySelector(`[name="task_type"][value="${taskType}"]`);
  if(radio)radio.checked=true;
  document.querySelector('#request-modal-title').textContent=analysis?'发起问题分析':'发起自主研发';
  document.querySelector('#request-modal-eyebrow').textContent=analysis?'问题诊断 / NEW ANALYSIS RUN':'新研发任务 / NEW DEVELOPMENT RUN';
  document.querySelector('#request-modal-description').textContent=analysis?'输入 TFS 用户情景编号，系统会结合代码、TFS 附件、配置、日志和本机开发库形成可复核的问题分析报告。':'输入 TFS 用户情景编号，系统会自动识别一个或多个项目，并统一完成研发、审核与交付。';
  document.querySelector('#request-delivery-block').hidden=analysis;
  document.querySelector('#analysis-mode-note').hidden=!analysis;
  document.querySelector('#request-submit-button').textContent=analysis?'开始问题分析 ↗':'确认并进入队列 ↗';
  document.querySelector('#request-modal').classList.toggle('analysis-mode',analysis);
}
function openRequestModal(taskType='development'){const form=document.querySelector('#request-form');form.reset();setRequestTaskType(taskType);renderRequestEmailOptions(true);document.querySelector('#request-error').hidden=true;document.querySelector('#request-modal').hidden=false;setTimeout(()=>form.querySelector('[name="work_item_id"]').focus(),0)}
document.querySelector('#open-request').onclick=()=>openRequestModal('development');
document.querySelector('#open-analysis').onclick=()=>openRequestModal('analysis');
document.querySelectorAll('[name="task_type"]').forEach(input=>input.addEventListener('change',()=>setRequestTaskType(input.value)));
document.querySelector('#logout').onclick=async()=>{await api('/api/auth/logout',{method:'POST'});location.href='/login'};
document.querySelector('#record-filters')?.addEventListener('submit',event=>{event.preventDefault();const data=new FormData(event.currentTarget),dateFrom=String(data.get('date_from')||''),dateTo=String(data.get('date_to')||'');if(dateFrom&&dateTo&&dateFrom>dateTo){toast('开始日期不能晚于结束日期');return}state.records.filters={keyword:String(data.get('keyword')||'').trim(),task_type:String(data.get('task_type')||''),project_key:String(data.get('project_key')||''),status:String(data.get('status')||''),requester_id:String(data.get('requester_id')||''),date_from:dateFrom,date_to:dateTo};closeLedgerPopovers();loadDeliveryRecords(1).catch(error=>toast(error.message))});
document.querySelector('#reset-record-filters')?.addEventListener('click',()=>{const form=document.querySelector('#record-filters');form.reset();resetRecordFilterControls();state.records.filters={keyword:'',task_type:'',project_key:'',status:'',requester_id:'',date_from:'',date_to:''};loadDeliveryRecords(1).catch(error=>toast(error.message))});
document.addEventListener('click',()=>closeLedgerPopovers());
document.querySelector('#new-user')?.addEventListener('click',()=>openUser());
document.querySelector('#add-user-email')?.addEventListener('click',()=>addEmailRow());

document.querySelector('#request-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,error=document.querySelector('#request-error'),button=form.querySelector('button[type="submit"]');error.hidden=true;const taskType=form.elements.task_type.value,data={work_item_id:Number(form.elements.work_item_id.value),task_type:taskType,notification_emails:[...new Set([...form.querySelectorAll('[name="notification_emails"]:checked')].map(input=>input.value))],delivery_options:taskType==='analysis'?[]:[...new Set([...form.querySelectorAll('[name="delivery_options"]:checked')].map(input=>input.value))]};if(!data.notification_emails.length){error.textContent='请至少选择一个通知邮箱';error.hidden=false;return}if(taskType==='development'&&!data.delivery_options.length){error.textContent='请至少选择一种交付产物';error.hidden=false;return}button.disabled=true;try{const result=await api('/api/requests',{method:'POST',body:JSON.stringify(data)});closeModals();form.reset();if(result.routing){addOptimisticIntake(result.id,data.work_item_id,result.runner_online!==false,data.delivery_options,taskType);toast(result.message||'提交成功，任务已进入运行看板');openRoutingDetail(result.id,data.work_item_id,result.status,taskType)}else{toast(result.message||(taskType==='analysis'?'问题分析任务已进入队列':'研发任务已进入队列'));await refresh();await openDetail(result.id)}}catch(err){form.elements.work_item_id.value=data.work_item_id;setRequestTaskType(taskType);document.querySelector('#request-modal').hidden=false;error.textContent=err.message;error.hidden=false}finally{button.disabled=false}});
document.querySelector('#user-form')?.addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,error=document.querySelector('#user-error');error.hidden=true;const id=Number(form.elements.id.value)||null;const emails=[...form.querySelectorAll('.user-email-input')].map(input=>input.value.trim()).filter(Boolean);const data={username:form.elements.username.value.trim(),display_name:form.elements.display_name.value.trim(),emails,password:form.elements.password.value,role:form.elements.role.value,active:form.elements.active.checked};if(!data.password)delete data.password;try{const result=await api(id?`/api/users/${id}`:'/api/users',{method:id?'PUT':'POST',body:JSON.stringify(data)});if(result.user.id===USER.id)Object.assign(USER,result.user);closeModals();form.reset();toast(id?'账号已更新':'账号已创建');await refresh()}catch(err){error.textContent=err.message;error.hidden=false}});

setInterval(()=>{document.querySelector('#clock').textContent=new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'medium',hour12:false}).format(new Date())},1000);
refresh().catch(e=>toast(e.message));setInterval(()=>refresh().catch(()=>{}),5000);
window.addEventListener('beforeunload',()=>{const live=state.live;if(live.requestId&&live.watcherId)fetch(`/api/requests/${live.requestId}/devcore-watch/${live.watcherId}/stop`,{method:'POST',keepalive:true})});
window.addEventListener('keydown',event=>{if(event.key!=='Escape')return;if(!document.querySelector('#artifact-preview').hidden)closeArtifactPreview();else closeLedgerPopovers()});
