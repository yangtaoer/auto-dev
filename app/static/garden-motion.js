/* The garden and its character share one clock so a caught leaf never drifts
 * away from the reaction it caused. This module has no browser dependencies. */

export const GARDEN_DURATIONS = Object.freeze({idle: 18000, working: 7200, success: 4800, quiet: 5600});

const TAU = Math.PI * 2;
const PROGRESS = ['wind', 'peek', 'hat', 'work', 'crown', 'bloom', 'petals'];
const finite = value => Number.isFinite(value) ? value : 0;
const clamp = value => Math.max(0, Math.min(1, finite(value)));
const smooth = value => { const x = clamp(value); return x * x * (3 - 2 * x); };
const ramp = (time, start, end) => smooth((time - start) / (end - start));
const envelope = (time, inAt, fullAt, outAt, endAt) => ramp(time, inAt, fullAt) * (1 - ramp(time, outAt, endAt));
const pulse = (time, start, end) => {
  const p = clamp((time - start) / (end - start));
  return Math.sin(Math.PI * p) ** 2;
};
const normalizeMode = mode => ['idle', 'working', 'success', 'quiet'].includes(mode) ? mode : 'idle';
const normalizedVariant = value => Math.abs(Math.trunc(finite(value)));
const duration = (mode, variant) => mode === 'idle'
  ? GARDEN_DURATIONS.idle + normalizedVariant(variant) % 3 * 2000
  : GARDEN_DURATIONS[mode];

function neutral(time = 0, phase = 'rest') {
  return {
    time, wind: .08, peek: 0, hat: 0, work: 0, crown: 0, bloom: 0, petals: 0,
    body: {hop: 0, sway: 0, depth: 0, roll: 0, pitch: 0, yaw: 0, stretch: 1, gazeX: 0, gazeY: 0, wink: 1, joy: 0, squeeze: 0},
    active: false, phase,
  };
}

/**
 * elapsed is milliseconds. Distances are scene units, angles radians. wink is
 * eye openness (1 open), joy is the curved happy-eye mix. peek brings the SAME
 * hero leaf down its branch; hat transfers that leaf from branch tip to head.
 * work is the only signal that enables the golden orbital light effect.
 */
