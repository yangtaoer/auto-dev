/* Creative studio: approved data goes live; app code and versions are never written here. */
window.CreativeWorkshop = (()=>{
  'use strict';
  const mount=document.querySelector('#workshop-mount');if(!mount)return{};
  const drafts=new WorkshopDrafts();let items=[],themes=[],tab='mine',timer=null,selected=null,viewRevision=null,reading=false;
  let newDialog=null,newState={key:crypto.randomUUID(),id:null,sending:false};
  const labels={draft:'等待你的想法',design_queued:'等待设计',designing:'imagegen 设计中',design_review:'设计待确认',
    production_queued:'等待制作',producing:'制作中',production_review:'制作待确认',publishing:'正在上线',publish_queued:'等待上线',
    sharing:'正在公开',share_queued:'等待公开',live:'已上线',design_failed:'设计未完成',production_failed:'制作未完成',publish_failed:'上线未完成',share_failed:'公开未完成'};
  const kinds={design:'设计',production:'制作',publish:'上线',share:'公开',notification:'上线通知',recommendation:'推荐邮件'};
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const busy=design=>design?.jobs?.some(job=>['queued','running'].includes(job.status)&&['design','production','publish','share'].includes(job.kind));
  const date=value=>new Date(value).toLocaleString('zh-CN',{hour12:false});
  const seconds=job=>{const value=job.status==='running'&&job.started_at?(Date.now()-Date.parse(job.started_at))/1000:job.elapsed_seconds||0;return value>=60?`${Math.floor(value/60)} 分 ${Math.floor(value%60)} 秒`:`${Math.max(0,Math.floor(value))} 秒`;};
  const request=(url,options={})=>api(url,options);
  const post=(url,body)=>request(url,{method:'POST',body:JSON.stringify(body)});
  const imageUrl=theme=>window.AutoDevMedia?.url(theme.preview)||theme.preview;
  function markdown(host,text){if(window.RequestFollowups)RequestFollowups.markdown(host,text);else host.textContent=text;}
  function status(text,error=false){const host=mount.querySelector('.studio-composer-status');if(host){host.textContent=text;host.style.color=error?'var(--skin-danger)':'';}}
  function shell(){
    mount.innerHTML=`<div class="studio"><div class="studio-toolbar"><h2>创意工坊</h2><small>让工作台，拥有你的风景。</small><nav class="studio-tabs" role="tablist" aria-label="工坊内容">${[['mine','个人设计'],['public','公共壁纸'],['system','系统自带'],['ranking','使用排行'],['history','我的记录']].map(([id,name])=>`<button type="button" role="tab" data-studio-tab="${id}" aria-selected="${id===tab}">${name}</button>`).join('')}</nav><button type="button" class="text-button" data-studio-restore>还原我的外观</button><button type="button" class="btn btn-primary" data-studio-create>＋ 开始创作</button></div><div class="studio-content"></div></div>`;
    mount.querySelectorAll('[data-studio-tab]').forEach(button=>button.onclick=()=>{tab=button.dataset.studioTab;drafts.select(null);paintContent();});
    mount.querySelector('[data-studio-create]').onclick=createDialog;
    mount.querySelector('[data-studio-restore]').onclick=()=>window.AutoDevAppearance?.restore();
  }
  function paintRail(){
    const rail=mount.querySelector('.studio-design-list');if(!rail)return;
    rail.innerHTML=items.length?items.map(item=>`<button type="button" class="studio-design-choice ${item.id===selected?'active':''}" data-design="${escape(item.id)}"><b>${escape(item.name)}</b><span>${escape(labels[item.status]||item.status)}${item.is_public?' · 已公开':''}</span><span>${escape(item.author)} · ${date(item.updated_at)}</span></button>`).join(''):'<div class="studio-empty-art"><p>从一个颜色、一片花叶，或者一张参考图开始。</p></div>';
    rail.querySelectorAll('[data-design]').forEach(button=>button.onclick=()=>select(button.dataset.design));
  }
  function paintContent(){
    mount.querySelectorAll('[data-studio-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.studioTab===tab)));
    const content=mount.querySelector('.studio-content');content.replaceChildren();
    if(tab==='mine'){
      content.innerHTML='<div class="studio-grid"><aside class="studio-rail"><div class="studio-rail-head">作品与草稿 · 每轮记录均保留</div><div class="studio-design-list"></div></aside><section class="studio-artboard"></section><aside class="studio-conversation"></aside></div>';
      paintRail();const initial=items.find(item=>item.id===selected)||items.find(item=>item.owner_id===window.__USER__.id)||items[0];
      if(initial)select(initial.id);else emptyDetail();
    }else if(tab==='system'||tab==='public')paintWallpapers(content,themes.filter(theme=>tab==='system'?!theme.is_custom:theme.is_custom&&theme.is_public&&!theme.trial));
    else paintUsage(content);
  }
  function emptyDetail(){
    mount.querySelector('.studio-artboard').innerHTML='<div class="studio-preview"><div class="studio-empty-art"><span class="studio-orb" aria-hidden="true"></span><b>为每天的工作，种一片新风景。</b><p>上传参考图，聊聊你的想法。imagegen 会先生成工作台设计预览，你确认后才进入制作。</p><button class="btn btn-primary" type="button" data-empty-create>开始第一张设计 ↗</button></div></div>';
    mount.querySelector('[data-empty-create]').onclick=createDialog;
    mount.querySelector('.studio-conversation').innerHTML='<div class="studio-preview"><div class="studio-empty-art"><b>先设计，再制作。</b><p>可以反复交流、比较每一轮效果。确认上线后加入个人设计，公开分享是另一个独立操作。</p></div></div>';
  }
  function select(id){
    selected=id;viewRevision=null;const generation=drafts.select(id);const design=items.find(item=>item.id===id);paintRail();
    const chat=mount.querySelector('.studio-conversation');
    chat.innerHTML='<div class="studio-chat-head"><b>设计对话</b><span>多轮交互 · 留痕记录</span></div><div class="studio-chat" aria-live="polite"></div><form class="studio-composer"><textarea aria-label="设计或制作意见" placeholder="聊聊参考图、色彩、花叶质感，或者希望调整的细节…" maxlength="8000"></textarea><div class="studio-ref-chips"></div><div class="studio-compose-tools"><select aria-label="交流阶段"><option value="design">设计修改</option><option value="production">制作修改</option></select><label>＋ 参考图片<input type="file" accept="image/png,image/jpeg,image/webp" multiple></label><button class="btn btn-primary" type="submit">发送 ↗</button></div><p class="studio-composer-status" role="status"></p></form>';
    const draft=drafts.draft(id),textarea=chat.querySelector('textarea');textarea.value=draft.text;textarea.oninput=()=>{draft.text=textarea.value;};
    chat.querySelector('select').value=design?.approved_design_id?'production':'design';
    chat.querySelector('input[type=file]').onchange=event=>{const files=[...event.target.files];if(files.length>5){status('每轮最多 5 张参考图。',true);return;}draft.files=files;draft.references=[];paintRefs();};
    chat.querySelector('form').onsubmit=event=>{event.preventDefault();sendRound(id);};
    textarea.onkeydown=event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();chat.querySelector('form').requestSubmit();}};
    if(design?.jobs)paintDetail(design);else mount.querySelector('.studio-artboard').innerHTML='<div class="studio-preview"><p class="muted">正在读取作品与历史…</p></div>';
    refreshDetail(id,generation);
  }
  function paintRefs(){const host=mount.querySelector('.studio-ref-chips');if(!host||!selected)return;const draft=drafts.draft(selected);
    host.innerHTML=[...draft.files.map(file=>file.name),...draft.references.map(()=> '已选版本参考图')].map(name=>`<span class="studio-ref-chip">${escape(name)}</span>`).join('');}
  function paintDetail(design){
    if(selected!==design.id||tab!=='mine')return;
    const art=mount.querySelector('.studio-artboard');if(!art)return;
    const revision=design.revisions.find(r=>r.id===(viewRevision||design.current_revision_id));
    const preview=design.assets.find(a=>a.id===revision?.preview_asset_id);const history=revision&&revision.id!==design.current_revision_id;
    art.innerHTML=`<header class="studio-art-head"><h3>${escape(design.name)}</h3><p>${escape(design.author)} · ${escape(labels[design.status]||design.status)}${revision?` · R${revision.number}`:''}</p><div class="studio-steps"><span class="${!design.approved_design_id?'current':''}">01 设计</span><span class="${design.approved_design_id&&!design.live_revision_id?'current':''}">02 制作</span><span class="${design.live_revision_id?'current':''}">03 上线与分享</span></div></header><div class="studio-preview">${preview?`<a href="${escape(preview.url)}" target="_blank" rel="noopener"><img src="${escape(preview.url)}" alt="${escape(design.name)} R${revision.number} 效果图" decoding="async"></a>`:'<div class="studio-empty-art"><span class="studio-orb" aria-hidden="true"></span><b>把想法交给 imagegen。</b><p>生成需要几分钟，可以离开页面，回来继续查看。失败时输入和历史不会丢失。</p></div>'}</div><div class="studio-revisions">${design.revisions.map(r=>`<button type="button" data-revision="${r.id}" class="${revision?.id===r.id?'active':''}">R${r.number} · ${r.stage==='design'?'设计':'制作'}</button>`).join('')}</div><div class="studio-actions"></div>`;
    const latestJob=design.jobs.at(-1),empty=art.querySelector('.studio-empty-art');
    if(empty&&latestJob){
      const title=empty.querySelector('b'),hint=empty.querySelector('p');
      if(latestJob.status==='failed'){
        empty.classList.add('studio-empty-failed');title.textContent='这一轮尚未生成设计图';
        hint.textContent=window.workshopFailure(latestJob).title+' 描述、参考图和对话仍在，右侧可以重试。';
      }else if(['queued','running'].includes(latestJob.status)){
        empty.setAttribute('role','status');title.textContent=latestJob.status==='queued'?'请求已保存，等待执行器':'正在生成你的设计';
        hint.textContent=latestJob.progress||'可离开页面，后台会继续处理。';
      }
    }
    art.querySelectorAll('[data-revision]').forEach(button=>button.onclick=()=>{viewRevision=button.dataset.revision;paintDetail(design);});
    const actions=art.querySelector('.studio-actions');
    function button(label,handler,primary=false){const node=document.createElement('button');node.type='button';node.className='btn '+(primary?'btn-primary':'btn-secondary');node.textContent=label;node.onclick=handler;actions.append(node);return node;}
    if(preview&&design.can_edit)button('用此版本作参考',()=>{const draft=drafts.draft(design.id);draft.references=[preview.id];paintRefs();status('当前查看版本已作为下一轮参考。');});
    if(design.can_edit&&!busy(design)&&!history&&revision?.stage==='design')button('确认设计，进入制作 ↗',()=>act(design,'approve_design',revision.id),true);
    if(design.can_edit&&!busy(design)&&!history&&revision?.stage==='production'&&design.live_revision_id!==revision.id){
      button('在工作台试用',()=>previewRevision(design,revision));button('确认效果，正式上线 ↗',()=>act(design,'publish',revision.id),true);
    }
    if(design.live_revision_id){
      button('使用已上线壁纸',()=>useTheme('workshop-'+design.id));
      if(design.can_edit&&!busy(design)&&!design.is_public)button('公开给所有用户',()=>{
        if(confirm('确认公开最终壁纸和预览图？参考图和设计对话不会公开。公开图片可能被缓存或保存。'))act(design,'share',design.live_revision_id);
      });
      if(design.can_edit&&design.is_public)button('发送推荐邮件',()=>{
        if(confirm('给所有启用推荐邮件的用户发送这张公开壁纸的推荐？同一版本不会重复群发。'))act(design,'recommend',design.live_revision_id);
      });
    }
    const hint=document.createElement('small');hint.textContent=history?'正在查看历史版本。仍在使用的版本不会被覆盖。':busy(design)?'后台正在处理这一轮；可以继续输入下一轮意见，内容会保留。':`${design.usage.users} 人使用过 · ${design.usage.current} 人正在使用 · ${design.usage.activations} 次选择。设计和制作可以反复沟通，公开并非默认。`;actions.append(hint);
    paintChat(design);
    const draft=drafts.draft(design.id);mount.querySelector('.studio-composer button[type=submit]').disabled=busy(design)||draft.sending||!design.can_edit;
    mount.querySelector('.studio-composer select option[value=production]').disabled=!design.approved_design_id;
    status(draft.error||(design.can_edit?'只有确认后才制作和上线；Ctrl + Enter 发送。':'这是其他制作者的私人作品，管理员可查看记录，不能代替本人确认。'),!!draft.error);
  }
  function paintChat(design){
    const host=mount.querySelector('.studio-chat');if(!host)return;
    const signature=JSON.stringify(design.jobs);if(host.dataset.signature===signature)return;
    const bottom=host.scrollHeight-host.scrollTop-host.clientHeight<80;host.dataset.signature=signature;host.replaceChildren();
    const visibleJobs=design.jobs.slice(-40);
    if(design.job_count>40){const older=document.createElement('button');older.type='button';older.className='text-button';older.textContent=`查看之前 ${design.job_count-40} 轮记录`;older.onclick=()=>openHistory(design);host.append(older);}
    for(const job of visibleJobs){
      const question=document.createElement('article');question.className='studio-message user';
      const meta=document.createElement('small');meta.textContent=`${kinds[job.kind]} · ${date(job.created_at)}`;
      const text=document.createElement('p');text.textContent=job.question;question.append(meta,text);host.append(question);
      const answer=document.createElement('article');answer.className='studio-message assistant'+(job.status==='failed'?' error':'');
      const info=document.createElement('small');info.textContent=`AutoDev · ${job.status==='completed'?'已完成':job.status==='failed'?'未完成':job.status==='running'?'处理中':'等待执行器'}${job.started_at?' · '+seconds(job):''}`;
      const body=document.createElement('div');body.className='followup-markdown';
      if(job.status==='completed')markdown(body,job.answer);else body.textContent=job.status==='failed'?window.workshopFailure(job).title:job.progress||'已保存请求，等待执行器处理。';answer.append(info,body);
      if(job.status==='failed'){
        const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
        summary.textContent='查看失败详情';pre.textContent=window.workshopFailure(job).details;
        if(job.output?.attempts?.length)pre.textContent+='\n\n尝试记录：\n'+job.output.attempts.map(a=>`第 ${a.attempt} 次 · ${a.model} / ${a.effort} · ${a.status}${a.category?' · '+a.category:''}`).join('\n');
        details.append(summary,pre);answer.append(details);
      }
      if(job.status==='failed'&&design.can_edit){const retry=document.createElement('button');retry.type='button';retry.className='text-button';retry.textContent='保留记录，再试一次 ↗';retry.onclick=()=>retryJob(design,job);answer.append(retry);}
      if(job.output?.imagegen_calls?.length){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='查看图像生成提示词';details.append(summary);
        const pre=document.createElement('pre');pre.textContent=job.output.imagegen_calls.map(c=>c.revised_prompt||'原生 imagegen 成功生成').join('\n\n');details.append(pre);answer.append(details);}
      host.append(answer);
    }
    if(!design.jobs.length)host.innerHTML='<p class="muted">在下方写下构图、配色、材质或参考图中的喜欢之处，每一轮都会记录在这里。</p>';
    if(bottom)host.scrollTop=host.scrollHeight;
  }
  function openHistory(design){
    const history=document.createElement('dialog');history.className='studio-history-dialog';
    const close=document.createElement('button');close.textContent='关闭历史';close.onclick=()=>{history.close();history.remove();};
    const body=document.createElement('div'),navigation=document.createElement('div');history.append(close,body,navigation);let offset=40;
    async function load(){body.textContent='正在读取之前的记录…';navigation.replaceChildren();
      try{const data=await request(`/api/workshop/designs/${design.id}/conversation?offset=${offset}`);if(!history.isConnected)return;body.replaceChildren();
        for(const job of [...data.items].reverse()){const section=document.createElement('section'),title=document.createElement('h4'),question=document.createElement('p'),answer=document.createElement('div');title.textContent=`${kinds[job.kind]} · ${date(job.created_at)} · ${seconds(job)}`;question.textContent=job.question;markdown(answer,job.answer||job.error_message||job.progress);section.append(title,question,answer);body.append(section);}
        for(const [label,delta,enabled] of [['更新的记录',-40,offset>40],['更早的记录',40,offset+40<design.job_count]]){if(!enabled)continue;const button=document.createElement('button');button.textContent=label;button.onclick=()=>{offset+=delta;load();};navigation.append(button);}
      }catch(error){body.textContent=error.message;}
    }
    history.addEventListener('cancel',()=>history.remove(),{once:true});document.body.append(history);history.showModal();load();
  }
  async function refreshDetail(id,generation){
    if(reading)return;reading=true;
    try{const data=await request(`/api/workshop/designs/${id}`);if(!drafts.isCurrent(id,generation)||tab!=='mine')return;
      const index=items.findIndex(item=>item.id===id);if(index<0)items.unshift(data.design);else items[index]=data.design;
      // Only the selected work keeps its complete conversation in memory.
      for(const item of items)if(item.id!==id){delete item.jobs;delete item.assets;delete item.revisions;}
      paintRail();paintDetail(data.design);
    }catch(error){if(drafts.isCurrent(id,generation))status('读取暂时失败：'+error.message,true);}
    finally{reading=false;}
  }
  async function uploadReferences(id,files){const ids=[];for(const file of files){if(file.size>12*1024*1024)throw new Error('每张图片不能超过 12 MB');
      if(!file.workshopAssetId){const form=new FormData();form.append('file',file);const response=await fetch(`/api/workshop/designs/${id}/references`,{method:'POST',body:form,credentials:'same-origin'});const body=await response.json();if(!response.ok)throw new Error(body.detail||'参考图上传失败');file.workshopAssetId=body.asset.id;}ids.push(file.workshopAssetId);}return ids;}
  async function sendRound(id){
    const draft=drafts.draft(id);if(draft.sending)return;
    const snapshot=drafts.snapshot(id);if(!snapshot.text.trim()){status('请先写下这一轮的想法。',true);return;}
    const stage=mount.querySelector('.studio-composer select').value;draft.sending=true;draft.error='';paintDetail(items.find(item=>item.id===id));
    try{const references=[...snapshot.references,...await uploadReferences(id,snapshot.files)];
      const payload={stage,description:snapshot.text,reference_ids:references},signature=JSON.stringify(payload);
      if(draft.pendingSubmit?.signature!==signature)draft.pendingSubmit={signature,key:crypto.randomUUID()};
      const data=await post(`/api/workshop/designs/${id}/rounds`,{...payload,idempotency_key:draft.pendingSubmit.key});
      draft.pendingSubmit=null;
      drafts.sent(id,snapshot);items[items.findIndex(item=>item.id===id)]=data.design;
      if(selected===id){mount.querySelector('.studio-composer textarea').value=draft.text;mount.querySelector('.studio-composer input[type=file]').value='';paintRefs();paintDetail(data.design);}
    }catch(error){draft.error=error.message;if(selected===id)status(error.message,true);}
    finally{draft.sending=false;if(selected===id){paintDetail(items.find(item=>item.id===id));refreshDetail(id,drafts.generation);}}
  }
  async function act(design,action,revision){
    try{const data=await post(`/api/workshop/designs/${design.id}/actions`,{action,revision_id:revision,idempotency_key:crypto.randomUUID()});
      items[items.findIndex(item=>item.id===design.id)]=data.design;paintDetail(data.design);paintRail();window.AutoDevAppearance?.refresh().catch(()=>{});
    }catch(error){status(error.message,true);toast(error.message);}
  }
  async function retryJob(design,job){try{const data=await post(`/api/workshop/jobs/${job.id}/retry`,{idempotency_key:crypto.randomUUID()});
      items[items.findIndex(item=>item.id===design.id)]=data.design;paintDetail(data.design);
    }catch(error){toast(error.message);}}
  async function previewRevision(design,revision){themes=(await request('/api/ui/themes')).themes;const trial=themes.find(t=>t.design_id===design.id&&t.revision_id===revision.id);if(trial){window.AutoDevAppearance?.previewTheme(trial);componentReview(trial);toast('正在试用制作版本，未保存；确认后才能正式上线。');}}
  function componentReview(theme){
    const panel=document.createElement('dialog');panel.className='studio-component-panel';
    panel.setAttribute('aria-label','真实组件主题检查');
    const icon=name=>window.AutoDevThemeIcons?.markup(name)||'';
    panel.innerHTML=`<header><div><h3>${escape(theme.name)} · 真实组件试用</h3><p>下面是实际运行的组件，不是效果图。可检查悬浮、聚焦、选中和禁用状态。</p></div><button type="button" class="btn btn-secondary" data-close>关闭样例</button></header><div class="studio-component-grid"><section><h4>菜单与图标</h4><button type="button" class="nav-item active">${icon('home')}任务总览</button><button type="button" class="nav-item">${icon('clipboard')}交付记录</button><p class="studio-component-icons">${['cube','chart','user','book','gear'].map(icon).join('')}</p></section><section><h4>按钮与交互</h4><div class="studio-component-buttons"><button type="button" class="btn btn-primary">${icon('plus')}发起研发</button><button type="button" class="btn btn-secondary">查看记录</button><button type="button" class="btn btn-danger">取消任务</button><button type="button" class="btn btn-primary" disabled>处理中</button></div></section><section><h4>表单与焦点</h4><label>需求编号<input type="text" placeholder="点击检查焦点边框" aria-label="组件试用需求编号"></label><label>补充说明<textarea placeholder="这只是样式试用，不会提交业务数据" aria-label="组件试用补充说明"></textarea></label><label><input type="checkbox" checked>选中态</label></section><section><h4>卡片与状态</h4><article class="run-card"><h4>主题组件校验</h4><p>壁纸、菜单、图标、按钮、表单、卡片统一适配。</p><span class="status-tag" data-status="delivered">验证通过</span> <span class="status-tag" data-status="failed">验证未通过</span></article></section></div><footer>这是临时预览。关闭后仍可继续检查工作台；只有你确认后才会上线。</footer>`;
    panel.querySelector('[data-close]').onclick=()=>{panel.close();panel.remove();};
    panel.addEventListener('cancel',()=>panel.remove(),{once:true});document.body.append(panel);panel.showModal();
  }
  async function useTheme(id){try{await window.AutoDevAppearance.useTheme(id);toast('已保存为你的个人壁纸，换浏览器登录也会保留。');}catch(error){toast(error.message);}}
  function paintWallpapers(content,collection){
    content.innerHTML=collection.length?`<div class="studio-wallpapers">${collection.map(t=>`<article class="studio-wallpaper"><div class="studio-wallpaper-art"><img src="${escape(imageUrl(t))}" data-media-source="${escape(t.preview_fallback||t.preview)}" loading="lazy" decoding="async" alt="${escape(t.name)}效果预览">${t.is_custom?`<span class="studio-creator-credit" title="制作者：${escape(t.author||'平台用户')}"><i aria-hidden="true">${escape(Array.from(t.author||'平台用户')[0])}</i><span>由 ${escape(t.author||'平台用户')} 创作</span></span>`:''}</div><div class="studio-wallpaper-copy"><h3>${escape(t.name)}</h3><p>${escape(t.subtitle)}</p><footer><button class="btn btn-secondary" type="button" data-preview-theme="${escape(t.id)}">预览</button><button class="btn btn-primary" type="button" data-use-theme="${escape(t.id)}">使用</button><span>${t.usage?.users||0} 人使用过</span></footer></div></article>`).join('')}</div>`:'<div class="studio-empty-art"><b>这里等待第一片新风景。</b><p>制作者确认并公开的作品，将实时出现在这里。</p></div>';
    content.querySelectorAll('[data-preview-theme]').forEach(button=>button.onclick=()=>window.AutoDevAppearance?.previewTheme(collection.find(t=>t.id===button.dataset.previewTheme)));
    content.querySelectorAll('[data-use-theme]').forEach(button=>button.onclick=()=>useTheme(button.dataset.useTheme));
  }
  async function paintUsage(content){
    content.innerHTML='<p class="muted">正在整理壁纸使用记录…</p>';const current=tab;
    try{const data=await request('/api/workshop/usage');if(tab!==current)return;
      if(current==='ranking')content.innerHTML=`<div class="studio-stats">${data.rankings.map((t,index)=>`<article class="studio-ranking"><em>${String(index+1).padStart(2,'0')}</em><h3>${escape(t.name)}</h3><p>${t.users} 位使用者 · ${t.current} 人正在使用<br>${t.activations} 次选择 · ${t.visits} 个用户访问日</p><button type="button" class="text-button" data-use-theme="${escape(t.id)}">使用这张壁纸 ↗</button></article>`).join('')}</div>`;
      else content.innerHTML=`<div class="studio-history"><table><thead><tr><th>壁纸</th><th>行为</th><th>时间</th></tr></thead><tbody>${data.history.map(event=>`<tr><td>${escape(data.rankings.find(t=>t.id===event.theme_id)?.name||event.theme_id)}</td><td>${{activate:'设为个人壁纸',open:'打开工作台',preview:'预览'}[event.kind]}</td><td>${date(event.created_at)}</td></tr>`).join('')||'<tr><td colspan="3">尚无使用记录</td></tr>'}</tbody></table></div>`;
      content.querySelectorAll('[data-use-theme]').forEach(button=>button.onclick=()=>useTheme(button.dataset.useTheme));
    }catch(error){content.textContent=error.message;}
  }
  async function createDialog(){
    themes=(await request('/api/ui/themes')).themes;
    if(!newDialog){newDialog=document.createElement('dialog');newDialog.className='studio-create';newDialog.setAttribute('aria-labelledby','studio-create-title');
      newDialog.innerHTML=`<header><h2 id="studio-create-title">从你的想法，长出一张壁纸。</h2><button class="studio-close" type="button" aria-label="关闭创作">×</button></header><p>先选择系统主题作为布局基础。上传的参考图和对话默认保密；imagegen 会先生成效果图，等待你确认。</p><form><label>作品名称<input type="text" name="name" maxlength="60" placeholder="例如：雨后书房" required></label><fieldset><legend>基础主题</legend><div class="studio-base-options">${themes.filter(t=>!t.is_custom).map((t,index)=>`<label class="studio-base-option"><img src="${escape(imageUrl(t))}" loading="lazy" alt=""><span><input type="radio" name="base" value="${t.id}" ${index===0?'checked':''}>${escape(t.name)}</span></label>`).join('')}</div></fieldset><label>想做成什么样？<textarea name="description" maxlength="8000" placeholder="描述色彩、氛围、构图、材质，或指出参考图中你喜欢的细节…" required></textarea></label><label>参考图（可选，每轮最多 5 张）<input name="references" type="file" accept="image/png,image/jpeg,image/webp" multiple></label><footer><span class="studio-create-status" role="status">提交后后台生成，离开页面也会保留。</span><button class="btn btn-primary" type="submit">提交设计 ↗</button></footer></form>`;
      document.body.append(newDialog);newDialog.querySelector('.studio-close').onclick=()=>{if(!newState.sending)newDialog.close();};
      newDialog.addEventListener('cancel',event=>{if(newState.sending)event.preventDefault();});
      newDialog.querySelector('form').onsubmit=async event=>{
        event.preventDefault();if(newState.sending)return;const form=event.currentTarget,button=form.querySelector('[type=submit]'),hint=form.querySelector('.studio-create-status');
        const files=[...form.elements.references.files];if(files.length>5){hint.textContent='每轮最多 5 张参考图。';return;}
        newState.sending=true;button.disabled=true;
        try{if(!newState.id){hint.textContent='正在保存草稿…';newState.id=(await post('/api/workshop',{name:form.elements.name.value,base_theme_id:form.elements.base.value,idempotency_key:newState.key})).design.id;}
          hint.textContent='正在上传参考图片…';const references=await uploadReferences(newState.id,files);
          const data=await post(`/api/workshop/designs/${newState.id}/rounds`,{stage:'design',description:form.elements.description.value,reference_ids:references,idempotency_key:newState.key+'-round'});
          items.unshift(data.design);selected=data.design.id;newDialog.close();newDialog.remove();newDialog=null;newState={key:crypto.randomUUID(),id:null,sending:false};tab='mine';paintContent();
        }catch(error){hint.textContent=error.message;hint.style.color='var(--skin-danger)';}
        finally{newState.sending=false;button.disabled=false;}
      };
    }newDialog.showModal();
  }
  async function open(){
    if(!mount.querySelector('.studio'))shell();
    try{const [data,catalog]=await Promise.all([request('/api/workshop'),request('/api/ui/themes')]);items=data.items;themes=catalog.themes;
      const requested=new URLSearchParams(location.search).get('workshop');
      if(requested){if(!items.some(item=>item.id===requested)){const detail=(await request('/api/workshop/designs/'+encodeURIComponent(requested))).design;items.unshift(detail);}selected=requested;}
      if(new URLSearchParams(location.search).has('workshop-public'))tab='public';
      paintContent();
      if(!mount.querySelector('[data-mail-preference]')){const label=document.createElement('label');label.className='studio-mail-preference';
        label.innerHTML=`<input type="checkbox" data-mail-preference ${data.recommendations_enabled?'checked':''}> 接收公开壁纸推荐邮件`;
        label.querySelector('input').onchange=async event=>{try{await request('/api/workshop/mail-preference',{method:'PUT',body:JSON.stringify({enabled:event.target.checked})});}catch(error){event.target.checked=!event.target.checked;toast(error.message);}};mount.querySelector('.studio').append(label);}
    }catch(error){mount.querySelector('.studio-content').textContent='暂时无法读取工坊：'+error.message;}
    if(!timer)timer=setInterval(()=>{if(document.hidden||document.body.dataset.view!=='workshop')return;
      if(tab==='mine'&&selected){const item=items.find(i=>i.id===selected);if(item?.jobs?.some(j=>['queued','running'].includes(j.status)))refreshDetail(selected,drafts.generation);}
    },3000);
  }
  addEventListener('pagehide',()=>{clearInterval(timer);drafts.select(null);});
  function suspend(){clearInterval(timer);timer=null;drafts.select(null);mount.replaceChildren();for(const item of items){delete item.jobs;delete item.assets;delete item.revisions;}}
  document.addEventListener('autodev:view',event=>{if(event.detail!=='workshop')suspend();});
  if(['workshop','workshop-public'].some(key=>new URLSearchParams(location.search).has(key)))setTimeout(()=>window.switchView?.('workshop'),0);
  return {open,select,get drafts(){return drafts;}};
})();
