const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = file => fs.readFileSync(path.join(__dirname, '../../app/static', file), 'utf8');

function loadingHarness() {
  class Node {
    constructor() {
      this.children = []; this.attributes = {}; this.inert = false;
      this.classes = new Set(); this.innerHTML = ''; this.retry = {};
      this.classList = {add: (...names) => names.forEach(name => this.classes.add(name)),
        remove: (...names) => names.forEach(name => this.classes.delete(name)), contains: name => this.classes.has(name)};
    }
    setAttribute(name, value) {this.attributes[name] = value;}
    appendChild(node) {this.children.push(node); node.parentNode = this;}
    remove() {if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this);}
    querySelector(selector) {return selector === '.loading-retry' ? this.retry : null;}
  }
  const host = new Node(), input = new Node(); input.value = '未提交的补充信息'; host.appendChild(input);
  const context = {window: {}, document: {createElement: () => new Node(), querySelector: () => host}};
  vm.runInNewContext(read('loading-ui.js'), context);
  return {ui: context.window.LoadingUI, host, input};
}

test('first-read skeleton preserves DOM nodes and restores accessibility on completion', () => {
  const {ui, host, input} = loadingHarness();
  const load = ui.begin(host, {kind:'dashboard',label:'正在准备任务总览'});
  assert.equal(input.inert, true);
  assert.equal(host.attributes['aria-busy'], 'true');
  assert.equal(host.children[0], input);
  assert.match(host.children[1].innerHTML, /loading-skeleton-metrics/);
  assert.match(host.children[1].innerHTML, /role="status"/);
  load.finish();
  assert.equal(input.inert, false);
  assert.equal(input.value, '未提交的补充信息');
  assert.deepEqual(host.children, [input]);
  assert.equal(host.attributes['aria-busy'], 'false');
});

test('refresh badge leaves existing content and input usable without a blocking layer', () => {
  const {ui, host, input} = loadingHarness(); host.scrollTop = 215;
  const load = ui.begin(host, {preserve:true});
  assert.equal(input.inert, false);
  assert.equal(host.classList.contains('loading-retained'), true);
  assert.equal(host.classList.contains('loading-empty'), false);
  input.value += '继续填写'; load.finish();
  assert.equal(input.value, '未提交的补充信息继续填写');
  assert.equal(host.scrollTop, 215);
});

test('a stale completion cannot remove or replace the newer loading region', () => {
  const {ui, host} = loadingHarness();
  const old = ui.begin(host, {label:'旧请求'}), current = ui.begin(host, {label:'新请求'});
  old.finish(); old.fail(new Error('旧失败'), () => assert.fail('old callback'));
  assert.equal(old.current(), false); assert.equal(current.current(), true);
  assert.equal(host.children.length, 2);
  assert.match(host.children[1].innerHTML, /新请求/);
});

test('failure ends aria-busy, escapes server text and offers exactly one current retry', () => {
  const {ui, host} = loadingHarness(); let retries = 0;
  const load = ui.begin(host); load.fail(new Error('<script>bad()</script>'), () => retries++);
  const panel = host.children[1], click = panel.retry.onclick;
  assert.equal(host.attributes['aria-busy'], 'false');
  assert.match(panel.innerHTML, /&lt;script&gt;/);
  assert.doesNotMatch(panel.innerHTML, /<script>/);
  assert.equal(host.classList.contains('loading-failed'), true);
  click(); click(); assert.equal(retries, 1);
  assert.equal(host.classList.contains('loading-empty'), false);
});

test('missing regions and pre-existing inert nodes are safe', () => {
  const {ui, host, input} = loadingHarness();
  assert.doesNotThrow(() => ui.begin(null).finish());
  input.inert = true; ui.begin(host).finish(); assert.equal(input.inert, true);
});

function deferred() {let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject};}
function detailHarness() {
  const requests = [], rendered = [], cancelled = [], loaders = [];
  const state = {selectedRequest:'first'};
  const context = vm.createContext({state, console, LoadingUI:{
    begin() {const loader={fail(error){loader.error=error.message;}};loaders.push(loader);return loader;}, cancel: value=>cancelled.push(value),
  },document:{querySelector:()=>({scrollTop:0,querySelectorAll:()=>[]}),activeElement:null},api:()=>{const request=deferred();requests.push(request);return request.promise;},renderDetail:d=>rendered.push(d.id),requestAnimationFrame:fn=>fn(),openDetail:()=>{}});
  const source = read('app.js');
  vm.runInContext('let detailGeneration=0,detailPending=false;'+source.slice(source.indexOf('async function refreshDetail('),source.indexOf('function renderAnalysisPanel('))+';globalThis.refreshDetail=refreshDetail;',context);
  return {context,state,requests,rendered,cancelled,loaders};
}

test('task detail polling does not compete with an in-flight initial read', async () => {
  const ui=detailHarness();const first=ui.context.refreshDetail('first');
  await ui.context.refreshDetail('first',true);
  assert.equal(ui.requests.length,1);assert.equal(ui.loaders.length,1);
  ui.requests[0].resolve({request:{id:'first'}});await first;
  assert.deepEqual(ui.rendered,['first']);assert.deepEqual(ui.cancelled,['#detail-content']);
});

