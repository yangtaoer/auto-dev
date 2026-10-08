/* One softly lit orange companion in a changing, true-3D botanical habitat. */
import * as THREE from './vendor/three/three.module.js';

const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const motionState = orb => orb.root.dataset.reaction || orb.state;

export class AutoDevOrbScene {
  constructor(orb) {
    this.orb = orb;
    this.root = orb.root;
    this.disposed = false;
    this.visible = true;
    this.frame = 0;
    this.last = 0;
    this.gaze = {x: 0, y: 0};
    this.foliageOffset = new THREE.Vector3();
    this.leaves = [];
    this.habitats = new Map();
    this.resources = new Set();
    this.phase = Math.random() * Math.PI * 2;
    this.habitatTime = 0;
    this.habitatLastAt = null;
    this.isLogin = Boolean(this.root.closest('.login-character-stage'));
    this.isSidebar = Boolean(this.root.closest('.sidebar-character-stage'));
    this.radius = this.isSidebar ? .87 : this.isLogin ? .83 : .75;
    this.origin = new THREE.Vector3(this.isLogin ? -.12 : .04, -.025, .12);
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'autodev-orb-scene';
    this.canvas.setAttribute('aria-hidden', 'true');
    try {
      // An explicit premultiplied-alpha contract avoids dark compositing seams.
      // The tiny canvas can afford supersampling even on a standard-DPI display.
      this.renderer = new THREE.WebGLRenderer({canvas: this.canvas, alpha: true,
        antialias: true, premultipliedAlpha: true, powerPreference: 'low-power'});
      this.renderer.setPixelRatio(this.pixelRatio());
      this.renderer.setClearColor(0xfff8ed, 0);
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1.05;
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = THREE.PCFShadowMap;
      this.scene = new THREE.Scene();
      this.camera = new THREE.PerspectiveCamera(36, 1, .1, 30);
      this.camera.zoom = this.isSidebar ? 1.35 : this.isLogin ? 1.12 : 1;
      this.camera.position.set(0, this.isLogin ? .8 : .48, this.isLogin ? 5.8 : 5.5);
      this.camera.lookAt(0, .09, 0);
      this.buildScene();
      this.root.append(this.canvas);
      this.onResize = () => this.resize();
      this.resizeObserver = new ResizeObserver(this.onResize);
      this.resizeObserver.observe(this.root);
      this.onLost = event => {
        event.preventDefault();
        this.destroy();
        if (orb.scene === this) orb.scene = null;
        this.root.classList.remove('is-scene');
        this.root.dataset.renderer = 'svg';
        orb.motionDriver?.setPaused(document.hidden || orb.manualPaused || orb.reducedMotion || !orb.visible);
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
    this.resources.add(material);
    const mesh = new THREE.Mesh(geometry, material);
    parent.add(mesh);
    return mesh;
  }

  buildScene() {
    // Only the key produces a highlight. Hemisphere fill cannot create the
    // previous two additional white spots; warm bounce keeps the edge orange.
    this.scene.add(new THREE.HemisphereLight(0xfff4df, 0xce8962, 1.65));
    const key = new THREE.DirectionalLight(0xfff4df, 2.6);
    key.position.set(-3, 5, 4);
    key.castShadow = true;
    Object.assign(key.shadow.camera, {left: -3.4, right: 3.4, top: 2.8, bottom: -2.4, near: .1, far: 15});
    key.shadow.mapSize.set(512, 512);
    key.shadow.normalBias = .035;
    key.shadow.bias = -.0002;
    this.scene.add(key);
    this.ball = new THREE.Group();
    this.ball.position.copy(this.origin);
    this.ball.scale.setScalar(this.radius);
    this.scene.add(this.ball);
    this.body = this.mesh(new THREE.SphereGeometry(1, 96, 64), new THREE.MeshPhysicalMaterial({
      color: 0xff6329, roughness: .43, metalness: 0, clearcoat: 0,
      specularIntensity: .42, premultipliedAlpha: true,
    }), this.ball);
    this.body.castShadow = true;
    this.eyes = [0, 1].map(() => this.mesh(new THREE.BufferGeometry(), new THREE.MeshPhysicalMaterial({
      color: 0x172e26, roughness: .48, clearcoat: 0, side: THREE.DoubleSide,
    }), this.ball));
    this.groundY = -this.radius + this.origin.y - .025;
    this.floor = this.mesh(new THREE.PlaneGeometry(7, 5), new THREE.ShadowMaterial({color:0x81ad93, opacity: .10}));
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.position.y = this.groundY;
    this.floor.receiveShadow = true;
    this.buildContactShadow();this.buildFoliage();
    const arcMaterial = new THREE.MeshStandardMaterial({color: 0x9ccbb0, roughness: .8,
      transparent: true, opacity: .48, depthWrite: false});
    this.orbit = new THREE.Group();
    this.orbit.position.copy(this.ball.position);
    this.scene.add(this.orbit);
    [0, Math.PI].forEach(angle => {
      const arc = this.mesh(new THREE.TorusGeometry(this.radius + .27, .008, 5, 36, .9), arcMaterial, this.orbit);
      arc.rotation.set(.6, .4, angle);
    });
    this.sparkles = new THREE.Group();
    this.scene.add(this.sparkles);
    for (let i = 0; i < 8; i++) {
      const sparkle = this.mesh(new THREE.OctahedronGeometry(.028, 0), new THREE.MeshBasicMaterial({
        color: i % 2 ? 0xb7dab2 : 0xffc986}), this.sparkles);
      sparkle.userData.phase = i / 8 * Math.PI * 2;
    }
  }

  buildContactShadow() {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 128;
    const context = canvas.getContext('2d');
    const gradient = context.createRadialGradient(64, 64, 4, 64, 64, 62);
    gradient.addColorStop(0, 'rgba(112,157,130,.56)');
    gradient.addColorStop(.35, 'rgba(133,176,145,.3)');
    gradient.addColorStop(1, 'rgba(151,192,160,0)');
    context.fillStyle = gradient;
    context.fillRect(0, 0, 128, 128);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    this.resources.add(texture);
    const material = new THREE.MeshBasicMaterial({map: texture, transparent: true,
      depthWrite: false, opacity: .58, premultipliedAlpha: true});
    this.contactShadow = this.mesh(new THREE.PlaneGeometry(2.25, 1.6), material);
    this.contactShadow.rotation.x = -Math.PI / 2;
    this.contactShadow.position.set(this.origin.x, this.groundY + .012, this.origin.z);
    this.contactShadow.renderOrder = 1;
  }

  leafGeometry() {
    const positions = [], indices = [], rows = 10, columns = 6;
    for (let row = 0; row <= rows; row++) for (let col = 0; col <= columns; col++) {
      const t = row / rows, w = col / columns * 2 - 1;
      positions.push(Math.sin(Math.PI * t) * w * .3, t,
        Math.sin(Math.PI * t) * (.16 - .12 * Math.abs(w)) + w * t * .08);
    }
    for (let row = 0; row < rows; row++) for (let col = 0; col < columns; col++) {
      const a = row * (columns + 1) + col, b = a + columns + 1;
      indices.push(a, b, a + 1, b, b + 1, a + 1);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    return {geometry, base: new Float32Array(positions)};
  }

  plantMaterial(color, opacity = 1) {
    return new THREE.MeshStandardMaterial({color, roughness: .82, side: THREE.DoubleSide,
      transparent: true, opacity, depthWrite: false, premultipliedAlpha: true});
  }

  leaf(parent, position, size, rotation, color, habitat, floatingIndex = null) {
    const pivot = new THREE.Group();
    pivot.position.fromArray(position);
    pivot.rotation.set(...rotation);
    pivot.scale.setScalar(size);
    parent.add(pivot);
    const {geometry, base} = this.leafGeometry();
    const leaf = this.mesh(geometry, this.plantMaterial(color), pivot);
    leaf.castShadow=true;leaf.receiveShadow=true;
    const vein = new THREE.CatmullRomCurve3([new THREE.Vector3(0, 0, .018),
      new THREE.Vector3(0, .5, .17), new THREE.Vector3(0, 1, .018)]);
    this.mesh(new THREE.TubeGeometry(vein, 8, .007, 4, false), this.plantMaterial(0xcce1bd), pivot);
    this.leaves.push({pivot, geometry, base, index: this.leaves.length,
      floatingIndex, habitat, restRotation: pivot.rotation.clone(), phase: this.leaves.length * 1.73});
    return pivot;
  }

  branch(parent, points, radius = .013, color = 0x94b593) {
    const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)));
    return this.mesh(new THREE.TubeGeometry(curve, 18, radius, 5, false), this.plantMaterial(color), parent);
  }

