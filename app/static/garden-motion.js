/* The garden and its character share one clock: a caught leaf belongs to the
 * same physical interaction as its gaze, squash and rebound. No DOM or timers. */

export const GARDEN_DURATIONS = Object.freeze({idle: 84000, working: 28000, success: 12000, quiet: 7000});

const TAU = Math.PI * 2;
const PROGRESS = ['wind', 'peek', 'hat', 'work', 'crown', 'bloom', 'petals'];
const finite = value => Number.isFinite(value) ? value : 0;
const clamp = value => Math.max(0, Math.min(1, finite(value)));
const smooth = value => { const x = clamp(value); return x * x * (3 - 2 * x); };
const ramp = (time, start, end) => smooth((time - start) / (end - start));
const envelope = (time, inAt, fullAt, outAt, endAt) => ramp(time, inAt, fullAt) * (1 - ramp(time, outAt, endAt));
const pulse = (time, start, end) => Math.sin(Math.PI * clamp((time - start) / (end - start))) ** 2;
const normalizeMode = mode => ['idle', 'working', 'success', 'quiet'].includes(mode) ? mode : 'idle';
const normalizedVariant = value => Math.abs(Math.trunc(finite(value))) >>> 0;

function hash(value) {
  let x = normalizedVariant(value) + 0x9e3779b9;
  x = Math.imul(x ^ x >>> 16, 0x21f0aaad);
  x = Math.imul(x ^ x >>> 15, 0x735a2d97);
  return (x ^ x >>> 15) >>> 0;
}

function randomStream(seed) {
  let state = normalizedVariant(seed);
  return () => { state = hash(state); return state / 0x100000000; };
}

const schedules = new Map();
const BASE_EPISODES = Object.freeze({
  'leaf-hat': 17500, 'leaf-peek': 7400, listen: 6200, breeze: 7800, curious: 7000,
});

function schedule(variant) {
  const key = normalizedVariant(variant);
  if (schedules.has(key)) return schedules.get(key);
  const random = randomStream(key ^ 0x4c656166);
  const kinds = Object.keys(BASE_EPISODES);
  // A shuffled bag gives the garden several different moments, never five
  // consecutive replays of the same leaf trick. Every bag still has one hat.
  for (let i = kinds.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [kinds[i], kinds[j]] = [kinds[j], kinds[i]];
  }
  let cursor = 2800 + random() * 2400;
  const episodes = kinds.map(kind => {
    const length = BASE_EPISODES[kind] * (.88 + random() * .28);
    const event = Object.freeze({kind, start: cursor, end: cursor + length,
      strength: .76 + random() * .24, direction: random() < .5 ? -1 : 1});
    cursor += length + 3200 + random() * 3600;
    return event;
  });
  const result = Object.freeze({episodes: Object.freeze(episodes), duration: Math.max(60000, cursor + 3600 + random() * 3200)});
  // Pure sampling remains deterministic; this bounded memo only avoids
  // rebuilding an immutable itinerary on every rendered frame.
  if (schedules.size >= 32) schedules.delete(schedules.keys().next().value);
  schedules.set(key, result);
  return result;
}

export function gardenEpisodes(variant = 0) { return schedule(variant).episodes; }
export function gardenDuration(mode = 'idle', variant = 0) {
  const kind = normalizeMode(mode);
  if (kind === 'idle') return schedule(variant).duration;
  if (kind === 'working') return 22000 + hash(variant ^ 0x776f726b) % 13001;
  return GARDEN_DURATIONS[kind];
}

function neutral(time = 0, phase = 'rest') {
  return {
    time, wind: .08, peek: 0, hat: 0, work: 0, crown: 0, bloom: 0, petals: 0,
    body: {hop: 0, sway: 0, depth: 0, roll: 0, pitch: 0, yaw: 0, stretch: 1, gazeX: 0, gazeY: 0, wink: 1, joy: 0, squeeze: 0},
    active: false, phase, episode: 'rest',
  };
}

