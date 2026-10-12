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
