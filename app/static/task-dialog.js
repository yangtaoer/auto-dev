/* One task, one workspace. Tab and draft state survive background polling. */
window.TaskDialog = (() => {
  const tabs = new Map(), scrolls = new Map(), openSections = new Map(), stageVisits = new Map();
  let requestId = null, returnFocus = null, archivedReplies = [];
  const dialog = () => document.querySelector('#detail-drawer');
  function open() {
    const root = dialog();
    if (!root.classList.contains('open')) returnFocus = document.activeElement;
    root.inert = false;
    document.body.classList.add('task-dialog-open');
    document.querySelector('.app-shell').inert = true;
    requestAnimationFrame(() => { if (root.classList.contains('open')) root.querySelector('#close-detail').focus({preventScroll: true}); });
  }
  function close() {
    rememberScroll();
    dialog().querySelector('.workflow-actions[open]')?.removeAttribute('open');
    rememberSections();
    dialog().inert = true;
    document.body.classList.remove('task-dialog-open');
    document.querySelector('.app-shell').inert = false;
    (returnFocus?.isConnected ? returnFocus : document.querySelector('.nav-item.active'))?.focus({preventScroll: true});
    returnFocus = null;
    requestId = null;
    // Remember small navigation state, release the closed task's entire DOM.
    dialog().querySelector('#detail-content')?.replaceChildren();
    while (tabs.size > 30) {
      const id = tabs.keys().next().value;
      tabs.delete(id);
      openSections.delete(id);
      stageVisits.delete(id);
      for (const key of scrolls.keys()) if (key.startsWith(id + ':')) scrolls.delete(key);
    }
  }
  function rememberScroll() {
    const panel = dialog().querySelector('.task-tab-panel:not([hidden])');
    if (panel && requestId) scrolls.set(`${requestId}:${panel.dataset.panel}`, panel.scrollTop);
  }
  function select(id, focus = false) {
    const root = dialog();
    if (!root.querySelector(`[data-panel="${id}"]`)) return;
    root.querySelector('.workflow-actions[open]')?.removeAttribute('open');
    rememberScroll();
    tabs.set(requestId, id);
    root.querySelectorAll('[data-task-tab]').forEach(button => {
      const selected = button.dataset.taskTab === id;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus({preventScroll: true});
      if (selected) button.scrollIntoView({block: 'nearest', inline: 'nearest'});
    });
    root.querySelectorAll('[data-panel]').forEach(panel => {
      panel.hidden = panel.dataset.panel !== id;
      if (!panel.hidden) panel.scrollTop = scrolls.get(`${requestId}:${id}`) || 0;
    });
    document.dispatchEvent(new CustomEvent('autodev:task-stage', {detail:{requestId, stage:id}}));
  }
  function render(id, head, panels, nodes = [], defaultPanel = 'overview', brief = '') {
    rememberScroll();
    rememberSections();
    requestId = id;
    const available = new Map(panels.map(([key, label]) => [key,label]));
    const selected = [tabs.get(id), defaultPanel, panels[0]?.[0]].find(key => available.has(key));
    const active = nodes.find(node => node.active);
    const changed = !!active && stageVisits.get(id) !== active.id;
    stageVisits.set(id, active?.id || '');
    const button = (key, body, attributes = '') => `<button type="button" id="task-tab-${key}" role="tab" data-task-tab="${key}" aria-controls="task-panel-${key}" aria-selected="${key === selected}" tabindex="${key === selected ? 0 : -1}" ${attributes}>${body}</button>`;
    const shortcuts = ['overview','followup'].filter(key => available.has(key)).map(key => button(key, escapeHtml(available.get(key)))).join('');
    const route = nodes.filter(node=>available.has(node.id)).map(node => {
      const current = node.active && node.state === 'running';
      const marker = current ? `<span class="workflow-orb" ${changed?'data-arrived':''}>${TaskWorkflow.asset('current-stage',32)}</span>` : editorialIcon(node.state === 'done' ? 'check' : node.icon);
      const {started, elapsed} = TaskWorkflow.timingLabels(node.timing);
      const startTime = started === '—' ? started : `<time datetime="${escapeHtml(node.timing.started_at)}" title="北京时间">${escapeHtml(started)}</time>`;
      const basis = [node.timing?.basis, node.timing?.running ? '截至本次刷新' : '',
        elapsed === '—' && node.state !== 'pending' && node.state !== 'skipped' ? '缺少完整计时记录' : ''].filter(Boolean).join('，');
      const timing = `<small class="workflow-node-timing" title="${escapeHtml(basis)}">
        <span>开始 ${startTime}</span><span>耗时 <b>${escapeHtml(elapsed)}</b></span></small>`;
      return button(node.id, `<span class="workflow-marker" aria-hidden="true">${marker}</span><b>${escapeHtml(node.label)}</b><small>${TaskWorkflow.STATES[node.state]}</small>${timing}`,
        `class="workflow-node" data-phase="${node.state}" ${node.active?'aria-current="step"':''}`);
    }).join('');
    const nav = `<nav class="workflow-nav" role="tablist" aria-label="任务流程">
      <div class="workflow-nav-head"><h3>任务流程</h3><div class="workflow-shortcuts">${shortcuts}</div></div>
      <div class="workflow-rail" style="--phase-count:${Math.max(1,nodes.length)}">${route}</div>
    </nav>`;
    return `${head}${brief}${nav}<div class="task-tab-body">${panels.map(([key, , content]) => `<section class="task-tab-panel" id="task-panel-${key}" role="tabpanel" data-panel="${key}" aria-labelledby="task-tab-${key}" tabindex="0" ${key === selected ? '' : 'hidden'}>${content}</section>`).join('')}</div>`;
  }
  function bind() {
    dialog().querySelectorAll('[data-task-disclosure]').forEach(details => {
      details.open = openSections.get(requestId)?.has(details.dataset.taskDisclosure) || false;
    });
    const archive = dialog().querySelector('[data-reply-history]');
    if (archive) {
      const paint = () => { if (archive.open) archive.querySelector('.task-replies').innerHTML = archivedReplies.map(replyCard).join(''); };
      archive.addEventListener('toggle',paint,{once:!archive.open});
      paint();
    }
    dialog().querySelectorAll('[data-task-tab]').forEach(button => {
      button.onclick = () => select(button.dataset.taskTab);
      button.onkeydown = event => {
        const buttons = [...dialog().querySelectorAll('[data-task-tab]')], index = buttons.indexOf(button);
        const next = event.key === 'ArrowRight' ? (index + 1) % buttons.length : event.key === 'ArrowLeft' ? (index + buttons.length - 1) % buttons.length : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
        if (next >= 0) { event.preventDefault(); select(buttons[next].dataset.taskTab, true); }
      };
    });
    const panel = dialog().querySelector('.task-tab-panel:not([hidden])');
    if (panel) panel.scrollTop = scrolls.get(`${requestId}:${panel.dataset.panel}`) || 0;
    dialog().querySelector('[aria-selected="true"]')?.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  function rememberSections() {
    if (!requestId) return;
    const opened = [...dialog().querySelectorAll('[data-task-disclosure][open]')];
    if (opened.length) openSections.set(requestId, new Set(opened.map(node => node.dataset.taskDisclosure)));
    else openSections.delete(requestId);
  }

  function replyMarkup(text) {
    if (!window.marked || !window.DOMPurify) return `<p>${escapeHtml(text)}</p>`;
    return window.DOMPurify.sanitize(window.marked.parse(String(text || ''), {gfm: true, breaks: true}), {
      ALLOWED_TAGS: ['p','br','ol','ul','li','pre','code','strong','em','blockquote','h1','h2','h3','h4','hr','table','thead','tbody','tr','th','td'],
      ALLOWED_ATTR: [],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
    });
  }

  function repositoryPlan(detail) {
    const plan = detail.repository_plan || {};
    const entries = Array.isArray(plan.repositories) ? plan.repositories : [];
    if (!plan.summary && !entries.length) return '';
    const list = entries.length ? `<ul class="task-plan-repositories">${entries.map(repo => `
      <li><code>${escapeHtml(repo.name)}</code>${repo.summary ? `<p>${escapeHtml(repo.summary)}</p>` : ''}</li>`).join('')}</ul>` : '';
    return `<section class="task-data-panel task-repository-plan">
      <h3>${editorialIcon('code')}预计改动的 Git 仓库</h3>
      ${plan.summary?`<p class="task-plan-summary">${escapeHtml(plan.summary)}</p>`:''}${list}
    </section>`;
  }

  function changedFiles(repositories) {
    const changed = (repositories || []).filter(repo => Array.isArray(repo.changed_files) && repo.changed_files.length);
    if (!changed.length) return '';
    const count = changed.reduce((total, repo) => total + repo.changed_files.length, 0);
    return `<details class="task-data-panel task-changed-files" data-task-disclosure="changed-files">
      <summary><b>实际改动文件</b><span>${changed.length} 个仓库 · ${count} 个文件</span></summary>
      <div class="task-file-repositories">${changed.map(repo => `<article>
        <h4><code>${escapeHtml(repo.name || repo.repository_short_name || '代码仓库')}</code><span>${repo.changed_files.length}</span></h4>
        <ul>${repo.changed_files.map(path => `<li><code>${escapeHtml(path)}</code></li>`).join('')}</ul>
      </article>`).join('')}</div>
    </details>`;
  }

  function replyCard(reply) {
    const labels = {'devcore.reply': 'AI 回复', 'devcore.summary': '本轮总结', 'workspace.plan_recorded': '需求拆解'};
    return `<article class="task-reply" data-kind="${escapeHtml(reply.event_type)}">
      <header><b>${labels[reply.event_type] || 'AI 回复'}</b><time datetime="${escapeHtml(reply.created_at || '')}">${fmt(reply.created_at)}</time></header>
      <div class="task-reply-body">${replyMarkup(reply.message)}</div>
    </article>`;
  }
  function conversation(detail, live) {
    const replies = Array.isArray(detail.assistant_replies) ? [...detail.assistant_replies] : [];
    if (detail.result_summary && !replies.some(reply => reply.event_type === 'devcore.summary')) {
      replies.push({event_type: 'devcore.summary', message: detail.result_summary,
                    created_at: detail.completed_at || detail.updated_at});
    }
    const current = detail.status === 'delivered' ? replies.slice(-1) : replies.slice(-2);
    archivedReplies = replies.slice(0,replies.length-current.length);
    return `<section class="task-data-panel task-conversation">
      <header><h3>${editorialIcon('message')}AI 回复与总结</h3>${live}</header>
      ${current.length ? `<div class="task-replies">${current.map(replyCard).join('')}</div>` : ''}
      ${archivedReplies.length ? `<details class="task-reply-history" data-task-disclosure="reply-history" data-reply-history><summary>较早回复 · ${archivedReplies.length} 条</summary><div class="task-replies"></div></details>` : ''}
    </section>`;
  }
  document.addEventListener('click', event => {
    const actions = dialog()?.querySelector('.workflow-actions[open]');
    if(actions && !actions.contains(event.target)) actions.open=false;
  }, true);

  // Handle the topmost overlay only; artifact preview and live output keep their own controls.
  document.addEventListener('keydown', event => {
    const root = dialog();
    if (!root?.classList.contains('open')) return;
    if (document.querySelector('#task-action-confirm[open]')) return;
    const overlay = [...document.querySelectorAll('#artifact-preview,#experience-modal')].some(node => !node.hidden);
    const stream = document.querySelector('#devcore-stream-panel.open');
    if (overlay || stream) {
      if (event.key === 'Escape' && stream && !overlay) { event.stopImmediatePropagation(); closeDevCoreStream(); }
      return;
    }
    if (event.key === 'Escape') {
      const actions = root.querySelector('.workflow-actions[open]');
      if(actions){
        event.stopImmediatePropagation();
        actions.open=false;
        actions.querySelector('summary').focus({preventScroll:true});
        return;
      }
      const popover = root.querySelector('.ledger-select-menu:not([hidden])');
      if (popover) { closeLedgerPopovers(); return; }
      event.stopImmediatePropagation(); closeDetail();
    }
    if (event.key === 'Tab') {
      const focusable = [...root.querySelectorAll('button:not([disabled]),a[href],input,textarea,select,summary,[tabindex="0"]')].filter(node => node.getClientRects().length && !node.closest('[hidden]') && node.tabIndex >= 0);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    }
  }, true);
  return {open, close, render, bind, select, repositoryPlan, changedFiles, conversation, replyMarkup};
})();
