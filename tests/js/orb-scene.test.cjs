const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const vm = require('node:vm');
const motion = require('../../app/static/orb-motion.js');

async function habitatScene() {
  // Real Three geometries/materials catch invalid APIs and scene composition;
  // only browser ownership and the GPU renderer are stubbed in this unit test.
  const THREE = await import(pathToFileURL(path.join(__dirname, '../../app/static/vendor/three/three.module.js')));
  let rendererOptions, canceled = 0;
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
  const window = {devicePixelRatio: 1, AutoDevOrbMotion: motion};
  const root = {dataset: {}, classList: {add() {}, remove() {}, contains: () => false},
    closest: selector => selector === '.sidebar-character-stage', append() {},
    getBoundingClientRect: () => ({width: 204, height: 180})};
  const orb = {root, pointer: {targetX: 0, targetY: 0}, state: 'idle',
    sampleMotion: () => motion.sample(null, 0), setVisible() {}};
  const Observer = class {observe() {} disconnect() {this.disconnected = true;} };
  const source = fs.readFileSync(path.join(__dirname, '../../app/static/orb-scene.js'), 'utf8')
    .replace(/^import .*$/m, '').replace('export class AutoDevOrbScene', 'class AutoDevOrbScene');
  vm.runInNewContext(source, {THREE: {...THREE, WebGLRenderer: Renderer}, document, window,
    ResizeObserver: Observer, IntersectionObserver: Observer, performance: {now: () => 100},
    requestAnimationFrame: () => 1, cancelAnimationFrame() {canceled++;}});
  const scene = new window.AutoDevOrbScene(orb);
  return {scene, orb, root, window, document, rendererOptions, canceled: () => canceled};
}

test('orange body has one soft key highlight and an enlarged, smooth sidebar silhouette', async () => {
  const {scene, rendererOptions} = await habitatScene();
  const lights = [];
  scene.scene.traverse(object => {if (object.isDirectionalLight) lights.push(object);});
  assert.equal(lights.length, 1);
  assert.equal(scene.body.material.clearcoat, 0);
  assert.ok(scene.body.material.roughness >= .4);
  assert.ok(scene.body.material.specularIntensity < .5);
  assert.equal(scene.body.geometry.parameters.widthSegments, 96);
  assert.equal(scene.body.geometry.parameters.heightSegments, 64);
  assert.equal(scene.radius, .87);
  assert.equal(scene.camera.zoom, 1.35);
  assert.equal(rendererOptions.alpha, true);
  assert.equal(rendererOptions.antialias, true);
  assert.equal(rendererOptions.premultipliedAlpha, true);
  assert.equal(scene.renderer.pixelRatio, 1.75);
  assert.equal(scene.renderer.clearAlpha, 0);
  scene.destroy();
});

test('flowers and branches are real depth-tested geometry and fade without occluding the face', async () => {
  const {scene, root} = await habitatScene();
  assert.deepEqual([...scene.habitats.keys()], ['meadow', 'blossom', 'canopy']);
  assert.equal(scene.flowers.length, 3);
  assert.equal(scene.leaves.length, 28);
  for (const group of scene.habitats.values()) {
    assert.ok(group.position.z < -.7);
    assert.ok(group.userData.materials.size > 3);
    for (const material of group.userData.materials) {
      assert.equal(material.depthTest, true);
      assert.equal(material.depthWrite, false);
    }
  }
  scene.habitatTime = 28600;
  scene.habitatLastAt = null;
  scene.updateHabitat(200, false, 1);
  assert.equal(root.dataset.habitat, 'meadow');
  assert.equal(root.dataset.habitatMix, '0.500');
  assert.equal(scene.habitats.get('meadow').visible, true);
  assert.equal(scene.habitats.get('blossom').visible, true);
  assert.equal(scene.habitats.get('canopy').visible, false);
  assert.ok([...scene.habitats.get('meadow').userData.materials].every(m => m.opacity === .41));
  scene.destroy();
});

test('floating leaves preserve physical clearance from the moving, enlarged body', async () => {
  const {scene, orb} = await habitatScene();
  for (const gesture of ['roam', 'slalom', 'leafchase', 'float']) {
    const clip = motion.clip(gesture, 0, 1, 1);
    for (let step = 1; step < 20; step++) {
      orb.sampleMotion = now => motion.sample(clip, now);
      scene.draw(clip.duration * step / 20);
      for (const {pivot, floatingIndex} of scene.leaves) if (floatingIndex !== null) {
        const clearance = scene.radius * 1.16 + pivot.scale.x + .04;
        assert.ok(pivot.position.distanceTo(scene.ball.position) >= clearance - .000001);
      }
    }
  }
  scene.destroy();
});

test('suspended scene clocks stay frozen; reduced motion keeps a still habitat', async () => {
  const {scene, orb, document, window} = await habitatScene();
  scene.habitatTime = 32000;
  scene.habitatLastAt = 100;
  scene.updateHabitat(200, false, 1);
  assert.equal(scene.habitatTime, 32100);
  for (const property of ['manualPaused', 'reducedMotion', 'hidden', 'visible']) {
    if (property === 'hidden') document.hidden = true;
    else if (property === 'visible') scene.visible = false;
    else orb[property] = true;
    scene.updateHabitat(10000, Boolean(orb.reducedMotion), 1);
    assert.equal(scene.habitatTime, 32100, property);
    if (property === 'hidden') document.hidden = false;
    else if (property === 'visible') scene.visible = true;
    else orb[property] = false;
  }
  scene.updateHabitat(11000, true, 1);
  assert.equal(scene.habitats.get('meadow').visible, true);
  assert.equal(scene.habitats.get('blossom').visible, false);
  assert.equal(scene.habitats.get('meadow').rotation.z, 0);
  window.devicePixelRatio = 4;
  scene.resize();
  assert.equal(scene.renderer.pixelRatio, 2.5);
  scene.destroy();
});

test('destroy releases botanical geometries, materials, shadow resources and observers once', async () => {
  const {scene, root, canceled} = await habitatScene();
  let disposed = 0;
  const count = scene.resources.size;
  scene.resources.forEach(resource => resource.addEventListener('dispose', () => {disposed++;}));
  scene.destroy();
  assert.equal(disposed, count);
  assert.equal(scene.resources.size, 0);
  assert.equal(scene.renderer.disposed, true);
  assert.equal(scene.resizeObserver.disconnected, true);
  assert.equal(scene.intersection.disconnected, true);
  assert.equal(root.dataset.habitat, undefined);
  assert.ok(canceled() > 0);
  scene.destroy();
  assert.equal(disposed, count);
});
