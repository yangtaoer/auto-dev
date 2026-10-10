/* The little garden is real geometry: folded leaves, shell petals and a woody arch.
 * Coordinates are shared with the character, including its squash and head tilt. */
import * as THREE from './vendor/three/three.module.js';

const TAU = Math.PI * 2;
const UP = new THREE.Vector3(0, 1, 0);
const clamp01 = value => Math.max(0, Math.min(1, Number(value) || 0));
const smooth = value => { const t = clamp01(value); return t * t * (3 - 2 * t); };
const vec = values => new THREE.Vector3(...values);
const tint = value => new THREE.Color(value);

/* Preserve smooth source normals while combining meshes. A whole planted border
 * needs one foliage draw, rather than a separate draw for every little vein. */
function combine(parts) {
  const positions = [], normals = [], colors = [], indices = [];
  const point = new THREE.Vector3(), normal = new THREE.Vector3();
  const normalMatrix = new THREE.Matrix3(), shade = new THREE.Color();
  let offset = 0;
  for (const part of parts) {
    const geometry = part.geometry || part;
    const matrix = part.matrix || new THREE.Matrix4();
    const position = geometry.getAttribute('position');
    const sourceNormals = geometry.getAttribute('normal');
    const sourceColors = geometry.getAttribute('color');
    normalMatrix.getNormalMatrix(matrix);
    for (let i = 0; i < position.count; i++) {
      point.fromBufferAttribute(position, i).applyMatrix4(matrix);
      normal.fromBufferAttribute(sourceNormals, i).applyMatrix3(normalMatrix).normalize();
      positions.push(point.x, point.y, point.z);
      normals.push(normal.x, normal.y, normal.z);
      if (sourceColors) shade.fromBufferAttribute(sourceColors, i); else shade.set(0xffffff);
      if (part.color) shade.multiply(part.color);
      colors.push(shade.r, shade.g, shade.b);
    }
    if (geometry.index) {
      for (let i = 0; i < geometry.index.count; i++) indices.push(offset + geometry.index.getX(i));
    } else {
      for (let i = 0; i < position.count; i++) indices.push(offset + i);
    }
    offset += position.count;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  geometry.computeBoundingSphere();
  return geometry;
}

function transform(position, scale = 1, rotation = [0, 0, 0]) {
  const quaternion = new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation));
  return new THREE.Matrix4().compose(vec(position), quaternion,
    Array.isArray(scale) ? vec(scale) : new THREE.Vector3(scale, scale, scale));
}

function aimed(position, direction, scale = 1, roll = 0) {
  const quaternion = new THREE.Quaternion().setFromUnitVectors(UP, vec(direction).normalize());
  quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(UP, roll));
  return new THREE.Matrix4().compose(vec(position), quaternion,
    Array.isArray(scale) ? vec(scale) : new THREE.Vector3(scale, scale, scale));
}

/* A closed, tapered shell. Top/back colours, curled edges and a real thin rim
 * remain visible when a leaf turns over; there is no billboard transparency. */