test('rapidly opening another task ignores the stale response and the stale error', async () => {
  const ui=detailHarness();const first=ui.context.refreshDetail('first');
  ui.state.selectedRequest='second';const second=ui.context.refreshDetail('second');
  ui.requests[1].resolve({request:{id:'second'}});await second;
  ui.requests[0].reject(new Error('old task failed'));await first;
  assert.deepEqual(ui.rendered,['second']);assert.equal(ui.loaders[0].error,undefined);
});

test('a read resolving after the task is closed cannot render or steal focus', async () => {
  const ui=detailHarness();const first=ui.context.refreshDetail('first');
  ui.state.selectedRequest=null;
  ui.requests[0].resolve({request:{id:'first'}});await first;
  assert.equal(ui.rendered.length,0);
});

test('detail background recovery clears a prior error without another skeleton', async () => {
  const ui=detailHarness();const first=ui.context.refreshDetail('first');
  ui.requests[0].reject(new Error('offline'));await first;assert.equal(ui.loaders[0].error,'offline');
  const silent=ui.context.refreshDetail('first',true);
  ui.requests[1].resolve({request:{id:'first'}});await silent;
  assert.equal(ui.loaders.length,1);assert.deepEqual(ui.cancelled,['#detail-content']);
});

test('task polling keeps typed supplementary answers and restores their text selection', async () => {
  const ui=detailHarness(); let focused=false;
  const original={dataset:{id:'choice-2'},value:'保留这段尚未提交的说明',selectionStart:3,selectionEnd:7,scrollTop:18,matches:selector=>selector==='.supplement-answer'};
  const replacement={value:'server value',focus(){focused=true;},setSelectionRange(start,end){this.range=[start,end];}};
  const drawer={scrollTop:72,querySelectorAll:()=>[original],querySelector:()=>replacement};
  ui.context.CSS={escape:value=>value};
  ui.context.document={activeElement:original,querySelector:selector=>selector==='#detail-drawer'?drawer:null};
  const silent=ui.context.refreshDetail('first',true);ui.requests[0].resolve({request:{id:'first'}});await silent;
  assert.equal(replacement.value,original.value);assert.equal(replacement.scrollTop,18);
  assert.deepEqual(replacement.range,[3,7]);assert.equal(focused,true);assert.equal(drawer.scrollTop,72);
});

test('refreshing filter choices does not discard unapplied project and date drafts', () => {
  const selectors=['[data-filter-select="task_type"]','[data-filter-select="status"]','#record-project-filter','#record-requester-filter','[data-filter-date="date_from"]','[data-filter-date="date_to"]'];
  const controls=new Map(selectors.map((selector,index)=>[selector,{dataset:{ready:'1'},querySelector:()=>({value:`draft-${index}`})}]));
  const applied=[];
  const source=read('app.js'),context=vm.createContext({document:{querySelector:selector=>controls.get(selector)},state:{records:{filters:{}},projects:[],users:[]},RECORD_FILTER_OPTIONS:{task_type:[],status:[]},setLedgerSelectOptions:(_,options,value)=>applied.push(value),initLedgerDate:(_,value)=>applied.push(value)});
  vm.runInContext(source.slice(source.indexOf('function renderRecordFilterOptions('),source.indexOf('function resetRecordFilterControls('))+';renderRecordFilterOptions();',context);
  assert.deepEqual(applied,selectors.map((_,index)=>`draft-${index}`));
});

test('task background refresh restores follow-up composer focus and selection', async () => {
  const ui=detailHarness();let focused=false;
  const original={id:'followup-question',value:'仍在输入',selectionStart:1,selectionEnd:3,scrollTop:9};
  const replacement={value:'仍在输入',focus(){focused=true;},setSelectionRange(start,end){this.range=[start,end];}};
  const drawer={scrollTop:0,querySelectorAll:()=>[],querySelector:key=>key==='#followup-question'?replacement:null};
  ui.context.document={activeElement:original,querySelector:key=>key==='#detail-drawer'?drawer:null};
  const refresh=ui.context.refreshDetail('first',true);ui.requests[0].resolve({request:{id:'first'}});await refresh;
  assert.equal(focused,true);assert.deepEqual(replacement.range,[1,3]);assert.equal(replacement.scrollTop,9);
});

test('read requests time out, release timers and expose a useful retry message', async () => {
  let expire, cleared=0;
  const context=vm.createContext({AbortController,location:{},setTimeout:fn=>{expire=fn;return 42;},clearTimeout:()=>cleared++,fetch:(_,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))))});
  const source=read('app.js');vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function toast('))+';globalThis.api=api;',context);
  const request=context.api('/api/dashboard');expire();
  await assert.rejects(request,/读取超时/);assert.equal(cleared,1);
});

test('loading assets follow shared theme and load before app initialization', () => {
  const html=fs.readFileSync(path.join(__dirname,'../../app/templates/index.html'),'utf8'),css=read('loading-ui.css'),app=read('app.js');
  assert.ok(html.indexOf('/static/loading-ui.js')<html.indexOf('/static/app.js'));
  assert.ok(html.indexOf('/static/loading-ui.css')>html.indexOf('/static/ui-refinements.css'));
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css,/loading-retained > \.loading-panel[^}]+pointer-events:none/s);
  assert.match(app,/refresh\(\{background:true\}\)/);
  assert.match(app,/if\(refreshPromise\)return refreshPromise/);
  assert.match(app,/recordsVersion===recordsGeneration&&recordsUrl===deliveryRecordsUrl\(\)/);
  assert.doesNotMatch(read('loading-ui.js'),/WebGLRenderer|setInterval/);
});
