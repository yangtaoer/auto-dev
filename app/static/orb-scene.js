/* The orange character and its physically connected miniature garden. */
import * as THREE from './vendor/three/three.module.js';
import {AutoDevGarden} from './orb-garden.js?v=1.0-Beta.5';
import {GardenDirector} from './garden-motion.js?v=1.0-Beta.5';

const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const instanceSeed = () => {
  if (window.crypto?.getRandomValues) return window.crypto.getRandomValues(new Uint32Array(1))[0];
  return Math.floor(Math.random() * 0x100000000);
};

export class AutoDevOrbScene {
  constructor(orb) {
    this.orb = orb;
    this.root = orb.root;
    this.disposed = false;
    this.visible = true;
    this.frame = 0;
    this.last = 0;
    this.sceneLastAt = null;
    this.gaze = {x: 0, y: 0};
    this.resources = new Set();
    // Each mounted component grows its own, reproducible motion sequence. It is
    // never tied to wall-clock time or shared with the character on another page.
    this.motionSeed = Number.isFinite(orb.options?.gardenSeed) ? orb.options.gardenSeed : instanceSeed();
    this.director = new GardenDirector({seed: this.motionSeed});
    this.isLogin = Boolean(this.root.closest('.login-character-stage'));
    this.isSidebar = Boolean(this.root.closest('.sidebar-character-stage'));
    this.radius = this.isSidebar ? .87 : .83;
    this.origin = new THREE.Vector3(0, -.04, .12);
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'autodev-orb-scene';
    this.canvas.setAttribute('aria-hidden', 'true');
    try {
      this.renderer = new THREE.WebGLRenderer({canvas: this.canvas, alpha: true,
        antialias: true, premultipliedAlpha: true, powerPreference: 'low-power'});
      this.renderer.setPixelRatio(this.pixelRatio());
      this.renderer.setClearColor(0xd3e8d9, 0);
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1.06;
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = THREE.PCFShadowMap;
      this.scene = new THREE.Scene();
      this.camera = new THREE.OrthographicCamera(-2, 2, 1.7, -1.7, .1, 30);
      this.camera.position.set(0, .83, 7);
      this.camera.lookAt(0, .38, 0);
      this.buildScene();
      this.root.append(this.canvas);
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.root);
      this.onLost = event => {
        event.preventDefault();
        this.destroy();
        if (orb.scene === this) orb.scene = null;
        this.root.dataset.renderer = 'svg';
        orb.motionDriver?.setPaused(document.hidden || orb.manualPaused || orb.reducedMotion || !orb.visible);
        orb._scheduleAmbient?.();
      };
      this.canvas.addEventListener('webglcontextlost', this.onLost);
      this.intersection = new IntersectionObserver(entries => {
        this.visible = entries[0]?.isIntersecting !== false;
        orb.setVisible(this.visible);
        this.invalidate();
      });
      this.intersection.observe(this.root);
      this.resize();
      this.root.classList.add('is-scene');
      this.root.dataset.renderer = 'three';
      this.invalidate();
    } catch (error) {
      this.destroy();
      throw error;
    }
  }

  pixelRatio() { return Math.min(Math.max(window.devicePixelRatio || 1, 1.75), 2.5); }

  mesh(geometry, material, parent = this.scene) {
    this.resources.add(geometry);
    (Array.isArray(material) ? material : [material]).forEach(item => this.resources.add(item));
    const mesh = new THREE.Mesh(geometry, material);
    parent.add(mesh);
    return mesh;
  }

  buildScene() {
    this.scene.add(new THREE.HemisphereLight(0xfff9e9, 0xd6cbb3, 1.8));
    const key = new THREE.DirectionalLight(0xfff4df, 2.7);
    key.position.set(-3, 5, 5);
    key.castShadow = true;
    Object.assign(key.shadow.camera, {left: -3.1, right: 3.1, top: 3.1, bottom: -2, near: .1, far: 16});
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.normalBias = .025;
    key.shadow.bias = -.00015;
    this.scene.add(key);
    this.ball = new THREE.Group();
    this.ball.position.copy(this.origin);
    this.ball.scale.setScalar(this.radius);
    this.scene.add(this.ball);
    const sphere = new THREE.SphereGeometry(1, 96, 64);
    const positions = sphere.getAttribute('position'), colors = [];
    const lower = new THREE.Color(0xfa480e), upper = new THREE.Color(0xff7925), color = new THREE.Color();
    for (let i = 0; i < positions.count; i++) {
      color.copy(lower).lerp(upper, clamp((positions.getY(i) + .8) / 1.5, 0, 1));
      colors.push(color.r, color.g, color.b);
    }
    sphere.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    this.body = this.mesh(sphere, new THREE.MeshPhysicalMaterial({color: 0xffffff,
      vertexColors: true, roughness: .40, metalness: 0, clearcoat: 0,
      specularIntensity: .48, premultipliedAlpha: true}), this.ball);
    this.body.castShadow = true;
    this.body.receiveShadow = true;
    this.eyes = [0, 1].map(() => this.mesh(new THREE.BufferGeometry(), new THREE.MeshPhysicalMaterial({
      color: 0x122b20, roughness: .57, metalness: 0, clearcoat: 0, specularIntensity: .15,
      side: THREE.DoubleSide}), this.ball));
    this.groundY = this.origin.y - this.radius - .025;
    this.floor = this.mesh(new THREE.PlaneGeometry(6, 4), new THREE.ShadowMaterial({color: 0x527d60, opacity: .13}));
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.position.y = this.groundY;
    this.floor.receiveShadow = true;
    this.buildContactShadow();
    this.garden = new AutoDevGarden({scene: this.scene, ball: this.ball, radius: this.radius,
      groundY: this.groundY, resources: this.resources, mesh: this.mesh.bind(this)});
  }

  buildContactShadow() {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 128;
    const context = canvas.getContext('2d');
    const gradient = context.createRadialGradient(64, 64, 4, 64, 64, 62);
    gradient.addColorStop(0, 'rgba(78,125,92,.58)');
    gradient.addColorStop(.38, 'rgba(117,160,126,.3)');
    gradient.addColorStop(1, 'rgba(151,192,160,0)');
    context.fillStyle = gradient;
    context.fillRect(0, 0, 128, 128);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    this.resources.add(texture);
    this.contactShadow = this.mesh(new THREE.PlaneGeometry(2.15, 1.65),
      new THREE.MeshBasicMaterial({map: texture, transparent: true, depthWrite: false,
        opacity: .58, premultipliedAlpha: true}));
    this.contactShadow.rotation.x = -Math.PI / 2;
    this.contactShadow.position.set(this.origin.x, this.groundY + .012, this.origin.z);
  }

  paintEyes(now) {
    const driver = this.orb.motionDriver, pose = this.gardenFrame?.body || {};
    const joy = clamp(pose.joy || 0, 0, 1);
    const squeeze = ['gather', 'spring'].includes(this.gardenFrame?.phase) ? clamp(pose.squeeze || 0, 0, 1) : 0;
    let polygons;
    if (driver?._currentPolys) {
      const polys = driver._currentPolys(clamp(driver.eyeMorph.x, 0, 1));
      const centers = polys.map(poly => poly.reduce((sum, p) =>
        [sum[0] + p[0] / poly.length, sum[1] + p[1] / poly.length], [0, 0]));
      const center = [(centers[0][0] + centers[1][0]) / 2, (centers[0][1] + centers[1][1]) / 2];
      polygons = polys.map((poly, index) => {
        const lid = window.GROK_EYES?.winkLid ? window.GROK_EYES.winkLid(driver.blink.x, now,
          driver.winkAt, driver.winkEye, index) : clamp(driver.blink?.x ?? 1, .06, 1.15);
        const wink = index === 0 ? pose.wink ?? 1 : 1;
        const scale = .0065 * clamp(driver.eyeScale?.x ?? 1, .7, 1.2);
        return poly.map(p => new THREE.Vector2((p[0] - center[0]) * scale,
          (center[1] - centers[index][1]) * scale + (centers[index][1] - p[1]) * scale * lid * wink));
      });
    } else {
      polygons = [0, 1].map(index => Array.from({length: 32}, (_, i) => {
        const angle = i / 32 * Math.PI * 2;
        return new THREE.Vector2((index ? .24 : -.24) + Math.cos(angle) * .095,
          Math.sin(angle) * .135 * (index === 0 ? pose.wink ?? 1 : 1));
      }));
    }
    if (joy > .01 || squeeze > .01) {
      polygons = [0, 1].map(index => Array.from({length: 40}, (_, i) => {
        const a = i / 40 * Math.PI * 2, x = Math.cos(a) * .16;
        const normalY = Math.sin(a) * .17;
        const smileY = .085 - (x / .16) ** 2 * .12 + Math.sin(a) * .032;
        const squeezedX = (index ? -1 : 1) * (.06 - Math.abs(Math.sin(a)) * .16) + Math.cos(a) * .033;
        return new THREE.Vector2((index ? .24 : -.24) + x * (1 - squeeze) + squeezedX * squeeze,
          (normalY * (1 - joy) + smileY * joy) * (1 - squeeze) + Math.sin(a) * .12 * squeeze);
      }));
    }
    polygons.forEach((points, index) => {
      const triangles = THREE.ShapeUtils.triangulateShape(points, []), positions = [], normals = [];
      const project = p => {
        const x = clamp(p.x + this.gaze.x * .11, -.8, .8);
        const y = clamp(p.y + .12 + this.gaze.y * .10, -.65, .68);
        const z = Math.sqrt(Math.max(.09, 1 - x * x - y * y));
        positions.push(x * 1.024, y * 1.024, z * 1.024);
        normals.push(x, y, z);
      };
      triangles.forEach(([a, b, c]) => {
        const p = points[a], q = points[b], r = points[c], pq = p.clone().add(q).multiplyScalar(.5);
        const qr = q.clone().add(r).multiplyScalar(.5), rp = r.clone().add(p).multiplyScalar(.5);
        [p, pq, rp, pq, q, qr, rp, qr, r, pq, qr, rp].forEach(project);
      });
      const geometry = this.eyes[index].geometry;
      const rebuild = geometry.getAttribute('position')?.array.length !== positions.length ||
        geometry.getAttribute('normal')?.array.length !== normals.length;
      // Morphs have different triangulations. Release old GPU buffers before
      // replacing both attributes; otherwise every smile leaks the old pair.
      if (rebuild) geometry.dispose();
      for (const [name, data] of [['position', positions], ['normal', normals]]) {
        const attribute = geometry.getAttribute(name);
        if (rebuild) geometry.setAttribute(name, new THREE.Float32BufferAttribute(data, 3));
        else { attribute.array.set(data); attribute.needsUpdate = true; }
      }
      geometry.computeBoundingSphere();
    });
  }

  resize() {
    if (this.disposed) return;
    const box = this.root.getBoundingClientRect();
    if (!box.width || !box.height) return;
    this.renderer.setPixelRatio(this.pixelRatio());
    this.renderer.setSize(box.width, box.height, false);
    const aspect = box.width / box.height;
    const width = Math.max(3.8, 3.08 * aspect), height = width / aspect;
    Object.assign(this.camera, {left: -width / 2, right: width / 2, top: height / 2, bottom: -height / 2});
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  paused() { return Boolean(this.orb.manualPaused || this.orb.reducedMotion || document.hidden || !this.visible); }
  invalidate() {
    if (this.disposed) return;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.last = 0;
    this.sceneLastAt = null;
    this.draw(performance.now());
    if (!this.paused()) this.frame = requestAnimationFrame(now => this.tick(now));
  }
  tick(now) {
    this.frame = 0;
    if (this.disposed || this.paused()) return;
    if (!this.last || now - this.last >= 1000 / 30) { this.draw(now); this.last = now; }
    this.frame = requestAnimationFrame(time => this.tick(time));
  }
  mode() {
    if (this.orb.state === 'success') return 'success';
    if (this.root.classList.contains('is-running')) return 'working';
    if (['blocked', 'error', 'sleeping'].includes(this.orb.state)) return 'quiet';
    return 'idle';
  }
  react(kind) {
    if (this.disposed || this.paused()) return false;
    // The running render loop picks up interaction intent. Restarting it on
    // pointer events would continually zero the elapsed time and freeze motion.
    return this.director.react(kind);
  }
  draw(now) {
    if (this.disposed || !this.renderer) return;
    const delta = this.sceneLastAt === null ? 0 : clamp(now - this.sceneLastAt, 0, 100);
    this.sceneLastAt = this.paused() ? null : now;
    const frame = this.director.update(delta, {mode: this.mode(), paused: this.paused(),
      reduced: Boolean(this.orb.reducedMotion), successToken: this.orb.gardenSuccessToken || 0});
    this.renderGardenFrame(frame, now);
  }
  renderGardenFrame(frame, now = performance.now()) {
    this.gardenFrame = frame;
    const pose = frame.body, driver = this.orb.motionDriver;
    const intent = Math.max(frame.peek, frame.hat, frame.crown, frame.work * .7);
    // Keep choreography readable while still acknowledging a nearby person,
    // even when a leaf is resting on the head. Attachments follow ball.matrixWorld.
    const nearby = Boolean(this.orb.pointer.near);
    const pointerWeight = nearby ? .64 - intent * .25 : .18 * (1 - intent * .6);
    const gazeX = clamp(pose.gazeX + this.orb.pointer.targetX * pointerWeight, -1.2, 1.2);
    const gazeY = clamp(pose.gazeY + this.orb.pointer.targetY * pointerWeight, -1.1, 1.1);
    this.gaze.x += (gazeX - this.gaze.x) * .20;
    this.gaze.y += (gazeY - this.gaze.y) * .20;
    const stretch = clamp(pose.stretch, .78, 1.19), yRadius = this.radius * stretch;
    this.ball.position.set(this.origin.x + pose.sway, this.origin.y + yRadius - this.radius + pose.hop, this.origin.z + pose.depth);
    this.ball.scale.set(this.radius / Math.sqrt(stretch), yRadius, this.radius / Math.sqrt(stretch));
    this.ball.rotation.set(pose.pitch + this.gaze.y * .08, pose.yaw + this.gaze.x * .15, pose.roll);
    this.ball.updateMatrixWorld(true);
    this.paintEyes(this.orb.reducedMotion ? driver?.t0 || now : now);
    const altitude = Math.max(0, pose.hop);
    this.contactShadow.position.x = this.ball.position.x;
    this.contactShadow.position.z = this.ball.position.z;
    this.contactShadow.scale.setScalar(1 + altitude * .75);
    this.contactShadow.material.opacity = clamp(.60 - altitude * .6, .16, .60);
    this.garden.update({...frame, reduced: Boolean(this.orb.reducedMotion)});
    this.root.dataset.gardenMode = this.mode();
    this.root.dataset.gardenPhase = frame.phase;
    this.root.dataset.gardenEpisode = frame.episode || frame.phase;
    this.renderer.render(this.scene, this.camera);
  }
  destroy() {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.resizeObserver?.disconnect();
    this.intersection?.disconnect();
    this.canvas?.removeEventListener('webglcontextlost', this.onLost);
    this.garden?.destroy?.();
    this.resources.forEach(resource => resource.dispose());
    this.resources.clear();
    this.scene?.traverse(object => { if (object.isLight) object.shadow?.dispose(); });
    this.renderer?.dispose();
    this.canvas?.remove();
    this.root.classList.remove('is-scene');
    for (const key of ['gardenMode', 'gardenPhase', 'gardenEpisode']) delete this.root.dataset[key];
  }
}
window.AutoDevOrbScene = AutoDevOrbScene;
window.AutoDevOrb?.instances.forEach(orb => orb.attachScene(AutoDevOrbScene));
