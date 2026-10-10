/* Persisted conversations. Polls never replace the composer or its draft. */
window.RequestFollowups = (() => {
  'use strict';
  const sessions = new Map();
  let current = null, timer = null, generation = 0;
  const labels = {queued:'等待执行器回答',running:'正在核对代码',completed:'已回答',failed:'回答未完成'};
  const uuid = () => crypto.randomUUID();
  function markdown(target, text) {
    target.replaceChildren();
    if (!window.marked || !window.DOMPurify) { target.textContent = text; return; }
    target.innerHTML = DOMPurify.sanitize(marked.parse(String(text || ''), {gfm:true, breaks:false}), {
      USE_PROFILES:{html:true}, FORBID_TAGS:['style','form','input','button','iframe','video','audio','img'],
      FORBID_ATTR:['style','id','name'],
    });
    target.querySelectorAll('a').forEach(link => {
      const raw = link.getAttribute('href') || '';
      if (!/^(https?:\/\/|mailto:|#|\/(?!\/))/i.test(raw)) link.removeAttribute('href');
      else { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    });
    target.querySelectorAll('table').forEach(table => {
      const wrap = document.createElement('div'); wrap.className = 'followup-table-scroll';
      table.replaceWith(wrap); wrap.append(table);
    });
  }
  function elapsed(item) {
    let seconds = item.elapsed_seconds || 0;
    if (item.started_at && item.status === 'running') seconds = Math.max(0,(Date.now()-Date.parse(item.started_at))/1000);
    return seconds >= 60 ? `${Math.floor(seconds/60)} 分 ${Math.floor(seconds%60)} 秒` : `${Math.floor(seconds)} 秒`;
  }
  function paint(session) {
    const root = document.querySelector('#task-followup-mount');
    if (!root || root.dataset.requestId !== session.id) return;
    const chat = root.querySelector('.followup-messages');
    const panel = chat;
    const bottom = panel.scrollHeight - panel.clientHeight - panel.scrollTop < 70;
    const signature = JSON.stringify(session.items);
    if (chat.dataset.signature !== signature) {
      chat.dataset.signature = signature;
      chat.replaceChildren();
      if (!session.items.length) chat.innerHTML = '<div class="followup-empty"><span class="followup-orb" aria-hidden="true"></span><h3>关于这次交付，还想了解什么？</h3><p>可以询问实现逻辑、数据保存方式、影响范围或使用方法。</p></div>';
      session.items.forEach(item => {
        const question = document.createElement('article'); question.className = 'followup-message user';
        const meta = document.createElement('div'); meta.className = 'followup-meta';
        meta.textContent = `${item.actor_name || '提问'} · ${new Date(item.created_at).toLocaleString('zh-CN',{hour12:false})}`;
        const text = document.createElement('p'); text.textContent = item.question; question.append(meta,text);
        const answer = document.createElement('article'); answer.className = `followup-message assistant ${item.status}`;
        const head = document.createElement('div'); head.className = 'followup-meta';
        const name = document.createElement('b'); name.textContent = 'AutoDev';
        const time = document.createElement('span'); time.dataset.followupTime = item.id;
        time.textContent = `${labels[item.status]}${item.started_at ? ` · 耗时 ${elapsed(item)}` : ''}`;
        head.append(name,time); answer.append(head);
        const body = document.createElement('div'); body.className = 'followup-markdown';
        if (item.status === 'completed') markdown(body,item.answer);
        else if (item.status === 'failed') {
          body.textContent = item.error_message || '回答失败，记录已保留。';
          const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'text-button';
          retry.textContent = '重新提问 ↗';
          retry.onclick = () => { session.draft = item.question; root.querySelector('textarea').value = session.draft; root.querySelector('textarea').focus(); };
          answer.append(retry);
        } else {
          body.classList.add('followup-pending');
          body.textContent = item.status === 'queued' && !session.online ? '执行器当前离线，问题已保存，上线后自动回答。' : item.progress || labels[item.status];
        }
        answer.append(body); chat.append(question,answer);
      });
      if (bottom && !session.firstPaint) requestAnimationFrame(() => { if (panel.isConnected) panel.scrollTop = panel.scrollHeight; });
      session.firstPaint = false;
    }
    const busy = session.items.some(item => ['queued','running'].includes(item.status));
    root.querySelector('[type=submit]').disabled = busy || session.sending;
    root.querySelector('.followup-composer-status').textContent = session.error || session.readError || (busy ? '回答完成后可以继续追问；当前输入会保留。' : '仅解释已交付内容，不修改代码或触发发版。');
  }
  async function refresh(session, token) {
    if (session.loading) return;
    session.loading = true;
    try {
      const data = await api(`/api/requests/${session.id}/followups`);
      if (token !== generation || current !== session.id) return;
      session.items = data.items || []; session.online = data.runner_online; session.readError = '';
      paint(session);
    } catch (error) {
      if (token === generation) { session.readError = `读取失败：${error.message}，正在重试。`; paint(session); }
    } finally { session.loading = false; }
  }
  function stop() { clearInterval(timer); timer = null; current = null; generation++; }
  function mount(detail) {
    const root = document.querySelector('#task-followup-mount');
    if (!root) { if (current) stop(); return; }
    let session = sessions.get(detail.id);
    if (!session) { session = {id:detail.id,items:[],draft:'',online:true,firstPaint:true,error:'',sending:false,loading:false}; sessions.set(detail.id,session); }
    if (current !== detail.id) { stop(); current = detail.id; }
    const token = generation;
    root.dataset.requestId = detail.id;
    root.innerHTML = '<section class="followup-workspace"><header class="followup-heading"><h3>交付之后，继续聊聊</h3><span>只读答疑 · 记录长期保存</span></header><div class="followup-messages" role="log" aria-label="需求追问记录"></div><form class="followup-composer"><label class="sr-only" for="followup-question">追问内容</label><textarea id="followup-question" rows="3" maxlength="6000" placeholder="询问这次交付的实现细节…" required></textarea><div><span class="followup-composer-status" role="status"></span><button type="submit" class="btn btn-primary">发送追问 ↑</button></div></form></section>';
    const input = root.querySelector('textarea'); input.value = session.draft;
    const chat = root.querySelector('.followup-messages');
    chat.onscroll = () => { session.scroll = chat.scrollTop; };
    input.oninput = () => { session.draft = input.value; session.submitKey = null; session.error = ''; };
    const form = root.querySelector('form');
    form.onsubmit = async event => {
      event.preventDefault();
      const question = session.draft.trim();
      if (!question || session.sending || session.items.some(item => ['queued','running'].includes(item.status))) return;
      session.sending = true; session.error = ''; paint(session);
      const key = session.submitKey || (session.submitKey = uuid());
      try {
        const {item} = await api(`/api/requests/${session.id}/followups`, {method:'POST',body:JSON.stringify({question,idempotency_key:key})});
        if (!session.items.some(old => old.id === item.id)) session.items.push(item);
        if (session.draft.trim() === question) { session.draft = ''; if (root.isConnected) input.value = ''; }
        session.submitKey = null;
      } catch(error) { session.error = error.message; }
      finally { session.sending = false; if (current === session.id) { paint(session); refresh(session,generation); } }
    };
    input.onkeydown = event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); form.requestSubmit(); } };
    paint(session); chat.scrollTop = session.scroll || 0; refresh(session,token);
    if (!timer) timer = setInterval(() => {
      if (!document.querySelector('#detail-drawer.open') || !document.querySelector('#task-followup-mount')) { stop(); return; }
      const active = sessions.get(current);
      const visible = !document.querySelector('#task-panel-followup')?.hidden;
      if (visible || active.items.some(item => ['queued','running'].includes(item.status))) refresh(active,generation);
      if (visible) active.items.forEach(item => { const label = document.querySelector(`[data-followup-time="${item.id}"]`); if (label && item.status === 'running') label.textContent = `${labels[item.status]} · 耗时 ${elapsed(item)}`; });
    },3000);
  }
  return {mount,stop,markdown};
})();
