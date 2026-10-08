const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const vm = require('node:vm');

async function gardenScene({seed = 17, cryptoSeed = 4321} = {}) {
  // All three modules use the real Three geometry, materials and transforms.
  // Only browser ownership and the GPU renderer are stubbed for Node.
  const moduleURL = file => pathToFileURL(path.join(__dirname, '../../app/static', file));
  const [THREE, {AutoDevGarden}, gardenMotion] = await Promise.all([
    import(moduleURL('vendor/three/three.module.js')),
    import(moduleURL('orb-garden.js')),
    import(moduleURL('garden-motion.js')),
  ]);
  const {GardenDirector, sampleGarden, gardenEpisodes, gardenDuration, GARDEN_DURATIONS} = gardenMotion;
  let rendererOptions, canceled = 0, now = 100;
  class Renderer {
    constructor(options) { rendererOptions = options; this.shadowMap = {}; }
    setPixelRatio(value) { this.pixelRatio = value; }
    setClearColor(color, alpha) { this.clearColor = color; this.clearAlpha = alpha; }
    setSize(width, height) { this.width = width; this.height = height; }
    render() {}
    dispose() { this.disposed = true; }
  }
  const document = {hidden: false, createElement() { return {
    getContext() { return {createRadialGradient() { return {addColorStop() {}}; }, fillRect() {}}; },
    setAttribute() {}, addEventListener() {}, removeEventListener() {}, remove() {},
  }; }};
  const window = {devicePixelRatio: 1, crypto: {getRandomValues(values) {values[0] = cryptoSeed; return values;}}};
  const classes = new Set();
  const root = {dataset: {}, classList: {
    add(...names) {names.forEach(name => classes.add(name));},
    remove(...names) {names.forEach(name => classes.delete(name));},
    contains: name => classes.has(name),
  }, closest: selector => selector === '.sidebar-character-stage', append() {},
  getBoundingClientRect: () => ({width: 204, height: 180})};
  const orb = {root, options: {gardenSeed: seed}, pointer: {targetX: 0, targetY: 0, near: false}, state: 'idle', gardenSuccessToken: 0, setVisible() {}};
  const Observer = class {observe() {} disconnect() {this.disconnected = true;}};
  const source = fs.readFileSync(path.join(__dirname, '../../app/static/orb-scene.js'), 'utf8')
    .replace(/^import .*$/gm, '').replace('export class AutoDevOrbScene', 'class AutoDevOrbScene');
  vm.runInNewContext(source, {THREE: {...THREE, WebGLRenderer: Renderer}, AutoDevGarden, GardenDirector,
    document, window, ResizeObserver: Observer, IntersectionObserver: Observer, performance: {now: () => now},
    requestAnimationFrame: () => 1, cancelAnimationFrame() {canceled++;}});
  const scene = new window.AutoDevOrbScene(orb);
  const drawAfter = ms => { now += ms; scene.draw(now); };
  const advance = ms => { for (let time = 0; time < ms; time += 40) drawAfter(Math.min(40, ms - time)); };
  return {scene, orb, root, window, document, THREE, sampleGarden, gardenEpisodes, gardenDuration, GARDEN_DURATIONS,
    rendererOptions, advance, drawAfter, canceled: () => canceled};
}

test('orange body keeps one soft highlight and a smooth, enlarged sidebar silhouette', async () => {
  const {scene, rendererOptions} = await gardenScene();
  const lights = [];
  scene.scene.traverse(object => {if (object.isDirectionalLight) lights.push(object);});
  assert.equal(lights.length, 1, 'multiple punctual highlights must not return');
  assert.equal(scene.body.material.clearcoat, 0);
  assert.ok(scene.body.material.roughness >= .4);
  assert.ok(scene.body.material.specularIntensity < .5);
  assert.ok(scene.body.geometry.parameters.widthSegments >= 96);
  assert.ok(scene.body.geometry.parameters.heightSegments >= 64);
  const diameterPixels = 2 * scene.radius / (scene.camera.top - scene.camera.bottom) * scene.renderer.height;
  assert.ok(diameterPixels >= 90 && diameterPixels <= 125, 'ball remains legible alongside its garden');
  assert.equal(rendererOptions.alpha, true);
  assert.equal(rendererOptions.antialias, true);
  assert.equal(rendererOptions.premultipliedAlpha, true);
  assert.equal(scene.renderer.pixelRatio, 1.75);
  assert.equal(scene.renderer.clearAlpha, 0);
  scene.destroy();
});

