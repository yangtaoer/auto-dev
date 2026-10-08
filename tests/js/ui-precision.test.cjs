const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8');
const context = vm.createContext({Intl});
vm.runInContext(source.slice(source.indexOf('const escapeHtml ='), source.indexOf('const requestDuration =')) +
  '\nglobalThis.helpers = {artifactFileName, artifactFileTag, artifactLinks};', context);
const {artifactFileName, artifactFileTag, artifactLinks} = context.helpers;

test('delivery file labels show the basename of both Unix and Windows paths', () => {
  assert.equal(artifactFileName({name: 'th-dc-biz-bazhong/th-dc-biz-bazhong-sql/V3.1.0__workitem_1690565_five_officers.sql'}),
    'V3.1.0__workitem_1690565_five_officers.sql');
  assert.equal(artifactFileName({name: 'build\\泸州网络发令\\泸州-1700200-前端.zip'}), '泸州-1700200-前端.zip');
  assert.equal(artifactFileName({name: ''}), '交付文件');
});

test('delivery labels retain the original download endpoint and complete accessible name', () => {
  const name = 'th-dc-biz-bazhong/th-dc-biz-bazhong-sql/V3.1.0__workitem_1690565_five_officers.sql';
  const tag = artifactFileTag({id: 77, kind: 'sql', name});
  assert.ok(tag.includes('href="/api/artifacts/77"'));
  assert.ok(tag.includes(`title="${name}"`));
  assert.ok(tag.includes(`aria-label="下载 ${name}"`));
  assert.ok(tag.includes('rel="noopener"'));
  assert.ok(tag.includes('onclick="event.stopPropagation()"'));
  assert.ok(tag.includes('editorial-icons.svg#code'));
  assert.ok(tag.includes('<span class="artifact-file-name">V3.1.0__workitem_1690565_five_officers.sql</span>'));
  assert.ok(!tag.includes('<span class="artifact-file-name">th-dc-biz-bazhong/'));
});

test('external files keep their external URL and filename markup is escaped', () => {
  const tag = artifactFileTag({id: 3, kind: 'package', name: 'dir/<report & "new">.zip', external_url: 'https://files.test/artifact?a=1&b=2'});
  assert.ok(tag.includes('href="https://files.test/artifact?a=1&amp;b=2"'));
  assert.ok(tag.includes('editorial-icons.svg#cube'));
  assert.ok(tag.includes('&lt;report &amp; &quot;new&quot;&gt;.zip'));
  assert.ok(!tag.includes('<report'));
});

test('file-label improvements preserve report actions and artifact policy filtering', () => {
  const rendered = artifactLinks([
    {id: 1, kind: 'analysis_report', name: 'TFS-1700200-问题分析报告.md'},
    {id: 2, kind: 'delivery_manifest', name: 'delivery-validation-manifest.json'},
    {id: 3, kind: 'sql', name: 'sql/change.sql'},
  ]);
  assert.ok(rendered.includes('analysis-report-link'));
  assert.ok(rendered.includes('问题分析报告 ↗'));
  assert.ok(!rendered.includes('delivery-validation-manifest'));
  assert.ok(rendered.includes('artifact-file-link'));
  assert.ok(rendered.includes('>change.sql</span>'));
});

function modelKeyboardHarness() {
  let listener;
  const classes = new Set();
  const document = {activeElement: null};
  const options = Array.from({length: 4}, (_, index) => ({index, focus() {document.activeElement = this;}}));
  const trigger = {click() {classes.add('open');}, focus() {document.activeElement = this;}};
  const choice = {
    classList: {contains: name => classes.has(name)},
    addEventListener(type, fn) {if (type === 'keydown') listener = fn;},
    querySelectorAll() {return options;},
    querySelector() {return trigger;},
  };
  const noopButton = {addEventListener() {}};
  const form = {querySelector(selector) {return selector === '#model-choice' ? choice : noopButton;}, addEventListener() {}};
  document.querySelector = () => form;
  const sandbox = vm.createContext({document, window: {}, closeLedgerPopovers() {classes.delete('open');}});
  vm.runInContext(fs.readFileSync(path.join(root, 'app/static/model-settings.js'), 'utf8'), sandbox);
  return {
    document, options, trigger, classes,
    key(key) {let prevented = false; listener({key, preventDefault() {prevented = true;}}); return prevented;},
  };
}

test('model catalogue ArrowUp starts at the last option and supports list endpoints', () => {
  const ui = modelKeyboardHarness();
  assert.equal(ui.key('ArrowUp'), true);
  assert.equal(ui.document.activeElement, ui.options[3]);
  assert.equal(ui.classes.has('open'), true);
  ui.key('Home');
  assert.equal(ui.document.activeElement, ui.options[0]);
  ui.key('ArrowUp');
  assert.equal(ui.document.activeElement, ui.options[3]);
  ui.key('End');
  assert.equal(ui.document.activeElement, ui.options[3]);
  assert.equal(ui.key('Escape'), true);
  assert.equal(ui.classes.has('open'), false);
  assert.equal(ui.document.activeElement, ui.trigger);
});

test('model catalogue ArrowDown starts at the first option and wraps', () => {
  const ui = modelKeyboardHarness();
  ui.key('ArrowDown');
  assert.equal(ui.document.activeElement, ui.options[0]);
  ui.key('End');
  ui.key('ArrowDown');
  assert.equal(ui.document.activeElement, ui.options[0]);
});

test('number field owns one focus ring and hides browser spinner controls', () => {
  const css = fs.readFileSync(path.join(root, 'app/static/task-form-ui.css'), 'utf8');
  assert.match(css, /\.request-number-input input:is\(:focus, :focus-visible\)\s*\{[^}]*outline:\s*none;[^}]*box-shadow:\s*none;/s);
  assert.match(css, /\.request-number-input::before\s*\{\s*display:\s*none;/);
  assert.ok(css.includes('input::-webkit-inner-spin-button'));
  assert.ok(css.includes('input::-webkit-outer-spin-button'));
  assert.ok(css.includes('-moz-appearance: textfield'));
});

test('delivery container can shrink and model menu expands without covering effort choices', () => {
  const css = fs.readFileSync(path.join(root, 'app/static/ui-refinements.css'), 'utf8');
  assert.match(css, /\.mint-workspace \.artifact-links\s*\{[^}]*min-width:\s*0;/s);
  const widths = [...css.matchAll(/\.delivery-table th:nth-child\(\d+\)\s*\{ width: (\d+)%; \}/g)].map(match => Number(match[1]));
  // The combined 8th/9th-column rule has the shared value once in the match.
  assert.equal(widths.reduce((sum, width) => sum + width, 0) + 8, 100);
  const models = fs.readFileSync(path.join(root, 'app/static/model-settings.css'), 'utf8');
  assert.match(models, /#model-choice \.ledger-select-menu\s*\{[^}]*position:\s*static;[^}]*max-height:\s*min\(248px, 38dvh\);/s);
});
