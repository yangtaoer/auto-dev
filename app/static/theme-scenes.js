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
export class ThemeDiorama{
  constructor(host){
    this.host=host;this.theme=host.theme;this.kind=this.theme.scene;this.ball=host.ball;
    this.group=new THREE.Group();this.group.name='theme-diorama-'+this.kind;host.scene.add(this.group);
    this.time=0;this.fireflies=[];this.clouds=[];this.orbiters=[];this.bubbles=[];this.coins=[];this.mobiles=[];
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
    for(let i=0;i<5;i++){const a=i/5*Math.PI*2,x=Math.cos(a)*.1,z=Math.sin(a)*.1;this.tube([[x,0,z],[x+.04,-.18,z],[x-.035,-.38,z+.04]],.008,jelly,this.jellyfish);}
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
      for(let i=0;i<3;i++){const seam=this.mesh(new THREE.BoxGeometry(.007,.23,.032),metal,this.satellite);seam.position.x=x-.1+i*.1;}}
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
  }
  destroy(){if(this.disposed)return;this.disposed=true;this.ornament?.removeFromParent();this.group.removeFromParent();}
}
