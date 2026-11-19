/* One task, one workspace. Tab and draft state survive background polling. */
window.TaskDialog = (() => {
  const tabs = new Map(), scrolls = new Map(), openSections = new Map();
  let requestId = null, returnFocus = null;
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
  }
  function render(id, head, panels) {
    rememberScroll();
    rememberSections();
    requestId = id;
    const selected = tabs.get(id) || 'overview';
    const icons = {overview:'clipboard',requirement:'layers',development:'code',delivery:'cube',acceptance:'check',followup:'message'};
    const nav = panels.map(([key, label], index) => `<button type="button" id="task-tab-${key}" role="tab" data-task-tab="${key}" aria-controls="task-panel-${key}" aria-selected="${key === selected}" tabindex="${key === selected ? 0 : -1}"><small>${String(index + 1).padStart(2, '0')}</small>${editorialIcon(icons[key])}${label}</button>`).join('');
    return `${head}<nav class="task-tabs" role="tablist" aria-label="任务流程">${nav}</nav><div class="task-tab-body">${panels.map(([key, , content]) => `<section class="task-tab-panel" id="task-panel-${key}" role="tabpanel" data-panel="${key}" aria-labelledby="task-tab-${key}" tabindex="0" ${key === selected ? '' : 'hidden'}>${content}</section>`).join('')}</div>`;
  }
  function bind() {
    dialog().querySelectorAll('[data-task-disclosure]').forEach(details => {
      details.open = openSections.get(requestId)?.has(details.dataset.taskDisclosure) || false;
    });
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
    const preparing = ['queued', 'validating', 'developing', 'waiting_retry'].includes(detail.status) && !detail.result_summary;
    const message = plan.summary || (preparing ? '拆解完成后显示预计修改的仓库。' : '这项任务没有已保存的仓库拆解记录。');
    const list = entries.length ? `<ul class="task-plan-repositories">${entries.map(repo => `
      <li><code>${escapeHtml(repo.name)}</code>${repo.summary ? `<p>${escapeHtml(repo.summary)}</p>` : ''}</li>`).join('')}</ul>` : '';
    return `<section class="task-data-panel task-repository-plan">
      <h3>${editorialIcon('code')}预计改动的 Git 仓库</h3>
      <p class="task-plan-summary">${escapeHtml(message)}</p>${list}
    </section>`;
  }

  function changedFiles(repositories) {
    const changed = (repositories || []).filter(repo => Array.isArray(repo.changed_files) && repo.changed_files.length);
    if (!changed.length) return '';
    const count = changed.reduce((total, repo) => total + repo.changed_files.length, 0);
    return `<section class="task-data-panel task-changed-files">
      <header><h3>${editorialIcon('code')}实际改动文件</h3><span>${changed.length} 个仓库 · ${count} 个文件</span></header>
      <div class="task-file-repositories">${changed.map(repo => `<article>
        <h4><code>${escapeHtml(repo.name || repo.repository_short_name || '代码仓库')}</code><span>${repo.changed_files.length}</span></h4>
        <ul>${repo.changed_files.map(path => `<li><code>${escapeHtml(path)}</code></li>`).join('')}</ul>
      </article>`).join('')}</div>
    </section>`;
  }

  function conversation(detail, live) {
    const replies = Array.isArray(detail.assistant_replies) ? [...detail.assistant_replies] : [];
    if (detail.result_summary && !replies.some(reply => reply.event_type === 'devcore.summary')) {
      replies.push({event_type: 'devcore.summary', message: detail.result_summary,
                    created_at: detail.completed_at || detail.updated_at});
    }
    const labels = {'devcore.reply': 'AI 回复', 'devcore.summary': '本轮总结', 'workspace.plan_recorded': '需求拆解'};
    const items = replies.map(reply => `<article class="task-reply" data-kind="${escapeHtml(reply.event_type)}">
      <header><b>${labels[reply.event_type] || 'AI 回复'}</b><time datetime="${escapeHtml(reply.created_at || '')}">${fmt(reply.created_at)}</time></header>
      <div class="task-reply-body">${replyMarkup(reply.message)}</div>
    </article>`).join('');
    return `<section class="task-data-panel task-conversation">
      <header><h3>${editorialIcon('message')}AI 回复与总结</h3>${live}</header>
      ${items ? `<div class="task-replies">${items}</div>` : '<p class="muted">暂无 AI 回复。</p>'}
    </section>`;
  }
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
