const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const links=require('../../app/static/deployment-links.js');
const service={project_name:'四川省调网络发令',url:'http://192.168.62.39:8581/netorder',build_id:123,audit_checked_at:'2026-10-15',release_url:'http://dev.tellhowsoft.com/DefaultCollection/DCS/_release?definitionId=53'};
test('service entries are clickable, not downloads, and never claim deployment readiness',()=>{
 const html=links.section([service]);assert.ok(html.includes('href="'+service.url+'"'));assert.ok(html.includes('打开服务 ↗'));
 assert.ok(html.includes('部署与就绪未验证'));assert.ok(html.includes('Build #123'));assert.ok(html.includes('rel="noopener"'));assert.equal(html.includes('download'),false);
});
test('unknown, absent or unsafe destinations do not generate a service panel',()=>{
 for(const value of [[],null,undefined,[{url:'javascript:alert(1)'}],[{url:'http://user:password@host/'}]])assert.equal(links.section(value),'');
 assert.ok(links.section([{...service,project_name:'<img onerror="bad">',warning:'配置未监听'}]).includes('&lt;img'));
 assert.ok(links.section([{...service,warning:'配置未监听'}]).includes('配置未监听'));
});
test('server 14 addresses and authentication links preserve verified release status',()=>{
 const first={...service,environment:'14 环境',url:'http://192.168.101.14:28116/netorder/',auth_url:'http://192.168.101.14:28111/auth/',readiness_status:'verified',notice:'14 环境自动部署完成，发布时服务健康与 HTTP 入口已核验。'};
 const second={...first,project_name:'成都网络发令',url:'http://192.168.101.14:28096/netorder/'};
 const html=links.section([first,second]);
 assert.ok(html.includes(first.url));assert.ok(html.includes(second.url));assert.ok(html.includes('认证入口'));
 assert.ok(html.includes('发布时验证通过'));assert.equal(html.includes('部署与就绪未验证'),false);
});
test('deployment services have one layout and a shortcut from the task brief',()=>{
 const app=fs.readFileSync('app/static/app.js','utf8'),page=fs.readFileSync('app/templates/index.html','utf8');
 assert.equal(app.split('AutoDevDeploymentLinks?.section(services)').length,2);
 assert.ok(app.includes("services.length?fact('部署入口',"));
 assert.ok(app.includes('deploy:deployment,delivery,acceptance:'));
 assert.ok(page.indexOf('/static/deployment-links.js')<page.indexOf('/static/app.js'));assert.ok(page.includes('/static/deployment-links.css'));
});
