const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('paused expression driver cancels RAF rather than scheduling idle frames forever',()=>{
 const source=fs.readFileSync('app/static/grok-character/src/character.js','utf8');
 const method=source.slice(source.indexOf('    setPaused(v) {'),source.indexOf('    setEmphasis(v) {'));
 let scheduled=0,canceled=[];const context={performance:{now:()=>100},requestAnimationFrame:()=>++scheduled,cancelAnimationFrame:id=>canceled.push(id)};
 vm.runInNewContext('this.Driver=class {'+method+'}',context);const driver=new context.Driver();driver.paused=false;driver._raf=23;
 driver.setPaused(true);assert.equal(driver._raf,0);assert.deepEqual(canceled,[23]);assert.equal(scheduled,0);
 driver.setPaused(false);assert.equal(scheduled,1);assert.equal(driver.last,100);
 driver.setPaused(false);assert.equal(scheduled,1);driver.setPaused(true);driver.destroyed=true;driver.setPaused(false);assert.equal(scheduled,1);
});
