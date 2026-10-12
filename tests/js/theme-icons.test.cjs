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
test('custom themes choose trusted icon shapes and ornaments, then restore the system dialect',()=>{
 icons.apply({id:'workshop-123',is_custom:true,components:{icon_family:'angular',icon_decoration:'star'}});
 assert.ok(icons.markup('home').includes('custom-angular'));assert.ok(icons.markup('home').includes('theme-icon-stamp'));
 icons.apply({id:'workshop-123',is_custom:true,components:{icon_family:'solid',icon_decoration:'none'}});
 assert.ok(icons.markup('home').includes('fill-rule="evenodd"'));assert.equal(icons.markup('home').includes('theme-icon-stamp'),false);
 icons.apply({id:'workshop-evil',is_custom:true,components:{icon_family:'" onload="bad',icon_decoration:'<script>'}});
 assert.equal(/onload|script/.test(icons.markup('home')),false);
 icons.apply('paper-workshop');assert.ok(icons.markup('home').includes('folded'));icons.apply('mint-garden');
});
