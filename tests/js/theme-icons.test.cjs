const test=require('node:test');const assert=require('node:assert/strict');
const icons=require('../../app/static/theme-icons.js');
test('ten themes have genuinely different local icon silhouettes/penwork rather than a color swap',()=>{
  assert.equal(Object.keys(icons.dialects).length,10);
  assert.equal(new Set(Object.values(icons.dialects).map(d=>d.family)).size,10);
  for(const name of ['home','clipboard','cube','chart','user','book','gear']){
    const paths=Object.keys(icons.dialects).map(id=>icons.glyph(name,id));assert.equal(new Set(paths).size,10,name);
    paths.forEach(svg=>{assert.ok(svg.includes('<path'));assert.equal(/script|onload|https?:/.test(svg),false);});
  }
});
test('fallback icons preserve their original business meaning and reject markup injection',()=>{
  assert.ok(icons.markup('network').includes('#network'));
  const evil=icons.markup('\" onload=\"alert(1)');assert.equal(evil.includes('onload'),false);assert.ok(evil.includes('#help'));
  icons.apply('pixel-arcade');assert.ok(icons.markup('cube').includes('fill-rule="evenodd"'));icons.apply('mint-garden');
});
