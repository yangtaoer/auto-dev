/* Each habitat has its own shuffled, multi-beat performance, not a long video
 * or a reskinned sine wave. Scene props and face/body sample the SAME beat. */
const TAU = Math.PI * 2;
const finite = n => Number.isFinite(n) ? n : 0;
const clamp = n => Math.max(0, Math.min(1, finite(n)));
const smooth = n => { const x = clamp(n); return x * x * (3 - 2 * x); };
const pulse = (p, a, b) => { const x=clamp((p-a)/(b-a));return x===0||x===1 ? 0 : Math.sin(Math.PI*x)**2; };
const envelope = p => smooth(p / .16) * (1 - smooth((p - .80) / .20));
const hash = n => { let x = (n >>> 0) + 0x9e3779b9; x = Math.imul(x ^ x >>> 16, 0x21f0aaad); x = Math.imul(x ^ x >>> 15, 0x735a2d97); return (x ^ x >>> 15) >>> 0; };
export const THEME_STORIES = Object.freeze({
  garden: ['butterfly-greeting', 'flower-bow', 'petal-shower', 'leaf-waltz', 'butterfly-crown'],
  moon: ['firefly-conductor', 'lantern-wish', 'constellation-dance', 'moon-greeting', 'firefly-crown'],
  sky: ['airplane-loop', 'cloud-puff', 'airplane-salute', 'cloud-surf', 'wind-ribbon'],
  autumn: ['ginkgo-twirl', 'leaf-catch', 'golden-shower', 'leaf-fan', 'autumn-bow'],
  paper: ['pinwheel-breath', 'origami-unfold', 'paper-bird-bow', 'paper-fan', 'folded-crown'],
  ocean: ['jellyfish-greeting', 'bubble-pop', 'fish-parade', 'coral-dance', 'jellyfish-duet'],
  space: ['satellite-inspect', 'solar-unfold', 'zero-gravity-hop', 'meteor-wish', 'satellite-salute'],
  porcelain: ['chime-listen', 'porcelain-bird', 'lotus-unfurl', 'chime-bow', 'tea-breeze'],
  arcade: ['coin-combo', 'mushroom-hop', 'pixel-spark', 'level-up-bow', 'coin-juggle'],
  gallery: ['pendulum-catch', 'sculpture-bow', 'block-balance', 'mobile-duet', 'gallery-curtsy'],
});

function identity(kind) { return Object.keys(THEME_STORIES).indexOf(kind) + 1; }
export function themeItinerary(kind = 'garden', seed = 0, cycle = 0) {
  const stories = [...(THEME_STORIES[kind] || THEME_STORIES.garden)];
  const shuffled = index => {
    const order=[...(THEME_STORIES[kind]||THEME_STORIES.garden)];
    let value=hash(seed^Math.imul(identity(kind),7919)^Math.imul(index+1,8191));
    for(let i=order.length-1;i>0;i--){value=hash(value);const j=Math.floor(value/0x100000000*(i+1));[order[i],order[j]]=[order[j],order[i]];}
    return {order,value};
  };
  const bag=shuffled(cycle);stories.splice(0,stories.length,...bag.order);
  if(cycle>0&&stories[0]===shuffled(cycle-1).order.at(-1))[stories[0],stories[1]]=[stories[1],stories[0]];
  let state = bag.value;
  const random = () => { state = hash(state); return state / 0x100000000; };
  // Every cycle contains every story once. Vary timing, direction, amplitude,
  // and order; individual actions stay snappy instead of being time-stretched.
  let cursor = 1.0 + random() * 1.3;
  const events = stories.map(id => {
    const length = 5.4 + random() * 3.7;
    const event = {id, start:cursor, end:cursor + length, direction:random() > .5 ? 1 : -1, strength:.78 + random() * .22};
    cursor += length + 2.5 + random() * 3.8;
    return event;
  });
  return {events, duration:96, cycle};
}

