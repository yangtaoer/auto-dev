const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const motion = require('../../app/static/orb-motion.js');

test('each business state has a non-repeating, state-appropriate random playlist', () => {
  for (const [state, pool] of Object.entries(motion.pools)) {
    for (const previous of pool) for (const random of [0, .3, .9, 1]) {
      const selected = motion.pick(state, previous, () => random);
      assert.ok(pool.includes(selected));
      assert.notEqual(selected, previous);
    }
  }
  for (const state of ['blocked', 'error', 'sleeping']) {
    assert.ok(!motion.pools[state].includes('twirl'));
    assert.ok(!motion.pools[state].includes('hop'));
  }
});

test('all clips ease into and out of a finite, bounded 3D pose', () => {
  const neutral = motion.sample(null, 100);
  for (const name of Object.keys(motion.gestures)) {
    const clip = motion.clip(name, 100);
    assert.deepEqual(motion.sample(clip, 99), neutral);
    assert.deepEqual(motion.sample(clip, 100), neutral);
    assert.deepEqual(motion.sample(clip, 100 + clip.duration), neutral);
    let changed = false;
    for (let frame = 1; frame < 100; frame++) {
      const pose = motion.sample(clip, 100 + clip.duration * frame / 100);
      assert.ok(Object.values(pose).every(Number.isFinite), name);
      assert.ok(pose.stretch >= .86 && pose.stretch <= 1.16, name);
      assert.ok(pose.hop >= 0 && pose.hop <= .3, name);
      assert.ok(pose.wink >= .05 && pose.wink <= 1, name);
      if (JSON.stringify(pose) !== JSON.stringify(neutral)) changed = true;
    }
    assert.ok(changed, `${name} should actually animate`);
  }
});

test('exploratory clips travel through the habitat and react to foliage', () => {
  for (const name of ['roam', 'slalom', 'leafchase', 'tumble', 'float']) {
    const clip = motion.clip(name, 100, 1, 1);
    const poses = Array.from({length: 99}, (_, index) => motion.sample(clip, 100 + clip.duration * (index + 1) / 100));
    assert.ok(poses.some(pose => Math.abs(pose.sway) > .1), `${name} moves across the stage`);
    assert.ok(poses.some(pose => Math.abs(pose.depth) > .07), `${name} moves in depth`);
    assert.ok(poses.every(pose => Math.abs(pose.sway) <= .4 && Math.abs(pose.depth) <= .25), `${name} stays framed`);
  }
  const chase = motion.clip('leafchase', 0, 1, .5);
  assert.ok(motion.sample(chase, chase.duration / 2).leaf > .9);
  const quiet = motion.clip('settle', 0, -1, 1);
  assert.equal(motion.sample(quiet, quiet.duration / 2).hop, 0);
});

test('clip variations change pace and travel without making sampling nondeterministic', () => {
  const slow = motion.clip('roam', 100, 1, 1);
  const quick = motion.clip('roam', 100, -1, 0);
  assert.ok(slow.duration > quick.duration);
  const sampleAt = slow.startedAt + slow.duration * .3;
  assert.deepEqual(motion.sample(slow, sampleAt), motion.sample(slow, sampleAt));
  assert.ok(motion.sample(slow, sampleAt).sway * motion.sample(quick, quick.startedAt + quick.duration * .3).sway < 0);
});

test('botanical habitats cross-fade smoothly and cycle through leaves, flowers and canopy', () => {
  assert.deepEqual(motion.habitats, ['meadow', 'blossom', 'canopy']);
  const period = 31200;
  for (let index = 0; index < 6; index++) {
    const start = motion.habitatAt(index * period);
    assert.equal(start.current, motion.habitats[index % 3]);
    assert.equal(start.next, motion.habitats[(index + 1) % 3]);
    assert.equal(start.mix, 0);
    assert.equal(motion.habitatAt(index * period + 26000).mix, 0);
    assert.equal(motion.habitatAt(index * period + 28600).mix, .5);
    const end = motion.habitatAt((index + 1) * period - .01);
    assert.ok(end.mix > .999999);
    assert.equal(end.next, motion.habitatAt((index + 1) * period).current);
  }
});

test('reduced motion preserves a single still habitat and invalid times are harmless', () => {
  for (const time of [0, 31000, 62000, 120000, NaN, Infinity, -1]) {
    assert.deepEqual(motion.habitatAt(time, true), {current: 'meadow', next: 'blossom', mix: 0});
  }
  for (const time of [NaN, Infinity, -1]) assert.deepEqual(motion.habitatAt(time), motion.habitatAt(0));
});

function controller() {
  let now = 100, serial = 0;
  const timers = new Map();
  const fakeWindow = {
    AutoDevOrbMotion: motion,
    setTimeout(fn, delay) { const id = ++serial; timers.set(id, {fn, at: now + delay}); return id; },
  };
  const document = {hidden: false};
  const clearTimeout = id => timers.delete(id);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/orb-character.js'), 'utf8'), {
    window: fakeWindow, document, performance: {now: () => now}, clearTimeout,
  });
  const orb = Object.create(fakeWindow.AutoDevOrb.prototype);
  Object.assign(orb, {
    root: {dataset: {}}, options: {ambient: true}, state: 'working', lastGesture: '',
    visible: true, scene: {invalidate() {}}, _schedule() {},
    motionDriver: {setMode() {}, setState(value) { this.state = value; }},
  });
  return {orb, timers, document, window: fakeWindow, next() {
    const entry = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
    assert.ok(entry, 'a next animation must remain scheduled');
    now = entry[1].at; timers.delete(entry[0]); entry[1].fn();
  }};
}

