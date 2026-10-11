/* Physical environment props share the mascot clock and depth buffer. */
import * as THREE from './vendor/three/three.module.js';

const clamp=v=>Math.max(0,Math.min(1,Number(v)||0));
const makePaperPlane=()=>{
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute([
    0,0,.58,-.52,0,-.32,-.10,.12,-.21,
    0,0,.58,-.10,.12,-.21,0,-.07,-.33,
    0,0,.58,0,-.07,-.33,.10,.12,-.21,
    0,0,.58,.10,.12,-.21,.52,0,-.32,
  ],3));geometry.computeVertexNormals();return geometry;
};
function foldedLeaf(){
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute([
    0,0,0,-.28,.43,.01,0,.51,.12,
    0,0,0,0,.51,.12,.27,.43,.01,
    -.28,.43,.01,0,1,0,0,.51,.12,
    0,.51,.12,0,1,0,.27,.43,.01,
  ],3));geometry.computeVertexNormals();return geometry;
}
function fanLeaf(){
  const vertices=[], colors=[], a=new THREE.Color(0xdfa12e), b=new THREE.Color(0xffdb70);
  for(let i=0;i<24;i++){
    const first=-.96+i/24*1.92,last=-.96+(i+1)/24*1.92;
    for(const angle of [null,first,last]){
      const r=angle===null ? .04 : .67*(1+Math.cos(angle*15)*.025),x=angle===null?0:Math.sin(angle)*r;
      vertices.push(x,angle===null?0:Math.cos(angle)*r,.05*Math.sin(angle||0));
      const color=a.clone().lerp(b,angle===null?0:.65);colors.push(color.r,color.g,color.b);
    }
  }
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));geometry.computeVertexNormals();return geometry;
}
function petalWing(){
  const shape=new THREE.Shape();shape.moveTo(0,0);shape.bezierCurveTo(.45,.70,.95,.62,.84,.23);
  shape.bezierCurveTo(.91,-.22,.39,-.46,0,0);return new THREE.ShapeGeometry(shape,24);
}
export class ThemeDiorama{
  constructor(host){
    this.host=host;this.theme=host.theme;this.kind=this.theme.scene;this.ball=host.ball;
    this.group=new THREE.Group();this.group.name='theme-diorama-'+this.kind;host.scene.add(this.group);
    this.time=0;this.fireflies=[];this.clouds=[];this.orbiters=[];this.bubbles=[];this.coins=[];this.mobiles=[];
    this.storyObjects=[];this.sparks=[];this.fallingLeaves=[];this.fish=[];this.tendrils=[];this.solarPanels=[];
    this.bodyInverse=new THREE.Matrix4();this.safePoint=new THREE.Vector3();this.headPoint=new THREE.Vector3();this.targetPoint=new THREE.Vector3();
    this.phase=(Number(host.seed)||0)%997/997*Math.PI*2;
    this.materials={
      paper:this.material(new THREE.MeshPhysicalMaterial({color:0xfffbec,roughness:.84,side:THREE.DoubleSide,sheen:.12,sheenColor:0xffffff})),
      fold:this.material(new THREE.MeshStandardMaterial({color:0xdcd4c0,roughness:.9,side:THREE.DoubleSide})),
      leaf:this.material(new THREE.MeshStandardMaterial({color:0x8ba18c,roughness:.82,side:THREE.DoubleSide})),
      cloud:this.material(new THREE.MeshPhysicalMaterial({color:0xffffff,roughness:.9,sheen:.25,sheenColor:0xdceaff,specularIntensity:.14})),
      stone:this.material(new THREE.MeshStandardMaterial({color:0x526973,roughness:.95})),
      light:this.material(new THREE.MeshBasicMaterial({color:0xffe8a0,toneMapped:false})),
    };
    if(this.kind==='sky')this.buildSky();
    if(this.kind==='paper')this.buildPaper();
    if(this.kind==='moon')this.buildMoon();
    if(this.kind==='garden')this.buildGardenStone();
    if(this.kind==='ocean')this.buildOcean();
    if(this.kind==='space')this.buildSpace();
    if(this.kind==='porcelain')this.buildPorcelain();
    if(this.kind==='arcade')this.buildArcade();
    if(this.kind==='gallery')this.buildGallery();
    this.buildStoryProps();
    this.update({time:0,reduced:true,work:0,hat:0,peek:0,crown:0});
  }
  material(material){this.host.resources.add(material);return material;}
  mesh(geometry,material,parent=this.group){const mesh=this.host.mesh(geometry,material,parent);mesh.castShadow=true;mesh.receiveShadow=true;return mesh;}
  buildSky(){
    const g=this.host.groundY;
    for(const [x,y,z,size]of [[-1.17,g+.20,.42,.37],[-.77,g+.14,.59,.43],[-.24,g+.06,.70,.40],[.23,g+.07,.65,.39],[.74,g+.13,.50,.47],[1.2,g+.23,.29,.36],[-1.45,g+.54,-.7,.22],[1.5,g+.82,-.8,.18]]){
      const mesh=this.mesh(new THREE.SphereGeometry(1,28,20),this.materials.cloud);mesh.position.set(x,y,z);mesh.scale.set(size*1.22,size*.67,size*.8);this.clouds.push({mesh,y,phase:x*2.3});
    }
    this.plane=new THREE.Group();this.plane.name='folded-paper-airplane';this.group.add(this.plane);
    this.mesh(makePaperPlane(),this.materials.paper,this.plane);this.plane.scale.setScalar(.40);
    const fold=this.mesh(new THREE.BoxGeometry(.016,.12,.22),this.materials.fold,this.plane);fold.position.set(0,-.05,-.15);
    this.buildOrbit(this.materials.cloud);
  }
  buildPaper(){
    const g=this.host.groundY;
    const paper=this.materials.paper;
    const slab=this.mesh(new THREE.BoxGeometry(2.65,.08,1.3),paper);slab.position.set(0,g-.012,-.05);slab.rotation.y=.07;
    const arch=new THREE.Group();arch.name='folded-paper-arch';this.group.add(arch);
    for(const [x,y,w,h,angle]of [[-1.13,g+1.1,.36,1.9,-.12],[1.12,g+1.1,.36,1.9,.12],[0,g+2.06,2.35,.34,.02]]){
      const part=this.mesh(new THREE.BoxGeometry(w,h,.15),paper,arch);part.position.set(x,y,-.64);part.rotation.z=angle;
    }
    this.ornament=new THREE.Group();this.ornament.name='origami-leaf-interaction';this.group.add(this.ornament);
    this.mesh(foldedLeaf(),this.materials.leaf,this.ornament);this.ornament.scale.setScalar(.71);
    this.headMatrix=new THREE.Matrix4().compose(new THREE.Vector3(0,1.05,.12),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(.7,.2,-1.1)),new THREE.Vector3(.61,.61,.61));
    this.contactMatrix=new THREE.Matrix4();this.inverseGroup=new THREE.Matrix4();
    this.flower=new THREE.Group();this.flower.position.set(-1.12,g+.16,.35);this.group.add(this.flower);
    for(let i=0;i<7;i++){const petal=this.mesh(foldedLeaf(),paper,this.flower);petal.rotation.set(-1.12,0,i/7*Math.PI*2);petal.scale.set(.35,.44,.35);}
    const scroll=this.mesh(new THREE.CylinderGeometry(.095,.095,.6,20,1,true),paper);scroll.rotation.z=Math.PI/2;scroll.position.set(.96,g+.12,.4);
    this.buildOrbit(paper);
  }
  buildMoon(){
    const g=this.host.groundY;
    const slab=this.mesh(new THREE.BoxGeometry(2.4,.20,1.35),this.materials.stone);slab.position.set(0,g-.075,-.04);
    for(let i=0;i<4;i++){
      const point=this.mesh(new THREE.SphereGeometry(.025,10,8),this.materials.light);point.name='warm-firefly';
      const haloMaterial=this.material(new THREE.MeshBasicMaterial({color:0xffdd81,transparent:true,opacity:.10,depthWrite:false,toneMapped:false}));
      const halo=this.mesh(new THREE.SphereGeometry(.082,10,8),haloMaterial,point);halo.castShadow=false;point.castShadow=false;
      this.fireflies.push({point,phase:i*1.73});
    }
  }
  buildGardenStone(){
    const material=this.material(new THREE.MeshPhysicalMaterial({color:0xe4e2cd,roughness:.97,sheen:.08}));
    const geometry=new THREE.SphereGeometry(1,48,24),position=geometry.getAttribute('position');
    for(let i=0;i<position.count;i++){const x=position.getX(i),y=position.getY(i),z=position.getZ(i),relief=1+Math.sin(x*5+y*7)*Math.cos(z*6)*.047;position.setXYZ(i,x*relief,y*relief,z*relief);}
    geometry.computeVertexNormals();const stone=this.mesh(geometry,material);stone.scale.set(1.47,.33,.9);stone.position.set(.04,this.host.groundY-.21,.10);stone.rotation.z=-.06;
  }
  tube(points,radius,material,parent=this.group){
    return this.mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points.map(p=>new THREE.Vector3(...p))),18,radius,7,false),material,parent);
  }
  headLeaf(material){
    this.ornament=new THREE.Group();this.ornament.name=this.kind+'-head-leaf-interaction';this.group.add(this.ornament);
    this.mesh(foldedLeaf(),material,this.ornament);
    this.headMatrix=new THREE.Matrix4().compose(new THREE.Vector3(0,1.06,.12),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(.7,.2,-1.1)),new THREE.Vector3(.61,.61,.61));
    this.contactMatrix=new THREE.Matrix4();this.inverseGroup=new THREE.Matrix4();
  }
  buildOcean(){
    const g=this.host.groundY,coral=this.material(new THREE.MeshStandardMaterial({color:0x50b3b8,roughness:.79}));
    const rock=this.mesh(new THREE.IcosahedronGeometry(1,2),this.material(new THREE.MeshStandardMaterial({color:0x4c7986,roughness:.96})));
    rock.scale.set(1.47,.23,.83);rock.position.set(0,g-.16,.05);
    this.coral=new THREE.Group();this.coral.name='branching-coral';this.group.add(this.coral);
    for(const side of [-1,1])for(let n=0;n<4;n++){
      const x=side*(1.02+n*.13),h=.66+n*.18;
      this.tube([[x,g,-.5],[x+side*.07,g+h*.5,-.57],[x+side*.1,g+h,-.6]],.025,coral,this.coral);
      for(let k=1;k<4;k++)this.tube([[x+side*.04,g+h*k/4,-.53],[x-side*.13,g+h*(k+.3)/4,-.46],[x-side*.23,g+h*(k+.75)/4,-.39]],.013,coral,this.coral);
    }
    this.headLeaf(this.material(new THREE.MeshStandardMaterial({color:0x66a68c,roughness:.84,side:THREE.DoubleSide})));
    this.jellyfish=new THREE.Group();this.jellyfish.name='ivory-jellyfish';this.group.add(this.jellyfish);
    const jelly=this.material(new THREE.MeshPhysicalMaterial({color:0xe1f5f6,roughness:.36,transparent:true,opacity:.72,depthWrite:false,side:THREE.DoubleSide}));
    this.mesh(new THREE.SphereGeometry(.23,32,16,0,Math.PI*2,0,Math.PI/2),jelly,this.jellyfish);
    for(let i=0;i<5;i++){const a=i/5*Math.PI*2,x=Math.cos(a)*.1,z=Math.sin(a)*.1;const tendril=this.tube([[x,0,z],[x+.04,-.18,z],[x-.035,-.38,z+.04]],.008,jelly,this.jellyfish);this.tendrils.push(tendril);}
    const bubble=this.material(new THREE.MeshPhysicalMaterial({color:0xb4f5ff,roughness:.16,metalness:.05,transparent:true,opacity:.32,depthWrite:false}));
    for(let i=0;i<7;i++){const mesh=this.mesh(new THREE.SphereGeometry(.035+i%3*.011,16,12),bubble);mesh.castShadow=false;this.bubbles.push(mesh);}
    this.buildOrbit(coral);
  }
  buildSpace(){
    const g=this.host.groundY,metal=this.material(new THREE.MeshStandardMaterial({color:0x7b8183,roughness:.55,metalness:.65}));
    const dark=this.material(new THREE.MeshStandardMaterial({color:0x333a3e,roughness:.68,metalness:.58}));
    const copper=this.material(new THREE.MeshStandardMaterial({color:0xc2926b,roughness:.55,metalness:.6}));
    const platform=this.mesh(new THREE.CylinderGeometry(1.43,1.52,.18,64),dark);platform.position.set(0,g-.14,0);
    const arch=this.mesh(new THREE.TorusGeometry(1.36,.115,16,72),metal);arch.position.set(0,g+1.04,-.69);arch.name='station-instrument-arch';
    for(let i=0;i<12;i++){const a=i/12*Math.PI*2,detail=this.mesh(new THREE.BoxGeometry(.12,.035,.13),copper);detail.position.set(Math.sin(a)*1.36,g+1.04+Math.cos(a)*1.36,-.57);detail.rotation.z=-a;}
    this.satellite=new THREE.Group();this.satellite.name='solar-panel-satellite';this.group.add(this.satellite);
    this.mesh(new THREE.BoxGeometry(.18,.21,.18),copper,this.satellite);
    const solar=this.material(new THREE.MeshStandardMaterial({color:0x223e61,roughness:.72,metalness:.24}));
    for(const x of [-.32,.32]){const panel=this.mesh(new THREE.BoxGeometry(.4,.22,.025),solar,this.satellite);panel.position.x=x;
      this.solarPanels.push(panel);
      // Cell dividers belong to the moving panel, not the satellite body.
      for(let i=0;i<3;i++){const seam=this.mesh(new THREE.BoxGeometry(.007,.23,.032),metal,panel);seam.position.x=-.1+i*.1;}}
    this.planet=new THREE.Group();this.planet.name='cream-ringed-planet';this.group.add(this.planet);
    this.mesh(new THREE.SphereGeometry(.19,24,18),this.materials.paper,this.planet);
    const ring=this.mesh(new THREE.TorusGeometry(.29,.028,10,36),copper,this.planet);ring.rotation.x=1.10;
    this.buildOrbit(copper);
  }
  buildPorcelain(){
    const g=this.host.groundY,celadon=this.material(new THREE.MeshPhysicalMaterial({color:0xc7e2d7,roughness:.27,metalness:0,specularIntensity:.28}));
    const white=this.material(new THREE.MeshPhysicalMaterial({color:0xfffef8,roughness:.4,specularIntensity:.3}));
    const blue=this.material(new THREE.MeshStandardMaterial({color:0x447b9a,roughness:.65,side:THREE.DoubleSide}));
    const base=this.mesh(new THREE.LatheGeometry([new THREE.Vector2(.91,-.24),new THREE.Vector2(1.12,-.20),new THREE.Vector2(1.13,-.16),new THREE.Vector2(.92,-.08),new THREE.Vector2(.99,0)],48),celadon);base.position.y=g-.06;
    const arch=this.mesh(new THREE.TorusGeometry(1.30,.135,18,80),white);arch.position.set(-.25,g+1.06,-.73);arch.name='porcelain-moon-gate';
    this.tube([[-1.5,g+.2,-.55],[-1.27,g+.8,-.57],[-.97,g+1.7,-.58]],.012,blue);
    for(let i=0;i<8;i++){const leaf=this.mesh(foldedLeaf(),blue);leaf.position.set(-1.49+i*.065,g+.27+i*.19,-.54);leaf.scale.setScalar(.20);leaf.rotation.set(.10,.2,-.85+i%2*1.25);}
    this.chime=new THREE.Group();this.chime.name='porcelain-windchime';this.group.add(this.chime);
    const bell=this.mesh(new THREE.LatheGeometry([new THREE.Vector2(.14,0),new THREE.Vector2(.14,.04),new THREE.Vector2(.105,.17),new THREE.Vector2(.04,.20),new THREE.Vector2(.015,.20)],32),white,this.chime);
    bell.position.y=-.45;
    const cord=this.mesh(new THREE.CylinderGeometry(.007,.007,.30,8),blue,this.chime);cord.position.y=-.13;
    const tag=this.mesh(new THREE.BoxGeometry(.08,.27,.018),white,this.chime);tag.position.y=-.68;
    for(let i=0;i<3;i++){const book=this.mesh(new THREE.BoxGeometry(.62,.1,.40),i%2?white:celadon);book.position.set(1.15,g+.035+i*.11,-.18);book.rotation.y=(i-1)*.10;}
    this.headLeaf(this.material(new THREE.MeshStandardMaterial({color:0x5d9274,roughness:.76,side:THREE.DoubleSide})));
    this.buildOrbit(blue);
  }
  buildArcade(){
    const g=this.host.groundY,green=this.material(new THREE.MeshStandardMaterial({color:0x518943,roughness:.87}));
    const lime=this.material(new THREE.MeshStandardMaterial({color:0xa4cf56,roughness:.79}));
    const gold=this.material(new THREE.MeshStandardMaterial({color:0xf2d267,roughness:.48,metalness:.32}));
    for(let x=-3;x<=3;x++)for(let z=0;z<3;z++){const h=Math.abs(x)>1?.18+(x+z+9)%3*.12:.10,block=this.mesh(new THREE.BoxGeometry(.43,h,.42),(x+z)%3?green:lime);block.position.set(x*.43,g-h/2-.02,-.3+z*.4);}
    const stalk=this.mesh(new THREE.BoxGeometry(.065,.36,.065),green);stalk.position.set(-1.23,g+.18,.55);
    for(const [x,y]of [[0,.15],[.12,0],[-.12,0],[0,-.12]]){const petal=this.mesh(new THREE.BoxGeometry(.14,.14,.07),this.materials.paper);petal.position.set(-1.23+x,g+.4+y,.55);}
    const center=this.mesh(new THREE.BoxGeometry(.12,.12,.10),gold);center.position.set(-1.23,g+.4,.59);
    for(let i=0;i<4;i++){const mesh=this.mesh(new THREE.BoxGeometry(.10,.14,.045),gold);mesh.name='floating-square-coin';this.coins.push(mesh);}
    this.buildOrbit(lime);
  }
  buildGallery(){
    const g=this.host.groundY,white=this.materials.cloud,black=this.material(new THREE.MeshStandardMaterial({color:0x171c1d,roughness:.34}));
    const plinth=this.mesh(new THREE.BoxGeometry(2.35,.29,1.3),white);plinth.position.set(0,g-.175,.12);
    const arch=this.mesh(new THREE.TorusGeometry(.9,.23,18,64,Math.PI),white);arch.position.set(-.42,g+1.05,-.8);arch.name='white-gallery-arch';
    for(const x of [-1.32,.48]){const pillar=this.mesh(new THREE.BoxGeometry(.46,1.05,.46),white);pillar.position.set(x,g+.52,-.8);}
    for(let i=0;i<2;i++){
      const mobile=new THREE.Group();mobile.name=i?'white-hanging-marble':'black-hanging-marble';mobile.position.set(1.10+i*.32,1.28,-.44);this.group.add(mobile);
      const length=.45+i*.35,cord=this.mesh(new THREE.CylinderGeometry(.007,.007,length,6),black,mobile);cord.position.y=-length/2;
      const marble=this.mesh(new THREE.SphereGeometry(.14+i*.025,24,18),i?white:black,mobile);marble.position.y=-length;
      this.mobiles.push(mobile);
    }
    this.buildOrbit(black);
  }
  buildOrbit(material){
    this.orbit=new THREE.Group();this.orbit.name='working-only-theme-signals';this.group.add(this.orbit);
    for(let i=0;i<3;i++){const mesh=this.mesh(new THREE.OctahedronGeometry(.055),material,this.orbit);this.orbiters.push(mesh);}
    this.orbit.visible=false;
  }
  creature(type,material){
    const group=new THREE.Group();group.name=this.kind+'-'+type;this.group.add(group);
    const body=this.mesh(new THREE.SphereGeometry(1,24,16),material,group);
    body.scale.set(type==='butterfly'?.045:.14,type==='butterfly'?.15:.10,.10);
    const wings=[];
    for(const side of [-1,1]){
      const pivot=new THREE.Group();pivot.position.x=side*.035;group.add(pivot);
      const wing=this.mesh(type==='butterfly'?petalWing():foldedLeaf(),material,pivot);
      wing.scale.set(side*.36,.38,.36);wing.rotation.x=-.15;
      wings.push({pivot,side});
      if(type==='butterfly')for(let n=0;n<3;n++){
        const vein=this.tube([[0,0,.01],[side*(.05+n*.025),.09,.025],[side*(.13+n*.014),.13+n*.015,.01]],.003,this.materials.fold,group);vein.rotation.z=side*.2;
      }
    }
    if(type!=='butterfly'){
      const beak=this.mesh(new THREE.ConeGeometry(.034,.10,8),this.materials.fold,group);beak.position.set(.16,.025,.03);beak.rotation.z=-Math.PI/2;
      const eye=this.mesh(new THREE.SphereGeometry(.013,8,6),this.materials.stone,group);eye.position.set(.09,.05,.085);
    } else for(const side of [-1,1])this.tube([[side*.025,.12,0],[side*.06,.22,0],[side*.085,.23,.01]],.006,this.materials.stone,group);
    group.userData.wings=wings;group.scale.setScalar(.68);this.storyObjects.push(group);return group;
  }
  buildStoryProps(){
    const g=this.host.groundY;
    if(this.kind==='garden'){
      const wing=this.material(new THREE.MeshPhysicalMaterial({color:0xb5c997,roughness:.75,side:THREE.DoubleSide,sheen:.4,sheenColor:0xffe9a3}));
      this.butterfly=this.creature('butterfly',wing);
      const pollen=this.material(new THREE.MeshBasicMaterial({color:0xf1cf6e,transparent:true,opacity:.7,depthWrite:false}));
      for(let i=0;i<9;i++)this.sparks.push(this.mesh(new THREE.SphereGeometry(.014,8,6),pollen));
    }
    if(this.kind==='moon'){
      this.lantern=new THREE.Group();this.lantern.name='wish-lantern';this.group.add(this.lantern);
      const paper=this.material(new THREE.MeshStandardMaterial({color:0xffedc8,roughness:.85,emissive:0xffbb50,emissiveIntensity:.20}));
      const shade=this.mesh(new THREE.SphereGeometry(1,28,20),paper,this.lantern);shade.scale.set(.13,.18,.13);
      for(const y of [-.15,.15]){const rim=this.mesh(new THREE.TorusGeometry(.071,.013,7,20),this.materials.stone,this.lantern);rim.rotation.x=Math.PI/2;rim.position.y=y;}
      this.tube([[0,.18,0],[0,.27,0],[.06,.33,0]],.006,this.materials.stone,this.lantern);
      for(let i=0;i<7;i++){const star=this.mesh(new THREE.OctahedronGeometry(.022),this.materials.light);star.name='constellation-star';this.sparks.push(star);}
    }
    if(this.kind==='sky'){
      for(let i=0;i<11;i++){const puff=this.mesh(new THREE.SphereGeometry(.018+i%3*.007,12,10),this.materials.cloud);puff.name='paper-airplane-vapor';this.sparks.push(puff);}
    }
    if(this.kind==='autumn'){
      const gold=this.material(new THREE.MeshStandardMaterial({color:0xffffff,vertexColors:true,roughness:.84,side:THREE.DoubleSide}));
      for(let i=0;i<8;i++){
        const group=new THREE.Group();group.name='falling-ginkgo-leaf';this.group.add(group);
        this.mesh(fanLeaf(),gold,group);
        const stalk=this.mesh(new THREE.CylinderGeometry(.004,.004,.16,5),this.materials.fold,group);stalk.position.y=-.06;
        group.scale.setScalar(.23+i%3*.025);this.fallingLeaves.push(group);
      }
      this.buildOrbit(gold);
    }
    if(this.kind==='paper'){
      this.bird=this.creature('folded-bird',this.materials.paper);
      this.pinwheel=new THREE.Group();this.pinwheel.name='folded-pinwheel';this.pinwheel.position.set(1.27,g+.88,.08);this.group.add(this.pinwheel);
      const rod=this.mesh(new THREE.BoxGeometry(.018,.70,.018),this.materials.fold);rod.position.set(1.27,g+.48,.08);
      for(let i=0;i<4;i++){const blade=this.mesh(foldedLeaf(),i%2?this.materials.leaf:this.materials.paper,this.pinwheel);blade.scale.setScalar(.25);blade.rotation.z=i/4*Math.PI*2;}
      this.pinwheelCenter=this.mesh(new THREE.SphereGeometry(.036,12,8),this.materials.fold,this.pinwheel);
    }
    if(this.kind==='ocean'){
      const silver=this.material(new THREE.MeshPhysicalMaterial({color:0xb8dcda,roughness:.53,sheen:.7,sheenColor:0x69b4bc,metalness:.15}));
      for(let i=0;i<3;i++){
        const fish=new THREE.Group();fish.name='reef-fish-'+i;this.group.add(fish);
        const body=this.mesh(new THREE.SphereGeometry(1,20,12),silver,fish);body.scale.set(.12,.066,.05);
        const tail=this.mesh(new THREE.ConeGeometry(.057,.10,3),silver,fish);tail.rotation.z=Math.PI/2;tail.position.x=-.14;
        const eye=this.mesh(new THREE.SphereGeometry(.010,8,6),this.materials.stone,fish);eye.position.set(.076,.018,.043);
        this.fish.push(fish);
      }
    }
    if(this.kind==='space'){
      const copper=this.material(new THREE.MeshStandardMaterial({color:0xc29c76,roughness:.48,metalness:.6}));
      this.antenna=new THREE.Group();this.antenna.name='satellite-dish';this.satellite.add(this.antenna);this.antenna.position.y=.15;
      this.mesh(new THREE.ConeGeometry(.10,.06,24,1,true),copper,this.antenna).rotation.z=.25;
      for(let i=0;i<5;i++){const meteor=this.mesh(new THREE.BoxGeometry(.015,.12,.015),this.materials.light);meteor.name='shooting-star';this.sparks.push(meteor);}
    }
    if(this.kind==='porcelain'){
      const ivory=this.material(new THREE.MeshPhysicalMaterial({color:0xf2f7ef,roughness:.31,specularIntensity:.23}));
      this.bird=this.creature('ceramic-bird',ivory);
      this.lotus=new THREE.Group();this.lotus.name='celadon-lotus';this.lotus.position.set(-1.25,g+.14,.48);this.group.add(this.lotus);
      this.lotusPetals=[];
      for(let i=0;i<8;i++){const pivot=new THREE.Group();pivot.rotation.y=i/8*Math.PI*2;this.lotus.add(pivot);const petal=this.mesh(petalWing(),ivory,pivot);petal.scale.set(.22,.29,.22);petal.rotation.set(.38,0,.55);this.lotusPetals.push(petal);}
    }
    if(this.kind==='arcade'){
      this.mushroom=new THREE.Group();this.mushroom.name='voxel-mushroom';this.mushroom.position.set(1.30,g+.04,.56);this.group.add(this.mushroom);
      const cream=this.materials.paper,red=this.material(new THREE.MeshStandardMaterial({color:0xe4a056,roughness:.8}));
      const stem=this.mesh(new THREE.BoxGeometry(.11,.18,.11),cream,this.mushroom);stem.position.y=.10;
      for(let x=-1;x<=1;x++)for(let z=-1;z<=1;z++){const block=this.mesh(new THREE.BoxGeometry(.105,.10,.105),Math.abs(x+z)%2?red:cream,this.mushroom);block.position.set(x*.10,.23,z*.10);}
      for(let i=0;i<12;i++){const spark=this.mesh(new THREE.BoxGeometry(.023,.023,.023),i%2?red:cream);spark.name='pixel-combo-spark';this.sparks.push(spark);}
    }
    if(this.kind==='gallery'){
      this.balanceBlocks=[];
      for(let i=0;i<3;i++){
        const block=this.mesh(new THREE.BoxGeometry(.24,.15,.24),i%2?this.materials.cloud:this.materials.stone);block.name='balance-sculpture-block';block.position.set(-1.28,g+.10+i*.16,.30);this.balanceBlocks.push(block);
      }
      this.kineticFrame=new THREE.Group();this.kineticFrame.name='kinetic-gallery-frame';this.kineticFrame.position.set(-1.23,.71,-.45);this.group.add(this.kineticFrame);
      const slab=this.mesh(new THREE.BoxGeometry(.32,.48,.028),this.materials.cloud,this.kineticFrame);slab.rotation.y=.22;
      const mark=this.mesh(new THREE.BoxGeometry(.045,.24,.038),this.materials.stone,this.kineticFrame);mark.position.set(-.08,.04,.018);
    }
  }
  avoidBody(object,margin=.30){
    // A real ellipsoid test includes squash, yaw and rebound. Project moving
    // props outside its surface; never fake depth by drawing them over the face.
    this.safePoint.copy(object.position).applyMatrix4(this.bodyInverse);
    const distance=this.safePoint.length(),limit=1+margin/this.host.radius;
    if(distance<limit){this.safePoint.multiplyScalar(limit/Math.max(distance,.01)).applyMatrix4(this.ball.matrixWorld);object.position.copy(this.safePoint);}
  }
  updateStoryProps(frame,t){
    const m=frame.themeMoment||{id:'ambient',progress:0,amount:0,reveal:0,play:0,settle:0,direction:1,reaction:0};
    const a=m.amount,p=m.progress,play=m.play,g=this.host.groundY,phase=t+this.phase;
    this.ball.updateWorldMatrix(true,false);this.bodyInverse.copy(this.ball.matrixWorld).invert();
    if(this.butterfly){
      const angle=phase*.83+m.progress*Math.PI*2;
      this.butterfly.position.set(Math.sin(angle)*1.34,1.15+Math.cos(angle)*.27,.52+Math.sin(angle*.7)*.15);
      if(m.id==='butterfly-crown'||m.id==='butterfly-greeting'){
        this.headPoint.set(.08,1.39,.11).applyMatrix4(this.ball.matrixWorld);
        this.butterfly.position.lerp(this.headPoint,m.reveal*(m.id==='butterfly-crown'?1:.32));
      }
      this.butterfly.rotation.set(Math.sin(phase*.7)*.12,Math.cos(angle)*.25,Math.cos(angle)*-.20);
      this.butterfly.userData.wings.forEach(({pivot,side})=>pivot.rotation.y=side*(.30+Math.sin(t*18)*(.58-m.reveal*.24)));
      this.avoidBody(this.butterfly,.36);
    }
    if(this.lantern){
      this.lantern.position.set(-1.3+Math.sin(phase*.29)*.13,.61+Math.sin(phase*.51)*.09+(m.id==='lantern-wish'?play*.45:0),-.48);
      this.lantern.rotation.z=Math.sin(phase*.81)*.14;
    }
    if(this.fireflies.length){
      this.fireflies.forEach(({point},i)=>{
        if(['firefly-conductor','constellation-dance','firefly-crown'].includes(m.id)){
          const angle=i/this.fireflies.length*Math.PI*2+phase*.5;
          this.headPoint.set(Math.cos(angle)*1.34,.68+Math.sin(angle)*.70,.45).lerp(this.targetPoint.set(Math.cos(angle)*.52,1.37+Math.sin(angle)*.1,.2),m.id==='firefly-crown'?m.reveal:0);
          point.position.lerp(this.headPoint,a);
        }
        this.avoidBody(point,.18);
      });
    }
    if(this.plane){
      const loop=m.id==='airplane-loop'||m.id==='wind-ribbon'?play:0;
      this.plane.position.set(Math.sin(phase*.58+p*Math.PI*2*loop)*1.48,1.36+Math.cos(phase*.43+p*Math.PI*2*loop)*.25,-.18+Math.sin(phase*.58)*.38);
      this.plane.rotation.set(-.08+Math.sin(phase*.58)*.18,Math.PI/2+Math.cos(phase*.58)*.75,Math.cos(phase*.58)*.23+loop*Math.sin(p*Math.PI*4)*.5);
      if(m.id==='airplane-salute')this.plane.rotation.z+=Math.sin(p*Math.PI*3)*play*.45;
      this.avoidBody(this.plane,.33);
      this.clouds.forEach(({mesh},i)=>mesh.scale.y=(.67+(m.id==='cloud-puff'?Math.sin(p*Math.PI*2-i*.4)*play*.09:0))*mesh.scale.x/1.22);
    }
    this.fallingLeaves.forEach((leaf,i)=>{
      const cycle=(t*(.08+i%3*.009)+i*.127+this.phase*.05)%1,theta=cycle*Math.PI*2+i*1.8;
      leaf.position.set(Math.sin(theta)*(1.12+i%2*.23),1.61-cycle*2.68,.32+Math.cos(theta)*.26);
      if(m.id==='ginkgo-twirl'||m.id==='leaf-fan')leaf.position.x+=(i%2?1:-1)*Math.sin(p*Math.PI*2)*a*.14;
      leaf.rotation.set(Math.sin(theta)*.75,theta+play*.7,Math.sin(theta*1.7)*.40);
      leaf.visible=cycle<.94;this.avoidBody(leaf,.20);
    });
    if(this.pinwheel){
      // Whole turns return to the ambient orientation without a reverse-spin.
      this.pinwheel.rotation.z=-t*.55-(m.id==='pinwheel-breath'?(p*p*(3-2*p))*Math.PI*4:0)-m.reaction*2;
      this.pinwheel.children.forEach((blade,i)=>{if(i<4)blade.rotation.x=m.id==='paper-fan'?Math.sin(p*Math.PI*4+i*.5)*play*.4:0;});
    }
    if(this.flower && this.kind==='paper'){
      this.flower.rotation.y=Math.sin(phase*.48)*.14+(m.id==='origami-unfold'?a*.55:0);
      this.flower.children.forEach(petal=>petal.rotation.x=-1.12+(m.id==='origami-unfold'?m.reveal*.65:0));
    }
    if(this.bird){
      const ceramic=this.kind==='porcelain',fly=ceramic?m.id==='porcelain-bird':m.id==='paper-bird-bow';
      this.bird.position.set(-1.28+(fly?Math.sin(p*Math.PI*2)*a*.45:Math.sin(phase*.35)*.05),ceramic?g+.65+a*.52:1.04+Math.sin(phase*.56)*.13,-.24);
      this.bird.rotation.set(0,Math.sin(phase*.27)*.3,Math.sin(phase*.55)*.14);
      this.bird.userData.wings.forEach(({pivot,side})=>pivot.rotation.y=side*(.32+Math.sin(t*(ceramic?4:10))*(.12+play*.48)));
      this.avoidBody(this.bird,.32);
    }
    if(this.jellyfish){
      const duet=m.id==='jellyfish-duet'||m.id==='jellyfish-greeting';
      this.jellyfish.position.x=1.35+Math.sin(phase*.64)*.11-(duet?play*.14:0);
      this.jellyfish.position.y=.78+Math.sin(phase*.84)*.15+(duet?Math.sin(p*Math.PI*3)*a*.15:0);
      this.tendrils.forEach((mesh,i)=>{mesh.rotation.z=Math.sin(phase*2.1+i*.8)*.13;mesh.rotation.x=Math.sin(phase*1.3+i)*.09;});
      this.coral.rotation.z=Math.sin(phase*.6)*.025+(m.id==='coral-dance'?Math.sin(p*Math.PI*4)*a*.045:0);
      this.avoidBody(this.jellyfish,.39);
    }
    this.bubbles.forEach((bubble,i)=>{
      const cycle=(t*(.065+i%3*.007)+i*.143)%1;
      bubble.position.set((i%2?1:-1)*(1.29+Math.sin(cycle*Math.PI*3+i)*.09),g+.10+cycle*2.58,.3);
      bubble.scale.setScalar(m.id==='bubble-pop'&&i===2?1+play*.8:1);this.avoidBody(bubble,.12);
      bubble.visible=!(m.id==='bubble-pop'&&i===2&&p>.49&&p<.72);
    });
    this.fish.forEach((fish,i)=>{
      const angle=phase*.63+i*1.8,parade=m.id==='fish-parade'?a:0;
      fish.position.set(Math.sin(angle)*(1.3+parade*.11),.66+Math.cos(angle)*.32+i*.09,-.43+Math.sin(angle)*.25);
      fish.rotation.set(0,Math.cos(angle)*.9,Math.sin(phase*2+i)*.06);this.avoidBody(fish,.21);
    });
    if(this.satellite){
      this.satellite.position.set(-1.32+Math.sin(phase*.41)*.16,1.10+Math.cos(phase*.53)*.13,-.28);
      this.satellite.rotation.z=Math.sin(phase*.47)*.21+(m.id==='satellite-salute'?Math.sin(p*Math.PI*4)*a*.15:0);
      this.solarPanels.forEach((panel,i)=>panel.rotation.y=(i?1:-1)*(m.id==='solar-unfold'?(1-m.reveal)*.9:Math.sin(phase*.33)*.09));
      this.antenna.rotation.z=Math.sin(phase*.72)*.22+(m.id==='satellite-inspect'?a*.35:0);
      this.avoidBody(this.satellite,.47);
    }
    if(this.planet){this.planet.rotation.y=phase*.18;this.avoidBody(this.planet,.35);}
    if(this.chime){this.chime.rotation.z=Math.sin(phase*1.8)*(.10+play*.25+m.reaction*.1);this.chime.children.at(-1).rotation.x=Math.sin(phase*2.4)*.13;}
    this.lotusPetals?.forEach((petal,i)=>{petal.rotation.x=.35+(m.id==='lotus-unfurl'?m.reveal*.65:Math.sin(phase*.26+i)*.06);});
    this.coins.forEach((coin,i)=>{
      const combo=['coin-combo','coin-juggle'].includes(m.id)?play:0,angle=phase*.8+i*1.7;
      coin.position.set((i%2?1:-1)*(1.22+Math.sin(angle)*.11),.39+i*.12+Math.sin(angle)*.18+combo*.27,.23);
      coin.rotation.y=phase*1.7+i;coin.scale.setScalar(1+combo*.23);this.avoidBody(coin,.15);
    });
    if(this.mushroom){this.mushroom.scale.y=1+(m.id==='mushroom-hop'?Math.sin(p*Math.PI*3)*play*.20:Math.sin(phase*.72)*.025);}
    this.mobiles.forEach((mobile,i)=>{
      const kick=m.id==='pendulum-catch'?Math.sin(p*Math.PI*4-i*1.2)*a*.30:0;
      mobile.rotation.z=Math.sin(phase*1.4+i*1.7)*.15+kick;
    });
    this.balanceBlocks?.forEach((block,i)=>{block.rotation.z=m.id==='block-balance'?Math.sin(p*Math.PI*3+i)*a*.15:Math.sin(phase*.54+i)*.018;block.position.y=g+.10+i*.16+(m.id==='sculpture-bow'?Math.sin(p*Math.PI)*a*.08*i:0);});
    if(this.kineticFrame){this.kineticFrame.rotation.y=Math.sin(phase*.72)*.24+(m.id==='mobile-duet'?play*.6:0);}
    this.sparks.forEach((spark,i)=>{
      if(this.kind==='garden'){
        spark.position.set(Math.sin(phase*.33+i*2)*1.35,.3+Math.sin(phase*.6+i)*.56,.32);spark.visible=m.id==='petal-shower'||i<3;this.avoidBody(spark,.12);
      }else if(this.kind==='moon'){
        spark.position.set(Math.cos(i*.88)*1.38,1.13+Math.sin(i*.88)*.44,-.67);spark.scale.setScalar(.55+Math.sin(phase*1.4+i)*.30+play*.25);
      }else if(this.kind==='sky'){
        const trail=phase*.58-i*.085;spark.position.set(Math.sin(trail)*1.48,1.36+Math.cos(phase*.43-i*.085)*.25,-.18+Math.sin(trail)*.38);spark.scale.setScalar((1-i/12)*(.5+a*.8));this.avoidBody(spark,.15);
      }else if(this.kind==='space'){
        const age=(phase*.25+i*.21)%1;spark.position.set(1.5-age*2.8,1.57-age*.57,-.7);spark.rotation.z=-.8;spark.visible=m.id==='meteor-wish'&&age<.6;
      }else if(this.kind==='arcade'){
        const angle=i/12*Math.PI*2,r=.16+play*.27;
        spark.position.set(Math.cos(angle)*r,1.40+Math.sin(angle)*r,.12);spark.visible=['pixel-spark','coin-combo'].includes(m.id)&&play>.01;spark.rotation.z=p*Math.PI*2;this.avoidBody(spark,.12);
      }
    });
    this.group.userData.story=m.id;this.group.userData.beat=m.phase;
  }
  focus(frame){
    if(frame.reduced)return null;
    const time=Number(frame.absoluteTime??frame.time)||0;
    if(this.kind==='moon')return{x:Math.sin(time*.62)*.75,y:.7+Math.cos(time*.43)*.22};
    if(this.kind==='sky')return{x:Math.sin(time*.40)*.72,y:.56+Math.cos(time*.27)*.25};
    if(this.kind==='ocean')return{x:.7+Math.sin(time*.37+this.phase)*.12,y:.65+Math.cos(time*.41)*.17};
    if(this.kind==='space')return{x:Math.sin(time*.28+this.phase)*.75,y:.75};
    if(this.kind==='porcelain')return{x:.72,y:.62+Math.sin(time*.55+this.phase)*.13};
    if(this.kind==='arcade')return{x:Math.sin(time*.49+this.phase)*.75,y:.45+Math.cos(time*.37)*.22};
    if(this.kind==='gallery')return{x:.75,y:.72+Math.sin(time*.45+this.phase)*.15};
    return null;
  }
  update(frame){
    if(this.disposed)return;
    if(!frame.reduced)this.time=Number(frame.absoluteTime??frame.time)||0;
    const t=this.time,g=this.host.groundY;
    const p=t+this.phase;
    if(this.jellyfish){this.jellyfish.position.set(1.27+Math.sin(p*.37)*.12,.76+Math.cos(p*.41)*.17,-.30);this.jellyfish.scale.set(1+Math.sin(p*1.9)*.06,1-Math.sin(p*1.9)*.07,1);this.jellyfish.rotation.z=Math.sin(p*.33)*.14;}
    this.bubbles.forEach((bubble,i)=>{const cycle=(t*.14+i*.39+this.phase)%(Math.PI*2);bubble.position.set((i%2?1:-1)*(1.15+Math.sin(cycle)*.17),g+.25+(Math.sin(cycle)*.5+.5)*2.0,.45+i%3*.08);});
    if(this.satellite){this.satellite.position.set(-1.10+Math.sin(p*.28)*.17,1.01+Math.cos(p*.35)*.10,-.50);this.satellite.rotation.set(.1,Math.sin(p*.31)*.22,-.12+Math.cos(p*.31)*.12);}
    if(this.planet){this.planet.position.set(1.28,.99+Math.sin(p*.33)*.06,-.55);this.planet.rotation.z=.3+Math.sin(p*.21)*.07;}
    if(this.chime){this.chime.position.set(1.31,1.18,-.45);this.chime.rotation.z=Math.sin(p*.93)*(.08+clamp(frame.wind)*.07);}
    this.coins.forEach((coin,i)=>{coin.position.set((i%2?1:-1)*(1.19+i*.05),.22+Math.sin(p*.7+i*1.9)*.21+i*.12,.04);coin.rotation.y=p*.9+i;});
    this.mobiles.forEach((mobile,i)=>{mobile.rotation.z=Math.sin(p*.68+i*1.7)*(.17+i*.03);});
    this.clouds.forEach(({mesh,y,phase})=>mesh.position.y=y+(frame.reduced?0:Math.sin(t*.47+phase)*.018));
    if(this.plane){
      const attention=clamp(frame.peek),height=1.05+Math.sin(t*.27)*.21;
      this.plane.position.set(Math.sin(t*.40)*1.16,height,.22+Math.cos(t*.40)*.32);
      this.plane.rotation.set(-.14+Math.sin(t*.31)*.08,Math.PI/2+Math.cos(t*.40)*.42,Math.cos(t*.40)*.16);
      this.plane.scale.setScalar(.40+attention*.025);
    }
    this.fireflies.forEach(({point,phase},i)=>{
      point.position.set(Math.sin(t*.62+phase)*(.68+i*.1),.62+Math.cos(t*.43+phase)*.43,.74+Math.sin(t*.33+phase)*.15);
      point.scale.setScalar(.75+Math.sin(t*2.1+phase)*.2);
    });
    if(this.ornament){
      const hat=clamp(frame.hat),peek=clamp(frame.peek);
      this.ornament.userData.headContact=hat>.995;
      this.ornament.matrixAutoUpdate=true;
      this.ornament.position.set(-1.04+peek*.15,g+.45,.50);
      this.ornament.rotation.set(.05,.18,-.36);this.ornament.scale.setScalar(.71);this.ornament.updateMatrix();
      this.ball.updateWorldMatrix(true,false);this.group.updateWorldMatrix(true,false);
      this.inverseGroup.copy(this.group.matrixWorld).invert();
      this.contactMatrix.multiplyMatrices(this.ball.matrixWorld,this.headMatrix).premultiply(this.inverseGroup);
      const current=this.ornament.matrix.elements,target=this.contactMatrix.elements;
      for(let i=0;i<16;i++)current[i]+=(target[i]-current[i])*hat;
      current[13]+=Math.sin(hat*Math.PI)*.46;
      this.ornament.matrixAutoUpdate=false;this.ornament.matrixWorldNeedsUpdate=true;
    }
    if(this.orbit){
      this.orbit.visible=Number(frame.work)>.001;
      this.orbiters.forEach((mesh,i)=>{const a=t*.8+i/3*Math.PI*2;mesh.position.set(Math.cos(a)*1.25,.52+Math.sin(a)*.7,Math.sin(a)*.55-.3);mesh.rotation.set(a*.4,a,a*.3);});
    }
    this.updateStoryProps(frame,t);
  }
  destroy(){if(this.disposed)return;this.disposed=true;this.ornament?.removeFromParent();this.group.removeFromParent();}
}
