const test=require('node:test');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const motion=()=>import(pathToFileURL(path.join(__dirname,'../../app/static/theme-choreography.js')));

test('each of the ten habitats owns five different multi-beat stories',async()=>{
  const {THEME_STORIES,themeItinerary,sampleThemeStory}=await motion();
  assert.equal(Object.keys(THEME_STORIES).length,10);
  const ids=Object.values(THEME_STORIES).flat();assert.equal(new Set(ids).size,50);
  for(const kind of Object.keys(THEME_STORIES))for(let seed=0;seed<9;seed++)for(let cycle=0;cycle<4;cycle++){
    const itinerary=themeItinerary(kind,seed,cycle);
    assert.equal(itinerary.events.length,5);assert.equal(new Set(itinerary.events.map(e=>e.id)).size,5);
    assert.ok(itinerary.events.at(-1).end<itinerary.duration);
    for(const event of itinerary.events){
      assert.ok(event.end-event.start>=5.4&&event.end-event.start<=9.1,'actions are not slow-motion stretches');
      for(const progress of [0,.08,.3,.52,.8,1]){
        const sample=sampleThemeStory(kind,event,event.start+(event.end-event.start)*progress);
        assert.ok(Object.values(sample.pose).every(Number.isFinite));
        if(progress===0||progress===1){assert.ok(sample.amount<1e-10);assert.ok(Object.entries(sample.pose).every(([key,n])=>Math.abs(n-(key==='wink'?1:0))<1e-10),'complete rest at both ends');}
      }
    }
    if(cycle)assert.notEqual(itinerary.events[0].id,themeItinerary(kind,seed,cycle-1).events.at(-1).id,'no adjacent repeat at a loop boundary');
  }
});

test('random itineraries change over minutes but retain seed reproducibility',async()=>{
  const {themeItinerary}=await motion();
  assert.deepEqual(themeItinerary('sky',93,7),themeItinerary('sky',93,7));
  assert.notDeepEqual(themeItinerary('sky',93,7),themeItinerary('sky',93,8));
  assert.notDeepEqual(themeItinerary('sky',93,7),themeItinerary('sky',94,7));
});

test('static preferences freeze body and prop beats; gentle and working keep much smaller gestures',async()=>{
  const {ThemePerformanceDirector,themeItinerary}=await motion();
  for(const kind of ['garden','moon','sky','autumn','paper','ocean','space','porcelain','arcade','gallery']){
    const director=new ThemePerformanceDirector(kind,13),event=themeItinerary(kind,13).events[0],time=(event.start+event.end)/2;
    const full=director.sample({time}),frozen=director.sample({time:600,frozen:true});assert.deepEqual(frozen,full);
    const gentle=director.sample({time,gentle:true});assert.ok(gentle.amount<=full.amount*.181);
    const working=director.sample({time,mode:'working'});assert.ok(working.amount<=full.amount*.301);
    const attached=director.sample({time,attachment:1});assert.equal(attached.amount,0);assert.equal(attached.pose.wink,1);
    assert.equal(director.sample({time,mode:'quiet'}).reaction,0);
  }
});

test('hover and click reactions have cooldowns without restarting the choreography clock',async()=>{
  const {ThemePerformanceDirector}=await motion();const director=new ThemePerformanceDirector('ocean',3);
  director.sample({time:30});assert.equal(director.react('tap'),true);assert.equal(director.react('tap'),false);
  assert.ok(director.sample({time:31}).reaction>.1);assert.equal(director.lastTime,31);
  assert.equal(director.sample({time:33}).reaction,0);assert.equal(director.react('tap'),true);
});