test('leaves and petals use shaped, lit opaque geometry rather than flat transparent cards', async () => {
  const {scene} = await gardenScene();
  for (const [name, geometry, material] of [
    ['leaf', scene.garden.leafShape, scene.garden.materials.leaf],
    ['petal', scene.garden.petalShape, scene.garden.materials.petal],
  ]) {
    assert.ok(geometry.index && geometry.getAttribute('color'), `${name} has textured surface geometry`);
    geometry.computeBoundingBox();
    assert.ok(geometry.boundingBox.max.z - geometry.boundingBox.min.z > .1, `${name} is curved in depth`);
    const normals = geometry.getAttribute('normal');
    const z = Array.from({length: normals.count}, (_, index) => normals.getZ(index));
    assert.ok(z.some(value => value > .2) && z.some(value => value < -.2), `${name} has lit front and back`);
    assert.equal(material.depthTest, true);
    assert.equal(material.depthWrite, true);
    assert.equal(material.transparent, false);
    assert.ok(material.roughness >= .6 && material.sheen > 0, `${name} has a soft satin finish`);
  }
  scene.scene.traverse(object => {
    if (!object.isMesh) return;
    const positions = object.geometry.getAttribute('position');
    if (positions) assert.ok(positions.array.every(Number.isFinite), `${object.name || 'mesh'} has finite positions`);
  });
  scene.destroy();
});

test('one leaf transfers from its branch onto the head and follows rebound and tilt without drifting', async () => {
  const {scene, sampleGarden, gardenEpisodes, THREE} = await gardenScene();
  const leaf = scene.garden.heroLeaf;
  const episode = gardenEpisodes(0).find(item => item.kind === 'leaf-hat');
  assert.ok(episode, 'the longer garden sequence preserves the designed leaf-hat interaction');
  const at = progress => episode.start + (episode.end - episode.start) * progress;
  scene.renderGardenFrame(sampleGarden({mode: 'idle', elapsed: at(.31)}));
  assert.equal(leaf.userData.headContact, false);
  for (const elapsed of [.52, .57, .61, .655, .68].map(at)) {
    scene.renderGardenFrame(sampleGarden({mode: 'idle', elapsed}));
    assert.equal(scene.garden.heroLeaf, leaf);
    assert.equal(leaf.userData.headContact, true);
    const expected = new THREE.Vector3(-.48, 1.07, .12).applyMatrix4(scene.ball.matrixWorld);
    assert.ok(leaf.position.distanceTo(expected) < .000001, `head contact at ${elapsed}`);
    assert.ok(scene.garden.heroLeafMesh.morphTargetInfluences[0] > .99, 'leaf drapes over the curved head');
  }
  scene.renderGardenFrame(sampleGarden({mode: 'idle', elapsed: at(.99)}));
  assert.equal(leaf.userData.headContact, false);
  const branchTip = scene.garden.heroAnchor.getWorldPosition(new THREE.Vector3());
  assert.ok(leaf.position.distanceTo(branchTip) < .000001, 'the same leaf returns to its branch');
  scene.destroy();
});