function neutral() {
  return {id:'ambient', phase:'rest', progress:0, amount:0, reveal:0, play:0, settle:0, direction:1, reaction:0,
    pose:{hop:0, sway:0, depth:0, roll:0, pitch:0, yaw:0, stretch:0, joy:0, squeeze:0, wink:1, gazeX:0, gazeY:0}, focus:null};
}

export function sampleThemeStory(kind, event, local, intensity = 1) {
  const moment = neutral(), p = clamp((local - event.start) / (event.end - event.start));
  const a = envelope(p) * event.strength * intensity, direction = event.direction;
  const b = moment.pose, id = event.id;
  Object.assign(moment, {id, progress:p, direction, amount:a, phase:p < .22 ? 'notice' : p < .44 ? 'approach' : p < .78 ? 'play' : 'settle',
    reveal:smooth((p - .15) / .26) * (1 - smooth((p - .79) / .20)) * intensity,
    play:pulse(p, .30, .82) * intensity, settle:pulse(p, .74, 1) * intensity});
  b.roll = Math.sin(p * TAU) * a * .085 * direction;
  b.pitch = -pulse(p, .10, .42) * .055 * intensity + pulse(p, .72, 1) * .045 * intensity;
  b.wink = 1 - pulse(p, .64, .75) * .78 * intensity;
  b.joy = pulse(p, .43, .85) * .55 * intensity;
  b.gazeX = direction * a * .34;
  b.gazeY = a * .30;
  if(kind === 'garden') {
    b.sway = Math.sin(p * TAU) * a * .07;
    b.gazeX = Math.sin(p * TAU * 1.2) * a * .70;
    if(id === 'butterfly-crown') { b.gazeY = a * .65; b.squeeze = pulse(p, .39, .50) * .45 * intensity; }
    if(id === 'flower-bow') { b.pitch = pulse(p, .26, .6) * .16 * intensity; b.gazeX = -a * .72; }
  } else if(kind === 'moon') {
    b.gazeX = Math.sin(p * TAU * 1.1) * a * .64;
    b.yaw = b.gazeX * .18; b.gazeY = a * .62;
    b.sway = Math.sin(p * TAU) * a * .045;
    if(id === 'lantern-wish') { b.wink = 1 - pulse(p, .36, .64) * .91 * intensity; b.stretch = -.025 * a; }
  } else if(kind === 'sky') {
    b.gazeX = Math.cos(p * TAU) * a * .74;
    b.roll = -b.gazeX * .14; b.sway = b.gazeX * .06;
    if(id === 'cloud-puff') { b.squeeze = pulse(p, .28, .46) * .52 * intensity; b.stretch = -.06 * b.squeeze; }
    if(id === 'cloud-surf') { b.hop = pulse(p, .32, .78) * .13 * intensity; b.roll = Math.sin(p * TAU * 2) * a * .13; }
  } else if(kind === 'autumn') {
    b.sway = Math.sin(p * TAU * 1.4) * a * .075;
    b.roll = -b.sway * 1.3; b.yaw = Math.sin(p * TAU) * a * .12;
    if(id === 'leaf-catch') { b.gazeY = a * .74; b.hop = pulse(p, .41, .64) * .13 * intensity; }
    if(id === 'autumn-bow') b.pitch = pulse(p, .31, .69) * .15 * intensity;
  } else if(kind === 'paper') {
    b.stretch = pulse(p, .29, .56) * .055 * intensity;
    b.gazeX = Math.sin(p * TAU) * a * .65;
    if(id === 'pinwheel-breath') { b.squeeze = pulse(p, .27, .52) * .4 * intensity; b.pitch = -a * .065; }
    if(id === 'paper-bird-bow') { b.pitch = pulse(p, .30, .59) * .14 * intensity; b.yaw = -a * .11; }
  } else if(kind === 'ocean') {
    b.hop = (Math.sin(p * TAU) * .5 + .5) * a * .08;
    b.roll = Math.sin(p * TAU * 1.3) * a * .12; b.stretch = Math.sin(p * TAU) * a * .035;
    b.gazeX = Math.sin(p * TAU * .8) * a * .65;
    if(id === 'bubble-pop') { b.squeeze = pulse(p, .44, .52) * .6 * intensity; b.joy = pulse(p, .48, .83) * .85 * intensity; }
  } else if(kind === 'space') {
    b.hop = pulse(p, .25, .84) * .18 * intensity;
    b.roll = Math.sin(p * TAU) * a * .12; b.depth = -a * .03;
    if(id === 'satellite-inspect') { b.gazeX = -a * .76; b.yaw = -a * .10; b.wink = 1 - pulse(p, .4, .5) * .8 * intensity; }
    if(id === 'zero-gravity-hop') { b.hop = (pulse(p, .18, .59) * .26 + pulse(p, .61, .91) * .14) * intensity; b.stretch = pulse(p, .15, .26) * -.04 * intensity; }
  } else if(kind === 'porcelain') {
    b.roll = Math.sin(p * TAU) * a * .06; b.gazeX = a * .70;
    if(id === 'chime-listen') { b.roll = a * -.15; b.wink = 1 - pulse(p, .42, .58) * .55 * intensity; }
    if(id === 'chime-bow') b.pitch = (pulse(p, .2, .48) + pulse(p, .58, .84) * .6) * .11 * intensity;
    if(id === 'lotus-unfurl') { b.stretch = a * .045; b.gazeY = -a * .32; }
  } else if(kind === 'arcade') {
    const hops = pulse(p, .16, .35) + pulse(p, .37, .57) * .75 + pulse(p, .60, .83) * .65;
    b.hop = hops * .18 * intensity;
    b.stretch = -pulse(p, .08, .2) * .065 * intensity + hops * .04 * intensity;
    b.gazeX = Math.sin(p * TAU * 1.5) * a * .75; b.joy = moment.play * .85 * intensity;
    if(id === 'level-up-bow') b.pitch = pulse(p, .44, .79) * .16 * intensity;
  } else if(kind === 'gallery') {
    b.gazeX = Math.sin(p * TAU) * a * .58; b.roll = b.gazeX * .08;
    b.sway = a * Math.sin(p * TAU) * .04;
    if(id === 'block-balance') { b.roll = a * .12 * direction; b.stretch = a * .035; }
    if(id === 'gallery-curtsy') { b.pitch = pulse(p, .30, .72) * .13 * intensity; b.stretch = -moment.play * .035 * intensity; }
  }
  moment.focus = {x:b.gazeX, y:b.gazeY};
  return moment;
}

