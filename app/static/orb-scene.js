/* Real geometry, shared depth and lighting. The authorized Grok engine owns expression timing. */
import * as THREE from './vendor/three/three.module.js';

const clamp = (n,a,b) => Math.max(a,Math.min(b,n));
const motionState = orb => orb.root.dataset.reaction || orb.state;

export class AutoDevOrbScene {
  constructor(orb) {
    this.orb=orb;this.root=orb.root;this.disposed=false;this.visible=true;this.frame=0;this.last=0;
    this.gaze={x:0,y:0};this.leaves=[];this.resources=new Set();
    this.canvas=document.createElement('canvas');this.canvas.className='autodev-orb-scene';this.canvas.setAttribute('aria-hidden','true');
    try {
      this.renderer=new THREE.WebGLRenderer({canvas:this.canvas,alpha:true,antialias:true,powerPreference:'low-power'});
      this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));
      this.renderer.setClearColor(0x000000,0);
      this.renderer.outputColorSpace=THREE.SRGBColorSpace;
      this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=1.25;
      this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFShadowMap;
      this.scene=new THREE.Scene();
      this.camera=new THREE.PerspectiveCamera(36,1,.1,30);
      this.camera.zoom=this.root.closest('.sidebar-character-stage') ? 1.16 : 1;
      this.camera.position.set(0,.45,5.5);this.camera.lookAt(0,-.12,0);
      this.buildScene();
      this.root.append(this.canvas);
      this.onResize=()=>this.resize();this.resizeObserver=new ResizeObserver(this.onResize);this.resizeObserver.observe(this.root);
      this.onLost=event=>{event.preventDefault();this.destroy();this.root.classList.remove('is-scene');this.root.dataset.renderer='svg';orb.motionDriver?.setPaused(document.hidden||orb.manualPaused||orb.reducedMotion);};
      this.canvas.addEventListener('webglcontextlost',this.onLost);
      this.intersection=new IntersectionObserver(entries=>{this.visible=entries[0]?.isIntersecting!==false;orb.motionDriver?.setPaused(!this.visible||document.hidden||orb.manualPaused||orb.reducedMotion);this.invalidate();});
      this.intersection.observe(this.root);
      this.resize();this.root.classList.add('is-scene');this.root.dataset.renderer='three';this.invalidate();
    } catch(error) {this.destroy();throw error;}
  }
  mesh(geometry,material,parent=this.scene) {
    this.resources.add(geometry);this.resources.add(material);
    const mesh=new THREE.Mesh(geometry,material);parent.add(mesh);return mesh;
  }
  buildScene() {
    this.scene.add(new THREE.HemisphereLight(0xfff9df,0x557b61,2.25));
    const key=new THREE.DirectionalLight(0xfff4e1,3.1);key.position.set(-3,5,4);key.castShadow=true;
    Object.assign(key.shadow.camera,{left:-2.4,right:2.4,top:2.4,bottom:-2.4,near:.1,far:15});
    key.shadow.mapSize.set(512,512);key.shadow.normalBias=.025;key.shadow.bias=-.0004;this.scene.add(key);
    const rim=new THREE.DirectionalLight(0xf1ffdc,1.4);rim.position.set(3,1,-2);this.scene.add(rim);
    const soft=new THREE.DirectionalLight(0xffffff,.5);soft.position.set(0,0,4);this.scene.add(soft);
    this.ball=new THREE.Group();this.ball.position.set(.08,.17,.12);this.ball.scale.setScalar(.75);this.scene.add(this.ball);
    this.body=this.mesh(new THREE.SphereGeometry(1,56,40),new THREE.MeshPhysicalMaterial({color:0xff5723,roughness:.27,metalness:0,clearcoat:1,clearcoatRoughness:.17}),this.ball);
    this.body.castShadow=true;this.body.receiveShadow=true;
    this.eyes=[0,1].map(()=>this.mesh(new THREE.BufferGeometry(),new THREE.MeshPhysicalMaterial({color:0x111e19,roughness:.25,clearcoat:1,side:THREE.DoubleSide}),this.ball));
    this.floor=this.mesh(new THREE.PlaneGeometry(7,5),new THREE.ShadowMaterial({color:0x3d513a,opacity:.29}));
    this.floor.rotation.x=-Math.PI/2;this.floor.position.y=-.72;this.floor.receiveShadow=true;
    const paperMaterial=new THREE.MeshStandardMaterial({color:0xfff8e4,roughness:.85,side:THREE.DoubleSide});
    const paper=this.mesh(new THREE.PlaneGeometry(2.25,.72),paperMaterial);paper.rotation.set(-1.23,.03,-.12);paper.position.set(.08,-.67,.12);paper.receiveShadow=true;
    const fold=this.mesh(new THREE.PlaneGeometry(.68,.48),paperMaterial);fold.rotation.set(-.72,-.18,.05);fold.position.set(-.73,-.53,.08);fold.receiveShadow=true;
    this.plant=new THREE.Group();this.plant.position.set(-.85,-.69,-.25);this.scene.add(this.plant);
    const stemMaterial=new THREE.MeshStandardMaterial({color:0x698d6b,roughness:.75});
    const stem=new THREE.CatmullRomCurve3([new THREE.Vector3(0,0,0),new THREE.Vector3(-.1,.45,-.03),new THREE.Vector3(.04,1.0,-.08)]);
    this.mesh(new THREE.TubeGeometry(stem,12,.014,5,false),stemMaterial,this.plant);
    const settings=[[-.01,.23,-.08,-1.15,.2,.58],[-.07,.44,-.08,1.02,-.3,.59],[0,.63,-.1,-.62,.1,.52],[.02,.83,-.08,.48,-.2,.4]];
    settings.forEach(([x,y,z,roll,turn,size],index)=>{
      const pivot=new THREE.Group();pivot.position.set(x,y,z);pivot.rotation.set(-.15,turn,roll);this.plant.add(pivot);
      const positions=[],uv=[],indices=[],rows=14,columns=8;
      for(let row=0;row<=rows;row++)for(let col=0;col<=columns;col++){
        const t=row/rows,w=col/columns*2-1;
        positions.push(Math.sin(Math.PI*t)*w*.3,t,Math.sin(Math.PI*t)*(.16-.12*Math.abs(w))+w*t*.08);uv.push(col/columns,t);
      }
      for(let row=0;row<rows;row++)for(let col=0;col<columns;col++){const a=row*(columns+1)+col,b=a+columns+1;indices.push(a,b,a+1,b,b+1,a+1);}
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));geometry.setIndex(indices);geometry.computeVertexNormals();
      const leaf=this.mesh(geometry,new THREE.MeshPhysicalMaterial({color:[0x9cc298,0x789f7d,0xb3cda0,0x87b38d][index],roughness:.47,clearcoat:.18,side:THREE.DoubleSide}),pivot);
      leaf.scale.setScalar(size);leaf.castShadow=true;leaf.receiveShadow=true;
      const vein=new THREE.CatmullRomCurve3([new THREE.Vector3(0,0,.015),new THREE.Vector3(0,size*.5,size*.17),new THREE.Vector3(0,size,.015)]);
      this.mesh(new THREE.TubeGeometry(vein,10,.006,4,false),stemMaterial,pivot);
      this.leaves.push({pivot,roll,turn,index});
    });
    const arcMaterial=new THREE.MeshStandardMaterial({color:0x83b394,roughness:.7,transparent:true,opacity:.62});
    this.orbit=new THREE.Group();this.orbit.position.copy(this.ball.position);this.scene.add(this.orbit);
    [0,Math.PI].forEach(angle=>{const arc=this.mesh(new THREE.TorusGeometry(.96,.008,5,36,1.15),arcMaterial,this.orbit);arc.rotation.set(.6,.4,angle);});
    this.sparkles=new THREE.Group();this.scene.add(this.sparkles);
    for(let i=0;i<7;i++){const sparkle=this.mesh(new THREE.SphereGeometry(.025,6,5),new THREE.MeshBasicMaterial({color:i%2?0xa7cda1:0xfbb56c}),this.sparkles);sparkle.position.set(Math.cos(i)*1.05,.7+Math.sin(i)*.42,.35);}
  }
  paintEyes(now) {
    const driver=this.orb.motionDriver;if(!driver?._currentPolys)return;
    const polys=driver._currentPolys(clamp(driver.eyeMorph.x,0,1));
    const centroids=polys.map(poly=>poly.reduce((sum,p)=>[sum[0]+p[0]/poly.length,sum[1]+p[1]/poly.length],[0,0]));
    const center=[(centroids[0][0]+centroids[1][0])/2,(centroids[0][1]+centroids[1][1])/2];
    polys.forEach((poly,index)=>{
      const lid=window.GROK_EYES.winkLid ? window.GROK_EYES.winkLid(driver.blink.x,now,driver.winkAt,driver.winkEye,index) : clamp(driver.blink.x,.06,1.15);
      const wink=driver.winkEye===index&&now>=driver.winkAt&&now<driver.winkAt+320 ? Math.max(.07,Math.abs((now-driver.winkAt)/160-1)) : 1;
      const scale=.0068*clamp(driver.eyeScale.x,.7,1.25);
      const points=poly.map(p=>new THREE.Vector2((p[0]-center[0])*scale,(center[1]-centroids[index][1])*scale+(centroids[index][1]-p[1])*scale*lid*wink));
      const triangles=THREE.ShapeUtils.triangulateShape(points,[]),positions=[];
      const project=p=>{
        const x=clamp(p.x+this.gaze.x*.11,-.8,.8),y=clamp(p.y+.12+this.gaze.y*.08,-.65,.65);
        // Subdivision and a raised shell keep triangle chords outside the sphere.
        const z=Math.sqrt(Math.max(.09,1-x*x-y*y));positions.push(x*1.026,y*1.026,z*1.026);
      };
      triangles.forEach(([a,b,c])=>{
        const p=points[a],q=points[b],r=points[c],pq=p.clone().add(q).multiplyScalar(.5),qr=q.clone().add(r).multiplyScalar(.5),rp=r.clone().add(p).multiplyScalar(.5);
        [p,pq,rp,pq,q,qr,rp,qr,r,pq,qr,rp].forEach(project);
      });
      const geometry=this.eyes[index].geometry;
      let attribute=geometry.getAttribute('position');
      if(!attribute||attribute.count!==positions.length/3)geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
      else{attribute.array.set(positions);attribute.needsUpdate=true;}
      geometry.computeVertexNormals();geometry.computeBoundingSphere();
    });
  }
  resize(){if(this.disposed)return;const box=this.root.getBoundingClientRect();if(!box.width||!box.height)return;this.renderer.setSize(box.width,box.height,false);this.camera.aspect=box.width/box.height;this.camera.updateProjectionMatrix();this.invalidate();}
  paused(){return this.orb.manualPaused||this.orb.reducedMotion||document.hidden||!this.visible;}
  invalidate(){if(this.disposed)return;if(this.frame)cancelAnimationFrame(this.frame);this.frame=0;this.last=0;this.draw(performance.now(),true);if(!this.paused())this.frame=requestAnimationFrame(t=>this.tick(t));}
  tick(now){this.frame=0;if(this.disposed||this.paused())return;if(!this.last||now-this.last>=1000/30){this.draw(now);this.last=now;}this.frame=requestAnimationFrame(t=>this.tick(t));}
  draw(now,staticFrame=false){
    if(this.disposed||!this.renderer)return;
    const driver=this.orb.motionDriver,reduced=this.orb.reducedMotion,t=reduced?0:now/1000;
    this.gaze.x+=(this.orb.pointer.targetX-this.gaze.x)*.12;this.gaze.y+=(this.orb.pointer.targetY-this.gaze.y)*.12;
    const ex=driver?.extras||{},radius=.75,squash=clamp(driver?.squash?.x||1,.86,1.1);
    const bounce=reduced?0:clamp(-(ex.hop||0)*.004,0,.18);
    this.ball.position.set(.08+clamp((driver?.tx?.x||0)*.002,-.08,.08),.17+bounce+Math.sin(t*1.6)*.009,.12);
    this.ball.scale.set(radius/Math.sqrt(squash),radius*squash,radius);
    this.ball.rotation.set(this.gaze.y*.1,this.gaze.x*.23+(reduced?0:(ex.turn||0)),clamp((driver?.spin?.x||0)*Math.PI/180,-.25,.25)+(reduced?0:Math.sin(t*.7)*.025));
    this.paintEyes(reduced?driver?.t0||now:now);
    const status=motionState(this.orb),amplitude=['blocked','error','sleeping'].includes(status) ? .022 : .06;
    this.leaves.forEach(({pivot,roll,turn,index})=>{pivot.rotation.z=roll+(reduced?0:Math.sin(t*.75+index*.55)*amplitude);pivot.rotation.y=turn+(reduced?0:Math.cos(t*.55+index)*.09);});
    this.orbit.visible=this.root.classList.contains('is-running')&&!reduced;
    this.orbit.rotation.set(.12,.16,t*.45);
    this.sparkles.visible=['success','celebrate'].includes(status)&&!reduced;
    this.sparkles.rotation.y=Math.sin(t*.7)*.25;
    this.renderer.render(this.scene,this.camera);
  }
  destroy(){
    if(this.disposed)return;this.disposed=true;if(this.frame)cancelAnimationFrame(this.frame);
    this.resizeObserver?.disconnect();this.intersection?.disconnect();this.canvas?.removeEventListener('webglcontextlost',this.onLost);
    this.resources?.forEach(resource=>resource.dispose());
    this.scene?.traverse(object=>{if(object.isLight)object.shadow?.dispose();});
    this.renderer?.dispose();this.canvas?.remove();this.root?.classList.remove('is-scene');
  }
}
window.AutoDevOrbScene=AutoDevOrbScene;
window.AutoDevOrb?.instances.forEach(orb=>orb.attachScene(AutoDevOrbScene));
