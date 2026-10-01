/* Brand films stay configurable; the sidebar awaits the new editorial film. */
(() => {
  'use strict';
  const videos = [...document.querySelectorAll('[data-brand-video]')];
  if (!videos.length) return;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const connection = navigator.connection;
  const allowed = value => typeof value === 'string' && /^\/static\/media\/[\w./-]+$/.test(value) && !value.includes('..');
  let disposed = false;
  function sync() {
    videos.forEach(video => {
      const stage = video.closest('[data-film-stage]');
      const shell = video.closest('.login-shell');
      const fallback = shell?.querySelector('[data-film-fallback]');
      if (fallback) fallback.hidden = shell.dataset.loginLayout === 'delivery-line' && stage?.classList.contains('film-ready') && !motion.matches;
      const pause = document.hidden || motion.matches || connection?.saveData || !video.getAttribute('src');
      if (pause) video.pause();
      else video.play().catch(() => video.closest('[data-film-stage]')?.classList.add('film-static'));
    });
  }
  videos.forEach(video => {
    video.muted = true;
    video.addEventListener('loadeddata', () => {
      video.closest('[data-film-stage]')?.classList.add('film-ready');
      sync();
    });
    video.addEventListener('error', () => {
      video.closest('[data-film-stage]')?.classList.remove('film-ready');
      sync();
    });
  });
  fetch('/static/brand-media.json', {cache:'no-cache'}).then(response => response.ok ? response.json() : {}).then(config => {
    if (disposed) return;
    videos.forEach(video => {
      const media = config[video.dataset.brandVideo] || {};
      if (allowed(media.poster)) video.poster = media.poster;
      if (allowed(media.src)) {
        const shell = video.closest('.login-shell');
        if (shell && media.layout === 'delivery-line') shell.dataset.loginLayout = 'delivery-line';
        video.src = media.src;
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
