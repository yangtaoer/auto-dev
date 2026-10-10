/* State-aware character choreography. Pure sampling keeps animation deterministic in tests. */
(function (global) {
  'use strict';
  const gestures = {
    peek: {duration: 3100, expression: 'searching'},
    tilt: {duration: 2600, expression: 'curious'},
    nod: {duration: 2300, expression: 'listening'},
    hop: {duration: 2600, expression: 'playful'},
    wiggle: {duration: 2900, expression: 'happy'},
    twirl: {duration: 3300, expression: 'excited'},
    stretch: {duration: 3100, expression: 'proud'},
    wink: {duration: 2400, expression: 'happy'},
    breathe: {duration: 4200, expression: 'sleeping'},
    roam: {duration: 4900, expression: 'curious'},
    slalom: {duration: 4100, expression: 'playful'},
    leafchase: {duration: 4400, expression: 'searching'},
    tumble: {duration: 3700, expression: 'excited'},
    float: {duration: 5300, expression: 'proud'},
    inspect: {duration: 3900, expression: 'thinking'},
    settle: {duration: 4600, expression: 'listening'},
  };
  const pools = {
    idle: ['peek', 'tilt', 'hop', 'wiggle', 'wink', 'stretch', 'twirl', 'roam', 'slalom', 'leafchase', 'tumble', 'float'],
    curious: ['peek', 'tilt', 'hop', 'wiggle', 'wink', 'stretch', 'twirl', 'roam', 'leafchase', 'slalom', 'float'],
    reading: ['peek', 'nod', 'tilt', 'inspect', 'leafchase'],
    thinking: ['tilt', 'peek', 'nod', 'inspect', 'float'],
    working: ['nod', 'hop', 'peek', 'wiggle', 'stretch', 'slalom', 'roam'],
    building: ['stretch', 'hop', 'nod', 'peek', 'inspect', 'slalom'],
    delivering: ['nod', 'wink', 'hop', 'float', 'roam'],
    listening: ['peek', 'tilt', 'nod', 'inspect', 'settle'],
    blocked: ['tilt', 'peek', 'nod', 'inspect', 'settle'],
    error: ['peek', 'tilt', 'settle'],
    success: ['hop', 'twirl', 'wink', 'wiggle', 'tumble', 'slalom', 'leafchase'],
    sleeping: ['breathe', 'settle'],
  };
  function pick(state, previous, random = Math.random) {
    const choices = (pools[state] || pools.curious).filter(name => name !== previous);
    return choices[Math.min(choices.length - 1, Math.max(0, Math.floor(random() * choices.length)))];
  }
  function clip(name, startedAt, direction = 1, variation = Math.random()) {
    const kind = gestures[name] ? name : 'tilt';
    const seed = Math.max(0, Math.min(1, Number.isFinite(variation) ? variation : .5));
    return {name: kind, startedAt, duration: gestures[kind].duration * (.88 + seed * .24), direction: direction < 0 ? -1 : 1, variation: seed};
  }
  function sample(animation, now) {
    const pose = {hop: 0, sway: 0, depth: 0, roll: 0, pitch: 0, yaw: 0, stretch: 1, gazeX: 0, gazeY: 0, wink: 1, leaf: 0};
    if (!animation) return pose;
    const p = (now - animation.startedAt) / animation.duration;
    // A varied decimal duration can round (start + duration - start) just below
    // duration. Treat that round-off-sized endpoint as finished, not a 2π pose
    // which suddenly snaps back on the following fallback-rendered frame.
    if (p <= 1e-9 || p >= 1 - 1e-9) return pose;
    const envelope = Math.sin(Math.PI * p) ** 2, wave = Math.sin(Math.PI * p * 4), direction = animation.direction;
    switch (animation.name) {
      case 'peek':
        pose.gazeX = Math.sin(p * Math.PI * 2) * envelope * .7;
        pose.yaw = pose.gazeX * .3; pose.roll = -pose.gazeX * .14;
        pose.gazeY = envelope * .18;
        pose.sway = direction * envelope * .09;
        pose.depth = envelope * .05;
        break;
      case 'tilt':
        pose.roll = direction * envelope * .22; pose.pitch = -.08 * envelope;
        pose.gazeX = direction * envelope * .28; pose.gazeY = envelope * .3;
        break;
      case 'nod':
        pose.pitch = wave * envelope * .22; pose.hop = Math.abs(wave) * envelope * .025;
        pose.gazeY = -wave * envelope * .2;
        break;
      case 'hop':
        pose.hop = Math.abs(Math.sin(p * Math.PI * 2)) * envelope * .27;
        pose.stretch = 1 - Math.cos(p * Math.PI * 4) * envelope * .10;
        pose.roll = direction * wave * envelope * .10;
        pose.sway = direction * Math.sin(p * Math.PI * 2) * envelope * .15;
        pose.depth = envelope * -.08;
        break;
      case 'wiggle':
        pose.sway = wave * envelope * .10; pose.roll = -wave * envelope * .19;
        pose.stretch = 1 + Math.sin(p * Math.PI * 6) * envelope * .035;
        break;
      case 'twirl':
        pose.yaw = direction * Math.PI * 2 * p * p * (3 - 2 * p);
        pose.hop = envelope * .18; pose.roll = wave * envelope * .07;
        pose.sway = Math.sin(p * Math.PI * 2) * envelope * .16;
        pose.depth = (1 - Math.cos(p * Math.PI * 2)) * envelope * -.08;
        break;
      case 'stretch':
        pose.stretch = 1 + envelope * .12; pose.hop = envelope * .06;
        pose.pitch = -.13 * envelope; pose.gazeY = envelope * .4;
        break;
      case 'wink':
        pose.roll = direction * envelope * .14;
        pose.wink = 1 - .94 * Math.max(0, 1 - Math.abs(p - .45) / .14);
        pose.hop = envelope * .025;
        break;
      case 'breathe':
        pose.stretch = 1 + envelope * .025; pose.roll = direction * envelope * .025;
        break;
      case 'roam':
        pose.sway = direction * Math.sin(p * Math.PI * 2) * envelope * .38;
        pose.depth = Math.sin(p * Math.PI) * envelope * -.24;
        pose.hop = Math.abs(wave) * envelope * .07;
        pose.roll = -pose.sway * .5;
        pose.yaw = direction * wave * envelope * .24;
        pose.gazeX = direction * Math.cos(p * Math.PI * 2) * envelope * .45;
        break;
      case 'slalom':
        pose.sway = direction * wave * envelope * .3;
        pose.depth = Math.sin(p * Math.PI * 2) * envelope * .15;
        pose.hop = Math.abs(wave) * envelope * .15;
        pose.roll = -direction * wave * envelope * .3;
        pose.stretch = 1 - Math.cos(p * Math.PI * 8) * envelope * .065;
        pose.gazeX = direction * wave * envelope * .4;
        break;
      case 'leafchase':
        pose.sway = direction * Math.sin(p * Math.PI * 2) * envelope * .32;
        pose.depth = -envelope * .16;
        pose.hop = envelope * .22;
        pose.roll = -direction * envelope * .19;
        pose.pitch = -.16 * envelope;
        pose.gazeX = direction * wave * envelope * .6;
        pose.gazeY = envelope * .6;
        pose.leaf = envelope;
        break;
      case 'tumble':
        pose.sway = direction * Math.sin(p * Math.PI * 2) * envelope * .3;
        pose.roll = direction * Math.PI * 2 * p * p * (3 - 2 * p);
        pose.hop = envelope * .27;
        pose.depth = -envelope * .12;
        pose.stretch = 1 - Math.cos(p * Math.PI * 4) * envelope * .075;
        break;
      case 'float':
        pose.sway = direction * Math.sin(p * Math.PI * 2) * envelope * .22;
        pose.depth = envelope * .2;
        pose.hop = envelope * .25;
        pose.pitch = -.11 * envelope;
        pose.roll = direction * wave * envelope * .11;
        pose.stretch = 1 + envelope * .04;
        pose.gazeY = envelope * .3;
        break;
      case 'inspect':
        pose.sway = direction * envelope * .13;
        pose.depth = envelope * .14;
        pose.pitch = Math.sin(p * Math.PI * 3) * envelope * .14;
        pose.yaw = direction * envelope * .25;
        pose.gazeX = direction * envelope * .4;
        pose.gazeY = Math.sin(p * Math.PI * 2) * envelope * .3;
        break;
      case 'settle':
        pose.sway = direction * Math.sin(p * Math.PI * 2) * envelope * .04;
        pose.depth = -envelope * .04;
        pose.roll = direction * envelope * .06;
        pose.stretch = 1 - envelope * .025;
        pose.gazeY = -envelope * .18;
        break;
    }
    const amplitude = .78 + (animation.variation ?? .5) * .22;
    ['hop', 'sway', 'depth', 'pitch', 'gazeX', 'gazeY'].forEach(key => { pose[key] *= amplitude; });
    return pose;
  }
  const habitats = ['meadow', 'blossom', 'canopy'];
  // Keep scene changes independent of polling/task updates. Cross-fades have
  // zero velocity at either endpoint, including the last-to-first transition.
  function habitatAt(elapsedMs, reduced = false) {
    if (reduced) return {current: 'meadow', next: 'blossom', mix: 0};
    const hold = 26000, fade = 5200, period = hold + fade;
    const time = Math.max(0, Number.isFinite(elapsedMs) ? elapsedMs : 0);
    const segment = Math.floor(time / period);
    const progress = Math.max(0, Math.min(1, (time % period - hold) / fade));
    return {current: habitats[segment % habitats.length], next: habitats[(segment + 1) % habitats.length],
      mix: progress * progress * (3 - 2 * progress)};
  }
  const api = {gestures, pools, pick, clip, sample, habitats, habitatAt};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.AutoDevOrbMotion = api;
})(typeof window === 'undefined' ? globalThis : window);
