const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../app/static/media-assets.js'),'utf8');
const local='/static/themes/backgrounds/mint-garden.png';
const remote='https://example.oss-cn-chengdu.aliyuncs.com/autodev-static/v1/bg.hash.webp';
function harness(assets={[local]:remote}){
  const listeners={},images=[];
  const window={__MEDIA_ASSETS__:{assets}};
  const document={addEventListener:(type,callback,capture)=>{listeners[type]={callback,capture};}};
  class Image {constructor(){images.push(this);}set src(value){this.url=value;}}
  vm.runInNewContext(source,{window,document,Image});
  return {media:window.AutoDevMedia,listeners,images};
}
test('only shipped media is mapped; CSS, modules, unknown paths remain local',()=>{
  const {media}=harness();assert.equal(media.url(local),remote);
  assert.equal(media.url('/static/app.js'),'/static/app.js');
  assert.equal(media.url('/static/themes/previews/missing.png'),'/static/themes/previews/missing.png');
  assert.equal(harness({}).media.url(local),local);
});
test('image errors fall back once and later selections retain local fallback',()=>{
  const {media,listeners}=harness();const writes=[];let current=remote;
  const image={tagName:'IMG',dataset:{mediaSource:local},getAttribute:()=>current,
    set src(value){current=value;writes.push(value);}};
  assert.equal(listeners.error.capture,true);listeners.error.callback({target:image});
  listeners.error.callback({target:image});assert.deepEqual(writes,[local]);
  assert.equal(media.url(local),local);
  listeners.error.callback({target:{tagName:'VIDEO'}});
});
test('background error selects the same local artwork and does not refetch failed OSS',()=>{
  const {media,images}=harness();const ready=[];
  media.background(local,value=>ready.push(value));assert.equal(images.length,1);assert.equal(images[0].url,remote);
  images[0].onerror();assert.deepEqual(ready,[local]);assert.equal(media.url(local),local);
  media.background(local,()=>assert.fail('no remote retry'));assert.equal(images.length,1);
});
test('theme adaptation is immutable and does not alter business metadata',()=>{
  const {media}=harness();const theme={id:'mint-garden',background:local,preview:'/static/themes/previews/01.png',
    scene:'garden',tokens:{'skin-bg':'#fff','skin-sidebar-art':`url("${local}")`}};
  const resolved=media.theme(theme);assert.equal(resolved.background,remote);
  assert.equal(resolved.tokens['skin-sidebar-art'],`url("${remote}")`);
  assert.equal(theme.background,local);assert.equal(resolved.scene,'garden');assert.equal(resolved.tokens['skin-bg'],'#fff');
});
