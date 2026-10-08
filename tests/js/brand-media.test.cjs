const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const FILM = '/static/media/login-delivery-line.mp4';
const POSTER = '/static/media/login-delivery-line-poster.jpg';
const script = new vm.Script(fs.readFileSync(path.join(__dirname, '../../app/static/brand-media.js'), 'utf8'), {
  filename: 'brand-media.js',
});
const flush = () => new Promise(resolve => setImmediate(resolve));

class EventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback, options) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push({callback, once: Boolean(options?.once)});
  }
  removeEventListener(type, callback) {
    const remaining = (this.listeners.get(type) || []).filter(entry => entry.callback !== callback);
    if (remaining.length) this.listeners.set(type, remaining);
    else this.listeners.delete(type);
  }
  dispatch(type) {
    for (const entry of [...this.listeners.get(type) || []]) {
      if (entry.once) this.removeEventListener(type, entry.callback);
      entry.callback({type, target: this});
    }
  }
  listenerCount(type) { return this.listeners.get(type)?.length || 0; }
}

function classList() {
  const values = new Set();
  return {
    add(...names) { names.forEach(name => values.add(name)); },
    remove(...names) { names.forEach(name => values.delete(name)); },
    contains(name) { return values.has(name); },
  };
}

function mediaHarness({src = FILM, poster = POSTER, fallbackHidden = true, loginLayout = 'delivery-line',
  readyState = 0, hidden = false, reduced = false, saveData = false, rejectPlay = false, withVideo = true} = {}) {
  const document = new EventTarget();
  document.hidden = hidden;
  const motion = new EventTarget();
  motion.matches = reduced;
  const connection = new EventTarget();
  connection.saveData = saveData;
  const window = new EventTarget();
  const stage = {classList: classList()};
  const fallback = {hidden: fallbackHidden};
  const shell = {dataset: {loginLayout}, querySelector: selector => selector === '[data-film-fallback]' ? fallback : null};
  const video = new EventTarget();
  const attributes = new Map();
  if (src) attributes.set('src', src);
  if (poster) attributes.set('poster', poster);
  Object.assign(video, {
    dataset: {brandVideo: 'login'}, hidden: !src, readyState, muted: false,
    sourceWrites: [], posterWrites: [], playCalls: 0, pauseCalls: 0, playing: false,
    getAttribute: name => attributes.has(name) ? attributes.get(name) : null,
    closest: selector => selector === '[data-film-stage]' ? stage : selector === '.login-shell' ? shell : null,
    play() { this.playCalls++; this.playing = !rejectPlay; return rejectPlay ? Promise.reject(new Error('Autoplay blocked')) : Promise.resolve(); },
    pause() { this.pauseCalls++; this.playing = false; },
  });
  Object.defineProperty(video, 'src', {
    get: () => attributes.get('src') || '',
    set(value) { video.sourceWrites.push(value); attributes.set('src', String(value)); },
  });
  Object.defineProperty(video, 'poster', {
    get: () => attributes.get('poster') || '',
    set(value) { video.posterWrites.push(value); attributes.set('poster', String(value)); },
  });
  document.querySelectorAll = selector => selector === '[data-brand-video]' && withVideo ? [video] : [];
  let resolveFetch, rejectFetch;
  const response = new Promise((resolve, reject) => { resolveFetch = resolve; rejectFetch = reject; });
  const fetchCalls = [];
  script.runInNewContext({
    document, navigator: {connection}, matchMedia: () => motion,
    fetch(url, options) { fetchCalls.push({url, options}); return response; },
    addEventListener: window.addEventListener.bind(window),
  });
  return {
    video, stage, fallback, shell, document, motion, connection, window, fetchCalls,
    async resolveConfig(config = {login: {src: FILM, poster: POSTER, layout: 'delivery-line'}}) {
      resolveFetch({ok: true, json: () => Promise.resolve(config)});
      await flush();
    },
    async rejectConfig() { rejectFetch(new Error('Configuration unavailable')); await flush(); },
  };
}

