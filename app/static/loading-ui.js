/* A tiny, DOM-preserving loading layer. Background polling deliberately never enters it. */
(() => {
  'use strict';
  const requests = new WeakMap();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const orb = '<span class="loading-orb-scene" aria-hidden="true"><i class="loading-orb-shadow"></i><i class="loading-orb-leaf"></i><span class="loading-orb"><i></i><i></i></span></span>';
  const bar = (width = '100%') => `<i class="loading-bone" style="--bone-width:${width}"></i>`;
  function skeleton(kind) {
    const row = `<div class="loading-skeleton-row">${bar('42%')}${bar('78%')}${bar('56%')}${bar('68%')}</div>`;
    if (kind === 'cards') return `<div class="loading-skeleton-cards">${Array.from({length:3}, () => `<article>${bar('30%')}${bar('75%')}${bar('94%')}${bar('58%')}</article>`).join('')}</div>`;
    if (kind === 'settings') return `<div class="loading-skeleton-settings">${bar('26%')}${bar()}<div>${bar()}${bar()}${bar()}</div>${bar('38%')}</div>`;
    if (kind === 'analytics') return `<div class="loading-skeleton-chart">${[36,55,42,78,61,89,73,96,79,91,67,85].map(height=>`<i class="loading-bone" style="height:${height}%"></i>`).join('')}</div>${row}`;
    return `${kind === 'dashboard' || kind === 'detail' ? `<div class="loading-skeleton-metrics">${bar()}${bar()}${bar()}${bar()}</div>` : ''}<div class="loading-skeleton-table">${row.repeat(kind === 'detail' ? 4 : 6)}</div>`;
  }
  function markup({kind = 'table', label = '正在准备工作台'} = {}) {
    return `<div class="loading-panel-heading">${orb}<div><span class="loading-eyebrow">AUTODEV / CONNECTING</span><p class="loading-label" role="status" aria-live="polite">${escape(label)}</p><span class="loading-caption">正在同步最新信息</span></div></div><div class="loading-skeleton" aria-hidden="true">${skeleton(kind)}</div>`;
  }
  function cancel(host) {
    if (typeof host === 'string') host = document.querySelector(host);
    const previous = host && requests.get(host);
    if (!previous) return;
    previous.panel.remove();
    previous.inert.forEach(([node, value]) => { node.inert = value; });
    host.classList.remove('loading-region', 'loading-empty', 'loading-retained', 'loading-failed');
    host.setAttribute('aria-busy', 'false');
    requests.delete(host);
  }
  function begin(host, options = {}) {
    if (typeof host === 'string') host = document.querySelector(host);
    if (!host) return {current: () => false, finish() {}, fail() {}};
    cancel(host);
    const panel = document.createElement('div');
    panel.className = 'loading-panel';
    panel.innerHTML = markup(options);
    const preserve = Boolean(options.preserve);
    const inert = preserve ? [] : [...host.children].map(node => [node, node.inert]);
    inert.forEach(([node]) => {node.inert = true;});
    const entry = {panel, inert};
    requests.set(host, entry);
    host.classList.add('loading-region', preserve ? 'loading-retained' : 'loading-empty');
    host.setAttribute('aria-busy', 'true');
    host.appendChild(panel);
    const current = () => requests.get(host) === entry;
    return {
      current,
      finish() {if (current()) cancel(host);},
      fail(error, retry) {
        if (!current()) return;
        host.setAttribute('aria-busy', 'false');
        host.classList.add('loading-failed');
        panel.innerHTML = `<div class="loading-panel-heading">${orb}<div><span class="loading-eyebrow">AUTODEV / RECONNECT</span><p class="loading-label" role="status">暂时没有连接上</p><span class="loading-caption">${escape(error?.message || '网络连接暂不可用，请重试。')}</span></div></div><button type="button" class="loading-retry">重新读取 <span aria-hidden="true">↗</span></button>`;
        panel.querySelector('.loading-retry').onclick = () => {
          if (!current()) return;
          cancel(host);
          retry?.();
        };
      },
    };
  }
  window.LoadingUI = {begin, cancel, markup};
})();