export class ThemePerformanceDirector {
  constructor(kind = 'garden', seed = 0) { this.kind = kind; this.seed = seed; this.cycle = -1; this.lastTime = 0; this.reactionAt = -Infinity; this.moment = neutral(); }
  react(kind, time = this.lastTime) {
    if(!['tap','approach'].includes(kind) || time - this.reactionAt < 2.4) return false;
    this.reactionAt = time; this.reactionKind = kind; return true;
  }
  sample({time = 0, mode = 'idle', frozen = false, gentle = false, attachment = 0} = {}) {
    if(frozen) return this.moment;
    const t = Math.max(0, finite(time)), cycle = Math.floor(t / 96);
    if(cycle !== this.cycle) { this.itinerary = themeItinerary(this.kind, this.seed, cycle); this.cycle = cycle; }
    this.lastTime = t;
    const local = t % 96, event = this.itinerary.events.find(e => local >= e.start && local <= e.end);
    const intensity = (mode === 'quiet' ? .13 : mode === 'working' ? .30 : mode === 'success' ? .50 : 1) * (gentle ? .18 : 1) * (1 - clamp(attachment));
    const moment = event ? sampleThemeStory(this.kind, event, local, intensity) : neutral();
    if(!gentle && mode !== 'quiet') {
      moment.reaction = pulse(t - this.reactionAt, 0, this.reactionKind === 'tap' ? 2.1 : 3.2);
      moment.pose.joy = Math.max(moment.pose.joy, moment.reaction * .56 * (1-clamp(attachment)));
    }
    this.moment = moment;
    return moment;
  }
}