function episodeFrame(event, elapsed) {
  const p = clamp((elapsed - event.start) / (event.end - event.start));
  const frame = neutral();
  const b = frame.body;
  const strength = event.strength;
  const direction = event.direction;
  const awake = envelope(p, 0, .17, .79, 1);
  frame.episode = event.kind;
  frame.active = awake > .001;
  frame.phase = event.kind;
  frame.wind += awake * .065;

  if (event.kind === 'leaf-hat') {
    const notice = envelope(p, .02, .18, .83, .97);
    const caught = envelope(p, .34, .48, .72, .9);
    const bounce = pulse(p, .59, .72);
    frame.peek = envelope(p, .12, .3, .76, .96);
    frame.hat = caught;
    frame.phase = p < .12 ? 'notice' : p < .34 ? 'peek' : p < .59 ? 'catch' : p < .72 ? 'rebound' : 'return-leaf';
    // The branch is always on the left. Random body tilt must not make the
    // eyes watch an imaginary leaf on the opposite side of the garden.
    b.gazeX = -notice * (1 - caught) * (.27 + Math.sin(p * 18) * .025);
    b.gazeY = notice * (.24 + caught * .14);
    b.roll = direction * notice * (.12 * (1 - caught) - caught * .025) * strength;
    b.yaw = direction * notice * .055 * (1 - caught);
    b.pitch = -notice * .035;
    b.sway = direction * notice * .025 * strength;
    b.depth = notice * -.018;
    b.wink = 1 - .93 * pulse(p, .49, .555);
    b.squeeze = pulse(p, .475, .565) * .35 + pulse(p, .69, .77) * .08;
    b.hop = bounce * .13 * strength;
    b.stretch += bounce * .035 - b.squeeze * .085;
    b.joy = pulse(p, .565, .75) * .44;
  } else if (event.kind === 'leaf-peek') {
    frame.peek = envelope(p, .12, .37, .64, .94) * (.82 + strength * .18);
    b.gazeX = -.28 * awake;
    b.gazeY = .27 * awake;
    b.roll = -.085 * awake * strength;
    b.yaw = -.075 * awake;
    b.pitch = -.035 * awake;
    b.sway = -.028 * awake;
    b.wink = 1 - .78 * pulse(p, .49, .6);
  } else if (event.kind === 'listen') {
    b.gazeX = direction * awake * .2;
    b.gazeY = awake * .12;
    b.roll = direction * awake * .12 * strength;
    b.yaw = direction * awake * .1;
    b.pitch = Math.sin(p * TAU * 2) * awake * .025;
    b.wink = 1 - .48 * pulse(p, .47, .62);
  } else if (event.kind === 'breeze') {
    frame.wind += awake * .2 * strength;
    b.sway = Math.sin(p * TAU * 1.5) * awake * .05 * strength;
    b.roll = -b.sway * .9;
    b.gazeX = direction * Math.sin(p * Math.PI) * awake * .23;
    b.gazeY = awake * .25;
    b.pitch = -.045 * awake;
    b.joy = pulse(p, .28, .76) * .3;
  } else if (event.kind === 'tap-rebound') {
    b.squeeze = pulse(p, 0, .23) * .65;
    b.hop = pulse(p, .14, .55) * .19 * strength + pulse(p, .59, .88) * .045;
    b.stretch += -.11 * b.squeeze + pulse(p, .18, .35) * .065;
    b.roll = direction * Math.sin(p * TAU) * awake * .095;
    b.gazeY = awake * .2;
    b.joy = envelope(p, .13, .3, .7, .94) * .75;
    b.wink = 1 - .6 * pulse(p, .015, .22);
  } else {
    b.gazeX = Math.sin(p * TAU * 1.4) * awake * .26 * direction;
    b.gazeY = awake * (.12 + Math.sin(p * Math.PI) * .1);
    b.yaw = b.gazeX * .32;
    b.pitch = -.055 * pulse(p, .16, .5) + .035 * pulse(p, .54, .87);
    b.roll = Math.sin(p * TAU) * awake * .06;
    b.wink = 1 - .55 * pulse(p, .63, .75);
  }
  return frame;
}

/**
 * Pure, repeatable choreography sampler. elapsed is ms, distances are scene
 * units and angles radians. wink is eye openness. peek and hat drive the SAME
 * physical leaf; work is the only signal enabling golden orbital light.
 */
