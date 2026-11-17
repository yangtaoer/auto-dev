/* Display deployment receipts and registered service destinations. */
(function(global) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  function safeUrl(value) {
    try {
      if (typeof value !== 'string' || /[\s<>"'\\]/.test(value)) return '';
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch {
      return '';
    }
  }

  function entry(service) {
    const authUrl = safeUrl(service.auth_url);
    const releaseUrl = safeUrl(service.release_url);
    const environment = service.environment ? ` · ${escape(service.environment)}` : '';
    const status = service.readiness_status === 'verified' ? '发布时验证通过' : '部署与就绪未验证';
    const authLink = authUrl ? `<a href="${escape(authUrl)}" target="_blank" rel="noopener">认证入口 ↗</a>` : '';
    const releaseLink = releaseUrl ? `<a href="${escape(releaseUrl)}" target="_blank" rel="noopener">查看发布记录 ↗</a>` : '';
    const date = service.audit_checked_at ? `<span>部署记录 ${escape(String(service.audit_checked_at).slice(0, 10))}</span>` : '';
    const warning = service.warning ? `<p class="deployment-service-warning">${escape(service.warning)}</p>` : '';
    const notice = service.notice || '构建产物已生成，本次部署进度和服务就绪未验证，请在可访问该内网地址的网络中打开。';
    return `<article>
      <a href="${escape(safeUrl(service.url))}" target="_blank" rel="noopener">
        <span><b>${escape(service.project_name)}${environment}</b><code>${escape(service.url)}</code></span>
        <strong>打开服务 ↗</strong>
      </a>
      <div class="deployment-service-meta"><span>Build #${escape(service.build_id)}</span><span>${status}</span>${authLink}${releaseLink}${date}</div>
      ${warning}<p class="deployment-service-notice">${escape(notice)}</p>
    </article>`;
  }

  function section(services) {
    const entries = (Array.isArray(services) ? services : []).filter(service => safeUrl(service.url));
    if (!entries.length) return '';
    return `<section class="deployment-services">
      <header><h3>自动部署服务入口</h3></header>
      <div class="deployment-service-list">${entries.map(entry).join('')}</div>
    </section>`;
  }

  const api = {section, safeUrl};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.AutoDevDeploymentLinks = api;
})(typeof window === 'undefined' ? globalThis : window);
