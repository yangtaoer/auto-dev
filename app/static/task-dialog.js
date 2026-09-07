/* One task, one workspace. Tab and draft state survive background polling. */
window.TaskDialog = (() => {
  const tabs = new Map(), scrolls = new Map();
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
    dialog().inert = true;
    document.body.classList.remove('task-dialog-open');
    document.querySelector('.app-shell').inert = false;
    (returnFocus?.isConnected ? returnFocus : document.querySelector('.nav-item.active'))?.focus({preventScroll: true});
    returnFocus = null;
    requestId = null;
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
    requestId = id;
    const selected = tabs.get(id) || 'overview';
    const nav = panels.map(([key, label], index) => `<button type="button" id="task-tab-${key}" role="tab" data-task-tab="${key}" aria-controls="task-panel-${key}" aria-selected="${key === selected}" tabindex="${key === selected ? 0 : -1}"><small>${String(index + 1).padStart(2, '0')}</small>${label}</button>`).join('');
    return `${head}<nav class="task-tabs" role="tablist" aria-label="任务流程">${nav}</nav><div class="task-tab-body">${panels.map(([key, , content]) => `<section class="task-tab-panel" id="task-panel-${key}" role="tabpanel" data-panel="${key}" aria-labelledby="task-tab-${key}" tabindex="0" ${key === selected ? '' : 'hidden'}>${content}</section>`).join('')}</div>`;
  }
  function bind() {
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
  // Handle the topmost overlay only; artifact preview and live output keep their own controls.
  document.addEventListener('keydown', event => {
    const root = dialog();
    if (!root?.classList.contains('open')) return;
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
      const focusable = [...root.querySelectorAll('button:not([disabled]),a[href],input,textarea,select,[tabindex="0"]')].filter(node => node.getClientRects().length && !node.closest('[hidden]') && node.tabIndex >= 0);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    }
  }, true);
  return {open, close, render, bind, select};
})();