export function sampleGarden({mode = 'idle', elapsed = 0, variant = 0, reduced = false} = {}) {
  const kind = normalizeMode(mode);
  const milliseconds = Math.max(0, finite(elapsed));
  const seed = normalizedVariant(variant);
  let frame = neutral(milliseconds / 1000);
  if (reduced) return {...neutral(0, 'still'), wind: 0};

  if (kind === 'success') {
    const t = milliseconds / 1000;
    if (milliseconds >= GARDEN_DURATIONS.success) return frame;
    const b = frame.body;
    frame.active = t > 0;
    frame.episode = 'celebration';
    frame.phase = t < .85 ? 'gather' : t < 1.8 ? 'spring' : t < 8.4 ? 'flower-crown' : 'settle';
    b.squeeze = pulse(t, 0, .95) * .95 + pulse(t, 9.6, 10.65) * .14;
    b.hop = envelope(t, .65, 1.6, 3, 4.7) * .36 + pulse(t, 5.1, 7.4) * .055;
    b.stretch = 1 - b.squeeze * .14 + pulse(t, .65, 1.5) * .105;
    b.roll = Math.sin(t * 1.65) * envelope(t, 1, 2, 8.4, 10.8) * .05;
    b.pitch = -.05 * envelope(t, .5, 1.8, 8.3, 11);
    b.yaw = Math.sin(t * .9) * envelope(t, 3.8, 5.2, 7.7, 9.1) * .085;
    b.joy = envelope(t, .6, 1.45, 8.4, 11.5);
    b.wink = 1 - .86 * pulse(t, .22, .85) - .4 * pulse(t, 6.7, 7.15);
    b.gazeY = envelope(t, .65, 1.6, 8.1, 10.7) * .3;
    frame.crown = envelope(t, 1.1, 2.2, 8.4, 11.7);
    frame.bloom = envelope(t, 1.25, 2.6, 8.8, 11.65);
    frame.petals = envelope(t, 2.6, 3.4, 9.2, 11.65);
    frame.wind += .12 * envelope(t, .8, 2.1, 9.2, 11.7);
    return frame;
  }

  const period = gardenDuration(kind, seed);
  const local = milliseconds % period;
  const cycle = local / period;
  if (kind === 'idle') {
    const event = gardenEpisodes(seed).find(item => local >= item.start && local <= item.end);
    if (event) frame = episodeFrame(event, local);
  } else if (kind === 'working') {
    const b = frame.body;
    const t = local / 1000;
    const seconds = period / 1000;
    const engaged = envelope(t, .35, 2.45, seconds - 3.8, seconds - .15);
    const flavor = hash(seed) % 3;
    const swaySpeed = [.5, .38, .61][flavor];
    const strength = .82 + hash(seed ^ 81) % 19 / 100;
    frame.work = engaged;
    frame.active = engaged > .001;
    frame.episode = ['working-orbit', 'working-observe', 'working-trace'][flavor];
    frame.phase = t < 2.45 ? 'charge' : t > seconds - 3.8 ? 'land' : frame.episode;
    frame.wind += engaged * .14;
    b.squeeze = pulse(t, .1, 1.7) * .2;
    b.hop = engaged * (.14 + Math.sin(t * .83) * .025);
    b.stretch += -.035 * b.squeeze + .009 * Math.sin(t * 1.4) * engaged;
    b.sway = Math.sin(t * swaySpeed) * engaged * .05 * strength;
    b.depth = Math.sin(t * swaySpeed + .5) * engaged * .035;
    b.yaw = Math.sin(t * .43 + flavor) * engaged * .17;
    b.roll = -b.sway * .5;
    b.pitch = -.055 * engaged;
    b.gazeX = Math.sin(t * .77 + flavor) * engaged * .24;
    b.gazeY = engaged * (.32 + Math.cos(t * .67) * .07);
    b.wink = 1 - .65 * pulse(t, 6.2 + flavor, 6.75 + flavor) - .45 * pulse(t, 16.1, 16.65);
  } else frame.phase = 'quiet';

  // Integer cycles meet exactly at neutral boundaries; unlike a short video,
  // breathing continues between independently timed, shuffled interactions.
  frame.time = milliseconds / 1000;
  frame.body.stretch += Math.sin(cycle * TAU * (kind === 'idle' ? 11 : 3)) * .004;
  frame.wind += (1 - Math.cos(cycle * TAU * 3)) * .02;
  return frame;
}

