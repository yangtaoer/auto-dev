const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const vm = require('node:vm');
const themes = JSON.parse(fs.readFileSync(path.join(__dirname, '../../app/static/themes/catalog.json'),'utf8')).themes;

async function gardenScene({seed = 17, cryptoSeed = 4321, theme} = {}) {
  // All three modules use the real Three geometry, materials and transforms.
  // Only browser ownership and the GPU renderer are stubbed for Node.
  const moduleURL = file => pathToFileURL(path.join(__dirname, '../../app/static', file));
  const [THREE, {AutoDevGarden}, gardenMotion, {ThemeDiorama}, {ThemePerformanceDirector}] = await Promise.all([
    import(moduleURL('vendor/three/three.module.js')),
    import(moduleURL('orb-garden.js')),
    import(moduleURL('garden-motion.js')),
    import(moduleURL('theme-scenes.js')),
    import(moduleURL('theme-choreography.js')),
  ]);
  const {GardenDirector, sampleGarden, gardenEpisodes, gardenDuration, GARDEN_DURATIONS} = gardenMotion;
  let rendererOptions, canceled = 0, now = 100;
  class Renderer {
    constructor(options) { rendererOptions = options; this.shadowMap = {}; }
    setPixelRatio(value) { this.pixelRatio = value; }
    setClearColor(color, alpha) { this.clearColor = color; this.clearAlpha = alpha; }
    setSize(width, height) { this.width = width; this.height = height; }
    render() { this.renders = (this.renders || 0) + 1; if (this.shadowMap.needsUpdate) this.shadowDraws = (this.shadowDraws || 0) + 1; }
    dispose() { this.disposed = true; }
  }
  const document = {hidden: false, createElement() { return {
    getContext() { return {createRadialGradient() { return {addColorStop() {}}; }, fillRect() {}}; },
    setAttribute() {}, addEventListener() {}, removeEventListener() {}, remove() {},
  }; }};
  const window = {devicePixelRatio: 1, crypto: {getRandomValues(values) {values[0] = cryptoSeed; return values;}}};
  if(theme)window.__THEME__=theme;
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
  vm.runInNewContext(source, {THREE: {...THREE, WebGLRenderer: Renderer}, AutoDevGarden, GardenDirector, ThemeDiorama, ThemePerformanceDirector,
    document, window, ResizeObserver: Observer, IntersectionObserver: Observer, performance: {now: () => now},
    requestAnimationFrame: () => 1, cancelAnimationFrame() {canceled++;}});
  const scene = new window.AutoDevOrbScene(orb);
  const drawAfter = ms => { now += ms; scene.draw(now); };
  const tickAfter = ms => { now += ms; scene.tick(now); };
  const advance = ms => { for (let time = 0; time < ms; time += 40) drawAfter(Math.min(40, ms - time)); };
  return {scene, orb, root, window, document, THREE, sampleGarden, gardenEpisodes, gardenDuration, GARDEN_DURATIONS,
    rendererOptions, advance, drawAfter, tickAfter, canceled: () => canceled};
}

test('the mascot renders every display frame while expensive shadows retain a separate cadence', async () => {
  const {scene, tickAfter} = await gardenScene();
  const start = scene.renderer.renders, shadows = scene.renderer.shadowDraws;
  for (let i = 0; i < 60; i++) tickAfter(1000 / 60);
  assert.equal(scene.renderer.renders - start, 60, 'no legacy 30 Hz frame gate');
  assert.ok(scene.renderer.shadowDraws - shadows >= 29 && scene.renderer.shadowDraws - shadows <= 31);
  assert.equal(scene.renderer.shadowMap.autoUpdate, false);
  assert.ok(Math.abs(scene.director.clock - 1000) < .001);
  scene.destroy();
});

test('gaze easing is frame-rate independent rather than snapping faster on high refresh displays', async () => {
  const fast = await gardenScene(), slow = await gardenScene();
  for (const item of [fast, slow]) { item.orb.pointer.near = true; item.orb.pointer.targetX = 1; }
  for (const [item, fps] of [[fast, 60], [slow, 30]]) {
    const frame = item.sampleGarden({mode: 'idle'});
    for (let i = 0; i < fps; i++) item.scene.renderGardenFrame(frame, 100 + i * 1000 / fps, 1000 / fps);
  }
  assert.ok(Math.abs(fast.scene.gaze.x - slow.scene.gaze.x) < 1e-10);
  assert.ok(fast.scene.gaze.x > .6);
  fast.scene.destroy(); slow.scene.destroy();
});

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
  assert.equal(scene.renderer.shadowMap.needsUpdate, true, 'a static reduced-motion frame receives fresh shadows');
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