test('SSR film hides the fallback before loadeddata and an unchanged config does not restart it', async () => {
  const {video, stage, fallback, resolveConfig} = mediaHarness({fallbackHidden: false});
  assert.equal(video.readyState, 0);
  assert.equal(stage.classList.contains('film-ready'), false);
  assert.equal(fallback.hidden, true, 'the first frame must use the film/poster without waiting for loadeddata');
  assert.equal(video.muted, true);
  assert.equal(video.playing, true);
  await resolveConfig();
  assert.equal(video.sourceWrites.length, 0, 'assigning the same src would restart an already loading SSR film');
  assert.equal(video.posterWrites.length, 0);
  assert.equal(video.getAttribute('src'), FILM);
  assert.equal(fallback.hidden, true);
});

test('a configured local film unhides its video and immediately takes over the fallback', async () => {
  const {video, fallback, shell, resolveConfig} = mediaHarness({src: '', poster: '', loginLayout: '', fallbackHidden: false});
  assert.equal(video.hidden, true);
  assert.equal(video.playCalls, 0);
  assert.equal(fallback.hidden, false);
  await resolveConfig();
  assert.deepEqual(video.sourceWrites, [FILM]);
  assert.deepEqual(video.posterWrites, [POSTER]);
  assert.equal(video.hidden, false);
  assert.equal(video.playing, true);
  assert.equal(shell.dataset.loginLayout, 'delivery-line');
  assert.equal(fallback.hidden, true);
});

test('loadeddata, a media error, and later recovery update the stage and fallback', () => {
  const {video, stage, fallback} = mediaHarness();
  video.readyState = 2;
  video.dispatch('loadeddata');
  assert.equal(stage.classList.contains('film-ready'), true);
  assert.equal(stage.classList.contains('film-failed'), false);
  assert.equal(fallback.hidden, true);
  video.dispatch('error');
  assert.equal(stage.classList.contains('film-ready'), false);
  assert.equal(stage.classList.contains('film-failed'), true);
  assert.equal(fallback.hidden, false, 'a failed film must expose readable content');
  video.dispatch('loadeddata');
  assert.equal(stage.classList.contains('film-ready'), true);
  assert.equal(stage.classList.contains('film-failed'), false);
  assert.equal(fallback.hidden, true);
});

test('an already decoded film is ready on initialization without a new loadeddata event', () => {
  const {video, stage, fallback} = mediaHarness({readyState: 2});
  assert.equal(stage.classList.contains('film-ready'), true);
  assert.equal(fallback.hidden, true);
  assert.equal(video.sourceWrites.length, 0);
});

for (const policy of ['hidden document', 'reduced motion', 'save data']) {
  test(`${policy} pauses playback and removing that restriction resumes it`, () => {
    const harness = mediaHarness();
    const {video, fallback} = harness;
    const target = policy === 'hidden document' ? harness.document : policy === 'reduced motion' ? harness.motion : harness.connection;
    const property = policy === 'hidden document' ? 'hidden' : policy === 'reduced motion' ? 'matches' : 'saveData';
    const event = policy === 'hidden document' ? 'visibilitychange' : 'change';
    const initialPlayCalls = video.playCalls;
    target[property] = true;
    target.dispatch(event);
    assert.equal(video.playing, false);
    assert.equal(video.playCalls, initialPlayCalls, 'a pause restriction must not request playback');
    assert.ok(video.pauseCalls > 0);
    if (policy === 'reduced motion') assert.equal(fallback.hidden, false);
    target[property] = false;
    target.dispatch(event);
    assert.equal(video.playing, true);
    assert.equal(video.playCalls, initialPlayCalls + 1);
    assert.equal(fallback.hidden, true);
  });
}