function mixFrames(from, to, amount) {
  const weight = smooth(amount);
  const frame = {...to, body: {...to.body}};
  for (const key of PROGRESS) frame[key] = from[key] + (to[key] - from[key]) * weight;
  for (const key of Object.keys(frame.body)) frame.body[key] = from.body[key] + (to.body[key] - from.body[key]) * weight;
  frame.active = to.active || weight < 1 && from.active;
  return frame;
}

/**
 * Per-instance seeded behavior, without timers or wall-clock animation time.
 * A long shuffled idle itinerary is followed by another independent itinerary.
 * A success token celebrates once; react() never changes business/task state.
 * Paused and reduced motion freeze every clock, including queued interaction
 * cooldowns. Long visible-frame gaps are capped so no leaf suddenly teleports.
 */
export class GardenDirector {
  constructor({seed, variant = 0, blendMs = 650} = {}) {
    this.seed = seed === undefined ? Math.floor(Math.random() * 0x100000000) : normalizedVariant(seed);
    this.initialVariant = normalizedVariant(variant);
    this.blendMs = Math.max(1, finite(blendMs) || 650);
    this.reset();
  }

  reset() {
    this.random = randomStream(this.seed ^ this.initialVariant);
    this.variant = Math.floor(this.random() * 0x100000000);
    this.elapsed = 0;
    this.clock = 0;
    this.mode = 'idle';
    this.requestedMode = 'idle';
    this.lastInputMode = null;
    this.lastToken = undefined;
    this.tokenSeen = false;
    this.pendingSuccesses = 0;
    this.transition = null;
    this.reduced = false;
    this.reaction = null;
    this.queuedReaction = null;
    this.lastReaction = {approach: -Infinity, tap: -Infinity};
    this.frame = sampleGarden({mode: 'idle', elapsed: 0, variant: this.variant});
    return this.frame;
  }

  _switch(mode) {
    if (this.mode === mode) return;
    this.transition = {from: this.frame, elapsed: 0};
    this.mode = mode;
    this.elapsed = 0;
    this.reaction = null;
    this.queuedReaction = null;
  }

  _nextVariant() {
    const previous = this.variant;
    const last = gardenEpisodes(previous).at(-1).kind;
    for (let attempt = 0; attempt < 32; attempt += 1) {
      this.variant = Math.floor(this.random() * 0x100000000);
      if (this.variant !== previous && gardenEpisodes(this.variant)[0].kind !== last &&
          (this.mode !== 'working' || hash(this.variant) % 3 !== hash(previous) % 3)) return;
    }
    // A deterministic fallback also guarantees no adjacent repeated episode.
    do { this.variant = (this.variant + 1) >>> 0; }
    while (gardenEpisodes(this.variant)[0].kind === last ||
      this.mode === 'working' && hash(this.variant) % 3 === hash(previous) % 3);
  }

  react(kind) {
    if (kind === 'leave') {
      if (this.queuedReaction?.kind === 'approach') this.queuedReaction = null;
      return false;
    }
    if (!['approach', 'tap'].includes(kind) || this.reduced || this.mode === 'quiet' || this.mode === 'success') return false;
    const cooldown = kind === 'tap' ? 2400 : 12000;
    if (this.clock - this.lastReaction[kind] < cooldown || this.reaction) return false;
    this.lastReaction[kind] = this.clock;
    // A click takes precedence over hover. A held leaf keeps its own motion;
    // stale hover requests expire instead of surprising the user much later.
    if (this.queuedReaction?.kind === 'tap' && kind !== 'tap') return false;
    this.queuedReaction = {kind, expires: this.clock + (kind === 'tap' ? 20000 : 9000)};
    return true;
  }

