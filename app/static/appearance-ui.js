import {AppearanceSession,DEFAULT_APPEARANCE,normalizeAppearance,selectedTheme} from './theme-core.js?v=1.0-Beta.8';

const root=document.documentElement,trigger=document.getElementById('open-appearance');
const authenticated=Boolean(window.__USER__?.id);
let dialog,session,themes=[],returnFocus=null,appliedTokenKeys=[];
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function apply(appearance,theme){
  if(!theme)return;
  root.dataset.theme=theme.id;root.dataset.themeMode=theme.mode;
  for(const key of ['font_size','density','motion'])root.dataset[key.replace(/_([a-z])/g,(_,c)=>c.toUpperCase())]=appearance[key];
  for(const key of appliedTokenKeys)if(!(key in theme.tokens))root.style.removeProperty('--'+key);
  for(const [key,value]of Object.entries(theme.tokens))root.style.setProperty('--'+key,value);
  appliedTokenKeys=Object.keys(theme.tokens);
  window.__THEME__=theme;
  const name=document.getElementById('appearance-current-name');if(name)name.textContent=theme.name;
  const edition=document.getElementById('sidebar-theme-name');if(edition)edition.textContent=theme.number+' · '+theme.name;
  const motto=document.getElementById('theme-scene-note');if(motto)motto.textContent=theme.motto||'让想法，\n更快落地。';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme.tokens['skin-bg']);
  document.dispatchEvent(new CustomEvent('autodev:appearance',{detail:{appearance:{...appearance},theme}}));
  if(dialog){dialog.querySelectorAll('[data-theme-choice]').forEach(button=>{const selected=button.dataset.themeChoice===theme.id;button.setAttribute('aria-pressed',String(selected));button.classList.toggle('selected',selected);});}
}
async function request(url,options={}){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{const response=await fetch(url,{...options,signal:controller.signal,credentials:'same-origin',headers:{'Content-Type':'application/json',...options.headers}});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'无法保存外观，请稍后重试');return result;
  }catch(error){if(error.name==='AbortError')throw new Error('连接超时，尚未保存，请重试');throw error;}
  finally{clearTimeout(timeout);}
}
function rememberHint(id){document.cookie=`autodev_theme=${encodeURIComponent(id)};path=/;max-age=31536000;samesite=strict${location.protocol==='https:'?';secure':''}`;}
function makeDialog(){
  dialog=document.createElement('dialog');dialog.className='appearance-dialog';dialog.setAttribute('aria-labelledby','appearance-title');
  dialog.innerHTML=`<form method="dialog" class="appearance-form"><header><div><p class="appearance-kicker">YOUR WORKSPACE / PERSONAL APPEARANCE</p><h2 id="appearance-title">让工作台，像你喜欢的样子。</h2><p>${authenticated?'选择后绑定当前账号，换浏览器登录也会保留。':'登录页选择仅保存在当前浏览器，登录后恢复账号主题。'}</p></div><button type="button" class="appearance-close" data-appearance-cancel aria-label="关闭外观设置">×</button></header><div class="appearance-scroll"><div class="appearance-gallery">${themes.map(t=>`<button type="button" class="theme-choice" data-theme-choice="${escape(t.id)}" aria-pressed="false"><span class="theme-preview"><img src="${escape(t.preview)}" alt="${escape(t.name)}工作台设计" loading="lazy" width="1672" height="940"><span class="theme-check" aria-hidden="true">✓</span></span><span class="theme-choice-copy"><b><small>${escape(t.number)}</small>${escape(t.name)}</b><span>${escape(t.subtitle)}</span><i class="theme-swatches" aria-hidden="true">${['skin-sidebar','skin-bg','skin-ink','skin-accent'].map(k=>`<i style="background:${escape(t.tokens[k])}"></i>`).join('')}</i></span></button>`).join('')}</div><div class="appearance-options">${[
    ['font_size','文字大小',[['normal','标准'],['large','稍大']]],
    ['density','信息密度',[['compact','紧凑'],['comfortable','舒适']]],
    ['motion','环境动效',[['full','生动'],['reduced','轻柔'],['static','静止']]],
  ].map(([key,label,values])=>`<fieldset><legend>${label}</legend><div>${values.map(([value,name])=>`<label><input type="radio" name="${key}" value="${value}"><span>${name}</span></label>`).join('')}</div></fieldset>`).join('')}</div></div><footer><button type="button" class="text-button" id="appearance-reset">恢复默认</button><p id="appearance-message" role="status" aria-live="polite">点击卡片即时预览，保存后生效。</p><button type="button" class="btn btn-secondary" data-appearance-cancel>取消</button><button class="btn btn-primary" id="appearance-save" type="submit">${authenticated?'保存个人外观':'保存登录页外观'}</button></footer></form>`;
  document.body.append(dialog);
  dialog.querySelectorAll('[data-theme-choice]').forEach(button=>button.addEventListener('click',()=>{session.change({theme_id:button.dataset.themeChoice});message('正在预览 '+selectedTheme(session.draft.theme_id,themes).name+'，尚未保存。');}));
  dialog.querySelectorAll('input[type=radio]').forEach(input=>input.addEventListener('change',()=>session.change({[input.name]:input.value})));
  dialog.querySelector('#appearance-reset').addEventListener('click',()=>{session.change(DEFAULT_APPEARANCE);syncOptions();message('正在预览默认外观，保存后生效。');});
  dialog.querySelectorAll('[data-appearance-cancel]').forEach(button=>button.addEventListener('click',cancel));
  dialog.addEventListener('cancel',event=>{event.preventDefault();cancel();});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)cancel();}});
  dialog.querySelector('form').addEventListener('submit',async event=>{
    event.preventDefault();if(session.busy)return;
    busy(true);message('正在保存到'+(authenticated?'你的账号':'当前浏览器')+'…');
    try{if(await session.save()){window.__APPEARANCE__={...session.saved};dialog.close();returnFocus?.focus();}}
    catch(error){message(error.message||'保存失败，原账号设置未改变。',true);}
    finally{busy(false);}
  });
}
function message(text,error=false){const node=dialog?.querySelector('#appearance-message');if(node){node.textContent=text;node.classList.toggle('is-error',error);}}
function syncOptions(){dialog.querySelectorAll('input[type=radio]').forEach(input=>{input.checked=session.draft[input.name]===input.value;});apply(session.draft,selectedTheme(session.draft.theme_id,themes));}
function busy(value){dialog.querySelectorAll('button,input').forEach(node=>node.disabled=value);dialog.setAttribute('aria-busy',String(value));}
function cancel(){if(!session?.cancel())return;dialog.close();returnFocus?.focus();}
async function open(){
  if(!session||session.busy)return;
  if(authenticated){trigger.disabled=true;try{const response=await request('/api/me/appearance');session.saved=normalizeAppearance(response.appearance,themes);}catch(error){trigger.title=error.message;}finally{trigger.disabled=false;}}
  if(!session.begin())return;
  if(!dialog)makeDialog();returnFocus=document.activeElement;syncOptions();message('点击卡片即时预览，保存后生效。');dialog.showModal();
}
if(trigger){
  try{
    const url=new URL('./themes/catalog.json',import.meta.url);url.search=new URL(import.meta.url).search;
    const catalog=await request(url);themes=catalog.themes;
    session=new AppearanceSession({themes,saved:window.__APPEARANCE__,render:apply,persist:async value=>{
      if(authenticated)return(await request('/api/me/appearance',{method:'PUT',body:JSON.stringify(value)})).appearance;
      rememberHint(value.theme_id);return value;
    }});
    apply(session.saved,selectedTheme(session.saved.theme_id,themes));trigger.addEventListener('click',open);
    window.AutoDevAppearance={open,get current(){return{...session.saved};},get preview(){return{...session.draft};}};
  }catch(error){trigger.disabled=true;trigger.title='暂时无法读取主题目录：'+error.message;}
}