test('restrictions already present at startup prevent the SSR film from playing', () => {
  for (const setting of [{hidden: true}, {reduced: true}, {saveData: true}]) {
    const {video, fallback} = mediaHarness(setting);
    assert.equal(video.playCalls, 0, JSON.stringify(setting));
    assert.equal(video.playing, false);
    assert.ok(video.pauseCalls > 0);
    if (setting.reduced) assert.equal(fallback.hidden, false);
  }
});

test('autoplay rejection leaves a static film instead of an unhandled rejection', async () => {
  const {video, stage, fallback} = mediaHarness({rejectPlay: true});
  await flush();
  assert.equal(video.playing, false);
  assert.equal(stage.classList.contains('film-static'), true);
  assert.equal(fallback.hidden, true, 'the configured poster remains the first-frame presentation');
});

test('unsafe source and poster paths cannot be installed or played', async t => {
  const unsafe = [
    'https://example.com/film.mp4', '//example.com/film.mp4', 'javascript:alert(1)',
    '/api/artifacts/film.mp4', '/static/media/../private.mp4', '/static/media/%2e%2e/private.mp4',
    '/static/media/clip.mp4?token=secret', '/static/media/a b.mp4', '\\static\\media\\film.mp4', 42, null,
  ];
  for (const value of unsafe) {
    await t.test(String(value), async () => {
      const {video, fallback, resolveConfig} = mediaHarness({src: '', poster: '', fallbackHidden: false});
      await resolveConfig({login: {src: value, poster: value, layout: 'delivery-line'}});
      assert.equal(video.sourceWrites.length, 0);
      assert.equal(video.posterWrites.length, 0);
      assert.equal(video.hidden, true);
      assert.equal(video.playCalls, 0);
      assert.equal(fallback.hidden, false);
    });
  }
});

test('a failed config fetch preserves the SSR film and first-frame fallback policy', async () => {
  const {video, fallback, rejectConfig} = mediaHarness();
  await rejectConfig();
  assert.equal(video.getAttribute('src'), FILM);
  assert.equal(video.sourceWrites.length, 0);
  assert.equal(video.playing, true);
  assert.equal(fallback.hidden, true);
});

test('pagehide pauses the film, cleans listeners, and rejects late work', async () => {
  const {video, document, motion, connection, window, resolveConfig} = mediaHarness();
  assert.equal(document.listenerCount('visibilitychange'), 1);
  assert.equal(motion.listenerCount('change'), 1);
  assert.equal(connection.listenerCount('change'), 1);
  assert.equal(window.listenerCount('pagehide'), 1);
  const playsBeforeExit = video.playCalls;
  window.dispatch('pagehide');
  assert.equal(video.playing, false);
  assert.equal(document.listenerCount('visibilitychange'), 0);
  assert.equal(motion.listenerCount('change'), 0);
  assert.equal(connection.listenerCount('change'), 0);
  assert.equal(window.listenerCount('pagehide'), 0);
  document.dispatch('visibilitychange');
  motion.dispatch('change');
  connection.dispatch('change');
  video.dispatch('loadeddata');
  video.dispatch('error');
  await resolveConfig({login: {src: '/static/media/late-film.mp4', poster: '/static/media/late-poster.jpg', layout: 'delivery-line'}});
  assert.equal(video.playCalls, playsBeforeExit, 'late media events must not restart a disposed film');
  assert.equal(video.playing, false);
  assert.equal(video.sourceWrites.length, 0, 'a pending configuration must not modify the disposed page');
  assert.equal(video.posterWrites.length, 0);
});

test('pages without a film do not fetch configuration or bind lifecycle listeners', () => {
  const {fetchCalls, document, motion, connection, window} = mediaHarness({withVideo: false});
  assert.equal(fetchCalls.length, 0);
  assert.equal(document.listenerCount('visibilitychange'), 0);
  assert.equal(motion.listenerCount('change'), 0);
  assert.equal(connection.listenerCount('change'), 0);
  assert.equal(window.listenerCount('pagehide'), 0);
});
