const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const rootPath = path.join(__dirname, '../../app/static');
const source = fs.readFileSync(path.join(rootPath, 'followup-ui.js'), 'utf8');
const marked = require(path.join(rootPath, 'vendor/markdown/marked.umd.js'));

function harness(api) {
  class Node {
    constructor() { this.children=[]; this.dataset={}; this.attributes={}; this.classList={add(){}};
      this.scrollHeight=500; this.clientHeight=300; this.scrollTop=0; this.isConnected=true; this.value=''; }
    append(...nodes) {this.children.push(...nodes);}
    replaceChildren(...nodes) {this.children=nodes;}
    querySelectorAll() {return [];}
    focus() {this.focused=true;}
    setAttribute(name,value) {this.attributes[name]=value;}
    getAttribute(name) {return this.attributes[name];}
    removeAttribute(name) {delete this.attributes[name];}
  }
  const root = new Node(), chat = new Node(), input = new Node(), form = new Node(), button = new Node(), status = new Node();
  root.querySelector = key => ({'.followup-messages':chat,textarea:input,form,'[type=submit]':button,'.followup-composer-status':status}[key]);
  root.closest = () => root;
  form.requestSubmit = () => form.onsubmit({preventDefault(){}});
  let interval, clears=0;
  const sanitizer=[];
  const context={window:{marked},marked,DOMPurify:{sanitize(html,options){sanitizer.push({html,options});return html;}},
    document:{querySelector:key => key==='#task-followup-mount'?root:key==='#detail-drawer.open'?root:key==='#task-panel-followup'?{hidden:false}:null,createElement:()=>new Node()},
    api,crypto:{randomUUID:()=> 'fixed-idempotency-key'},setInterval:fn=>(interval=fn,1),clearInterval:()=>{clears++;interval=null;},
    requestAnimationFrame:fn=>fn(),Date,Map,JSON};
  context.window.DOMPurify=context.DOMPurify;
  vm.runInNewContext(source,context);
  return {ui:context.window.RequestFollowups,root,chat,input,form,button,status,sanitizer,
    poll:()=>interval?.(),clears:()=>clears,Node};
}
const flush = () => new Promise(resolve=>setImmediate(resolve));
const item = (status='completed') => ({id:'answer-one',status,question:'保存在哪？',answer:'## 结论\n\n**按用户保存**。',started_at:'2026-10-10T01:00:00Z',created_at:'2026-10-10T01:00:00Z',elapsed_seconds:65});

test('polls preserve the exact composer node, focus and a partially typed draft', async()=>{
  const h=harness(async()=>({items:[item()],runner_online:true}));
  h.ui.mount({id:'task-one'}); await flush();
  h.input.value='还有一个问题，还没写完';h.input.oninput();h.input.focus();
  h.poll();await flush();
  assert.equal(h.root.querySelector('textarea'),h.input);
  assert.equal(h.input.value,'还有一个问题，还没写完');assert.equal(h.input.focused,true);
  h.ui.stop();h.ui.mount({id:'task-one'});await flush();
  assert.equal(h.input.value,'还有一个问题，还没写完');
});

test('questions align right, Markdown answers align left and include elapsed time', async()=>{
  const h=harness(async()=>({items:[item()],runner_online:true}));h.ui.mount({id:'task-one'});await flush();
  const [question,answer]=h.chat.children;
  assert.equal(question.className,'followup-message user');assert.equal(answer.className,'followup-message assistant completed');
  assert.equal(question.children[1].textContent,'保存在哪？');
  assert.match(answer.children[0].children[1].textContent,/耗时 1 分 5 秒/);
  assert.match(answer.children[1].innerHTML,/<h2>结论<\/h2>/);
  assert.match(answer.children[1].innerHTML,/<strong>按用户保存<\/strong>/);
  assert.equal(h.sanitizer[0].options.USE_PROFILES.html,true);
  assert.ok(h.sanitizer[0].options.FORBID_TAGS.includes('iframe'));
  assert.ok(h.sanitizer[0].options.FORBID_ATTR.includes('style'));
});

