const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{}};vm.runInNewContext(fs.readFileSync('app/static/workshop-core.js','utf8'),context);
test('studio drafts survive late polls and sending while new text is being typed',()=>{
 const drafts=new context.window.WorkshopDrafts(),a=drafts.draft('a');a.text='first';const file={name:'ref'};a.files=[file];
 const version=drafts.select('a'),snapshot=drafts.snapshot('a');drafts.select('b');assert.equal(drafts.isCurrent('a',version),false);
 a.text='new idea';drafts.sent('a',snapshot);assert.equal(a.text,'new idea');assert.equal(a.files.length,0);
 drafts.select('a');assert.equal(drafts.draft('a'),a);
});
test('hundreds of empty visited works have a bounded draft cache without losing unsent input',()=>{
 const drafts=new context.window.WorkshopDrafts();drafts.draft('important').text='not sent';
 for(let i=0;i<400;i++){drafts.select(String(i));drafts.draft(String(i));}
 assert.ok(drafts.values.size<=20);assert.equal(drafts.draft('important').text,'not sent');
});
test('only the active work keeps history and background polling is gated',()=>{
 const source=fs.readFileSync('app/static/workshop-ui.js','utf8');
 assert.match(source,/delete item.jobs;delete item.assets;delete item.revisions/);
 assert.match(source,/document.hidden\|\|document.body.dataset.view/);
 assert.match(source,/design.jobs.slice\(-40\)/);
 assert.ok(source.includes("input[type=file]').value=''"));
});