  _startReaction() {
    if (!this.queuedReaction || this.reaction) return;
    if (this.queuedReaction.expires < this.clock) { this.queuedReaction = null; return; }
    if (this.mode === 'idle' && this.frame.active && this.queuedReaction.kind !== 'tap') return;
    const kind = this.queuedReaction.kind;
    this.queuedReaction = null;
    this.reaction = {elapsed: 0, kind, overlay: this.mode === 'working' || this.frame.active,
      event: {kind: kind === 'tap' ? 'tap-rebound' : this.mode === 'working' ? 'listen' : 'leaf-peek',
        start: 0, end: (kind === 'tap' ? 4300 : 6100) * (.92 + this.random() * .16),
        strength: .85 + this.random() * .15, direction: this.random() < .5 ? -1 : 1}};
    if (!this.reaction.overlay) this.transition = {from: this.frame, elapsed: 0};
  }

  update(deltaMs = 0, {mode = 'idle', successToken, reduced = false, paused = false} = {}) {
    const requested = normalizeMode(mode);
    const enteredSuccess = requested === 'success' && this.lastInputMode !== 'success';
    const hasToken = successToken !== undefined && successToken !== null;
    const changedToken = hasToken && this.tokenSeen && !Object.is(successToken, this.lastToken);
    if (hasToken) { this.tokenSeen = true; this.lastToken = successToken; }
    if (enteredSuccess || changedToken) this.pendingSuccesses += 1;
    this.lastInputMode = requested;
    this.requestedMode = requested === 'success' ? 'idle' : requested;
    if (reduced) {
      this.reduced = true;
      this.queuedReaction = null;
      return sampleGarden({reduced: true});
    }
    if (paused) return this.frame;
    if (this.reduced) {
      this.transition = {from: sampleGarden({reduced: true}), elapsed: 0};
      this.reduced = false;
    }
    if (this.mode !== 'success' && this.pendingSuccesses > 0) {
      this.pendingSuccesses -= 1;
      this._switch('success');
    } else if (this.mode !== 'success') this._switch(this.requestedMode);

    const delta = Math.min(80, Math.max(0, finite(deltaMs)));
    this.clock += delta;
    this._startReaction();
    if (!(this.reaction && !this.reaction.overlay)) this.elapsed += delta;
    if (this.mode === 'success' && this.elapsed >= GARDEN_DURATIONS.success) {
      this.frame = sampleGarden({mode: 'success', elapsed: GARDEN_DURATIONS.success});
      if (this.pendingSuccesses > 0) {
        this.pendingSuccesses -= 1;
        this.elapsed = 0;
        this.transition = {from: this.frame, elapsed: 0};
      } else this._switch(this.requestedMode);
    } else if (this.mode !== 'success' && this.elapsed >= gardenDuration(this.mode, this.variant)) {
      this.elapsed -= gardenDuration(this.mode, this.variant);
      this._nextVariant();
    }

    let target = sampleGarden({mode: this.mode, elapsed: this.elapsed, variant: this.variant});
    if (this.reaction) {
      this.reaction.elapsed += delta;
      const response = episodeFrame(this.reaction.event, this.reaction.elapsed);
      if (this.reaction.elapsed >= this.reaction.event.end) {
        if (!this.reaction.overlay) this.transition = {from: response, elapsed: 0};
        this.reaction = null;
      }
      else if (!this.reaction.overlay) target = response;
      else {
        // An active leaf transfer or work orbit keeps its own physical path.
        // A click responds immediately with a softer gesture on top of it,
        // never detaches the hat or disguises the task as a success event.
        target.episode = response.episode;
        for (const key of ['hop', 'sway', 'roll', 'pitch', 'yaw', 'gazeX', 'gazeY', 'joy']) target.body[key] += response.body[key] * .45;
        target.body.stretch += (response.body.stretch - 1) * .5;
        target.body.wink = Math.min(target.body.wink, response.body.wink);
      }
    }
    target.time = this.clock / 1000;
    if (this.transition) {
      this.transition.elapsed += delta;
      this.frame = mixFrames(this.transition.from, target, this.transition.elapsed / this.blendMs);
      if (this.transition.elapsed >= this.blendMs) this.transition = null;
    } else this.frame = target;
    return this.frame;
  }
}