export function sampleGarden({mode = 'idle', elapsed = 0, variant = 0, reduced = false} = {}) {
  const kind = normalizeMode(mode);
  const milliseconds = Math.max(0, finite(elapsed));
  const seed = normalizedVariant(variant);
  const frame = neutral(milliseconds / 1000);
  const body = frame.body;
  if (reduced) {
    frame.time = 0;
    frame.wind = 0;
    frame.phase = 'still';
    return frame;
  }

  if (kind === 'success') {
    const t = milliseconds / 1000;
    if (milliseconds >= GARDEN_DURATIONS.success) return frame;
    frame.active = t > 0;
    frame.phase = t < .65 ? 'gather' : t < 1.45 ? 'spring' : t < 3.4 ? 'flower-crown' : 'settle';
    body.squeeze = pulse(t, 0, .78) * .95 + pulse(t, 3.88, 4.48) * .16;
    body.hop = envelope(t, .55, 1.35, 2.85, 4.25) * .36;
    body.stretch = 1 - body.squeeze * .14 + pulse(t, .55, 1.18) * .105;
    body.roll = Math.sin(t * 3.2) * envelope(t, .75, 1.5, 3, 4.2) * .055;
    body.pitch = -.065 * pulse(t, .4, 4.1);
    body.joy = envelope(t, .55, 1.2, 3.75, 4.55);
    body.wink = 1 - .86 * pulse(t, .18, .75);
    body.gazeY = envelope(t, .48, 1.15, 3.2, 4.15) * .3;
    frame.crown = envelope(t, .8, 1.6, 3.75, 4.65);
    frame.bloom = envelope(t, 1.05, 1.95, 3.8, 4.65);
    frame.petals = envelope(t, 1.4, 2.0, 3.65, 4.6);
    frame.wind += .15 * pulse(t, .55, 4.65);
    return frame;
  }

  const period = duration(kind, seed) / 1000;
  const t = milliseconds / 1000 % period;
  const cycle = t / period;
  const direction = seed % 2 ? -1 : 1;
  body.stretch += Math.sin(cycle * TAU * (kind === 'idle' ? 3 : 1)) * .006;
  frame.wind += (1 - Math.cos(cycle * TAU)) * .025;
  if (kind === 'quiet') {
    frame.phase = 'quiet';
    return frame;
  }

  if (kind === 'working') {
    const engaged = envelope(t, .4, 1.55, 5.8, 7.1);
    frame.work = engaged;
    frame.active = engaged > .001;
    frame.phase = t < 1.45 ? 'charge' : t < 5.8 ? 'collaborate' : 'land';
    frame.wind += engaged * .17;
    body.squeeze = pulse(t, .1, 1.15) * .2;
    body.hop = engaged * (.15 + Math.sin(t * 2.7) * .022);
    body.stretch += -.035 * body.squeeze + .013 * Math.sin(t * 3) * engaged;
    body.sway = Math.sin(t * 1.15) * engaged * .055;
    body.depth = Math.sin(t * 1.15 + .5) * engaged * .04;
    body.yaw = direction * Math.sin(t * .9) * engaged * .22;
    body.roll = -body.sway * .5;
    body.pitch = -.065 * engaged;
    body.gazeX = Math.sin(t * 1.9) * engaged * .24;
    body.gazeY = engaged * (.36 + Math.cos(t * 1.9) * .055);
    return frame;
  }

  // Every variant changes pace and direction together. Interaction coordinates
  // remain shared, with five or more seconds of quiet breathing after the leaf
  // has returned to its branch. A loop boundary is exactly the neutral pose.
  const beat = t / (1 + seed % 3 * .07);
  const notice = envelope(beat, 1.6, 3.25, 10.9, 12.55);
  const catchLeaf = envelope(beat, 6.1, 7.75, 10.25, 11.8);
  const rebound = pulse(beat, 8.52, 10.05);
  frame.peek = envelope(beat, 2.7, 5.5, 10.45, 12.45);
  frame.hat = catchLeaf;
  frame.active = notice > .001;
  frame.phase = beat < 2.7 ? 'rest' : beat < 6.1 ? 'peek' : beat < 8.52 ? 'catch' : beat < 10.25 ? 'rebound' : beat < 12.55 ? 'return-leaf' : 'rest';
  // The hero branch is planted on the left in every variant. Track its leaf,
  // then centre the gaze as that same leaf settles on top of the head.
  body.gazeX = 0 - notice * (1 - catchLeaf) * (.26 + Math.sin(beat * 1.2) * .025);
  body.gazeY = notice * (.24 + catchLeaf * .14);
  body.roll = direction * notice * (.13 * (1 - catchLeaf) - catchLeaf * .035);
  body.yaw = direction * notice * .055 * (1 - catchLeaf);
  body.pitch = -notice * .035;
  body.sway = direction * notice * .025;
  body.depth = notice * -.018;
  body.wink = 1 - .93 * pulse(beat, 7.84, 8.68);
  body.squeeze = pulse(beat, 7.64, 8.72) * .35 + pulse(beat, 9.8, 10.5) * .08;
  body.hop = rebound * .11;
  body.stretch += rebound * .035 - body.squeeze * .085;
  body.joy = pulse(beat, 8.4, 10.5) * .38;
  frame.wind += notice * .075;
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
 * Stateful playback without timers, randomness, DOM or wall-clock time.
 * First successToken is a baseline; subsequent changes queue a celebration.
 * Entering mode=success also celebrates, but holding it never repeats. A queued
 * success finishes in full before the latest requested mode resumes. The caller
 * supplies frame deltas; long gaps are capped at 80 ms so waking a hidden tab
 * cannot leap over an interaction. Paused/reduced frames do not advance clocks.
 */
export class GardenDirector {
  constructor({variant = 0, blendMs = 500} = {}) {
    this.initialVariant = normalizedVariant(variant);
    this.blendMs = Math.max(1, finite(blendMs) || 500);
    this.reset();
  }

  reset() {
    this.variant = this.initialVariant;
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
    this.frame = sampleGarden({mode: 'idle', elapsed: 0, variant: this.variant});
    return this.frame;
  }

  _switch(mode) {
    if (this.mode === mode) return;
    this.transition = {from: this.frame, elapsed: 0};
    this.mode = mode;
    this.elapsed = 0;
  }

  update(deltaMs = 0, {mode = 'idle', successToken, reduced = false, paused = false} = {}) {
    const requested = normalizeMode(mode);
    const enteredSuccess = requested === 'success' && this.lastInputMode !== 'success';
    const hasToken = successToken !== undefined && successToken !== null;
    const changedToken = hasToken && this.tokenSeen && !Object.is(successToken, this.lastToken);
    if (hasToken) {
      this.tokenSeen = true;
      this.lastToken = successToken;
    }
    if (enteredSuccess || changedToken) this.pendingSuccesses += 1;
    this.lastInputMode = requested;
    this.requestedMode = requested === 'success' ? 'idle' : requested;

    if (reduced) {
      this.reduced = true;
      return sampleGarden({mode: 'quiet', reduced: true});
    }
    if (paused) return this.frame;
    if (this.reduced) {
      this.transition = {from: sampleGarden({reduced: true}), elapsed: 0};
      this.reduced = false;
    }

    if (this.mode !== 'success' && this.pendingSuccesses > 0) {
      this.pendingSuccesses -= 1;
      this._switch('success');
    } else if (this.mode !== 'success') {
      this._switch(this.requestedMode);
    }

    const delta = Math.min(80, Math.max(0, finite(deltaMs)));
    this.elapsed += delta;
    this.clock += delta;
    if (this.mode === 'success' && this.elapsed >= GARDEN_DURATIONS.success) {
      this.frame = sampleGarden({mode: 'success', elapsed: GARDEN_DURATIONS.success});
      if (this.pendingSuccesses > 0) {
        this.pendingSuccesses -= 1;
        this.elapsed = 0;
        this.transition = {from: this.frame, elapsed: 0};
      } else {
        this._switch(this.requestedMode);
      }
    } else if (this.mode !== 'success' && this.elapsed >= duration(this.mode, this.variant)) {
      this.elapsed -= duration(this.mode, this.variant);
      this.variant += 1;
    }

    const target = sampleGarden({mode: this.mode, elapsed: this.elapsed, variant: this.variant});
    target.time = this.clock / 1000;
    if (this.transition) {
      this.transition.elapsed += delta;
      this.frame = mixFrames(this.transition.from, target, this.transition.elapsed / this.blendMs);
      if (this.transition.elapsed >= this.blendMs) this.transition = null;
    } else {
      this.frame = target;
    }
    return this.frame;
  }
}
