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
  };
  const pools = {
    idle: ['peek', 'tilt', 'hop', 'wiggle', 'wink', 'stretch', 'twirl'],
    curious: ['peek', 'tilt', 'hop', 'wiggle', 'wink', 'stretch', 'twirl'],
    reading: ['peek', 'nod', 'tilt'],
    thinking: ['tilt', 'peek', 'nod'],
    working: ['nod', 'hop', 'peek', 'wiggle', 'stretch'],
    building: ['stretch', 'hop', 'nod', 'peek'],
    delivering: ['nod', 'wink', 'hop'],
    listening: ['peek', 'tilt', 'nod'],
    blocked: ['tilt', 'peek', 'nod'],
    error: ['peek', 'tilt'],
    success: ['hop', 'twirl', 'wink', 'wiggle'],
    sleeping: ['breathe', 'tilt'],
  };
  function pick(state, previous, random = Math.random) {
    const choices = (pools[state] || pools.curious).filter(name => name !== previous);
    return choices[Math.min(choices.length - 1, Math.max(0, Math.floor(random() * choices.length)))];
  }
  function clip(name, startedAt, direction = 1) {
    const kind = gestures[name] ? name : 'tilt';
    return {name: kind, startedAt, duration: gestures[kind].duration, direction: direction < 0 ? -1 : 1};
  }
  function sample(animation, now) {
    const pose = {hop: 0, sway: 0, roll: 0, pitch: 0, yaw: 0, stretch: 1, gazeX: 0, gazeY: 0, wink: 1};
    if (!animation) return pose;
    const p = (now - animation.startedAt) / animation.duration;
    if (p <= 0 || p >= 1) return pose;
    const envelope = Math.sin(Math.PI * p) ** 2, wave = Math.sin(Math.PI * p * 4), direction = animation.direction;
    switch (animation.name) {
      case 'peek':
        pose.gazeX = Math.sin(p * Math.PI * 2) * envelope * .7;
        pose.yaw = pose.gazeX * .3; pose.roll = -pose.gazeX * .14;
        pose.gazeY = envelope * .18;
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
        break;
      case 'wiggle':
        pose.sway = wave * envelope * .10; pose.roll = -wave * envelope * .19;
        pose.stretch = 1 + Math.sin(p * Math.PI * 6) * envelope * .035;
        break;
      case 'twirl':
        pose.yaw = direction * Math.PI * 2 * p * p * (3 - 2 * p);
        pose.hop = envelope * .18; pose.roll = wave * envelope * .07;
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
    }
    return pose;
  }
  const api = {gestures, pools, pick, clip, sample};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.AutoDevOrbMotion = api;
})(typeof window === 'undefined' ? globalThis : window);