test('ambient loop continues without changing the actual task state', () => {
  const {orb, next} = controller();
  orb._scheduleAmbient(1);
  let previous = '', clips = 0;
  for (let step = 0; step < 16; step++) {
    next();
    const name = orb.root.dataset.gesture;
    if (name !== previous) { clips++; previous = name; }
    assert.equal(orb.state, 'working');
  }
  assert.ok(clips >= 7);
});

test('hidden, paused, reduced-motion and destroyed characters stop the random timer', () => {
  for (const property of ['manualPaused', 'reducedMotion', 'destroyed', 'visible', 'hidden']) {
    const {orb, timers, document} = controller();
    orb._scheduleAmbient(1);
    assert.equal(timers.size, 1);
    if (property === 'hidden') document.hidden = true;
    else orb[property] = property !== 'visible';
    orb._scheduleAmbient();
    assert.equal(timers.size, 0, property);
  }
});

test('suspended characters expose a neutral pose rather than advancing a clip', () => {
  for (const property of ['manualPaused', 'reducedMotion', 'visible', 'hidden']) {
    const {orb, document} = controller();
    orb.motionClip = motion.clip('hop', 0, 1, .5);
    if (property === 'hidden') document.hidden = true;
    else orb[property] = property !== 'visible';
    assert.deepEqual(JSON.parse(JSON.stringify(orb.sampleMotion(orb.motionClip.duration / 2))), motion.sample(null, 0));
  }
});

test('conversation portals stay inside the viewport and follow their anchor', () => {
  const {orb, window} = controller();
  Object.assign(window, {innerWidth: 1000, innerHeight: 800});
  let rect = {left: 24, top: 450, right: 220, width: 196, height: 180};
  orb.root.getBoundingClientRect = () => rect;
  orb.root.closest = () => true;
  orb.whisper = {style: {}, dataset: {}, getBoundingClientRect: () => ({height: 68})};
  orb._positionWhisper();
  assert.equal(orb.whisper.dataset.side, 'right');
  assert.equal(orb.whisper.style.left, '234px');
  const firstTop = Number.parseFloat(orb.whisper.style.top);
  rect = {...rect, top: 300};
  orb._positionWhisper();
  assert.equal(Number.parseFloat(orb.whisper.style.top), firstTop - 150);
  Object.assign(window, {innerWidth: 320, innerHeight: 200});
  rect = {left: 270, top: 150, right: 470, width: 200, height: 180};
  orb._positionWhisper();
  const left = Number.parseFloat(orb.whisper.style.left), top = Number.parseFloat(orb.whisper.style.top);
  assert.ok(left >= 16 && left + Number.parseFloat(orb.whisper.style.width) <= 304);
  assert.ok(top >= 16 && top + 68 <= 184);
});

test('outside interaction and Escape dismiss the conversation while orb taps preserve it', () => {
  const {orb, timers, window} = controller();
  let shown = true;
  const target = {};
  orb.root.contains = item => item === target;
  orb.whisper = {classList: {contains: () => shown, remove() { shown = false; }}};
  orb.whisperTimer = window.setTimeout(() => {}, 2000);
  orb._onWhisperDismiss({type: 'pointerdown', target});
  assert.equal(shown, true);
  orb._onWhisperDismiss({type: 'keydown', key: 'Enter'});
  assert.equal(shown, true);
  orb._onWhisperDismiss({type: 'keydown', key: 'Escape'});
  assert.equal(shown, false);
  assert.equal(timers.size, 0);
  shown = true;
  orb._onWhisperDismiss({type: 'pointerdown', target: {}});
  assert.equal(shown, false);
});

test('destroy removes the portal, stops its timer and unregisters viewport listeners', () => {
  const {orb, timers, window, document} = controller();
  const removedWindow = [], removedDocument = [];
  window.removeEventListener = (name, listener, capture) => removedWindow.push([name, capture]);
  document.removeEventListener = name => removedDocument.push(name);
  orb.root.removeEventListener = () => {};
  orb.reduceMotionQuery = {removeEventListener() {}};
  orb.scene = {destroy() {}};
  let removed = 0;
  orb.whisper = {remove() { removed++; }};
  orb.whisperTimer = window.setTimeout(() => {}, 2600);
  orb._scheduleAmbient(1000);
  assert.equal(timers.size, 2);
  orb.destroy();
  assert.equal(removed, 1);
  assert.equal(orb.whisper, null);
  assert.equal(timers.size, 0);
  assert.ok(removedWindow.some(([name, capture]) => name === 'scroll' && capture === true));
  assert.ok(removedWindow.some(([name]) => name === 'resize'));
  assert.ok(removedDocument.includes('pointerdown'));
  assert.ok(removedDocument.includes('keydown'));
  orb.destroy();
  assert.equal(removed, 1);
});