  flower(parent, position, size, color, phase) {
    const blossom = new THREE.Group();
    blossom.position.fromArray(position);
    blossom.scale.setScalar(size);
    blossom.rotation.set(-.12, -.14, phase * .18);
    parent.add(blossom);
    const petalMaterial = this.plantMaterial(color);
    for (let i = 0; i < 5; i++) {
      const petal = this.mesh(new THREE.SphereGeometry(.12, 12, 8), petalMaterial, blossom);
      const angle = i / 5 * Math.PI * 2;
      petal.position.set(Math.cos(angle) * .11, Math.sin(angle) * .11, .025);
      petal.scale.set(1, .64, .25);
      petal.rotation.z = angle;
    }
    this.mesh(new THREE.SphereGeometry(.065, 12, 8), this.plantMaterial(0xe9c674), blossom).position.z = .075;
    blossom.userData.flowerPhase = phase;
    return blossom;
  }

  habitat(name) {
    const group = new THREE.Group();
    // All planted branches/petals stay behind the whole character's travel
    // envelope, sharing its depth buffer rather than masking the face in CSS.
    group.position.z = -.76;
    group.userData.name = name;
    this.scene.add(group);
    this.habitats.set(name, group);
    return group;
  }

  buildFoliage() {
    this.foliage = new THREE.Group();
    this.scene.add(this.foliage);
    const colors = [0x93bda1, 0xb7cda1, 0x7dab95, 0xa0c8b1, 0xc2d4a7, 0xaccab3, 0x91bea7, 0xc7d9b6];
    for (let index = 0; index < 8; index++) {
      this.leaf(this.foliage, [0, 0, 0], [.38, .31, .22, .32, .26, .20, .29, .24][index],
        [0, 0, 0], colors[index], null, index);
    }
    const meadow = this.habitat('meadow');
    for (const side of [-1, 1]) {
      this.branch(meadow, [[side * .95, -.82, 0], [side * 1.14, -.28, -.07], [side * 1.08, .27, 0]], .011);
      for (let i = 0; i < 4; i++) {
        this.leaf(meadow, [side * (1.01 + i * .035), -.65 + i * .22, .02], .32 - i * .035,
          [.1, side * .12, side * (.7 + (i % 2) * .48)], colors[i], 'meadow');
      }
    }
    const blossom = this.habitat('blossom');
    this.branch(blossom, [[-1.2, -.85, 0], [-1.05, -.05, .02], [-1.27, .73, 0]], .016);
    this.branch(blossom, [[-1.07, -.17, 0], [-1.43, .20, .03], [-1.49, .42, .04]], .009);
    this.branch(blossom, [[1.2, -.84, -.03], [1.36, -.18, 0], [1.15, .42, .03]], .012);
    this.flowers = [
      this.flower(blossom, [-1.27, .74, .04], .80, 0xf1daca, .3),
      this.flower(blossom, [-1.49, .44, .08], .54, 0xf4e8c9, 1.4),
      this.flower(blossom, [1.16, .44, .06], .70, 0xf7e5ce, 2.6),
    ];
    this.leaf(blossom, [-1.04, -.35, .025], .45, [.1, 0, -.95], 0x98b798, 'blossom');
    this.leaf(blossom, [1.31, -.34, .02], .36, [.1, 0, .7], 0xb7cba5, 'blossom');
    const canopy = this.habitat('canopy');
    for (const side of [-1, 1]) {
      this.branch(canopy, [[side * 1.48, -.80, -.05], [side * 1.35, .32, 0], [side * .80, 1.21, .02]], .021, 0x9bb294);
      this.branch(canopy, [[side * 1.35, .33, 0], [side * 1.63, .69, .025]], .011, 0x9bb294);
      for (let i = 0; i < 5; i++) {
        this.leaf(canopy, [side * (1.39 - i * .135), .33 + i * .18, .03], .44 - i * .027,
          [.18, side * .10, side * (.78 + i * .07)], colors[(i + 2) % colors.length], 'canopy');
      }
    }
    this.habitats.forEach(group => {
      group.userData.materials = new Set();
      group.traverse(object => { if (object.isMesh) group.userData.materials.add(object.material); });
    });
    this.dew = new THREE.Group();
    this.scene.add(this.dew);
    for (let index = 0; index < 4; index++) {
      const bead = this.mesh(new THREE.SphereGeometry(.022, 8, 6), this.plantMaterial(0xc7dcc0, .6), this.dew);
      bead.userData.phase = index / 4 * Math.PI * 2;
    }
  }