test('working light tracks are absent at rest and only enabled by the running task state', async () => {
  const {scene, root, orb, advance} = await gardenScene();
  const tracks = () => [scene.garden.orbit, scene.garden.signals, scene.garden.peekSignal].map(object => object.visible);
  advance(9000);
  assert.deepEqual(tracks(), [false, false, false]);
  root.classList.add('is-running');
  advance(2400);
  assert.equal(root.dataset.gardenMode, 'working');
  assert.ok(scene.gardenFrame.work > .99);
  assert.deepEqual(tracks(), [true, true, true]);
  root.classList.remove('is-running');
  advance(1000);
  assert.deepEqual(tracks(), [false, false, false]);
  orb.state = 'blocked';
  advance(3000);
  assert.equal(root.dataset.gardenMode, 'quiet');
  assert.deepEqual(tracks(), [false, false, false]);
  scene.destroy();
});

test('a completion token celebrates once even while another task continues running', async () => {
  const {scene, root, orb, advance, GARDEN_DURATIONS} = await gardenScene();
  root.classList.add('is-running');
  advance(2000);
  orb.gardenSuccessToken = 1;
  advance(3200);
  assert.equal(scene.director.mode, 'success');
  assert.equal(scene.garden.crown.visible, true);
  assert.ok(scene.gardenFrame.bloom > .99);
  assert.equal(scene.garden.orbit.visible, false);
  advance(GARDEN_DURATIONS.success);
  assert.equal(scene.director.mode, 'working');
  assert.equal(scene.garden.crown.visible, false);
  assert.equal(scene.garden.petals.visible, false);
  assert.equal(scene.director.pendingSuccesses, 0);
  orb.gardenSuccessToken = 2;
  advance(3200);
  assert.equal(scene.garden.crown.visible, true);
  scene.destroy();
});

test('hidden or paused scenes freeze their clocks, and reduced motion also hides interactive tracks', async () => {
  const {scene, orb, root, document, window, advance, drawAfter} = await gardenScene();
  root.classList.add('is-running');
  advance(2500);
  for (const property of ['manualPaused', 'hidden', 'visible']) {
    const clock = scene.director.clock;
    if (property === 'hidden') document.hidden = true;
    else if (property === 'visible') scene.visible = false;
    else orb[property] = true;
    drawAfter(30000);
    assert.equal(scene.director.clock, clock, property);
    if (property === 'hidden') document.hidden = false;
    else if (property === 'visible') scene.visible = true;
    else orb[property] = false;
    drawAfter(30000);
    assert.equal(scene.director.clock, clock, `${property} resumes without fast-forwarding`);
    advance(40);
    assert.equal(scene.director.clock, clock + 40);
  }
  const clock = scene.director.clock;
  orb.reducedMotion = true;
  scene.invalidate();
  assert.equal(scene.director.clock, clock);
  assert.equal(scene.gardenFrame.phase, 'still');
  assert.equal(scene.garden.orbit.visible, false);
  assert.equal(scene.garden.signals.visible, false);
  assert.equal(scene.garden.crown.visible, false);
  window.devicePixelRatio = 4;
  scene.resize();
  assert.equal(scene.renderer.pixelRatio, 2.5);
  scene.destroy();
});

test('destroy releases shared garden geometry, materials, shadow resources and observers once', async () => {
  const {scene, root, canceled} = await gardenScene();
  let disposed = 0;
  const count = scene.resources.size;
  scene.resources.forEach(resource => resource.addEventListener('dispose', () => {disposed++;}));
  scene.destroy();
  assert.equal(disposed, count);
  assert.equal(scene.resources.size, 0);
  assert.equal(scene.renderer.disposed, true);
  assert.equal(scene.resizeObserver.disconnected, true);
  assert.equal(scene.intersection.disconnected, true);
  assert.equal(root.dataset.gardenMode, undefined);
  assert.equal(root.dataset.gardenPhase, undefined);
  assert.equal(root.dataset.gardenEpisode, undefined);
  assert.ok(canceled() > 0);
  scene.destroy();
  assert.equal(disposed, count);
});

