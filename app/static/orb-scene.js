/* A living mint habitat. Character, foliage and soft contact shadows share one depth buffer. */
import * as THREE from './vendor/three/three.module.js';

const clamp = (n,a,b) => Math.max(a,Math.min(b,n));
const motionState = orb => orb.root.dataset.reaction || orb.state;

export class AutoDevOrbScene {
  constructor(orb) {
    this.orb=orb;this.root=orb.root;this.disposed=false;this.visible=true;this.frame=0;this.last=0;
    this.gaze={x:0,y:0};this.leaves=[];this.resources=new Set();this.phase=Math.random()*Math.PI*2;
    this.isLogin=Boolean(this.root.closest('.login-character-stage'));
    this.origin=new THREE.Vector3(this.isLogin?-.12:.04,.035,.12);
    this.canvas=document.createElement('canvas');this.canvas.className='autodev-orb-scene';this.canvas.setAttribute('aria-hidden','true');
    try {
      this.renderer=new THREE.WebGLRenderer({canvas:this.canvas,alpha:true,antialias:true,powerPreference:'low-power'});
      this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));this.renderer.setClearColor(0x000000,0);
      this.renderer.outputColorSpace=THREE.SRGBColorSpace;
      this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=1.15;
      this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFShadowMap;
      this.scene=new THREE.Scene();this.camera=new THREE.PerspectiveCamera(36,1,.1,30);
      this.camera.zoom=this.root.closest('.sidebar-character-stage') ? 1.13 : this.isLogin ? 1.12 : 1;
      this.camera.position.set(0,this.isLogin?.8:.48,this.isLogin?5.8:5.5);this.camera.lookAt(0,.02,0);
      this.buildScene();this.root.append(this.canvas);
      this.onResize=()=>this.resize();this.resizeObserver=new ResizeObserver(this.onResize);this.resizeObserver.observe(this.root);
      this.onLost=event=>{event.preventDefault();this.destroy();if(orb.scene===this)orb.scene=null;this.root.classList.remove('is-scene');this.root.dataset.renderer='svg';orb.motionDriver?.setPaused(document.hidden||orb.manualPaused||orb.reducedMotion||!orb.visible);};
      this.canvas.addEventListener('webglcontextlost',this.onLost);
      this.intersection=new IntersectionObserver(entries=>{this.visible=entries[0]?.isIntersecting!==false;orb.setVisible(this.visible);this.invalidate();});
      this.intersection.observe(this.root);
      this.resize();this.root.classList.add('is-scene');this.root.dataset.renderer='three';this.invalidate();
    } catch(error) {this.destroy();throw error;}
  }
  mesh(geometry,material,parent=this.scene) {
    this.resources.add(geometry);this.resources.add(material);
    const mesh=new THREE.Mesh(geometry,material);parent.add(mesh);return mesh;
  }
  buildScene() {
    this.scene.add(new THREE.HemisphereLight(0xfffbe9,0x9bc6aa,2.5));
    const key=new THREE.DirectionalLight(0xfff6e6,2.8);key.position.set(-3,5,4);key.castShadow=true;
    Object.assign(key.shadow.camera,{left:-3.4,right:3.4,top:2.8,bottom:-2.4,near:.1,far:15});
    key.shadow.mapSize.set(512,512);key.shadow.normalBias=.035;key.shadow.bias=-.0002;key.shadow.radius=4;this.scene.add(key);
    const rim=new THREE.DirectionalLight(0xe4ffdf,1.6);rim.position.set(3,1,-2);this.scene.add(rim);
    const fill=new THREE.DirectionalLight(0xffffff,.8);fill.position.set(0,-1,4);this.scene.add(fill);
    this.ball=new THREE.Group();this.ball.position.copy(this.origin);this.ball.scale.setScalar(this.isLogin?.83:.75);this.scene.add(this.ball);
    this.body=this.mesh(new THREE.SphereGeometry(1,56,40),new THREE.MeshPhysicalMaterial({color:0xff6230,roughness:.31,metalness:0,clearcoat:1,clearcoatRoughness:.2}),this.ball);
    this.body.castShadow=true;
    this.eyes=[0,1].map(()=>this.mesh(new THREE.BufferGeometry(),new THREE.MeshPhysicalMaterial({color:0x172e26,roughness:.34,clearcoat:.7,side:THREE.DoubleSide}),this.ball));
    this.floor=this.mesh(new THREE.PlaneGeometry(7,5),new THREE.ShadowMaterial({color:0x81ad93,opacity:.105}));
    this.floor.rotation.x=-Math.PI/2;this.floor.position.y=-.815;this.floor.receiveShadow=true;
    this.buildContactShadow();this.buildFoliage();
    const arcMaterial=new THREE.MeshStandardMaterial({color:0x9ccbb0,roughness:.65,transparent:true,opacity:.48});
    this.orbit=new THREE.Group();this.orbit.position.copy(this.ball.position);this.scene.add(this.orbit);
    [0,Math.PI].forEach(angle=>{const arc=this.mesh(new THREE.TorusGeometry(this.isLogin?1.1:.96,.008,5,36,.9),arcMaterial,this.orbit);arc.rotation.set(.6,.4,angle);});
    this.sparkles=new THREE.Group();this.scene.add(this.sparkles);
    for(let i=0;i<8;i++){
      const sparkle=this.mesh(new THREE.OctahedronGeometry(.028,0),new THREE.MeshBasicMaterial({color:i%2?0xb7dab2:0xffc986}),this.sparkles);
      sparkle.userData.phase=i/8*Math.PI*2;
    }
  }
  buildContactShadow() {
    const canvas=document.createElement('canvas');canvas.width=canvas.height=128;
    const context=canvas.getContext('2d'),gradient=context.createRadialGradient(64,64,4,64,64,62);
    gradient.addColorStop(0,'rgba(112,157,130,.56)');gradient.addColorStop(.35,'rgba(133,176,145,.3)');gradient.addColorStop(1,'rgba(151,192,160,0)');
    context.fillStyle=gradient;context.fillRect(0,0,128,128);
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;this.resources.add(texture);
    const material=new THREE.MeshBasicMaterial({map:texture,transparent:true,depthWrite:false,opacity:.58});
    this.contactShadow=this.mesh(new THREE.PlaneGeometry(2.05,1.4),material);
    this.contactShadow.rotation.x=-Math.PI/2;this.contactShadow.position.set(this.origin.x,-.81,this.origin.z);this.contactShadow.renderOrder=1;
  }
  buildFoliage() {
    this.foliage=new THREE.Group();this.scene.add(this.foliage);
    const colors=[0x98c4a5,0xbad6a9,0x7eae98,0xa2cfbb,0xc4dba9];
    for(let index=0;index<5;index++){
      const pivot=new THREE.Group();this.foliage.add(pivot);
      const positions=[],uv=[],indices=[],rows=12,columns=6;
      for(let row=0;row<=rows;row++)for(let col=0;col<=columns;col++){
        const t=row/rows,w=col/columns*2-1;
        positions.push(Math.sin(Math.PI*t)*w*.3,t,Math.sin(Math.PI*t)*(.16-.12*Math.abs(w))+w*t*.08);uv.push(col/columns,t);
      }
      for(let row=0;row<rows;row++)for(let col=0;col<columns;col++){const a=row*(columns+1)+col,b=a+columns+1;indices.push(a,b,a+1,b,b+1,a+1);}
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));geometry.setIndex(indices);geometry.computeVertexNormals();
      const leaf=this.mesh(geometry,new THREE.MeshPhysicalMaterial({color:colors[index],roughness:.48,clearcoat:.28,side:THREE.DoubleSide}),pivot);
      leaf.castShadow=true;leaf.receiveShadow=true;
      const size=[.4,.3,.24,.32,.2][index];pivot.scale.setScalar(size);
      const vein=new THREE.CatmullRomCurve3([new THREE.Vector3(0,0,.018),new THREE.Vector3(0,.5,.17),new THREE.Vector3(0,1,.018)]);
      this.mesh(new THREE.TubeGeometry(vein,10,.007,4,false),new THREE.MeshStandardMaterial({color:0xd7ead0,roughness:.7}),pivot);
      this.leaves.push({pivot,geometry,base:new Float32Array(positions),index,phase:index/5*Math.PI*2});
    }
    this.dew=new THREE.Group();this.scene.add(this.dew);
    for(let index=0;index<4;index++){
      const bead=this.mesh(new THREE.SphereGeometry(.022,8,6),new THREE.MeshPhysicalMaterial({color:0xcce7c8,roughness:.22,transparent:true,opacity:.7,clearcoat:1}),this.dew);
      bead.userData.phase=index/4*Math.PI*2;
    }
  }
  paintEyes(now) {
    const driver=this.orb.motionDriver;if(!driver?._currentPolys)return;
    const polys=driver._currentPolys(clamp(driver.eyeMorph.x,0,1));
    const centroids=polys.map(poly=>poly.reduce((sum,p)=>[sum[0]+p[0]/poly.length,sum[1]+p[1]/poly.length],[0,0]));
    const center=[(centroids[0][0]+centroids[1][0])/2,(centroids[0][1]+centroids[1][1])/2];
    polys.forEach((poly,index)=>{
      const lid=window.GROK_EYES.winkLid ? window.GROK_EYES.winkLid(driver.blink.x,now,driver.winkAt,driver.winkEye,index) : clamp(driver.blink.x,.06,1.15);
      const driverWink=driver.winkEye===index&&now>=driver.winkAt&&now<driver.winkAt+320 ? Math.max(.07,Math.abs((now-driver.winkAt)/160-1)) : 1;
      const wink=driverWink*(index===0 ? this.motionPose?.wink??1 : 1);
      const scale=.0068*clamp(driver.eyeScale.x,.7,1.25);
      const points=poly.map(p=>new THREE.Vector2((p[0]-center[0])*scale,(center[1]-centroids[index][1])*scale+(centroids[index][1]-p[1])*scale*lid*wink));
      const triangles=THREE.ShapeUtils.triangulateShape(points,[]),positions=[],normals=[];
      const project=p=>{
        const x=clamp(p.x+this.gaze.x*.11,-.8,.8),y=clamp(p.y+.12+this.gaze.y*.08,-.65,.65);
        const z=Math.sqrt(Math.max(.09,1-x*x-y*y));positions.push(x*1.026,y*1.026,z*1.026);
        const length=Math.hypot(x,y,z);normals.push(x/length,y/length,z/length);
      };
      triangles.forEach(([a,b,c])=>{
        const p=points[a],q=points[b],r=points[c],pq=p.clone().add(q).multiplyScalar(.5),qr=q.clone().add(r).multiplyScalar(.5),rp=r.clone().add(p).multiplyScalar(.5);
        [p,pq,rp,pq,q,qr,rp,qr,r,pq,qr,rp].forEach(project);
      });
      const geometry=this.eyes[index].geometry;let attribute=geometry.getAttribute('position');
      if(!attribute||attribute.count!==positions.length/3)geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
      else{attribute.array.set(positions);attribute.needsUpdate=true;}
      let normalAttribute=geometry.getAttribute('normal');
      if(!normalAttribute||normalAttribute.count!==normals.length/3)geometry.setAttribute('normal',new THREE.Float32BufferAttribute(normals,3));
      else{normalAttribute.array.set(normals);normalAttribute.needsUpdate=true;}
      geometry.computeBoundingSphere();
    });
  }
  resize(){if(this.disposed)return;const box=this.root.getBoundingClientRect();if(!box.width||!box.height)return;this.renderer.setSize(box.width,box.height,false);this.camera.aspect=box.width/box.height;this.camera.updateProjectionMatrix();this.invalidate();}
  paused(){return this.orb.manualPaused||this.orb.reducedMotion||document.hidden||!this.visible;}
  invalidate(){if(this.disposed)return;if(this.frame)cancelAnimationFrame(this.frame);this.frame=0;this.last=0;this.draw(performance.now(),true);if(!this.paused())this.frame=requestAnimationFrame(t=>this.tick(t));}
  tick(now){this.frame=0;if(this.disposed||this.paused())return;if(!this.last||now-this.last>=1000/30){this.draw(now);this.last=now;}this.frame=requestAnimationFrame(t=>this.tick(t));}
  draw(now){
    if(this.disposed||!this.renderer)return;
    const driver=this.orb.motionDriver,reduced=this.orb.reducedMotion,t=reduced?0:now/1000;
    const pose=this.orb.sampleMotion(now)||{hop:0,sway:0,depth:0,roll:0,pitch:0,yaw:0,stretch:1,gazeX:0,gazeY:0,wink:1,leaf:0};this.motionPose=pose;
    const gazeX=reduced?0:clamp(this.orb.pointer.targetX+(driver?.gazeX?.x||0)*.018+pose.gazeX,-1.2,1.2);
    const gazeY=reduced?0:clamp(this.orb.pointer.targetY-(driver?.gazeY?.x||0)*.018+pose.gazeY,-1,1);
    this.gaze.x+=(gazeX-this.gaze.x)*.12;this.gaze.y+=(gazeY-this.gaze.y)*.12;
    const ex=driver?.extras||{},radius=this.isLogin?.83:.75,squash=reduced?1:clamp((driver?.squash?.x||1)*pose.stretch,.86,1.16);
    const bounce=reduced?0:clamp(-((driver?.ty?.x||0)+(ex.hop||0)+(ex.ki||0))*.005+pose.hop,-.025,.36);
    const sway=reduced?0:clamp(((driver?.tx?.x||0)+(ex.yi||0))*.004,-.10,.10)+pose.sway+Math.sin(t*.37+this.phase)*.045;
    const depth=reduced?0:(pose.depth||0)+Math.sin(t*.29+this.phase)*.035;
    this.ball.position.set(this.origin.x+sway,this.origin.y+bounce+(reduced?0:Math.sin(t*1.7)*.012),this.origin.z+depth);
    this.ball.scale.set(radius/Math.sqrt(squash),radius*squash,radius/Math.sqrt(squash));
    const roll=reduced?0:clamp(((driver?.spin?.x||0)+(ex.Kr||0)+(ex.Yr||0))*Math.PI/180,-.35,.35)+pose.roll+Math.sin(t*.8)*.025;
    this.ball.rotation.set(this.gaze.y*.13+pose.pitch,this.gaze.x*.26+(reduced?0:(ex.turn||0)+pose.yaw),roll);
    this.paintEyes(reduced?driver?.t0||now:now);
    this.contactShadow.position.x=this.ball.position.x;this.contactShadow.position.z=this.ball.position.z;
    const altitude=clamp(bounce,0,.4);this.contactShadow.scale.setScalar(1+altitude*.9);this.contactShadow.material.opacity=.58-altitude*.8;
    const status=motionState(this.orb),quiet=['blocked','error','sleeping'].includes(status),wind=quiet?.35:1;
    this.leaves.forEach(({pivot,geometry,base,index,phase})=>{
      const angle=phase+(reduced?0:t*.13*wind),flutter=reduced?0:Math.sin(t*1.1+phase)*.09*wind;
      const chase=(pose.leaf||0)*(index===0?.42:.12),distance=1.02+Math.sin(phase)*.1-chase;
      pivot.position.set(this.ball.position.x+Math.cos(angle)*distance,this.origin.y+.34+Math.sin(phase)*.58+flutter+chase,this.ball.position.z+Math.sin(angle)*.62);
      pivot.rotation.set(.3+Math.sin(angle)*.3,angle+.7,Math.cos(angle)*.7+(reduced?0:Math.sin(t*.8+phase)*.24));
      const attribute=geometry.getAttribute('position');
      for(let offset=0;offset<base.length;offset+=3){
        const tip=base[offset+1];attribute.array[offset+2]=base[offset+2]+(reduced?0:Math.sin(t*1.7+phase+tip*2.2)*tip*tip*.075*wind);
      }
      attribute.needsUpdate=true;geometry.computeVertexNormals();
    });
    this.dew.children.forEach(bead=>{const angle=bead.userData.phase+(reduced?0:t*.19);bead.position.set(this.ball.position.x+Math.cos(angle)*1.2,.12+Math.sin(angle*1.3)*.65,this.ball.position.z+Math.sin(angle)*.65);});
    this.orbit.visible=this.root.classList.contains('is-running')&&!reduced;this.orbit.position.copy(this.ball.position);this.orbit.rotation.set(.12,.16,t*.45);
    this.sparkles.visible=['success','celebrate'].includes(status)&&!reduced;
    this.sparkles.children.forEach(sparkle=>{const angle=sparkle.userData.phase+t*.75;sparkle.position.set(this.ball.position.x+Math.cos(angle)*1.13,this.ball.position.y+.25+Math.sin(angle*2)*.64,this.ball.position.z+Math.sin(angle)*.75);sparkle.rotation.set(t,t*.7,angle);});
    this.renderer.render(this.scene,this.camera);
  }
  destroy(){
    if(this.disposed)return;this.disposed=true;if(this.frame)cancelAnimationFrame(this.frame);
    this.resizeObserver?.disconnect();this.intersection?.disconnect();this.canvas?.removeEventListener('webglcontextlost',this.onLost);
    this.resources?.forEach(resource=>resource.dispose());this.resources?.clear();
    this.scene?.traverse(object=>{if(object.isLight)object.shadow?.dispose();});
    this.renderer?.dispose();this.canvas?.remove();this.root?.classList.remove('is-scene');
  }
}
window.AutoDevOrbScene=AutoDevOrbScene;
window.AutoDevOrb?.instances.forEach(orb=>orb.attachScene(AutoDevOrbScene));