function shell({petal = false, blush = false, ginkgo = false, palette} = {}) {
  const rows = petal ? 14 : 18, columns = 8;
  const positions = [], colors = [], indices = [];
  const top = tint(palette || (petal ? (blush ? 0xf7d8bc : 0xf6efda) : 0x668f69));
  const tip = palette ? top.clone().lerp(tint(0xfff7c9), .22) : tint(petal ? 0xfff8e9 : 0x91ad82);
  const back = palette ? top.clone().lerp(tint(0xc6c4a3), .23) : tint(petal ? (blush ? 0xe5c2a1 : 0xe5dfc1) : 0x91ab86);
  const base = palette ? top.clone().multiplyScalar(.78) : tint(petal ? 0xdcca94 : 0x507953);
  const colour = new THREE.Color();
  for (let side = 0; side < 2; side++) {
    for (let row = 0; row <= rows; row++) {
      const t = row / rows;
      for (let column = 0; column <= columns; column++) {
        const across = column / columns * 2 - 1;
        const breadth = Math.pow(Math.sin(Math.PI * t), petal ? .53 : .77);
        const width = ginkgo ? .56 * Math.sin(t * Math.PI / 2) + .0015 : (petal ? .44 : .31) * breadth + .0015;
        const x = across * width;
        const midrib = Math.sin(Math.PI * t) * (petal ? .12 : .145);
        const cup = Math.sin(Math.PI * t) * (petal ? across * across * .22 :
          -.095 * across * across + .12 * Math.pow(Math.abs(across), 4));
        const curl = petal ? Math.pow(t, 3) * .19 : -.095 * Math.pow(t, 3);
        const twist = petal ? across * t * .028 : across * t * .045;
        const thickness = (petal ? .025 : .017) * (.4 + .6 * Math.sin(Math.PI * t));
        const height = ginkgo ? t * (.82 + .18 * Math.sqrt(1 - across * across)) - .11 * Math.exp(-across * across * 85) * Math.pow(t, 8) : t;
        positions.push(x, height, midrib + cup + curl + twist + (side ? -thickness : thickness));
        colour.copy(side ? back : top).lerp(tip, t * .34);
        colour.lerp(base, Math.pow(1 - t, 3) * .38);
        // Subtle lamina variations catch light without a noisy painted texture.
        const satin = 1 + Math.sin(t * 23 + across * 5) * .017 + Math.cos(across * 9) * .009;
        colour.multiplyScalar(satin);
        colors.push(colour.r, colour.g, colour.b);
      }
    }
  }
  const stride = columns + 1, faceCount = (rows + 1) * stride;
  for (let row = 0; row < rows; row++) for (let column = 0; column < columns; column++) {
    const a = row * stride + column, b = a + stride;
    indices.push(a, a + 1, b, a + 1, b + 1, b);
    indices.push(a + faceCount, b + faceCount, a + 1 + faceCount,
      a + 1 + faceCount, b + faceCount, b + 1 + faceCount);
  }
  const edge = [];
  for (let c = 0; c <= columns; c++) edge.push(c);
  for (let r = 1; r <= rows; r++) edge.push(r * stride + columns);
  for (let c = columns - 1; c >= 0; c--) edge.push(rows * stride + c);
  for (let r = rows - 1; r > 0; r--) edge.push(r * stride);
  for (let i = 0; i < edge.length; i++) {
    const a = edge[i], b = edge[(i + 1) % edge.length];
    indices.push(a, a + faceCount, b, b, a + faceCount, b + faceCount);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

function tube(points, radius, segments = 20, sides = 6, taper = .7) {
  const curve = new THREE.CatmullRomCurve3(points.map(p => Array.isArray(p) ? vec(p) : p));
  const geometry = new THREE.TubeGeometry(curve, segments, radius, sides, false);
  const position = geometry.getAttribute('position'), center = new THREE.Vector3();
  for (let ring = 0; ring <= segments; ring++) {
    const t = ring / segments, width = 1 - taper * t;
    curve.getPointAt(t, center);
    for (let side = 0; side <= sides; side++) {
      const i = ring * (sides + 1) + side;
      position.setXYZ(i, center.x + (position.getX(i) - center.x) * width,
        center.y + (position.getY(i) - center.y) * width,
        center.z + (position.getZ(i) - center.z) * width);
    }
  }
  geometry.computeVertexNormals();
  return {geometry, curve};
}

function leafGeometry(options = {}) {
  const body = shell(options), parts = [{geometry: body}];
  if(options.ginkgo){
    for(let i=-3;i<=3;i++){
      const x=i/3*.50,y=.86+(1-Math.abs(i)/3)*.06;
      const vein=tube([[0,.06,.045],[x*.45,.47,.18],[x,y,.09]],.005,12,4,.65).geometry;
      parts.push({geometry:vein,color:tint(0xf9dc88)});
    }
    const geometry=combine(parts);parts.forEach(part=>part.geometry.dispose());return geometry;
  }
  const veinColour = tint(0xbbcd9f);
  const mid = tube([[0, 0, .022], [0, .24, .118], [0, .53, .149], [0, .78, .069], [0, 1, -.073]], .009, 20, 5, .75).geometry;
  parts.push({geometry: mid, color: veinColour});
  for (let i = 0; i < 5; i++) {
    const start = .16 + i * .127, end = start + .18;
    for (const direction of [-1, 1]) {
      const x = direction * .31 * Math.pow(Math.sin(Math.PI * end), .77) * .82;
      const z = (t, w) => Math.sin(Math.PI * t) * (.145 - w * w * .095 + .12 * Math.pow(Math.abs(w), 4)) - .095 * Math.pow(t, 3) + w * t * .045 + .023;
      const vein = tube([[0, start, z(start, 0)], [x * .58, start + .09, z(start + .09, direction * .45)],
        [x, end, z(end, direction * .82)]], .0042, 6, 4, .8).geometry;
      parts.push({geometry: vein, color: veinColour.clone().multiplyScalar(.97)});
    }
  }
  const geometry = combine(parts);
  parts.forEach(part => part.geometry.dispose());
  return geometry;
}

function flowerGeometry(petal, fold) {
  const parts = [];
  for (let i = 0; i < 5; i++) {
    const angle = i / 5 * TAU;
    const rotation = new THREE.Matrix4().makeRotationZ(angle);
    const hinge = new THREE.Matrix4().makeRotationX(fold);
    const scale = new THREE.Matrix4().makeScale(.33, .41, .39);
    const base = new THREE.Matrix4().makeTranslation(0, .026, .018);
    parts.push({geometry: petal, matrix: rotation.multiply(base).multiply(hinge).multiply(scale),
      color: tint(0xffffff)});
  }
  return combine(parts);
}

function flowerShell(petal) {
  const open = flowerGeometry(petal, .11), bud = flowerGeometry(petal, 1.07);
  open.morphAttributes.position = [bud.getAttribute('position').clone()];
  open.morphAttributes.normal = [bud.getAttribute('normal').clone()];
  bud.dispose();
  return open;
}

function bellFlower(palette) {
  // A closed ceramic-soft bell: five subtly scalloped lobes, a thin opaque rim,
  // and an inner cup. Unlike a textured card it remains convincing from below.
  const points=[[.012,-.08],[.085,-.01],[.13,.08],[.23,.19],[.29,.31],[.285,.35],
    [.263,.345],[.26,.31],[.20,.19],[.105,.08],[.065,0],[.012,-.06]].map(([x,y])=>new THREE.Vector2(x,y));
  const geometry=new THREE.LatheGeometry(points,48),position=geometry.getAttribute('position'),colours=[];
  const colour=tint(palette||0xfff3dc);
  for(let i=0;i<position.count;i++){
    const x=position.getX(i),y=position.getY(i),z=position.getZ(i),angle=Math.atan2(x,z);
    const lobe=Math.max(0,(y-.20)/.15),flare=1+Math.cos(angle*5)*.035*lobe;
    position.setXYZ(i,x*flare,y+Math.cos(angle*5)*.018*lobe,z*flare);
    const shade=colour.clone().multiplyScalar(.94+Math.max(0,y)*.17);colours.push(shade.r,shade.g,shade.b);
  }
  geometry.rotateX(Math.PI/2);geometry.computeVertexNormals();
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(colours,3));
  const bud=geometry.clone(),budPosition=bud.getAttribute('position');
  for(let i=0;i<budPosition.count;i++){
    const amount=Math.max(0,(budPosition.getZ(i)-.1)/.25);
    budPosition.setXY(i,budPosition.getX(i)*(1-amount*.32),budPosition.getY(i)*(1-amount*.32));
  }
  bud.computeVertexNormals();geometry.morphAttributes.position=[budPosition.clone()];
  geometry.morphAttributes.normal=[bud.getAttribute('normal').clone()];bud.dispose();return geometry;
}

export class AutoDevGarden {
  constructor(host) {
    this.scene = host.scene;
    this.ball = host.ball;
    this.radius = host.radius;
    this.groundY = host.groundY;
    this.resources = host.resources;
    this.makeMesh = host.mesh;
    this.theme = host.theme;
    this.group = new THREE.Group();
    this.group.name = 'miniature-garden';
    this.scene.add(this.group);
    this.leaves = [];
    this.flowers = [];
    this.crownLeaves = [];
    this.branchCurves = [];
    this.worldPosition = new THREE.Vector3();
    this.worldScale = new THREE.Vector3();
    this.worldQuaternion = new THREE.Quaternion();
    this.hatPosition = new THREE.Vector3();
    this.hatQuaternion = new THREE.Quaternion();
    this.hatScale = new THREE.Vector3();
    this.hatWorldMatrix = new THREE.Matrix4();
    this.restPosition = new THREE.Vector3();
    this.restQuaternion = new THREE.Quaternion();
    this.dummy = new THREE.Object3D();
    this.lastTime = 0;
    this.bounds = {min: [-1.9, this.groundY - .025, -1.18], max: [1.9, this.groundY + 2.35, .66]};
    this.notes = 'Shared closed leaf shells with geometric veins; planted borders are batched. All moving head ornaments use the ball world transform.';
    this.materials = {
      leaf: new THREE.MeshPhysicalMaterial({color: 0xffffff, vertexColors: true, roughness: .64,
        metalness: 0, sheen: .28, sheenColor: 0xd0d9b3, sheenRoughness: .83,
        clearcoat: .035, clearcoatRoughness: .76, specularIntensity: .23}),
      petal: new THREE.MeshPhysicalMaterial({color: 0xffffff, vertexColors: true, roughness: .7,
        metalness: 0, sheen: .36, sheenColor: 0xfff4dc, sheenRoughness: .86,
        specularIntensity: .21, clearcoat: 0}),
      wood: new THREE.MeshStandardMaterial({color: 0xa99572, vertexColors: true, roughness: .86}),
      pollen: new THREE.MeshStandardMaterial({color: 0xf1cc68, roughness: .72, emissive: 0x8f631c, emissiveIntensity: .035}),
      stone: new THREE.MeshStandardMaterial({color: 0xffffff, vertexColors: true, roughness: .95}),
      light: new THREE.MeshBasicMaterial({color: 0xffe8a3, transparent: true, opacity: .98, depthWrite: false, toneMapped: false}),
      halo: new THREE.MeshBasicMaterial({color: 0xffe8a8, transparent: true, opacity: .24, depthWrite: false, toneMapped: false}),
    };
    Object.values(this.materials).forEach(material => this.resources.add(material));
    const autumn = host.theme?.scene === 'autumn', palette = host.theme?.scenePalette;
    this.leafShape = leafGeometry({ginkgo:autumn,palette:palette?.leaf});
    this.petalShape = shell({petal: true,palette:palette?.petal});
    this.blushShape = shell({petal: true, blush: true,palette:autumn?palette?.petal:undefined});
    this.flowerShape = flowerShell(this.petalShape);
    this.blushFlowerShape = flowerShell(this.blushShape);
    if(host.theme?.scene==='garden') {this.bellShape=bellFlower(palette?.petal);this.resources.add(this.bellShape);}
    [this.leafShape, this.petalShape, this.blushShape, this.flowerShape, this.blushFlowerShape].forEach(geometry => this.resources.add(geometry));
    this.buildPollen();
    this.buildBorders();
    this.buildHeroLeaf();
    this.buildCrown();
    this.buildOrbit();
    this.buildPetals();
    this.update({time: 0, wind: .3, peek: 0, hat: 0, work: 0, crown: 0, bloom: 0, petals: 0, reduced: true});
  }

  mesh(geometry, material, parent = this.group) {
    // Raw tubes have no colour attribute. Supplying white avoids an undefined
    // attribute turning just the independently moving woody arch black.
    if (material.vertexColors && !geometry.getAttribute('color')) {
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(
        new Float32Array(geometry.getAttribute('position').count * 3).fill(1), 3));
    }
    const mesh = this.makeMesh(geometry, material, parent);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  buildPollen() {
    const sphere = new THREE.SphereGeometry(.023, 7, 5), parts = [];
    for (let i = 0; i < 11; i++) {
      const angle = i * 2.39996, r = Math.sqrt((i + .5) / 11) * .073;
      parts.push({geometry: sphere, matrix: transform([Math.cos(angle) * r, Math.sin(angle) * r, .096 + Math.cos(r * 11) * .013], [.76, .76, 1.15])});
      const filament = tube([[Math.cos(angle) * r * .7, Math.sin(angle) * r * .7, .045],
        [Math.cos(angle) * r, Math.sin(angle) * r, .102]], .004, 2, 3, .2).geometry;
      parts.push({geometry: filament});
    }
    this.pollenShape = combine(parts);
    this.resources.add(this.pollenShape);
    parts.forEach(part => { if (part.geometry !== sphere) part.geometry.dispose(); });
    sphere.dispose();
  }

  flower(parent, position, scale, {peach = false, rotation = [0, 0, 0], crown = false, phase = 0, open = .82} = {}) {
    const pivot = new THREE.Group();
    pivot.position.fromArray(position);
    pivot.rotation.set(...rotation);
    pivot.scale.setScalar(scale);
    pivot.name = crown ? 'crown-blossom' : 'garden-blossom';
    parent.add(pivot);
    const petals = this.mesh(!crown&&this.bellShape?this.bellShape:peach ? this.blushFlowerShape : this.flowerShape, this.materials.petal, pivot);
    const pollen = this.mesh(this.pollenShape, this.materials.pollen, pivot);
    if(!crown&&this.bellShape){pollen.position.z=.19;pollen.scale.setScalar(.65);}
    if (crown) {
      petals.scale.z = 1.4;
      pollen.position.z = .034;
    }
    const flower = {pivot, petals, pollen, scale, crown, phase, open, baseRotation: pivot.rotation.clone()};
    this.flowers.push(flower);
    return flower;
  }

  buildBorders() {
    const foliage = [], wood = [], pebbles = [], temporary = [];
    const addLeaf = (position, direction, size, shade = 0xffffff, roll = 0) => {
      const matrix = aimed(position, direction, size, roll);
      foliage.push({geometry: this.leafShape, matrix, color: tint(shade)});
      this.leaves.push({planted: true, position: vec(position), matrix});
    };
    const addBranch = (points, width, signal = false, taper = .8) => {
      const branch = tube(points, width, 23, 8, taper);
      wood.push({geometry: branch.geometry});
      temporary.push(branch.geometry);
      if (signal) this.branchCurves.push(branch.curve);
      return branch;
    };
    const g = this.groundY;
    // The right arch, with alternate rather than mirrored leaves, frames the face.
    const right = addBranch([[1.45, g + .03, -.83], [1.53, g + .5, -.85], [1.41, g + 1.04, -.85],
      [1.22, g + 1.57, -.82], [.96, g + 1.94, -.78]], .055, true, .62).curve;
    [0.22, .36, .51, .67, .8].forEach((t, i) => {
      const p = right.getPoint(t);
      addLeaf(p.toArray(), [i % 2 ? -.68 : .62, .56, .3], .51 - i * .022, i % 2 ? 0xe7edd8 : 0xffffff, i % 2 ? -.42 : .5);
    });
    addLeaf(right.getPoint(.98).toArray(), [-.22, .79, .30], .37, 0xf3f1d8, -.32);
    // A smaller rear shoot adds depth through occlusion, not blur or transparency.
    for (const side of [-1, 1]) {
      addBranch([[side * 1.55, g + .03, -1.02], [side * 1.66, g + .49, -1.09],
        [side * 1.62, g + 1.07, -1.04]], .023);
      for (let i = 0; i < 4; i++) {
        addLeaf([side * (1.56 + i * .017), g + .22 + i * .2, -1.035],
          [side * (i % 2 ? -.53 : .4), .78, .07], .36, 0xe0e8cf, side * .23);
      }
    }
    // Broad low leaves, medium leaves, and fresh tips make three readable layers.
    for (const side of [-1, 1]) {
      for (let i = 0; i < 9; i++) {
        const angle = i * 2.39996;
        const outer = i < 5;
        const x = side * (1.31 + Math.sin(angle) * .16);
        const z = -.24 + Math.cos(angle) * .22 - (outer ? 0 : .27);
        const direction = [side * (Math.sin(angle) * .7 - .1), outer ? .42 : .8, .31 + Math.cos(angle) * .45];
        addLeaf([x, g + .035 + i % 3 * .019, z], direction,
          outer ? .43 + i % 3 * .045 : .46 + i % 2 * .1,
          i % 3 === 0 ? 0xf1efd5 : i % 3 === 1 ? 0xdbe4ca : 0xffffff, Math.sin(angle) * .25);
      }
    }
    const blooms = [
      {p: [-1.14, g + .35, .18], s: .84, r: [-.46, -.21, -.18], peach: false, open: .91},
      {p: [-1.28, g + .91, -.57], s: .80, r: [-.72, .62, -.42], peach: false, open: .50},
      {p: [-1.53, g + .57, -.38], s: .63, r: [-.56, -.57, .4], peach: true, open: .58},
      {p: [1.25, g + .52, -.04], s: .76, r: [-.44, -.36, .29], peach: false, open: .78},
      {p: [1.24, g + 1.22, -.70], s: .89, r: [-.84, -.58, -.45], peach: true, open: .45},
      {p: [1.56, g + .84, -.57], s: .60, r: [-.65, .55, .15], peach: false, open: .64},
    ];
    blooms.forEach((spec, i) => {
      const side = Math.sign(spec.p[0]), base = [side * 1.42, g + .05, spec.p[2] - .12];
      addBranch([base, [side * 1.38, (g + spec.p[1]) / 2, spec.p[2] - .05], spec.p], .014);
      addLeaf([side * 1.38, spec.p[1] - .2, spec.p[2] - .025], [side * (i % 2 ? .7 : -.6), .6, .1], .26, 0xf0f3dc);
      this.flower(this.group, spec.p, spec.s, {peach: spec.peach, rotation: spec.r, phase: i * 1.93, open: spec.open});
    });
    for (const side of [-1, 1]) for (let i = 0; i < 4; i++) {
      const geometry = new THREE.IcosahedronGeometry(1, 2);
      const position = geometry.getAttribute('position');
      for (let j = 0; j < position.count; j++) {
        const irregularity = 1 + Math.sin(position.getX(j) * 5 + position.getY(j) * 7 + i) * .035;
        position.setXYZ(j, position.getX(j) * irregularity, position.getY(j) * irregularity, position.getZ(j) * irregularity);
      }
      geometry.computeVertexNormals();
      temporary.push(geometry);
      pebbles.push({geometry, matrix: transform([side * (1.1 + i * .17), g + .037 + i % 2 * .017, .21 - i % 2 * .22],
        [.12 + i % 2 * .045, .055 + i % 2 * .016, .09], [0, side * i * .6, -.1 + i * .04]),
      color: tint(i % 2 ? 0xb1b49a : 0xc7c5a7)});
    }
    this.borderFoliage = this.mesh(combine(foliage), this.materials.leaf);
    this.borderFoliage.name = 'layered-sage-leaf-border';
    this.mesh(combine(wood), this.materials.wood).name = 'woody-border-stems';
    this.mesh(combine(pebbles), this.materials.stone).name = 'rounded-garden-pebbles';
    temporary.forEach(geometry => geometry.dispose());
    this.buildPeekBranch();
  }

  buildPeekBranch() {
    const g = this.groundY;
    this.peekBranch = new THREE.Group();
    this.peekBranch.position.set(-1.44, g + .53 + Math.max(0, this.radius - .8) * 2.5, -.76);
    this.group.add(this.peekBranch);
    const branch = tube([[0, -.47, 0], [.01, .13, 0], [.21, .64, .015], [.61, 1.11, .04], [1.11, 1.38, .065]], .052, 26, 8, .65);
    this.mesh(branch.geometry, this.materials.wood, this.peekBranch);
    this.peekCurve = branch.curve;
    const parts = [];
    [[.20, [-.64, .78, .25], .49], [.36, [.72, .54, .32], .54], [.52, [-.60, .76, .18], .55],
      [.70, [.58, .56, .35], .47], [.84, [-.15, .82, .1], .39]].forEach(([t, direction, size], i) => {
      const p = branch.curve.getPoint(t);
      const matrix = aimed(p.toArray(), direction, size, (i % 2 ? -1 : 1) * .2);
      parts.push({geometry: this.leafShape, matrix, color: tint(i % 2 ? 0xf0f2dc : 0xffffff)});
      this.leaves.push({planted: true, branch: this.peekBranch, matrix});
    });
    this.mesh(combine(parts), this.materials.leaf, this.peekBranch);
    this.heroAnchor = new THREE.Group();
    this.heroAnchor.position.copy(branch.curve.getPoint(1));
    this.heroAnchor.quaternion.setFromUnitVectors(UP, vec([.75, -.48, .30]).normalize());
    this.peekBranch.add(this.heroAnchor);
  }

  buildHeroLeaf() {
    this.heroLeaf = new THREE.Group();
    this.heroLeaf.name = 'interactive-leaf-hat';
    this.group.add(this.heroLeaf);
    const slope = .66, lift = Math.sqrt(1 - slope * slope);
    this.hatLocalQuaternion = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(
      new THREE.Vector3(0, -slope, lift), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, lift, slope)));
    this.hatLocalMatrix = new THREE.Matrix4().compose(new THREE.Vector3(-.48, 1.07, .12),
      this.hatLocalQuaternion, new THREE.Vector3(.95, .95, .95));
    const hatInverse = this.hatLocalMatrix.clone().invert(), hatPoint = new THREE.Vector3();
    const geometry = this.leafShape.clone();
    const sourcePosition = geometry.getAttribute('position');
    for (let i = 0; i < sourcePosition.count; i++) sourcePosition.setX(i, sourcePosition.getX(i) * 1.32);
    const heroColours = geometry.getAttribute('color');
    for (let i = 0; i < heroColours.count; i++) {
      heroColours.setXYZ(i, heroColours.getX(i) * .87, heroColours.getY(i), heroColours.getZ(i) * .94);
    }
    geometry.computeVertexNormals();
    const draped = geometry.clone(), position = draped.getAttribute('position');
    for (let i = 0; i < position.count; i++) {
      const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
      // The front edge rests on the upper forehead while the rear edge curls up.
      // A fully spherical drape is edge-on from this UI camera; a lifted real
      // leaf exposes its veined top, exactly like the illustrated leaf visor.
      hatPoint.set(-.53 + y * 1.06, .885 - x * .62 + Math.sin(Math.PI * y) * .035 + Math.pow(y, 5) * .09,
        .31 + x * .65 + Math.sin(Math.PI * y) * .025);
      // Preserve the leaf's real central ridge, embossed veins and turned rim
      // along its visible normal instead of flattening all relief vertically.
      hatPoint.y += z * .792;
      hatPoint.z += z * .759;
      if (hatPoint.length() < 1.028) hatPoint.setLength(1.028);
      hatPoint.applyMatrix4(hatInverse);
      position.setXYZ(i, hatPoint.x, hatPoint.y, hatPoint.z);
    }
    draped.computeVertexNormals();
    geometry.morphAttributes.position = [position.clone()];
    geometry.morphAttributes.normal = [draped.getAttribute('normal').clone()];
    draped.dispose();
    this.heroLeafMesh = this.mesh(geometry, this.materials.leaf, this.heroLeaf);
    this.leaves.push({pivot: this.heroLeaf, interactive: true});
  }

  buildCrown() {
    this.crown = new THREE.Group();
    this.crown.name = 'flower-crown-attached-to-head';
    this.group.add(this.crown);
    const ringPoints = [];
    for (let i = 0; i <= 32; i++) {
      const angle = i / 32 * TAU;
      ringPoints.push([Math.sin(angle) * .49, .91 + Math.cos(angle) * .014, Math.cos(angle) * .49]);
    }
    const ring = tube(ringPoints, .011, 40, 5, 0).geometry;
    this.crownBand = this.mesh(ring, this.materials.wood, this.crown);
    for (let i = 0; i < 12; i++) {
      const angle = i / 12 * TAU;
      const pivot = new THREE.Group();
      pivot.position.set(Math.sin(angle) * .48, .944, Math.cos(angle) * .48);
      pivot.quaternion.setFromUnitVectors(UP, vec([Math.cos(angle), .46, -Math.sin(angle)]).normalize());
      pivot.rotateY(-.55);
      this.crown.add(pivot);
      this.mesh(this.leafShape, this.materials.leaf, pivot);
      this.crownLeaves.push({pivot, size: i % 2 ? .29 : .34});
    }
    [-1.05, .02, 1.04].forEach((angle, i) => {
      this.flower(this.crown, [Math.sin(angle) * .69, 1.04 + (i === 1 ? .075 : 0), Math.cos(angle) * .48],
        i === 1 ? .80 : .73, {peach: i === 2, crown: true, rotation: [-.45, angle * .3, -angle * .25], phase: i * 1.7});
    });
    this.flower(this.crown, [.51, 1.015, -.14], .40, {crown: true, rotation: [-.56, .45, .32], phase: 2.5});
  }

  buildOrbit() {
    this.orbit = new THREE.Group();
    this.orbit.name = 'working-only-leaf-collaboration';
    this.group.add(this.orbit);
    this.orbitLeaves = [];
    for (let i = 0; i < 3; i++) {
      const pivot = new THREE.Group();
      this.orbit.add(pivot);
      const leaf = this.mesh(this.leafShape, this.materials.leaf, pivot);
      leaf.position.y = -.47;
      pivot.scale.setScalar(.61);
      this.orbitLeaves.push(pivot);
    }
    this.orbitPath = new THREE.CatmullRomCurve3(Array.from({length: 48}, (_, i) => {
      const a = i / 48 * TAU;
      return new THREE.Vector3(Math.cos(a) * 1.01, 1.14 + Math.sin(a) * .28 + Math.cos(a) * .11, Math.sin(a) * .50 - .19);
    }), true);
    this.mesh(new THREE.TubeGeometry(this.orbitPath, 96, .009, 6, true), this.materials.light, this.orbit).castShadow = false;
    this.mesh(new THREE.TubeGeometry(this.orbitPath, 96, .029, 6, true), this.materials.halo, this.orbit).castShadow = false;
    this.glowGeometry = new THREE.SphereGeometry(.021, 8, 6);
    this.resources.add(this.glowGeometry);
    this.orbitLights = new THREE.InstancedMesh(this.glowGeometry, this.materials.light, 8);
    this.orbitLights.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.orbit.add(this.orbitLights);
    this.signals = new THREE.Group();
    this.signals.name = 'working-only-branch-light';
    this.group.add(this.signals);
    this.branchCurves.forEach(curve => {
      const rail = this.mesh(new THREE.TubeGeometry(curve, 40, .005, 4, false), this.materials.light, this.signals);
      rail.castShadow = false;
    });
    this.peekSignal = this.mesh(new THREE.TubeGeometry(this.peekCurve, 40, .005, 4, false), this.materials.light, this.peekBranch);
    this.peekSignal.castShadow = false;
    this.signalLights = new THREE.InstancedMesh(this.glowGeometry, this.materials.light, 8);
    this.signalLights.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.signals.add(this.signalLights);
    this.buildLightGlows();
  }

  buildLightGlows() {
    this.orbitGlows = new Map();
    this.branchGlows = new Map();
    if (typeof document === 'undefined') return;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 96;
    const context = canvas.getContext('2d');
    if (!context) return;
    const glow = context.createRadialGradient(48, 48, 0, 48, 48, 47);
    glow.addColorStop(0, 'rgba(255,255,235,1)');
    glow.addColorStop(.13, 'rgba(255,243,187,.95)');
    glow.addColorStop(.32, 'rgba(255,224,132,.48)');
    glow.addColorStop(.64, 'rgba(255,215,115,.12)');
    glow.addColorStop(1, 'rgba(255,215,115,0)');
    context.fillStyle = glow;
    context.fillRect(0, 0, 96, 96);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    this.resources.add(texture);
    this.glowMaterial = new THREE.SpriteMaterial({map: texture, transparent: true,
      color: 0xffecc0, opacity: .9, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending});
    this.resources.add(this.glowMaterial);
    for (const index of [0, 3, 6]) {
      const sprite = new THREE.Sprite(this.glowMaterial);
      sprite.scale.setScalar(.20);
      this.orbit.add(sprite);
      this.orbitGlows.set(index, sprite);
    }
    for (const index of [0, 2, 4, 6]) {
      const sprite = new THREE.Sprite(this.glowMaterial);
      sprite.scale.setScalar(.17);
      this.signals.add(sprite);
      this.branchGlows.set(index, sprite);
    }
  }

  buildPetals() {
    this.petals = new THREE.Group();
    this.petals.name = 'celebration-falling-petals';
    this.group.add(this.petals);
    for (let i = 0; i < 3; i++) {
      const petal = this.mesh(i === 1 ? this.blushShape : this.petalShape, this.materials.petal, this.petals);
      petal.scale.set(.19, .26, .23);
      petal.userData.index = i;
    }
  }

  update(frame = {}) {
    if (this.disposed) return;
    const reduced = Boolean(frame.reduced);
    const time = reduced ? this.lastTime : Math.max(0, Number(frame.time) || 0);
    this.lastTime = time;
    const wind = reduced ? 0 : clamp01(frame.wind);
    // Choreography has already eased these weights. Easing a second time made
    // a leaf linger at its endpoints and then lurch through the middle.
    const peek = clamp01(frame.peek), hat = clamp01(frame.hat), work = clamp01(frame.work);
    const crown = smooth(frame.crown), bloom = smooth(frame.bloom), petals = smooth(frame.petals);
    this.ball.updateWorldMatrix(true, false);
    this.ball.matrixWorld.decompose(this.worldPosition, this.worldQuaternion, this.worldScale);
    this.group.updateWorldMatrix(true, false);
    this.peekBranch.rotation.z = -.075 * peek + Math.sin(time * 1.15) * .065 * wind;
    this.peekBranch.rotation.y = Math.sin(time * .8) * .045 * wind;
    this.peekBranch.updateWorldMatrix(true, true);
    this.heroAnchor.getWorldPosition(this.restPosition);
    this.heroAnchor.getWorldQuaternion(this.restQuaternion);
    // Compose the local tangent basis before decomposing the world matrix, so
    // squash/stretch is applied along the correct leaf axes as well as the ball.
    this.hatWorldMatrix.multiplyMatrices(this.ball.matrixWorld, this.hatLocalMatrix);
    this.hatWorldMatrix.decompose(this.hatPosition, this.hatQuaternion, this.hatScale);
    this.heroLeaf.position.copy(this.restPosition).lerp(this.hatPosition, hat);
    this.heroLeaf.position.y += Math.sin(hat * Math.PI) * .46;
    this.heroLeaf.quaternion.copy(this.restQuaternion).slerp(this.hatQuaternion, hat);
    this.heroLeaf.scale.setScalar(.73).lerp(this.hatScale, hat);
    this.heroLeafMesh.morphTargetInfluences[0] = hat;
    this.heroLeaf.userData.headContact = hat > .995;
    this.heroLeaf.userData.hatWeight = hat;
    this.heroLeaf.rotateY(Math.sin(time * 1.3) * .028 * wind * (1 - hat));
    this.heroLeaf.updateMatrix();
    if (hat > .75) {
      // Nonuniform squash combined with a tilted leaf contains shear. Preserve
      // the complete contact matrix as it lands, rather than losing that shear
      // through a position/quaternion/scale decomposition and cutting the rim
      // through the orange surface during a rebound.
      const contact = smooth((hat - .75) / .25);
      const current = this.heroLeaf.matrix.elements, target = this.hatWorldMatrix.elements;
      for (let i = 0; i < 16; i++) current[i] += (target[i] - current[i]) * contact;
      this.heroLeaf.matrixAutoUpdate = false;
      this.heroLeaf.matrixWorldNeedsUpdate = true;
    } else this.heroLeaf.matrixAutoUpdate = true;
    this.crown.position.copy(this.worldPosition);
    this.crown.quaternion.copy(this.worldQuaternion);
    this.crown.scale.copy(this.worldScale);
    this.crown.visible = crown > .001;
    this.crownBand.visible = crown > .12;
    this.crownLeaves.forEach(({pivot, size}, i) => {
      const growth = smooth((crown - i % 3 * .08) / .84);
      pivot.scale.setScalar(size * (.025 + growth * .975));
    });
    this.flowers.forEach((flower, i) => {
      const opening = flower.crown ? crown * .9 : flower.open + bloom * (1 - flower.open) * .86;
      flower.petals.morphTargetInfluences[0] = 1 - opening;
      flower.pivot.scale.setScalar(flower.scale * (flower.crown ? .06 + crown * .94 : 1));
      flower.pivot.rotation.z = flower.baseRotation.z + Math.sin(time * 1.1 + flower.phase) * .09 * wind;
      flower.pivot.rotation.x = flower.baseRotation.x + Math.sin(time * .92 + i) * .075 * wind;
    });
    // The whole working assembly, including branch lights, is actually absent at
    // rest. No permanently rotating halo leaks into login or paused states.
    const working = work > .001;
    this.orbit.visible = working;
    this.signals.visible = working;
    this.peekSignal.visible = working;
    if (working) {
      this.materials.light.opacity = work * .98;
      this.materials.halo.opacity = work * .24;
      if (this.glowMaterial) this.glowMaterial.opacity = work * .94;
      this.orbit.position.copy(this.worldPosition);
      this.orbit.rotation.z = Math.sin(time * .37) * .028;
      this.orbitLeaves.forEach((pivot, i) => {
        const a = time * .46 + i / 3 * TAU;
        pivot.position.set(Math.cos(a) * 1.01, 1.14 + Math.sin(a) * .28 + Math.cos(a) * .11, Math.sin(a) * .50 - .19);
        pivot.rotation.set(.60 + Math.sin(a) * .24, Math.cos(a) * .34, -a + .65);
        pivot.scale.setScalar(.61 * (.7 + work * .3));
      });
      for (let i = 0; i < 8; i++) {
        const phase = (time * .081 + i / 8) % 1;
        this.dummy.position.copy(this.orbitPath.getPointAt(phase));
        this.dummy.scale.setScalar(.8 + Math.sin(time * 2 + i) * .2);
        this.dummy.updateMatrix();
        this.orbitLights.setMatrixAt(i, this.dummy.matrix);
        this.orbitGlows.get(i)?.position.copy(this.dummy.position);
        const left = i < 4;
        const curve = left ? this.peekCurve : this.branchCurves[0];
        this.dummy.position.copy(curve.getPointAt((time * .12 + (i % 4) / 4) % 1));
        if (left) this.dummy.position.applyMatrix4(this.peekBranch.matrixWorld);
        this.dummy.updateMatrix();
        this.signalLights.setMatrixAt(i, this.dummy.matrix);
        this.branchGlows.get(i)?.position.copy(this.dummy.position);
      }
      this.orbitLights.instanceMatrix.needsUpdate = true;
      this.signalLights.instanceMatrix.needsUpdate = true;
    }
    this.petals.visible = petals > .001;
    if (this.petals.visible) {
      this.petals.children.forEach((petal, i) => {
        const t = (time * .17 + i / 3) % 1;
        const side = i % 2 ? -1 : 1;
        petal.position.set(this.worldPosition.x + side * (.72 + t * .38) + Math.sin(t * TAU + i) * .12,
          this.worldPosition.y + 1.22 - t * 1.78, .3 + Math.cos(t * TAU + i) * .24);
        petal.rotation.set(t * 4 + i, Math.sin(t * TAU + i) * .9, side * (.7 + t * 2));
        const edge = smooth(Math.min(t * 8, (1 - t) * 8));
        petal.scale.set(.19, .26, .23).multiplyScalar(petals * edge);
      });
    }
  }

  destroy() {
    if (this.disposed) return;
    this.disposed = true;
    this.group.removeFromParent();
    // Geometries and materials belong to the host's shared resource registry;
    // instance buffers have their own disposal event and are released here.
    this.orbitLights.dispose();
    this.signalLights.dispose();
  }
}