test('every shipped theme has finite physical scenery and switching releases old environments without resetting task time', async()=>{
  const {scene,advance}=await gardenScene();advance(420);
  const clock=scene.director.clock,counts=new Map();
  for(let round=0;round<3;round++)for(const theme of themes){
    const old=new Set(scene.environmentResources);let disposed=0;
    if(scene.theme.id!==theme.id)old.forEach(item=>item.addEventListener('dispose',()=>disposed++));
    const changed=scene.theme.id!==theme.id;scene.setTheme(theme);
    if(changed)assert.equal(disposed,old.size,theme.id+' releases each old GPU resource once');
    assert.equal(scene.director.clock,clock,'skin preview never restarts the task choreography');
    if(counts.has(theme.id))assert.equal(scene.resources.size,counts.get(theme.id),'no growth after repeated skin switches');
    else counts.set(theme.id,scene.resources.size);
    scene.renderGardenFrame(scene.director.frame,100,16);
    scene.scene.traverse(object=>{const positions=object.geometry?.getAttribute('position');if(positions)for(const value of positions.array)assert.ok(Number.isFinite(value));});
  }
  scene.destroy();
});

test('the origami leaf eases continuously onto the head and follows squash without intersecting the orange sphere',async()=>{
  const theme=themes.find(t=>t.scene==='paper');if(!theme)return;
  const {scene,sampleGarden,THREE}=await gardenScene({theme});
  const base=sampleGarden({mode:'idle'}),point=new THREE.Vector3();let previous;
  for(let step=0;step<=100;step++){
    scene.renderGardenFrame({...base,hat:step/100});scene.scene.updateMatrixWorld(true);
    const current=new THREE.Vector3().setFromMatrixPosition(scene.garden.ornament.matrixWorld);
    if(previous)assert.ok(current.distanceTo(previous)<.06,'no abrupt parent-transfer jump');previous=current;
  }
  for(const hop of [0,.24,.48]){
    scene.renderGardenFrame({...base,hat:1,body:{...base.body,hop,roll:.12,stretch:.11}});scene.scene.updateMatrixWorld(true);
    const ornament=scene.garden.ornament,mesh=ornament.children[0],positions=mesh.geometry.getAttribute('position'),inverse=scene.ball.matrixWorld.clone().invert();
    assert.equal(ornament.userData.headContact,true);
    for(let i=0;i<positions.count;i++){point.fromBufferAttribute(positions,i).applyMatrix4(mesh.matrixWorld).applyMatrix4(inverse);assert.ok(point.length()>=1.02,'paper leaf stays outside sphere');}
  }
  scene.destroy();
});

test('clouds and paper keep depth-tested solids, independent motion, and signals only while tasks work',async()=>{
  for(const theme of themes.filter(t=>['sky','paper','moon'].includes(t.scene))){
    const {scene,sampleGarden}=await gardenScene({theme});const env=scene.scenery||scene.garden;
    const base=sampleGarden({mode:'idle'});env.update({...base,absoluteTime:1});
    const object=env.plane||env.fireflies[0]?.point||env.ornament,before=object.matrix.clone();
    env.update({...base,absoluteTime:4});object.updateMatrixWorld(true);
    assert.ok(object,theme.id+' owns moving physical props');
    env.group.traverse(item=>{if(item.isMesh&&!item.material.transparent)assert.equal(item.material.depthTest,true);});
    if(env.orbit){assert.equal(env.orbit.visible,false);env.update({...base,work:1});assert.equal(env.orbit.visible,true);}
    if(env.plane||env.fireflies.length)assert.notDeepEqual(object.matrix.elements,before.elements);
    scene.destroy();
  }
});

test('new worlds own distinct moving props, freeze with reduced motion and keep solid depth-tested surfaces',async()=>{
  const props={ocean:'jellyfish',space:'satellite',porcelain:'chime',arcade:'coins',gallery:'mobiles'};
  for(const theme of themes.filter(t=>t.scene in props)){
    const {scene,sampleGarden}=await gardenScene({theme});const env=scene.garden;
    let object=env[props[theme.scene]];if(Array.isArray(object))object=object[0];
    assert.ok(object,theme.id+' has its own physical interaction');
    const base=sampleGarden({mode:'idle'});
    env.update({...base,absoluteTime:2});object.updateMatrixWorld(true);const before=object.matrix.clone();
    env.update({...base,absoluteTime:6});object.updateMatrixWorld(true);assert.notDeepEqual(object.matrix.elements,before.elements);
    const atRest=object.matrix.clone();env.update({...base,absoluteTime:200,reduced:true});object.updateMatrixWorld(true);
    assert.deepEqual(object.matrix.elements,atRest.elements,'reduced scenery does not run a second timer');
    env.group.traverse(item=>{if(item.isMesh&&!item.material.transparent)assert.equal(item.material.depthTest,true);});
    assert.equal(env.orbit.visible,false);env.update({...base,work:1});assert.equal(env.orbit.visible,true);
    scene.destroy();
  }
});

