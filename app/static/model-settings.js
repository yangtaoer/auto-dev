(() => {
  const form = document.querySelector('#model-settings-form');
  if (!form) return;
  let loaded = false, models = [], saved = {}, loading = false;
  const choice = form.querySelector('#model-choice');
  const message = form.querySelector('#model-settings-message');
  const submit = form.querySelector('[type="submit"]');
  const labels = {none:'不额外思考',minimal:'最少',low:'轻量',medium:'标准',high:'深入',xhigh:'更深入',max:'最大',ultra:'极深'};
  function efforts(preferred) {
    const model = models.find(item => item.model === choice.querySelector('input').value);
    const options = model?.efforts || [];
    const value = options.includes(preferred) ? preferred : model?.default_effort;
    form.querySelector('.effort-options').innerHTML = options.map(effort => `<label class="effort-choice"><input type="radio" name="effort" value="${escapeHtml(effort)}" ${value===effort?'checked':''}><span>${escapeHtml(labels[effort]||effort)}<small>${escapeHtml(effort)}</small></span></label>`).join('');
    submit.disabled = !options.length;
  }
  choice.addEventListener('keydown', event => {
    const buttons = [...choice.querySelectorAll('[role="option"]')];
    if (event.key === 'Escape') {closeLedgerPopovers();choice.querySelector(':scope > button').focus();}
    if (['ArrowDown','ArrowUp'].includes(event.key) && buttons.length) {
      event.preventDefault();
      if (!choice.classList.contains('open')) choice.querySelector(':scope > button').click();
      const index = buttons.indexOf(document.activeElement);
      buttons[(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();
    }
  });
  async function open(force=false) {
    if ((loaded && !force) || loading) return; // Polling must never reset a user's draft.
    loading = true;
    try {
      const data = await api('/api/admin/model-settings');
      models = data.models || []; saved = data.settings;
      setLedgerSelectOptions(choice,models.map(item=>[item.model,item.name]),saved.model);
      choice.querySelectorAll('[role="option"]').forEach(option => {
        if (option.dataset.modelBound) return;
        option.dataset.modelBound='1';
        option.addEventListener('click',()=>efforts(saved.effort));
      });
      if (!models.some(item=>item.model===saved.model)) choice.querySelector('b').textContent=saved.model;
      efforts(saved.effort);
      document.querySelector('#model-settings-current').textContent = `当前配置：${saved.model} / ${labels[saved.effort]||saved.effort}`;
      message.textContent = models.length ? '仅保存后生效；不会改变运行中任务。' : '执行器尚未上报模型列表，请等待上线后刷新。当前配置保持不变。';
      loaded = true;
    } catch(error) {message.textContent = error.message;}
    finally {loading=false;}
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();submit.disabled=true;
    try {
      const payload = Object.fromEntries(new FormData(form));
      const data = await api('/api/admin/model-settings',{method:'PUT',body:JSON.stringify(payload)});
      saved=data.settings;
      document.querySelector('#model-settings-current').textContent=`当前配置：${saved.model} / ${labels[saved.effort]||saved.effort}`;
      message.textContent='已保存。下一次开工使用此配置，当前任务不受影响。';
      toast('全局研发配置已保存');
    } catch(error) {message.textContent=error.message;}
    finally {submit.disabled=false;}
  });
  form.querySelector('#reload-models').addEventListener('click',()=>open(true));
  window.ModelSettings={open};
})();
