const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/app.js','utf8');
const expression=source.split('\n').find(line=>line.startsWith('const visibleArtifacts = ')).replace('const visibleArtifacts = ','').replace(/;\s*$/,'');
const visible=vm.runInNewContext(expression);
test('business deliverables include SQL and config in every mode, regardless of old options',()=>{
 const artifacts=['package','sql','config','release_artifact','menu_link','merge_screenshot','license_request','verification_report','delivery_manifest','package_note'].map(kind=>({kind,name:kind==='sql'?'upgrade.sql':kind==='config'?'application.yml':kind}));
 for(const mode of ['local_package','sichuan_auto_review','sichuan_review_local_package','product_manual_review']){
  assert.deepEqual(Array.from(visible(artifacts,mode,[]),item=>item.kind),['package','sql','config','release_artifact']);
 }
});
test('internal manifests and source XML cannot masquerade as config, analysis download remains',()=>{
 const artifacts=['delivery-validation-manifest.json','pom.xml','UserMapper.xml','direct.view.xml','application.yml'].map(name=>({kind:'config',name}));
 artifacts.push({kind:'analysis_report',name:'report.md'});
 assert.deepEqual(Array.from(visible(artifacts),item=>item.name),['application.yml','report.md']);
});
test('request form offers package release plus automatic SQL/config, no screenshot or License products',()=>{
 const page=fs.readFileSync('app/templates/index.html','utf8');
 assert.ok(page.includes('SQL 脚本'));assert.ok(page.includes('必要配置'));
 assert.ok(page.includes('value="auto_release" checked'));
 assert.equal(page.includes('value="merge_screenshot"'),false);assert.equal(page.includes('value="license_request"'),false);
});
