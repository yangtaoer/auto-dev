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
function point(event, progress) { return event.start + (event.end - event.start) * progress; }

test('long garden scenes stay finite, bounded and independent of sampling history', async () => {
  const {sampleGarden, gardenDuration} = await modulePromise;
  for (const mode of ['idle', 'working', 'success', 'quiet']) {
    for (const variant of [0, 1, 2, 7, 0xffffffff]) {
      for (let elapsed = 0; elapsed < gardenDuration(mode, variant) * 1.2; elapsed += 237) {
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

test('idle is a minute-scale shuffled behavior bag with varied pace, strength and breathing space', async () => {
  const {gardenEpisodes, gardenDuration} = await modulePromise;
  const orders = new Set();
  const periods = new Set();
  for (let variant = 0; variant < 100; variant += 1) {
    const episodes = gardenEpisodes(variant);
    const period = gardenDuration('idle', variant);
    assert.ok(period >= 60000 && period <= 100000);
    assert.equal(episodes.length, 9);
    assert.equal(new Set(episodes.map(event => event.kind)).size, 9);
    assert.ok(episodes[0].start <= 1800, 'the character wakes up promptly after mount');
    assert.ok(Object.isFrozen(episodes) && episodes.every(Object.isFrozen));
    episodes.forEach((event, i) => {
      assert.ok(event.strength >= .76 && event.strength <= 1);
      assert.ok(event.end > event.start && event.end < period);
      assert.ok(event.end - event.start <= 9100, 'gestures are not stretched into slow motion');
      if (i) assert.ok(event.start - episodes[i - 1].end >= 1800);
    });
    assert.ok(period - episodes.at(-1).end >= 5800);
    orders.add(episodes.map(event => event.kind).join(','));
    periods.add(Math.round(period));
    assert.deepEqual(episodes, gardenEpisodes(variant));
    assert.ok(gardenDuration('working', variant) >= 22000 && gardenDuration('working', variant) <= 35000);
  }
  assert.ok(orders.size > 40, 'many independently ordered itineraries, not a few fixed video clips');
  assert.ok(periods.size > 90, 'timing varies per itinerary');
});

test('loop and every idle episode boundary remain continuous with quiet intervals', async () => {
  const {sampleGarden, gardenDuration, gardenEpisodes} = await modulePromise;
  for (const mode of ['idle', 'working', 'quiet']) {
    for (const variant of [0, 1, 2, 99]) {
      const period = gardenDuration(mode, variant);
      const boundaries = [period];
      if (mode === 'idle') boundaries.push(...gardenEpisodes(variant).flatMap(event => [event.start, event.end]));
      for (const boundary of boundaries) {
        const before = numbers(sampleGarden({mode, elapsed: boundary - .001, variant}));
        const after = numbers(sampleGarden({mode, elapsed: boundary + .001, variant}));
        before.forEach((value, index) => assert.ok(Math.abs(value - after[index]) < .0001, `${mode} ${boundary} channel ${index}`));
      }
      if (mode === 'idle') {
        for (let elapsed = period - 4000; elapsed < period; elapsed += 500) {
          const frame = sampleGarden({mode, elapsed, variant});
          assert.equal(frame.active, false);
          assert.equal(frame.hat, 0);
          assert.equal(frame.peek, 0);
        }
      }
    }
  }
});

test('every hat variant watches the left leaf and catches it before winking and rebounding', async () => {
  const {sampleGarden, gardenEpisodes} = await modulePromise;
  for (const variant of [0, 1, 2, 7, 12345]) {
    const event = gardenEpisodes(variant).find(item => item.kind === 'leaf-hat');
    const peek = sampleGarden({elapsed: point(event, .31), variant});
    const caught = sampleGarden({elapsed: point(event, .5225), variant});
    const bounce = sampleGarden({elapsed: point(event, .655), variant});
    assert.ok(peek.peek > .99 && peek.hat === 0);
    assert.ok(peek.body.gazeX < -.23 && peek.body.gazeY > .2);
    assert.ok(caught.hat > .99 && caught.body.wink < .1 && caught.body.squeeze > .1);
    assert.ok(Math.abs(caught.body.gazeX) < .000001 && caught.body.gazeY > peek.body.gazeY);
    assert.ok(bounce.hat > .99 && bounce.body.hop > .098);
  }
});

test('golden orbital light only accompanies working, whose styles last 22–35 seconds', async () => {
  const {sampleGarden, gardenDuration} = await modulePromise;
  for (const mode of ['idle', 'quiet', 'success']) {
    for (let elapsed = 0; elapsed < 100000; elapsed += 500) assert.equal(sampleGarden({mode, elapsed}).work, 0);
  }
  const flavors = new Set();
  for (let variant = 0; variant < 20; variant += 1) {
    const frame = sampleGarden({mode: 'working', elapsed: 9000, variant});
    flavors.add(frame.episode);
    assert.ok(frame.work > .99);
    assert.ok(sampleGarden({mode: 'working', elapsed: gardenDuration('working', variant) - 6000, variant}).work > .99);
  }
  assert.equal(flavors.size, 3);
});

test('success is a 12-second one-shot, survives task changes, and later tokens celebrate again', async () => {
  const {GardenDirector, GARDEN_DURATIONS} = await modulePromise;
  assert.equal(GARDEN_DURATIONS.success, 12000);
  const director = new GardenDirector({seed: 17});
  director.update(0, {mode: 'idle', successToken: 'initial'});
  advance(director, 2700, {mode: 'success', successToken: 'first'});
  assert.ok(director.frame.crown > .99 && director.frame.bloom > .99);
  advance(director, 5300, {mode: 'working', successToken: 'first'});
  assert.equal(director.mode, 'success');
  assert.ok(director.frame.crown > .99);
  advance(director, 4700, {mode: 'working', successToken: 'first'});
  assert.equal(director.mode, 'working');
  assert.equal(director.frame.crown, 0);
  advance(director, 2800, {mode: 'working', successToken: 'second'});
  assert.equal(director.mode, 'success');
  assert.ok(director.frame.bloom > .99);

  const held = new GardenDirector({seed: 17});
  advance(held, 40000, {mode: 'success', successToken: 'held'});
  assert.equal(held.mode, 'idle');
  assert.equal(held.pendingSuccesses, 0);
  assert.equal(held.frame.crown, 0);
});

test('seeded instances reproduce motion but different instances and later itineraries diverge', async () => {
  const {GardenDirector, gardenDuration, gardenEpisodes} = await modulePromise;
  const first = new GardenDirector({seed: 41});
  const matching = new GardenDirector({seed: 41});
  const other = new GardenDirector({seed: 42});
  const variants = new Set([first.variant]);
  assert.notEqual(first.variant, other.variant);
  for (let cycle = 0; cycle < 5; cycle += 1) {
    const previous = first.variant;
    const lastEpisode = gardenEpisodes(previous).at(-1).kind;
    const remaining = gardenDuration('idle', previous) - first.elapsed + 1;
    assert.deepEqual(advance(first, remaining), advance(matching, remaining));
    assert.notEqual(first.variant, previous);
    assert.notEqual(gardenEpisodes(first.variant)[0].kind, lastEpisode);
    variants.add(first.variant);
  }
  assert.equal(variants.size, 6);
  const fresh = new GardenDirector({seed: 41});
  first.reset();
  assert.deepEqual(advance(first, 10000), advance(fresh, 10000));
});

test('working changes style after each long cycle rather than repeating the same orbit', async () => {
  const {GardenDirector, gardenDuration, sampleGarden} = await modulePromise;
  const director = new GardenDirector({seed: 77});
  director.update(0, {mode: 'working'});
  for (let cycle = 0; cycle < 6; cycle += 1) {
    const previous = sampleGarden({mode: 'working', variant: director.variant, elapsed: 9000}).episode;
    advance(director, gardenDuration('working', director.variant) - director.elapsed + 1, {mode: 'working'});
    const next = sampleGarden({mode: 'working', variant: director.variant, elapsed: 9000}).episode;
    assert.notEqual(previous, next);
  }
});

test('paused and reduced frames freeze every active clock; resuming never skips a scene', async () => {
  const {GardenDirector} = await modulePromise;
  const director = new GardenDirector({seed: 41});
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
  assert.deepEqual(director.update(60000, {reduced: true, paused: true}), staticFrame);
  assert.equal(director.react('tap'), false);
  director.update(60000, {});
  assert.equal(director.elapsed - elapsed, 80);
  assert.equal(director.clock - clock, 80);
});

test('mode changes interpolate from the visible pose instead of cutting between clips', async () => {
  const {GardenDirector, gardenEpisodes} = await modulePromise;
  const director = new GardenDirector({seed: 41});
  const event = gardenEpisodes(director.variant).find(item => item.kind === 'leaf-hat');
  advance(director, point(event, .53), {mode: 'idle'});
  const before = numbers(director.frame);
  const start = numbers(director.update(0, {mode: 'working'}));
  start.forEach((value, index) => assert.ok(Math.abs(value - before[index]) < 1e-12));
  advance(director, 2600, {mode: 'working'});
  assert.equal(director.frame.hat, 0);
  assert.equal(director.frame.peek, 0);
  assert.ok(director.frame.work > .99);
  advance(director, 1000, {mode: 'quiet'});
  assert.equal(director.frame.work, 0);
  assert.equal(director.frame.phase, 'quiet');
});

test('tap is a throttled live response, freezes idle itinerary and never changes task state', async () => {
  const {GardenDirector} = await modulePromise;
  const director = new GardenDirector({seed: 5});
  director.update(0, {mode: 'idle', successToken: 'baseline'});
  assert.equal(director.react('tap'), true);
  assert.equal(director.react('tap'), false);
  const timeline = director.elapsed;
  advance(director, 700, {mode: 'idle', successToken: 'baseline'});
  assert.equal(director.frame.episode, 'tap-rebound');
  assert.ok(director.frame.body.hop > .12);
  assert.equal(director.elapsed, timeline);
  assert.equal(director.mode, 'idle');
  assert.equal(director.pendingSuccesses, 0);
  assert.equal(director.lastToken, 'baseline');
  assert.equal(director.frame.work, 0);
  const reactionElapsed = director.reaction.elapsed;
  director.update(9000, {paused: true});
  assert.equal(director.reaction.elapsed, reactionElapsed);
  advance(director, 5000, {mode: 'idle'});
  assert.equal(director.reaction, null);
  assert.ok(director.elapsed > timeline);
});

test('hover waits for a held leaf, while tap responds immediately without losing the leaf', async () => {
  const {GardenDirector, gardenEpisodes} = await modulePromise;
  const director = new GardenDirector({seed: 41});
  const event = gardenEpisodes(director.variant).find(item => item.kind === 'leaf-hat');
  advance(director, point(event, .52));
  assert.ok(director.frame.hat > .99);
  assert.equal(director.react('approach'), true);
  director.update(40);
  assert.equal(director.reaction, null);
  assert.equal(director.queuedReaction.kind, 'approach');
  assert.equal(director.react('leave'), false);
  assert.equal(director.queuedReaction, null);
  assert.equal(director.react('tap'), true);
  director.react('leave');
  assert.equal(director.queuedReaction.kind, 'tap');
  const oldElapsed = director.elapsed;
  advance(director, 500);
  assert.equal(director.frame.episode, 'tap-rebound');
  assert.equal(director.reaction.overlay, true);
  assert.equal(director.mode, 'idle');
  assert.ok(director.frame.hat > .99);
  assert.ok(director.elapsed > oldElapsed);
  assert.ok(director.frame.body.hop > .04);
});

test('working accepts gestures without losing its work lights; quiet/success reject play', async () => {
  const {GardenDirector} = await modulePromise;
  const director = new GardenDirector({seed: 41});
  advance(director, 5000, {mode: 'working'});
  assert.equal(director.react('tap'), true);
  advance(director, 700, {mode: 'working'});
  assert.equal(director.frame.episode, 'tap-rebound');
  assert.equal(director.mode, 'working');
  assert.ok(director.frame.work > .99);
  assert.equal(director.frame.crown, 0);
  director.update(0, {mode: 'quiet'});
  assert.equal(director.react('tap'), false);
  assert.equal(director.queuedReaction, null);
  director.update(0, {mode: 'success'});
  assert.equal(director.react('approach'), false);
  assert.equal(director.react('unknown'), false);
});
