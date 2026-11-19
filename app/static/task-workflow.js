/* The route is derived from workflow state and recorded receipts. */
(function (root) {
  'use strict';

  const STATES = {
    done: '已完成', running: '进行中', waiting: '待处理', failed: '未完成',
    pending: '未开始', skipped: '已跳过', unknown: '无记录',
  };
  const ACCEPTANCE = {accepted:'验收通过', partial:'部分通过', changes_requested:'需返修', pending:'待验收'};
  const TERMINAL = new Set(['delivered','failed','cancelled','rejected']);
  const REVIEW = new Set(['sichuan_auto_review','sichuan_review_local_package','product_manual_review']);
  const DEPLOYED = new Set(['deployed','already-current']);
  const DEPLOY_FAILURES = new Set(['blocked','failed','failed-rolled-back','failed-rollback-failed']);

  function list(value) { return Array.isArray(value) ? value : []; }
  function counts(record) {
    const files = list(record.repository_states).map(repo => Number(repo.changed_file_count) || list(repo.changed_files).length).filter(Boolean);
    return {repos: files.length, files: files.reduce((total, count) => total + count, 0)};
  }
  function currentStage(record) {
    const status = record.display_status || record.status || '';
    const release = record.task_trace?.release || {};
    if (status === 'validating' && record.current_step === 'prepare') return 'requirement';
    if (status === 'developing' && record.planning_repository_scope) return 'requirement';
    const byStatus = {
      routing:'intake', waiting_runner:'intake', queued:'intake', validating:'intake',
      developing:record.current_step === 'prepare' ? 'requirement' : 'development',
      submitting:'submit', waiting_merge:'merge', capturing:'merge', building:'release',
      waiting_release:'release', releasing:'release', delivering:'delivery', waiting_analysis_sync:'delivery',
      delivered:'acceptance', rejected:'intake', waiting_input:'development',
    };
    if (DEPLOY_FAILURES.has(release.deployment_status) && status === 'failed') return 'deploy';
    if (byStatus[status]) return byStatus[status];
    return {validate:'intake', prepare:'requirement', develop:'development', clarify:'development',
      submit:'submit', release:'release', deliver:'delivery'}[record.current_step] || 'intake';
  }
  function changesLabel(record) {
    const changed = counts(record);
    return changed.files ? `${changed.repos} 仓库 · ${changed.files} 文件` : '';
  }
  function stages(record) {
    const status = record.display_status || record.status || '';
    const steps = Object.fromEntries(list(record.steps).map(step=>[step.step_code, step.status]));
    const events = record.task_trace?.milestones || {};
    const release = record.task_trace?.release || {};
    const acceptance = record.acceptance_progress || {};
    const analysis = record.task_type === 'analysis';
    const intakeOnly = record.record_type === 'intake' || record.intake_id || status === 'routing';
    const plan = record.repository_plan && typeof record.repository_plan === 'object' ? record.repository_plan : {};
    const prs = list(record.repository_states).filter(repo => repo.pr_id || repo.pr_url);
    const hasReview = REVIEW.has(record.delivery_mode) || prs.length || record.pr_id || events['pr.merged'];
    const autoRelease = list(record.delivery_options).includes('auto_release');
    const localBuild = ['local_package','sichuan_review_local_package'].includes(record.delivery_mode);
    const visibleRelease = !analysis && (localBuild || autoRelease || events['release.started'] || release.build_id || record.current_step === 'release');
    const links = list(record.deployment_services);
    const verified = links.filter(item => item.readiness_status === 'verified');
    const deployed = verified.length || DEPLOYED.has(release.deployment_status);
    const visibleDeploy = !analysis && (links.length || deployed || DEPLOY_FAILURES.has(release.deployment_status));
    const merged = prs.length ? prs.every(repo => !!repo.merge_commit) : !!events['pr.merged'] || !!record.merge_commit;
    const final = record.status === 'delivered';
    const key = currentStage(record);
    const nodes = [];
    function add(id, label, short, icon, done, note = '', state) {
      const active = id === key && (!TERMINAL.has(status) || status === 'failed' || status === 'rejected');
      let code = state || (done ? 'done' : final ? 'unknown' : 'pending');
      if (active && code !== 'done') {
        if (['failed','rejected'].includes(status)) code = 'failed';
        else if (status.startsWith('waiting_') || status === 'queued' || record.runner_online === false) code = 'waiting';
        else if (code !== 'failed') code = 'running';
      }
      nodes.push({id,label,short,icon,state:code,note,active});
    }
    add('intake','识别需求','识别','search',steps.validate === 'completed' || events['tfs.validated'],
      status === 'waiting_runner' ? '等待执行器' : record.project_name || '');
    if (intakeOnly) return nodes;
    const requirements = list(acceptance.items).length || list(record.acceptance_ledger).length;
    add('requirement','需求拆解','拆解','layers',plan.status === 'planned' || record.repository_plan_recorded || events['workspace.plan_recorded'] || requirements > 0,
      requirements ? `${requirements} 项验收范围` : list(plan.repositories).length ? `${plan.repositories.length} 个预计仓库` : '');
    add('development',analysis?'问题分析':'研发验证',analysis?'分析':'研发','code',steps.develop === 'completed',changesLabel(record));
    if (!analysis) {
      add('submit','提交代码','提交','code',steps.submit === 'completed' || record.commit_hash || events['git.pushed']);
      if (hasReview) add('merge','审核合并','合并','network',merged,record.pr_id ? `PR #${record.pr_id}` : '');
    }
    if (visibleRelease) {
      const complete = release.result === 'succeeded' || steps.release === 'completed' || !!events['build.completed'];
      add('release',localBuild?'本地构建':'发版构建',localBuild?'构建':'发版','cube',complete,
        release.build_id ? `Build #${release.build_id}` : '',
        steps.release === 'skipped' && !complete ? 'skipped' : undefined);
    }
    if (visibleDeploy) {
      const verification = deployed ? (verified.length ? `${verified.length} 个入口已验证` : '已部署') : links.length ? '待核验' : '';
      const deployState = DEPLOY_FAILURES.has(release.deployment_status) ? 'failed'
        : links.length && !deployed ? 'waiting' : final && !deployed ? 'unknown' : undefined;
      add('deploy','部署验证','部署','building',deployed,verification,deployState);
    }
    add('delivery',analysis?'报告交付':'交付结果','交付','clipboard',final || steps.deliver === 'completed',
      list(record.artifacts).length ? `${record.artifacts.length} 项产物` : '');
    add('acceptance','验收反馈','验收','check',acceptance.status === 'accepted',final ? (ACCEPTANCE[acceptance.status] || ACCEPTANCE.pending) : '',
      final && acceptance.status !== 'accepted' ? 'waiting' : undefined);
    return nodes;
  }
  function summary(record) {
    const replies = list(record.assistant_replies);
    const latest = [...replies].reverse().find(item=>item.event_type === 'devcore.reply' || item.event_type === 'devcore.summary');
    if (TERMINAL.has(record.status) && record.result_summary) return record.result_summary;
    if (record.error_message && ['failed','rejected','waiting_approval','waiting_input'].includes(record.status)) return record.error_message;
    if (record.runner_online === false && record.display_message) return record.display_message;
    if (record.status === 'waiting_merge' && record.pr_id) return `PR #${record.pr_id} 正在等待合并`;
    return latest?.message || record.latest_reply || record.result_summary || '';
  }
  function escape(value) {
    return String(value ?? '').replace(/[&<>"']/g, char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }
  function concise(value, max = 160) {
    const cleaned = String(value ?? '').trim().replace(/\n+/g, ' ');
    return cleaned.length > max ? `${cleaned.slice(0,max-1)}…` : cleaned;
  }
  function asset(name, size) {
    const version = root.document?.body?.dataset?.appVersion || '';
    const url = `/static/task-media/${name}.webp${version?'?v='+encodeURIComponent(version):''}`;
    return `<img src="${url}" alt="" width="${size}" height="${size}" decoding="async">`;
  }
  function miniRoute(nodes) {
    return `<span class="workflow-mini" role="list" aria-label="任务进度">${nodes.map(node=>`<span role="listitem" data-phase="${node.state}" ${node.active?'aria-current="step"':''}><i aria-hidden="true"></i><span title="${escape(node.label)} · ${STATES[node.state]}">${escape(node.short)}</span><span class="sr-only">${STATES[node.state]}</span></span>`).join('')}</span>`;
  }

  const api = {stages,currentStage,summary,counts,concise,asset,miniRoute,STATES,ACCEPTANCE};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TaskWorkflow = api;
})(globalThis);