test('submit failure remains visible after successful polling and never clears the question', async()=>{
  const h=harness(async(_url,options)=>{if(options?.method==='POST')throw new Error('网络不可用');return {items:[],runner_online:true};});
  h.ui.mount({id:'task-one'});await flush();h.input.value='请解释实现';h.input.oninput();
  await h.form.requestSubmit();await flush();h.poll();await flush();
  assert.equal(h.input.value,'请解释实现');assert.equal(h.status.textContent,'网络不可用');assert.equal(h.button.disabled,false);
});

test('an in-flight send is idempotent and does not erase newer typing when it succeeds', async()=>{
  let resolvePost,posts=0,payload;
  const h=harness(async(_url,options)=>{
    if(options?.method==='POST'){posts++;payload=JSON.parse(options.body);return await new Promise(resolve=>{resolvePost=resolve;});}
    return {items:[],runner_online:true};
  });
  h.ui.mount({id:'task-one'});await flush();h.input.value='原始问题';h.input.oninput();
  const send=h.form.requestSubmit();h.form.requestSubmit();
  h.input.value='下一条问题';h.input.oninput();resolvePost({item:item('queued')});await send;await flush();
  assert.equal(posts,1);assert.equal(payload.question,'原始问题');assert.equal(payload.idempotency_key,'fixed-idempotency-key');
  assert.equal(h.input.value,'下一条问题');
});

test('a stale read from another task cannot overwrite the current task chat', async()=>{
  let resolveOld;
  const h=harness(async url=>url.includes('task-one')?await new Promise(resolve=>{resolveOld=resolve;}):{items:[],runner_online:true});
  h.ui.mount({id:'task-one'});h.ui.mount({id:'task-two'});await flush();
  resolveOld({items:[item()],runner_online:true});await flush();
  assert.equal(h.root.dataset.requestId,'task-two');assert.equal(h.chat.children.length,0);
  h.ui.stop();assert.ok(h.clears()>0);
});

test('Markdown supports nested lists, tables, links and fenced code without flattening structure',()=>{
  const h=harness(async()=>({items:[]})),target=new h.Node();
  h.ui.markdown(target,'## 结论\n\n1. 保存\n   - 用户维度\n\n| 字段 | 含义 |\n|---|---|\n| USERID | 用户 |\n\n```sql\nSELECT * FROM T_A_USERLIKE;\n```\n\n[说明](https://example.com)');
  assert.match(target.innerHTML,/<ol>/);assert.match(target.innerHTML,/<ul>/);assert.match(target.innerHTML,/<table>/);
  assert.match(target.innerHTML,/<pre><code class="language-sql">/);assert.match(target.innerHTML,/<a href="https:\/\/example.com">说明<\/a>/);
  const css=fs.readFileSync(path.join(rootPath,'followup-ui.css'),'utf8');
  assert.match(css,/\.followup-messages\{[^}]*overflow:auto/);assert.match(css,/\.followup-markdown pre\{[^}]*overflow-x:auto/);
  assert.match(css,/#task-panel-followup\{overflow:hidden/);
});

test('Markdown links reject script, file and protocol-relative destinations',()=>{
  const h=harness(async()=>({items:[]})),target=new h.Node();
  const links=['javascript:alert(1)','file:///private','//tracking.invalid','C:/work/file.py','https://example.com','#note','/report/1'].map(href=>{
    const node=new h.Node();node.setAttribute('href',href);return node;
  });
  target.querySelectorAll=selector=>selector==='a'?links:[];
  h.ui.markdown(target,'[说明](https://example.com)');
  links.slice(0,4).forEach(node=>assert.equal(node.getAttribute('href'),undefined));
  links.slice(4).forEach(node=>assert.equal(node.rel,'noopener noreferrer'));
});
