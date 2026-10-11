/* Only the server-verified, shipped public-media map may select an external URL. */
(() => {
  'use strict';
  const assets = window.__MEDIA_ASSETS__?.assets || {};
  const failed = new Set();
  const resolve = source => !failed.has(source) && typeof assets[source] === 'string' ? assets[source] : source;
  function fallback(image) {
    const source = image.dataset?.mediaSource;
    if (!source || image.getAttribute('src') === source) return;
    failed.add(source); image.src = source;
  }
  document.addEventListener('error', event => {
    if (event.target?.tagName === 'IMG') fallback(event.target);
  }, true);
  window.AutoDevMedia = Object.freeze({url:resolve, fail:source => failed.add(source),
    theme:theme => ({...theme, preview:resolve(theme.preview), background:resolve(theme.background),
      tokens:{...theme.tokens, 'skin-sidebar-art':`url("${resolve(theme.background)}")`}}),
    background(source, ready) {
      const target = resolve(source);
      if (target === source) return;
      const image = new Image();
      image.onerror = () => { failed.add(source); ready(source); };
      image.src = target;
    },
  });
})();
