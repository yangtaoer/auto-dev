const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function character() {
  const window = {}, document = {hidden: false};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/orb-character.js'), 'utf8'), {
    window, document, performance: {now: () => 100},
  });
  const orb = Object.create(window.AutoDevOrb.prototype);
  const events = [], classes = new Set();
  let invalidations = 0, legacyMoves = 0;
  Object.assign(orb, {
    root: {dataset: {}, getBoundingClientRect: () => ({left: 20, top: 40, width: 200, height: 180}),
      classList: {contains: name => classes.has(name), toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }}},
    pointer: {targetX: 0, targetY: 0, near: false}, visible: true,
    scene: {garden: {}, react: kind => events.push(kind), invalidate() {invalidations++;}},
    state: 'working', gardenSuccessToken: 7, tapCycle: 0,
    motionDriver: {bounceOnce() {legacyMoves++;}, spinOnce() {legacyMoves++;}, burstOnce() {legacyMoves++;}},
    _temporaryDriverState() {}, _markReaction() {}, _showWhisper() {},
  });
  return {orb, document, events, invalidations: () => invalidations, legacyMoves: () => legacyMoves};
}

test('pointer approach reacts once while movement updates gaze without restarting the scene clock', () => {
  const {orb, events, invalidations} = character();
  for (let x = 40; x <= 210; x += 10) orb._onPointerMove({clientX: x, clientY: 100, pointerType: 'mouse'});
  assert.deepEqual(events, ['approach']);
  assert.equal(orb.pointer.near, true);
  assert.ok(orb.pointer.targetX > .6, 'nearby gaze acknowledges a person without large body rotation');
  assert.equal(invalidations(), 0);
  orb._onPointerMove({clientX: 400, clientY: 100, pointerType: 'mouse'});
  orb._onPointerMove({clientX: 420, clientY: 100, pointerType: 'mouse'});
  assert.deepEqual(events, ['approach', 'leave']);
  assert.equal(orb.pointer.near, false);
  assert.equal(orb.state, 'working');
  assert.equal(orb.gardenSuccessToken, 7);
});

test('pointer leave clears attention and touch dragging never creates persistent hover', () => {
  const {orb, events, document} = character();
  orb._onPointerMove({clientX: 160, clientY: 130, pointerType: 'touch'});
  assert.deepEqual(events, []);
  assert.equal(orb.pointer.near, false);
  orb._onPointerMove({clientX: 160, clientY: 130, pointerType: 'pen'});
  orb._onPointerLeave();
  orb._onPointerLeave();
  assert.deepEqual(events, ['approach', 'leave']);
  assert.equal(orb.pointer.targetX, 0);
  assert.equal(orb.pointer.targetY, 0);
  document.hidden = true;
  orb._onPointerMove({clientX: 160, clientY: 130, pointerType: 'mouse'});
  assert.deepEqual(events, ['approach', 'leave']);
});

test('tap, Enter and Space ask for garden interaction without mutating business state or success tokens', () => {
  const {orb, events, legacyMoves, invalidations} = character();
  let prevented = 0;
  orb._onInteract();
  for (const key of ['Enter', ' ', 'ArrowLeft']) orb._onInteractKey({key, preventDefault() {prevented++;}});
  assert.deepEqual(events, ['tap', 'tap', 'tap']);
  assert.equal(prevented, 2);
  assert.equal(legacyMoves(), 0, 'the old driver must not compete with garden body choreography');
  assert.equal(invalidations(), 0);
  assert.equal(orb.state, 'working');
  assert.equal(orb.gardenSuccessToken, 7);
  orb.manualPaused = true;
  orb._onInteract();
  assert.equal(events.length, 3);
});

test('repeated running-state polling does not reset animation timing or render scheduling', () => {
  const {orb, invalidations} = character();
  orb.setRunning(true);
  for (let poll = 0; poll < 8; poll++) orb.setRunning(true);
  assert.equal(invalidations(), 1);
  orb.setRunning(false);
  orb.setRunning(false);
  assert.equal(invalidations(), 2);
});