test('eye morphs release replaced GPU attributes and reuse same-sized buffers', async () => {
  const {scene, sampleGarden} = await gardenScene();
  const eye = scene.eyes[0].geometry;
  let released = 0;
  eye.addEventListener('dispose', () => {released++;});
  const initial = eye.getAttribute('position');
  scene.renderGardenFrame(sampleGarden({mode: 'success', elapsed: 3000}));
  assert.equal(released, 1, 'changing the triangulation releases both old buffers');
  const smile = eye.getAttribute('position');
  assert.notEqual(smile, initial);
  scene.renderGardenFrame(sampleGarden({mode: 'success', elapsed: 3100}));
  assert.equal(released, 1);
  assert.equal(eye.getAttribute('position'), smile, 'matching geometry is updated in place');
  scene.renderGardenFrame(sampleGarden({mode: 'idle', elapsed: 0}));
  assert.equal(released, 2, 'returning to normal also releases the replaced pair');
  scene.destroy();
  assert.equal(released, 3);
});

test('each garden receives its own seed while explicit seeds make component reviews reproducible', async () => {
  const first = await gardenScene({seed: NaN, cryptoSeed: 1007});
  const second = await gardenScene({seed: NaN, cryptoSeed: 1703});
  const repeat = await gardenScene({seed: 1007, cryptoSeed: 9876});
  assert.equal(first.scene.motionSeed, 1007);
  assert.equal(second.scene.motionSeed, 1703);
  assert.equal(repeat.scene.motionSeed, 1007);
  assert.notEqual(first.scene.director.variant, second.scene.director.variant);
  assert.equal(first.scene.director.variant, repeat.scene.director.variant);
  for (const {scene} of [first, second, repeat]) scene.destroy();
});

test('component taps preserve scene time and task state, and suspended scenes reject reactions', async () => {
  const {scene, orb, root, document, advance} = await gardenScene();
  advance(200);
  const clock = scene.director.clock;
  assert.equal(scene.react('tap'), true);
  advance(800);
  assert.equal(scene.director.clock, clock + 800, 'a tap does not reset the running clock');
  assert.equal(scene.gardenFrame.episode, 'tap-rebound');
  assert.equal(root.dataset.gardenEpisode, 'tap-rebound');
  assert.equal(orb.state, 'idle');
  assert.equal(orb.gardenSuccessToken, 0);
  for (const property of ['manualPaused', 'reducedMotion', 'hidden', 'visible']) {
    if (property === 'hidden') document.hidden = true;
    else if (property === 'visible') scene.visible = false;
    else orb[property] = true;
    assert.equal(scene.react('approach'), false, property);
    if (property === 'hidden') document.hidden = false;
    else if (property === 'visible') scene.visible = true;
    else orb[property] = false;
  }
  scene.destroy();
  assert.equal(scene.react('tap'), false);
});

test('nearby gaze stays responsive during leaf-hat choreography without loosening head attachment', async () => {
  const {scene, orb, THREE, sampleGarden, gardenEpisodes} = await gardenScene();
  const episode = gardenEpisodes(0).find(item => item.kind === 'leaf-hat');
  const frame = sampleGarden({mode: 'idle', elapsed: episode.start + (episode.end - episode.start) * .52});
  orb.pointer.targetX = 1;
  for (let step = 0; step < 30; step++) scene.renderGardenFrame(frame);
  const distantGaze = scene.gaze.x;
  orb.pointer.near = true;
  for (let step = 0; step < 30; step++) scene.renderGardenFrame(frame);
  assert.ok(scene.gaze.x > distantGaze + .2, 'the nearby person can still get the character’s attention');
  assert.equal(scene.garden.heroLeaf.userData.headContact, true);
  const expected = new THREE.Vector3(-.48, 1.07, .12).applyMatrix4(scene.ball.matrixWorld);
  assert.ok(scene.garden.heroLeaf.position.distanceTo(expected) < .000001);
  scene.destroy();
});
