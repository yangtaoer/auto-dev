const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../app/static/theme-core.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const themes=[{id:'mint-garden'},{id:'moon-courtyard'},{id:'paper-workshop'}];
test('invalid appearance values fall back without accepting arbitrary theme/CSS',async()=>{
 const {normalizeAppearance}=await modulePromise;
 for(const input of [null,[],false,'bad',{theme_id:'<script>',motion:'bad',font_size:'tiny'}])assert.deepEqual(normalizeAppearance(input,themes),{theme_id:'mint-garden',font_size:'normal',density:'compact',motion:'full'});
});
test('preview is transient and cancel restores every saved setting without persistence',async()=>{
 const {AppearanceSession}=await modulePromise;let writes=0,last;
 const session=new AppearanceSession({themes,saved:{theme_id:'moon-courtyard',font_size:'large',density:'comfortable',motion:'static'},persist:()=>writes++,render:p=>last=p});
 session.begin();session.change({theme_id:'paper-workshop',motion:'full'});assert.equal(last.theme_id,'paper-workshop');session.cancel();
 assert.equal(last.theme_id,'moon-courtyard');assert.equal(last.motion,'static');assert.equal(last.font_size,'large');assert.equal(writes,0);
});
test('save persists once and blocks cancellation or further edits while in flight',async()=>{
 const {AppearanceSession}=await modulePromise;let resolve,writes=0;
 const session=new AppearanceSession({themes,saved:{},render:()=>{},persist:p=>{writes++;return new Promise(r=>resolve=()=>r(p));}});
 session.begin();session.change({theme_id:'paper-workshop'});const save=session.save();
 assert.equal(session.cancel(),false);assert.equal(session.change({theme_id:'moon-courtyard'}),false);assert.equal(await session.save(),false);
 resolve();assert.equal(await save,true);assert.equal(writes,1);assert.equal(session.saved.theme_id,'paper-workshop');assert.equal(session.open,false);
});
test('failed save leaves draft retryable and account selection unchanged',async()=>{
 const {AppearanceSession}=await modulePromise;
 const session=new AppearanceSession({themes,saved:{theme_id:'moon-courtyard'},render:()=>{},persist:async()=>{throw Error('offline');}});
 session.begin();session.change({theme_id:'paper-workshop'});await assert.rejects(session.save(),/offline/);
 assert.equal(session.busy,false);assert.equal(session.saved.theme_id,'moon-courtyard');assert.equal(session.draft.theme_id,'paper-workshop');assert.equal(session.open,true);
 session.cancel();assert.equal(session.draft.theme_id,'moon-courtyard');
});