test('every theme story couples real prop motion to body expressions without allocating new GPU resources',async()=>{
  const {themeItinerary}=await import(pathToFileURL(path.join(__dirname,'../../app/static/theme-choreography.js')));
  for(const theme of themes){
    const {scene,sampleGarden,THREE}=await gardenScene({theme});
    const env=scene.scenery||scene.garden,resourceCount=scene.resources.size;
    assert.ok(env.storyObjects.length||env.sparks.length||env.fallingLeaves.length||env.fish.length||env.balanceBlocks||env.mushroom,theme.id+' has extra physical story props');
    for(const event of themeItinerary(theme.scene,17).events)for(const beat of [.08,.23,.39,.53,.69,.85,.98]){
      scene.director.clock=(event.start+(event.end-event.start)*beat)*1000;
      scene.renderGardenFrame(sampleGarden({mode:'idle'}),scene.director.clock,16);
      assert.equal(scene.root.dataset.themeStory,event.id);assert.equal(env.group.userData.story,event.id);
      assert.ok(scene.gardenFrame.themeMoment.amount>=0);
      if(beat===.53)assert.ok(scene.gardenFrame.body.joy>0||Math.abs(scene.gardenFrame.body.sway)>0||scene.gardenFrame.body.hop>0,'face/body acknowledges its scene');
      assert.equal(scene.resources.size,resourceCount,'no per-frame geometries or materials');
      for(const object of [...env.storyObjects,...env.fallingLeaves,...env.fish,...env.coins,...env.bubbles,...env.mobiles,...env.fireflies.map(f=>f.point),env.lantern,env.chime,env.plane,env.jellyfish,env.satellite,env.planet].filter(Boolean)){
        object.updateWorldMatrix(true,true);
        const inverse=new THREE.Matrix4().copy(scene.ball.matrixWorld).invert(),point=new THREE.Vector3();
        object.traverse(mesh=>{if(!mesh.isMesh||!mesh.visible)return;const positions=mesh.geometry.getAttribute('position');for(let i=0;i<positions.count;i++){
          point.fromBufferAttribute(positions,i).applyMatrix4(mesh.matrixWorld).applyMatrix4(inverse);
          assert.ok(point.length()>1.005,theme.id+' '+event.id+' '+object.name+' never intersects the actual orange ellipsoid: '+point.length());
        }});
      }
    }
    scene.destroy();
  }
});

test('solar cell seams remain attached when panels fold and unfold',async()=>{
  const theme=themes.find(t=>t.scene==='space'),{scene,THREE}=await gardenScene({theme});
  for(const panel of scene.garden.solarPanels){
    assert.equal(panel.children.length,3);
    for(const yaw of [-.9,0,.9]){panel.rotation.y=yaw;panel.updateWorldMatrix(true,true);
      const inverse=panel.matrixWorld.clone().invert();
      panel.children.forEach((seam,i)=>{const center=new THREE.Vector3().setFromMatrixPosition(seam.matrixWorld).applyMatrix4(inverse);assert.ok(center.distanceTo(new THREE.Vector3(-.1+i*.1,0,0))<1e-6);});
    }
  }
  scene.destroy();
});

test('ocean and porcelain ornaments follow the real head surface during squash and rebound',async()=>{
  for(const theme of themes.filter(t=>['ocean','porcelain'].includes(t.scene))){
    const {scene,sampleGarden,THREE}=await gardenScene({theme}),base=sampleGarden({mode:'idle'}),point=new THREE.Vector3();
    for(const hop of [0,.24,.48]){
      scene.renderGardenFrame({...base,hat:1,body:{...base.body,hop,roll:.12,stretch:.11}});scene.scene.updateMatrixWorld(true);
      const mesh=scene.garden.ornament.children[0],positions=mesh.geometry.getAttribute('position'),inverse=scene.ball.matrixWorld.clone().invert();
      for(let i=0;i<positions.count;i++){point.fromBufferAttribute(positions,i).applyMatrix4(mesh.matrixWorld).applyMatrix4(inverse);assert.ok(point.length()>=1.02,theme.id+' ornament never cuts into the orange body');}
    }
    scene.destroy();
  }
});
