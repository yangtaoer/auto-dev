/* Brand films stay configurable; the sidebar awaits the new editorial film. */
(() => {
  'use strict';
  const videos = [...document.querySelectorAll('[data-brand-video]')];
  if (!videos.length) return;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const connection = navigator.connection;
  const allowed = value => typeof value === 'string' && /^\/static\/media\/[\w./-]+$/.test(value) && !value.includes('..');
  const assets = globalThis.__MEDIA_ASSETS__?.assets || {};
  const failed = new Set();
  const mediaURL = source => failed.has(source) ? source : (globalThis.AutoDevMedia?.url(source) || source);
  let disposed = false;
  function sync() {
    if (disposed) return;
    videos.forEach(video => {
      const stage = video.closest('[data-film-stage]');
      const shell = video.closest('.login-shell');
      const fallback = shell?.querySelector('[data-film-fallback]');
      if (fallback) fallback.hidden = shell.dataset.loginLayout === 'delivery-line' && !!video.getAttribute('src') && !stage?.classList.contains('film-failed') && !motion.matches;
      const pause = document.hidden || motion.matches || connection?.saveData || !video.getAttribute('src');
      if (pause) video.pause();
      else video.play().catch(() => video.closest('[data-film-stage]')?.classList.add('film-static'));
    });
  }
  videos.forEach(video => {
    video.muted = true;
    video.addEventListener('loadeddata', () => {
      video.closest('[data-film-stage]')?.classList.add('film-ready');
      video.closest('[data-film-stage]')?.classList.remove('film-failed');
      sync();
    });
    const recover = () => {
      video.closest('[data-film-stage]')?.classList.remove('film-ready');
      if (disposed) return;
      const original = Object.keys(assets).find(source => allowed(source) && assets[source] === video.getAttribute('src'));
      if (original && !failed.has(original)) {
        failed.add(original); globalThis.AutoDevMedia?.fail(original);
        video.src = original;
        const poster = Object.keys(assets).find(source => allowed(source) && assets[source] === video.getAttribute('poster'));
        if (poster) {
          failed.add(poster); globalThis.AutoDevMedia?.fail(poster); video.poster = poster;
        }
        sync(); return;
      }
      video.closest('[data-film-stage]')?.classList.add('film-failed');
      sync();
    };
    video.addEventListener('error', recover);
    // A fast network failure can precede this deferred script's event binding.
    if (video.error) recover();
    if (video.readyState >= 2) video.closest('[data-film-stage]')?.classList.add('film-ready');
  });
  sync();
  fetch('/static/brand-media.json', {cache:'no-cache'}).then(response => response.ok ? response.json() : {}).then(config => {
    if (disposed) return;
    videos.forEach(video => {
      const media = config[video.dataset.brandVideo] || {};
      if (allowed(media.poster) && video.getAttribute('poster') !== mediaURL(media.poster)) video.poster = mediaURL(media.poster);
      if (allowed(media.src)) {
        const shell = video.closest('.login-shell');
        if (shell && media.layout === 'delivery-line') shell.dataset.loginLayout = 'delivery-line';
        if (video.getAttribute('src') !== mediaURL(media.src)) video.src = mediaURL(media.src);
        video.hidden = false;
      }
    });
    sync();
  }).catch(() => {});
  document.addEventListener('visibilitychange', sync);
  motion.addEventListener('change', sync);
  connection?.addEventListener?.('change', sync);
  addEventListener('pagehide', () => {
    disposed = true;
    videos.forEach(video => video.pause());
    document.removeEventListener('visibilitychange', sync);
    motion.removeEventListener('change', sync);
    connection?.removeEventListener?.('change', sync);
  }, {once:true});
})();