  paintEyes(now) {
    const driver = this.orb.motionDriver;
    if (!driver?._currentPolys) return;
    const polys = driver._currentPolys(clamp(driver.eyeMorph.x, 0, 1));
    const centroids = polys.map(poly => poly.reduce((sum, p) => [sum[0] + p[0] / poly.length,
      sum[1] + p[1] / poly.length], [0, 0]));
    const center = [(centroids[0][0] + centroids[1][0]) / 2, (centroids[0][1] + centroids[1][1]) / 2];
    polys.forEach((poly, index) => {
      const lid = window.GROK_EYES.winkLid ? window.GROK_EYES.winkLid(driver.blink.x, now, driver.winkAt,
        driver.winkEye, index) : clamp(driver.blink.x, .06, 1.15);
      const driverWink = driver.winkEye === index && now >= driver.winkAt && now < driver.winkAt + 320
        ? Math.max(.07, Math.abs((now - driver.winkAt) / 160 - 1)) : 1;
      const wink = driverWink * (index === 0 ? this.motionPose?.wink ?? 1 : 1);
      const scale = .0068 * clamp(driver.eyeScale.x, .7, 1.25);
      const points = poly.map(p => new THREE.Vector2((p[0] - center[0]) * scale,
        (center[1] - centroids[index][1]) * scale + (centroids[index][1] - p[1]) * scale * lid * wink));
      const triangles = THREE.ShapeUtils.triangulateShape(points, []), positions = [], normals = [];
      const project = p => {
        const x = clamp(p.x + this.gaze.x * .11, -.8, .8), y = clamp(p.y + .12 + this.gaze.y * .08, -.65, .65);
        const z = Math.sqrt(Math.max(.09, 1 - x * x - y * y));
        positions.push(x * 1.026, y * 1.026, z * 1.026);
        const length = Math.hypot(x, y, z);normals.push(x/length,y/length,z/length);
      };
      triangles.forEach(([a, b, c]) => {
        const p = points[a], q = points[b], r = points[c], pq = p.clone().add(q).multiplyScalar(.5);
        const qr = q.clone().add(r).multiplyScalar(.5), rp = r.clone().add(p).multiplyScalar(.5);
        [p, pq, rp, pq, q, qr, rp, qr, r, pq, qr, rp].forEach(project);
      });
      const geometry = this.eyes[index].geometry;
      let attribute = geometry.getAttribute('position');
      if (!attribute || attribute.count !== positions.length / 3) geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      else { attribute.array.set(positions); attribute.needsUpdate = true; }
      let normalAttribute = geometry.getAttribute('normal');
      if (!normalAttribute || normalAttribute.count !== normals.length / 3) geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
      else { normalAttribute.array.set(normals); normalAttribute.needsUpdate = true; }
      geometry.computeBoundingSphere();
    });
  }

