/* Real geometry, shared depth and lighting. The authorized Grok engine owns expression timing. */
import * as THREE from './vendor/three/three.module.js';

const clamp = (n,a,b) => Math.max(a,Math.min(b,n));
const motionState = orb => orb.root.dataset.reaction || orb.state;

export class AutoDevOrbScene {
  constructor(orb) {
    this.orb=orb;this.root=orb.root;this.disposed=false;this.visible=true;this.frame=0;this.last=0;
    this.gaze={x:0,y:0};this.leaves=[];this.resources=new Set();
    this.isLogin=Boolean(this.root.closest('.login-character-stage'));
    this.origin=this.isLogin ? new THREE.Vector3(-.86,.03,.4) : new THREE.Vector3(.08,.17,.12);
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
      this.camera.zoom=this.root.closest('.sidebar-character-stage') ? 1.16 : this.isLogin ? 1.1 : 1;
      this.camera.position.set(0,this.isLogin?2.15:.45,this.isLogin?6:5.5);this.camera.lookAt(0,this.isLogin ? -.37 : -.12,0);
      this.buildScene();
      this.root.append(this.canvas);
      this.onResize=()=>this.resize();this.resizeObserver=new ResizeObserver(this.onResize);this.resizeObserver.observe(this.root);
      this.onLost=event=>{event.preventDefault();this.destroy();this.root.classList.remove('is-scene');this.root.dataset.renderer='svg';orb.motionDriver?.setPaused(document.hidden||orb.manualPaused||orb.reducedMotion);};
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
    this.scene.add(new THREE.HemisphereLight(0xfff9df,0x557b61,2.25));
    const key=new THREE.DirectionalLight(0xfff4e1,3.1);key.position.set(-3,5,4);key.castShadow=true;
    Object.assign(key.shadow.camera,{left:-3.4,right:3.4,top:2.8,bottom:-2.4,near:.1,far:15});
    key.shadow.mapSize.set(this.isLogin?1024:512,this.isLogin?1024:512);key.shadow.normalBias=.025;key.shadow.bias=-.0004;this.scene.add(key);
    const rim=new THREE.DirectionalLight(0xf1ffdc,1.4);rim.position.set(3,1,-2);this.scene.add(rim);
    const soft=new THREE.DirectionalLight(0xffffff,.5);soft.position.set(0,0,4);this.scene.add(soft);
    this.ball=new THREE.Group();this.ball.position.copy(this.origin);this.ball.scale.setScalar(this.isLogin ? .83 : .75);this.scene.add(this.ball);
    this.body=this.mesh(new THREE.SphereGeometry(1,56,40),new THREE.MeshPhysicalMaterial({color:0xff5723,roughness:.27,metalness:0,clearcoat:1,clearcoatRoughness:.17}),this.ball);
    this.body.castShadow=true;this.body.receiveShadow=true;
    this.eyes=[0,1].map(()=>this.mesh(new THREE.BufferGeometry(),new THREE.MeshPhysicalMaterial({color:0x111e19,roughness:.25,clearcoat:1,side:THREE.DoubleSide}),this.ball));
    this.floor=this.mesh(new THREE.PlaneGeometry(7,5),new THREE.ShadowMaterial({color:0x3d513a,opacity:.29}));
    this.floor.rotation.x=-Math.PI/2;this.floor.position.y=-.72;this.floor.receiveShadow=true;
    const grain=new Uint8Array(128*128);let seed=73;
    for(let index=0;index<grain.length;index++){seed=(seed*1664525+1013904223)>>>0;grain[index]=100+(seed%56);}
    this.paperGrain=new THREE.DataTexture(grain,128,128,THREE.RedFormat);this.paperGrain.wrapS=this.paperGrain.wrapT=THREE.RepeatWrapping;this.paperGrain.repeat.set(4,2);this.paperGrain.needsUpdate=true;this.resources.add(this.paperGrain);
    const paperMaterial=new THREE.MeshStandardMaterial({color:0xfff8e4,roughness:.85,bumpMap:this.paperGrain,bumpScale:.012,side:THREE.DoubleSide});
    if(this.isLogin)this.buildEditorialDesk(paperMaterial);
    else{
      const paper=this.mesh(new THREE.PlaneGeometry(2.25,.72),paperMaterial);paper.rotation.set(-1.23,.03,-.12);paper.position.set(.08,-.67,.12);paper.receiveShadow=true;
      const fold=this.mesh(new THREE.PlaneGeometry(.68,.48),paperMaterial);fold.rotation.set(-.72,-.18,.05);fold.position.set(-.73,-.53,.08);fold.receiveShadow=true;
    }
    this.plant=new THREE.Group();this.plant.position.set(this.isLogin?-3.75:-.85,-.69,-.25);if(this.isLogin)this.plant.scale.setScalar(1.5);this.scene.add(this.plant);
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
    [0,Math.PI].forEach(angle=>{const arc=this.mesh(new THREE.TorusGeometry(this.isLogin?1.09:.96,.008,5,36,1.15),arcMaterial,this.orbit);arc.rotation.set(.6,.4,angle);});
    this.sparkles=new THREE.Group();this.scene.add(this.sparkles);
    for(let i=0;i<7;i++){const sparkle=this.mesh(new THREE.SphereGeometry(.025,6,5),new THREE.MeshBasicMaterial({color:i%2?0xa7cda1:0xfbb56c}),this.sparkles);sparkle.position.set(this.origin.x+Math.cos(i)*1.05,.7+Math.sin(i)*.42,.35);}
  }
  lettering(lines,{size=512,color='#28423b',fontSize=57}={}) {
    const canvas=document.createElement('canvas');canvas.width=size;canvas.height=256;
    const context=canvas.getContext('2d');
    context.fillStyle=color;context.textAlign='center';context.textBaseline='middle';
    context.font=`600 ${fontSize}px "Noto Serif SC","SimSun",serif`;
    lines.forEach((line,index)=>context.fillText(line,size/2,128+(index-(lines.length-1)/2)*(fontSize*1.35)));
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;this.resources.add(texture);
    return new THREE.MeshStandardMaterial({map:texture,transparent:true,roughness:.95,depthWrite:false,side:THREE.DoubleSide});
  }
  buildEditorialDesk(paperMaterial) {
    // The video remains an environmental layer. Foreground objects share the mascot's depth buffer.
    const desk=this.mesh(new THREE.BoxGeometry(6.8,.11,3),new THREE.MeshStandardMaterial({color:0xe5eadb,roughness:.88}));
    desk.position.set(.15,-.84,-.25);desk.receiveShadow=true;
    const stack=new THREE.Group();stack.position.set(1.48,-.57,-.5);stack.rotation.y=-.16;this.scene.add(stack);
    ['交付上线','测试验证','研发实现','需求拆解'].forEach((title,index)=>{
      const book=new THREE.Group();book.position.set(index%2?.05:-.06,index*.36,0);book.rotation.y=[-.018,.026,-.014,.018][index];stack.add(book);
      const width=[2.82,2.92,2.88,3][index],coverMaterial=new THREE.MeshStandardMaterial({color:index===3?0xb6c8af:0xe4deca,roughness:.9,bumpMap:this.paperGrain,bumpScale:.008});
      const pages=this.mesh(new THREE.BoxGeometry(width-.07,.285,1.12),new THREE.MeshStandardMaterial({color:0xf7efd9,roughness:1}),book);pages.receiveShadow=true;pages.castShadow=true;
      [-.16,.16].forEach(y=>{const cover=this.mesh(new THREE.BoxGeometry(width,.035,1.2),coverMaterial,book);cover.position.y=y;cover.castShadow=true;cover.receiveShadow=true;});
      const spine=this.mesh(new THREE.BoxGeometry(width,.29,.045),coverMaterial,book);spine.position.z=.585;spine.castShadow=true;spine.receiveShadow=true;
      const label=this.mesh(new THREE.PlaneGeometry(1.72,.7),this.lettering([title],{fontSize:58}),book);label.position.set(.18,0,.614);
      const tab=this.mesh(new THREE.BoxGeometry(.105,.2,.018),new THREE.MeshStandardMaterial({color:[0xff672e,0xffad25,0x76a584,0xff672e][index],roughness:.65}),book);tab.position.set(-.9,-.035,.627);tab.rotation.z=-.13;
    });
    const card=this.mesh(new THREE.BoxGeometry(1.72,1.0,.025),paperMaterial);card.position.set(1.45,1.09,-.55);card.rotation.set(-.18,-.15,-.08);card.castShadow=true;card.receiveShadow=true;
    const code=this.mesh(new THREE.PlaneGeometry(1.25,.58),this.lettering(['〈 / 〉'],{fontSize:96,color:'#667c6b'}),card);code.position.z=.017;
    const flag=this.mesh(new THREE.BoxGeometry(.25,.16,.028),new THREE.MeshStandardMaterial({color:0xff6330,roughness:.8}),card);flag.position.set(.37,.55,0);flag.castShadow=true;
    const envelope=new THREE.Group();envelope.position.set(-.87,-.5,1.5);envelope.rotation.set(-.04,-.04,-.045);this.scene.add(envelope);
    const shape=new THREE.Shape();shape.moveTo(-1.22,-.4);shape.lineTo(1.19,-.4);shape.lineTo(1.08,.18);shape.lineTo(-1.12,.8);shape.closePath();
    const face=this.mesh(new THREE.ExtrudeGeometry(shape,{depth:.025,bevelEnabled:true,bevelSize:.007,bevelThickness:.005,bevelSegments:1,steps:1}),paperMaterial,envelope);face.castShadow=true;face.receiveShadow=true;
    const back=this.mesh(new THREE.BoxGeometry(2.25,.055,.8),paperMaterial,envelope);back.position.set(0,-.4,-.36);back.receiveShadow=true;back.castShadow=true;
    const leftShape=new THREE.Shape();leftShape.moveTo(-1.2,-.38);leftShape.lineTo(-1.12,.8);leftShape.lineTo(-.12,-.38);leftShape.closePath();
    const flap=this.mesh(new THREE.ShapeGeometry(leftShape),new THREE.MeshStandardMaterial({color:0xf0e5cc,roughness:.95,side:THREE.DoubleSide}),envelope);flap.position.z=.032;flap.receiveShadow=true;
    const note=this.mesh(new THREE.PlaneGeometry(2.05,.83),this.lettering(['让想法','变成可运行的软件。'],{fontSize:48}),envelope);note.position.set(.14,.03,.052);
    const mark=this.mesh(new THREE.PlaneGeometry(.38,.2),this.lettering(['〈∞〉'],{fontSize:108,color:'#718275'}),envelope);mark.position.set(.15,-.3,.052);
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
        // Subdivision and a raised shell keep triangle chords outside the sphere.
        const z=Math.sqrt(Math.max(.09,1-x*x-y*y));positions.push(x*1.026,y*1.026,z*1.026);
        // These triangles are unindexed; radial normals avoid faceted eye highlights.
        const length=Math.hypot(x,y,z);normals.push(x/length,y/length,z/length);
      };
      triangles.forEach(([a,b,c])=>{
        const p=points[a],q=points[b],r=points[c],pq=p.clone().add(q).multiplyScalar(.5),qr=q.clone().add(r).multiplyScalar(.5),rp=r.clone().add(p).multiplyScalar(.5);
        [p,pq,rp,pq,q,qr,rp,qr,r,pq,qr,rp].forEach(project);
      });
      const geometry=this.eyes[index].geometry;
      let attribute=geometry.getAttribute('position');
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
  draw(now,staticFrame=false){
    if(this.disposed||!this.renderer)return;
    const driver=this.orb.motionDriver,reduced=this.orb.reducedMotion,t=reduced?0:now/1000;
    const pose=this.orb.sampleMotion(now)||{hop:0,sway:0,roll:0,pitch:0,yaw:0,stretch:1,gazeX:0,gazeY:0,wink:1};this.motionPose=pose;
    const gazeX=reduced?0:clamp(this.orb.pointer.targetX+(driver?.gazeX?.x||0)*.018+pose.gazeX,-1.2,1.2);
    const gazeY=reduced?0:clamp(this.orb.pointer.targetY-(driver?.gazeY?.x||0)*.018+pose.gazeY,-1,1);
    this.gaze.x+=(gazeX-this.gaze.x)*.12;this.gaze.y+=(gazeY-this.gaze.y)*.12;
    const ex=driver?.extras||{},radius=this.isLogin ? .83 : .75,squash=reduced?1:clamp((driver?.squash?.x||1)*pose.stretch,.86,1.16);
    const bounce=reduced?0:clamp(-((driver?.ty?.x||0)+(ex.hop||0)+(ex.ki||0))*.005+pose.hop,-.025,.34);
    const sway=reduced?0:clamp(((driver?.tx?.x||0)+(ex.yi||0))*.004,-.10,.10)+pose.sway;
    this.ball.position.set(this.origin.x+sway,this.origin.y+bounce+(reduced?0:Math.sin(t*1.7)*.016),this.origin.z);
    this.ball.scale.set(radius/Math.sqrt(squash),radius*squash,radius/Math.sqrt(squash));
    const roll=reduced?0:clamp(((driver?.spin?.x||0)+(ex.Kr||0)+(ex.Yr||0))*Math.PI/180,-.35,.35)+pose.roll+Math.sin(t*.8)*.025;
    this.ball.rotation.set(this.gaze.y*.13+pose.pitch,this.gaze.x*.26+(reduced?0:(ex.turn||0)+pose.yaw),roll);
    this.paintEyes(reduced?driver?.t0||now:now);
    const status=motionState(this.orb),amplitude=['blocked','error','sleeping'].includes(status) ? .022 : .06;
    this.leaves.forEach(({pivot,roll,turn,index})=>{pivot.rotation.z=roll+(reduced?0:Math.sin(t*.95+index*.55)*(amplitude+pose.hop*.4));pivot.rotation.y=turn+(reduced?0:Math.cos(t*.75+index)*.13);});
    this.orbit.visible=this.root.classList.contains('is-running')&&!reduced;
    this.orbit.position.copy(this.ball.position);
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
