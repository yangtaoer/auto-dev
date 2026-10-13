/* Drafts survive polls, late replies and moving between works. No persisted model secrets. */
window.WorkshopDrafts = class WorkshopDrafts {
  constructor(){this.values=new Map();this.generation=0;this.current=null;}
  draft(id){if(!this.values.has(id))this.values.set(id,{text:'',files:[],references:[],sending:false,error:''});
    const value=this.values.get(id);this.values.delete(id);this.values.set(id,value);
    for(const [key,draft] of this.values){if(this.values.size<=20)break;if(key!==this.current&&!draft.sending&&!draft.text&&!draft.files.length)this.values.delete(key);}
    return value;}
  select(id){this.current=id;this.generation++;return this.generation;}
  isCurrent(id,generation){return this.current===id&&this.generation===generation;}
  snapshot(id){const draft=this.draft(id);return {text:draft.text,files:[...draft.files],references:[...draft.references]};}
  sent(id,snapshot){const draft=this.draft(id);if(draft.text===snapshot.text)draft.text='';
    draft.files=draft.files.filter(file=>!snapshot.files.includes(file));draft.references=draft.references.filter(id=>!snapshot.references.includes(id));draft.error='';}
};

// Also summarize older records that predate structured recovery evidence.
window.workshopFailure = function(job){
  if(['notification','recommendation'].includes(job.kind))return {title:'邮件发送未完成，壁纸与设计记录不受影响。',details:job.output?.failure?.details||job.error_message||job.progress};
  if(job.output?.failure?.title)return {title:job.output.failure.title,details:job.output.failure.details||job.error_message};
  const details=job.error_message||job.progress||'上游未提供错误详情';
  const signature=details.toLowerCase();
  let title='这一轮未完成，输入与历史已保留。可以查看详情后重试。';
  if(/usagelimitexceeded|usage_limit|insufficient_quota|credit_balance_exhausted|spend_limit/.test(signature))title='账户额度或用量限制已达上限，请恢复额度后重试。';
  else if(/serveroverloaded|server_overloaded|server_is_overloaded|at capacity/.test(signature))title='当前模型暂时拥堵，不是设计描述有问题，也不是额度不足。';
  return {title,details};
};