  resize() {
    if (this.disposed) return;
    const box = this.root.getBoundingClientRect();
    if (!box.width || !box.height) return;
    this.renderer.setPixelRatio(this.pixelRatio());
    this.renderer.setSize(box.width, box.height, false);
    this.camera.aspect = box.width / box.height;
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }
  paused() { return this.orb.manualPaused || this.orb.reducedMotion || document.hidden || !this.visible; }
  invalidate() {
    if (this.disposed) return;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.last = 0;
    this.habitatLastAt = null;
    this.draw(performance.now());
    if (!this.paused()) this.frame = requestAnimationFrame(t => this.tick(t));
  }
  tick(now) {
    this.frame = 0;
    if (this.disposed || this.paused()) return;
    if (!this.last || now - this.last >= 1000 / 30) { this.draw(now); this.last = now; }
    this.frame = requestAnimationFrame(t => this.tick(t));
  }

  updateHabitat(now, reduced, wind) {
    if (!this.paused() && this.habitatLastAt !== null) this.habitatTime += clamp(now - this.habitatLastAt, 0, 120);
    this.habitatLastAt = this.paused() ? null : now;
    const habitat = window.AutoDevOrbMotion?.habitatAt(this.habitatTime, reduced)
      || {current: 'meadow', next: 'blossom', mix: 0};
    this.root.dataset.habitat = habitat.current;
    this.root.dataset.habitatNext = habitat.next;
    this.root.dataset.habitatMix = habitat.mix.toFixed(3);
    const t = reduced ? 0 : this.habitatTime / 1000;
    this.habitats.forEach((group, name) => {
      const opacity = name === habitat.current ? 1 - habitat.mix : name === habitat.next ? habitat.mix : 0;
      group.visible = opacity > .002;
      group.userData.materials.forEach(material => { material.opacity = opacity * .82; });
      group.rotation.z = reduced ? 0 : Math.sin(t * .46 + (name === 'canopy' ? 1.6 : .3)) * .014 * wind;
      group.position.y = reduced ? 0 : Math.sin(t * .39) * .016 * wind;
    });
    this.flowers.forEach(flower => {
      const phase = flower.userData.flowerPhase;
      flower.rotation.z = phase * .18 + (reduced ? 0 : Math.sin(t * .72 + phase) * .09 * wind);
      flower.rotation.y = -.14 + (reduced ? 0 : Math.sin(t * .43 + phase) * .10 * wind);
    });
  }

