const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../app/static/garden-motion.js'), 'utf8');
const modulePromise = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const progress = ['wind', 'peek', 'hat', 'work', 'crown', 'bloom', 'petals'];

function numbers(frame) { return [...progress.map(key => frame[key]), ...Object.values(frame.body)]; }
function advance(director, ms, options) {
  let frame;
  for (let elapsed = 0; elapsed < ms; elapsed += 40) frame = director.update(Math.min(40, ms - elapsed), options);
  return frame;
}

test('garden clips stay finite, bounded and independent of sampling history', async () => {
  const {sampleGarden} = await modulePromise;
  for (const mode of ['idle', 'working', 'success', 'quiet']) {
    for (const variant of [0, 1, 2, 7]) {
      for (let elapsed = 0; elapsed < 44000; elapsed += 137) {
        const frame = sampleGarden({mode, elapsed, variant});
        assert.ok(numbers(frame).every(Number.isFinite), `${mode} ${elapsed}`);
        for (const key of progress) assert.ok(frame[key] >= 0 && frame[key] <= 1, `${key} ${mode} ${elapsed}`);
        assert.ok(frame.body.stretch >= .85 && frame.body.stretch <= 1.12);
        assert.ok(Math.abs(frame.body.yaw) < .3, 'the face never rotates out of view');
        assert.deepEqual(frame, sampleGarden({mode, elapsed, variant}));
      }
    }
  }
  for (const elapsed of [NaN, Infinity, -100]) assert.deepEqual(sampleGarden({elapsed}), sampleGarden({elapsed: 0}));
});

test('loop boundaries are continuous and each idle loop leaves a quiet resting interval', async () => {
  const {sampleGarden, GARDEN_DURATIONS} = await modulePromise;
  for (const mode of ['idle', 'working', 'quiet']) {
    for (const variant of [0, 1, 2]) {
      const period = GARDEN_DURATIONS[mode] + (mode === 'idle' ? variant * 2000 : 0);
      const before = numbers(sampleGarden({mode, elapsed: period - .001, variant}));
      const after = numbers(sampleGarden({mode, elapsed: period + .001, variant}));
      before.forEach((value, index) => assert.ok(Math.abs(value - after[index]) < .0001, `${mode} boundary ${index}`));
      if (mode === 'idle') {
        for (let elapsed = period - 3500; elapsed < period; elapsed += 500) {
          const frame = sampleGarden({mode, elapsed, variant});
          assert.equal(frame.active, false);
          assert.equal(frame.hat, 0);
          assert.equal(frame.peek, 0);
        }
      }
    }
  }
});

test('the same leaf arrives before the wink and rebound; light tracks only accompany work', async () => {
  const {sampleGarden} = await modulePromise;
  const peek = sampleGarden({mode: 'idle', elapsed: 5000});
  const caught = sampleGarden({mode: 'idle', elapsed: 8250});
  const hop = sampleGarden({mode: 'idle', elapsed: 9200});
  assert.ok(peek.peek > .9 && peek.hat === 0);
  assert.ok(caught.hat > .99 && caught.body.wink < .2 && caught.body.squeeze > .1);
  assert.ok(hop.hat > .99 && hop.body.hop > .1);
  for (const mode of ['idle', 'quiet', 'success']) {
    for (let elapsed = 0; elapsed < 20000; elapsed += 250) assert.equal(sampleGarden({mode, elapsed}).work, 0);
  }
  assert.ok(sampleGarden({mode: 'working', elapsed: 3000}).work > .99);
});

test('every idle variant follows the left branch before looking up at the leaf on its head', async () => {
  const {sampleGarden} = await modulePromise;
  for (const variant of [0, 1, 2]) {
    const pace = 1 + variant * .07;
    const peek = sampleGarden({mode: 'idle', elapsed: 5000 * pace, variant});
    const hat = sampleGarden({mode: 'idle', elapsed: 8250 * pace, variant});
    assert.ok(peek.body.gazeX < -.2 && peek.body.gazeY > .2, `variant ${variant} tracks the left leaf tip`);
    assert.ok(Math.abs(hat.body.gazeX) < .000001 && hat.body.gazeY > peek.body.gazeY,
      `variant ${variant} centres its gaze under the hat`);
  }
  const left = sampleGarden({mode: 'idle', elapsed: 5000, variant: 0});
  const right = sampleGarden({mode: 'idle', elapsed: 5350, variant: 1});
  assert.ok(left.body.roll * right.body.roll < 0, 'body tilt retains its alternating variation');
});

test('success finishes once, survives a work-state change, and can be triggered by a later token', async () => {
  const {GardenDirector} = await modulePromise;
  const director = new GardenDirector();
  director.update(0, {mode: 'idle', successToken: 'initial'});
  advance(director, 1700, {mode: 'success', successToken: 'first'});
  assert.ok(director.frame.crown > .9);
  advance(director, 1500, {mode: 'working', successToken: 'first'});
  assert.equal(director.mode, 'success');
  assert.ok(director.frame.crown > .9);
  advance(director, 2000, {mode: 'working', successToken: 'first'});
  assert.equal(director.mode, 'working');
  assert.equal(director.frame.crown, 0);
  advance(director, 2000, {mode: 'working', successToken: 'second'});
  assert.equal(director.mode, 'success');
  assert.ok(director.frame.bloom > .99);

  const held = new GardenDirector();
  advance(held, 14000, {mode: 'success', successToken: 'held'});
  assert.equal(held.mode, 'idle');
  assert.equal(held.pendingSuccesses, 0);
  assert.equal(held.frame.crown, 0);
});

test('paused and reduced frames freeze the active clock and resuming cannot skip the scene', async () => {
  const {GardenDirector} = await modulePromise;
  const director = new GardenDirector();
  advance(director, 8200, {mode: 'idle'});
  const frozen = director.frame;
  const elapsed = director.elapsed;
  const clock = director.clock;
  assert.deepEqual(director.update(60000, {paused: true}), frozen);
  assert.equal(director.elapsed, elapsed);
  assert.equal(director.clock, clock);
  const staticFrame = director.update(60000, {reduced: true});
  assert.equal(staticFrame.phase, 'still');
  assert.equal(staticFrame.hat, 0);
  assert.equal(staticFrame.work, 0);
  assert.equal(director.elapsed, elapsed);
  assert.deepEqual(director.update(60000, {reduced: true}), staticFrame);
  assert.deepEqual(director.update(60000, {reduced: true, paused: true}), staticFrame,
    'reduced motion is still honoured when the renderer also pauses its frame loop');
  director.update(60000, {});
  assert.equal(director.elapsed - elapsed, 80);
  assert.equal(director.clock - clock, 80);
});

test('changing modes starts from the visible pose and interpolates toward the new scene', async () => {
  const {GardenDirector} = await modulePromise;
  const director = new GardenDirector();
  advance(director, 8000, {mode: 'idle'});
  const before = numbers(director.frame);
  const start = numbers(director.update(0, {mode: 'working'}));
  assert.deepEqual(start, before);
  advance(director, 2400, {mode: 'working'});
  assert.equal(director.frame.hat, 0);
  assert.equal(director.frame.peek, 0);
  assert.ok(director.frame.work > .99);
  advance(director, 1000, {mode: 'quiet'});
  assert.equal(director.frame.work, 0);
  assert.equal(director.frame.phase, 'quiet');
});
