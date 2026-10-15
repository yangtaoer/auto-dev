/* Service entry points are not downloads, and a successful Build is not readiness. */
(function(global){
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function safeUrl(value){try{if(typeof value!=='string'||/[\s<>"'\\]/.test(value))return '';const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}}
  function section(services){
    const entries=(Array.isArray(services)?services:[]).filter(s=>safeUrl(s.url));if(!entries.length)return '';
    return `<section class="deployment-services"><header><h3>自动部署服务入口</h3><span>构建成功 · 部署与就绪未验证</span></header><div class="deployment-service-list">${entries.map(s=>`<article><a href="${escape(safeUrl(s.url))}" target="_blank" rel="noopener"><span><b>${escape(s.project_name)}</b><code>${escape(s.url)}</code></span><strong>打开服务 ↗</strong></a><div class="deployment-service-meta"><span>Build #${escape(s.build_id)}</span>${safeUrl(s.release_url)?`<a href="${escape(safeUrl(s.release_url))}" target="_blank" rel="noopener">查看自动部署管道 ↗</a>`:''}<span>部署登记 ${escape(String(s.audit_checked_at||'').slice(0,10))}</span></div>${s.warning?`<p class="deployment-service-warning">${escape(s.warning)}</p>`:''}</article>`).join('')}</div><p class="deployment-service-notice">构建产物已生成，TFS 按既有规则自动部署。本次部署进度和服务就绪未验证，服务可能需要稍候；内网地址需在可访问的网络中打开。</p></section>`;
  }
  const api={section,safeUrl};if(typeof module!=='undefined'&&module.exports)module.exports=api;else global.AutoDevDeploymentLinks=api;
})(typeof window==='undefined'?globalThis:window);