  draw(now) {
    if (this.disposed || !this.renderer) return;
    const driver = this.orb.motionDriver, reduced = this.orb.reducedMotion, t = reduced ? 0 : now / 1000;
    const pose = this.orb.sampleMotion(now) || {hop: 0, sway: 0, depth: 0, roll: 0, pitch: 0,
      yaw: 0, stretch: 1, gazeX: 0, gazeY: 0, wink: 1, leaf: 0};
    this.motionPose = pose;
    const gazeX = reduced ? 0 : clamp(this.orb.pointer.targetX + (driver?.gazeX?.x || 0) * .018 + pose.gazeX, -1.2, 1.2);
    const gazeY = reduced ? 0 : clamp(this.orb.pointer.targetY - (driver?.gazeY?.x || 0) * .018 + pose.gazeY, -1, 1);
    this.gaze.x += (gazeX - this.gaze.x) * .12;
    this.gaze.y += (gazeY - this.gaze.y) * .12;
    const ex = driver?.extras || {}, radius = this.radius;
    const squash = reduced ? 1 : clamp((driver?.squash?.x || 1) * pose.stretch, .86, 1.16);
    const bounce = reduced ? 0 : clamp(-((driver?.ty?.x || 0) + (ex.hop || 0) + (ex.ki || 0)) * .005 + pose.hop, -.025, .32);
    const sway = reduced ? 0 : clamp(((driver?.tx?.x || 0) + (ex.yi || 0)) * .004, -.10, .10)
      + pose.sway * (this.isSidebar ? .8 : 1) + Math.sin(t * .37 + this.phase) * .04;
    const depth = reduced ? 0 : (pose.depth || 0) + Math.sin(t * .29 + this.phase) * .035;
    this.ball.position.set(this.origin.x + sway, this.origin.y + bounce + (reduced ? 0 : Math.sin(t * 1.7) * .012), this.origin.z + depth);
    this.ball.scale.set(radius / Math.sqrt(squash), radius * squash, radius / Math.sqrt(squash));
    const roll = reduced ? 0 : clamp(((driver?.spin?.x || 0) + (ex.Kr || 0) + (ex.Yr || 0)) * Math.PI / 180, -.35, .35)
      + pose.roll + Math.sin(t * .8) * .025;
    this.ball.rotation.set(this.gaze.y * .13 + pose.pitch, this.gaze.x * .26 + (reduced ? 0 : (ex.turn || 0) + pose.yaw), roll);
    this.paintEyes(reduced ? driver?.t0 || now : now);
    this.contactShadow.position.x = this.ball.position.x;
    this.contactShadow.position.z = this.ball.position.z;
    const altitude = clamp(bounce, 0, .4);
    this.contactShadow.scale.setScalar(1 + altitude * .9);
    this.contactShadow.material.opacity = .58 - altitude * .8;
    const status = motionState(this.orb), quiet = ['blocked', 'error', 'sleeping'].includes(status), wind = quiet ? .35 : 1;
    this.updateHabitat(now, reduced, wind);
    this.leaves.forEach(({pivot, geometry, base, floatingIndex, restRotation, phase}) => {
      if (floatingIndex !== null) {
        const angle = floatingIndex / 8 * Math.PI * 2 + (reduced ? 0 : t * .09 * wind);
        const chase = (pose.leaf || 0) * (floatingIndex === 0 ? .15 : .03);
        const distance = 1.34 + Math.sin(phase) * .07 - chase;
        // Real depth, with leaves kept outside the enlarged orange body's
        // volume instead of flat overlays that cut through its silhouette.
        pivot.position.set(this.ball.position.x + Math.cos(angle) * distance,
          this.origin.y + .31 + Math.sin(angle) * .94 + (reduced ? 0 : Math.sin(t * .9 + phase) * .035),
          this.ball.position.z - .34 + Math.sin(angle) * .33);
        this.foliageOffset.copy(pivot.position).sub(this.ball.position);
        const clearance = radius * 1.16 + pivot.scale.x + .04;
        if (this.foliageOffset.length() < clearance) {
          pivot.position.copy(this.ball.position).add(this.foliageOffset.setLength(clearance));
        }
        pivot.rotation.set(.3 + Math.sin(angle) * .3, angle + .7,
          Math.cos(angle) * .7 + (reduced ? 0 : Math.sin(t * .8 + phase) * .20));
      } else {
        pivot.rotation.copy(restRotation);
        if (!reduced) pivot.rotation.z += Math.sin(t * .7 + phase) * .028 * wind;
      }
      const attribute = geometry.getAttribute('position');
      for (let offset = 0; offset < base.length; offset += 3) {
        const tip = base[offset + 1];
        attribute.array[offset + 2] = base[offset + 2] + (reduced ? 0 : Math.sin(t * 1.1 + phase + tip * 2.2) * tip * tip * .055 * wind);
      }
      attribute.needsUpdate = true;
      if (pivot.parent.visible) geometry.computeVertexNormals();
    });
    this.dew.children.forEach(bead => {
      const angle = bead.userData.phase + (reduced ? 0 : t * .12);
      bead.position.set(this.ball.position.x + Math.cos(angle) * 1.35, .20 + Math.sin(angle) * 1.08, -.46);
    });
    this.orbit.visible=this.root.classList.contains('is-running')&&!reduced;
    this.orbit.position.copy(this.ball.position);
    this.orbit.rotation.set(.12, .16, t * .45);
    this.sparkles.visible = ['success', 'celebrate'].includes(status) && !reduced;
    this.sparkles.children.forEach(sparkle => {
      const angle = sparkle.userData.phase + t * .75;
      sparkle.position.set(this.ball.position.x + Math.cos(angle) * 1.27, this.ball.position.y + .25 + Math.sin(angle * 2) * .74,
        this.ball.position.z + Math.sin(angle) * .82);
      sparkle.rotation.set(t, t * .7, angle);
    });
    this.renderer.render(this.scene, this.camera);
  }

  destroy() {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.resizeObserver?.disconnect();
    this.intersection?.disconnect();
    this.canvas?.removeEventListener('webglcontextlost', this.onLost);
    this.resources?.forEach(resource => resource.dispose());
    this.resources?.clear();
    this.scene?.traverse(object => { if (object.isLight) object.shadow?.dispose(); });
    this.renderer?.dispose();
    this.canvas?.remove();
    this.root?.classList.remove('is-scene');
    if (this.root) ['habitat', 'habitatNext', 'habitatMix'].forEach(key => delete this.root.dataset[key]);
  }
}
window.AutoDevOrbScene = AutoDevOrbScene;
window.AutoDevOrb?.instances.forEach(orb => orb.attachScene(AutoDevOrbScene));
