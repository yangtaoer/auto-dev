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
  return {orb, timers, document, next() {
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
