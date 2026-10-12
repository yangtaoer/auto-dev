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
test('overload uses a readable summary and quota errors do not imply retry will restore access',()=>{
 assert.match(context.window.workshopFailure({error_message:'unknown 上游详情：serverOverloaded Selected model is at capacity'}).title,/暂时拥堵/);
 assert.match(context.window.workshopFailure({error_message:'insufficient_quota at capacity'}).title,/恢复额度/);
 const result=context.window.workshopFailure({output:{failure:{title:'precise reason',details:'technical'}},error_message:'old'});
 assert.equal(result.title,'precise reason');assert.equal(result.details,'technical');
 const source=fs.readFileSync('app/static/workshop-ui.js','utf8');
 assert.ok(source.includes('查看失败详情'));assert.ok(source.includes('这一轮尚未生成设计图'));
});
test('custom themes have an independent component dialect and a non-persisting real component review',()=>{
 const appearance=fs.readFileSync('app/static/appearance-ui.js','utf8'),css=fs.readFileSync('app/static/theme-components.css','utf8');
 assert.ok(appearance.includes("theme.is_custom?'workshop-custom':theme.id"));
 for(const token of ['skin-primary-ink','skin-field-focus','skin-button-radius','skin-card-bg','skin-nav-active-ink','skin-icon-accent'])assert.ok(css.includes('var(--'+token+')'),token);
 const ui=fs.readFileSync('app/static/workshop-ui.js','utf8');assert.ok(ui.includes('真实组件主题检查'));assert.ok(ui.includes('这只是样式试用，不会提交业务数据'));
 for(const template of ['index','login'])assert.ok(fs.readFileSync('app/templates/'+template+'.html','utf8').includes("'workshop-custom' if appearance_theme.is_custom"));
 assert.ok(css.includes('.request-number-input:focus-within{border-color:var(--skin-field-focus)'));
 assert.ok(css.includes('input:checked{background-color:var(--skin-field-focus)'));
 assert.ok(css.includes(':is(#request-form,.model-settings-panel,.ledger-menu,.ledger-calendar)'));
});
