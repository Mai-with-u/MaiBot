const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup({decode = () => Promise.resolve(), dpr = 1} = {}) {
  let now = 0, callback;
  const element = () => {
    const draws = [], stack = [];
    const ctx = {globalAlpha:1, globalCompositeOperation:'source-over', clearRect(){draws.length=0},
      save(){stack.push([this.globalAlpha,this.globalCompositeOperation])}, scale(){},translate(){},rotate(){},
      drawImage(...args){draws.push({args,alpha:this.globalAlpha,operation:this.globalCompositeOperation})},
      getImageData(x,y,width,height){return {data:new Uint8ClampedArray(width*height*4)}},
      restore(){[this.globalAlpha,this.globalCompositeOperation]=stack.pop()}};
    return {draws, style: {setProperty(){}}, dataset: {}, querySelectorAll(){return []}, classList: {values: new Set(), add(n){this.values.add(n)}, remove(n){this.values.delete(n)}, contains(n){return this.values.has(n)}}, getContext: () => ctx};
  };
  const elements = Object.fromEntries(['stage','pet','builtin-pet','skin-canvas','skin-loading'].map(id => [id, element()]));
  elements['skin-loading'].hidden = true;
  const context = {document:{getElementById:id=>elements[id],createElement:element}, window:{dispatchEvent(){},devicePixelRatio:dpr}, Event:class{}, Image:class {decode(){return decode(this.src)}}, performance:{now:()=>now}, requestAnimationFrame:f=>{callback=f}, setTimeout, clearTimeout, crypto:require('node:crypto').webcrypto};
  vm.runInNewContext(fs.readFileSync('src/desktop_pet/web/alpha-region.js','utf8'),context);
  vm.runInNewContext(fs.readFileSync('src/desktop_pet/web/skin.js','utf8'),context);
  const frame={file:'image',rect:[0,0,100,100],duration_ms:1000};
  const skin={id:'custom',name:'test',version:'1',canvas:{width:128,height:128,anchor:[64,128],bubble_anchor:[64,0],hitbox:[0,0,128,128]}, resources:{image:'data:'},animations:{idle:{duration_ms:1000,sequence:[frame]},wave:{duration_ms:1000,sequence:[frame]}},states:{idle:'idle',thinking:'idle',talking:'idle',dragging:'idle'},events:{greeting:'wave'}};
  return {api:context.window.petSkin, skin, elements, tick:t=>{now=t;callback(t)}};
}
test('switch displays the first frame without waiting for unrelated assets, at display resolution', async () => {
  const fetched = [];
  const {api,skin,elements} = setup({dpr: 2.5});
  skin.resources = {image:'a'.repeat(64), action:'b'.repeat(64)};
  skin.animations.wave.sequence = [{...skin.animations.idle.sequence[0], file:'action'}];
  await api.install(skin, async id => {
    fetched.push(id);
    return 'data:image/png;base64,' + id;
  });
  assert.deepEqual(fetched, ['a'.repeat(64)]);
  assert.equal(elements['skin-canvas'].width, 320);
  assert.equal(elements['skin-canvas'].draws.length, 1);
});
test('after the first frame is displayed, the rest of the skin is prefetched once', async () => {
  const {api, skin} = setup();
  const first = 'a'.repeat(64), secondary = 'b'.repeat(64);
  skin.resources = {image: first, action: secondary, duplicate: secondary};
  const calls = [];
  await api.install(skin, async digest => {
    calls.push(['display', digest]);
    return 'data:image/png;base64,' + digest;
  }, async digest => {
    assert.equal(api.command('get_skin').id, skin.id);
    calls.push(['prefetch', digest]);
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [['display', first], ['prefetch', secondary]]);
});
test('a failed download hides the spinner and preserves the previous skin', async () => {
  const {api, skin, elements} = setup();
  await api.install(skin);
  let rejectAsset;
  const pending = api.install({...skin, id: 'new'}, () => new Promise((_, reject) => { rejectAsset = reject; }));
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(elements['skin-loading'].hidden, false);
  assert.equal(api.command('get_skin').id, skin.id);
  rejectAsset(new Error('asset unavailable'));
  await assert.rejects(pending, /asset unavailable/);
  assert.equal(elements['skin-loading'].hidden, true);
  assert.equal(api.command('get_skin').id, skin.id);
});
test('ownership, replacement, status, preemption and hidden window',async()=>{
 const {api,skin,elements,tick}=setup();await api.install(skin);
 const first=api.command('play_event',{event:'greeting'},'a');assert.equal(first.status,'playing');
 assert.equal(api.command('play_animation',{name:'wave',repeat:1},'b').error.code,'busy');
 assert.equal(api.command('stop_animation',{playback_id:first.playback_id},'b').error.code,'not_owner');
 const second=api.command('play_animation',{name:'wave',repeat:1},'a');
 assert.equal(api.command('animation_status',{playback_id:first.playback_id},'a').reason,'replaced');
 elements.stage.classList.add('talking');tick(1);
 assert.equal(api.command('animation_status',{playback_id:second.playback_id},'a').reason,'talking');
 elements.stage.classList.remove('talking');
 const third=api.command('play_animation',{name:'wave',repeat:1},'a');tick(1002);
 assert.equal(api.command('animation_status',{playback_id:third.playback_id},'a').status,'finished');
 api.visibility(false);assert.equal(api.command('play_event',{event:'greeting'},'a').error.code,'hidden');
});

test('repeated events restart at frame zero and get their full duration',async()=>{
 const {api,skin,elements,tick}=setup();
 skin.animations.wave.sequence=[
   {file:'image',rect:[100,0,100,100],duration_ms:500},
   {file:'image',rect:[200,0,100,100],duration_ms:500},
 ];
 await api.install(skin);
 const sourceX=()=>elements['skin-canvas'].draws.at(-1).args[1];
 tick(0);tick(200);api.event('greeting');tick(200);
 assert.equal(sourceX(),100);
 tick(750);assert.equal(sourceX(),200);
 api.event('greeting');tick(750);assert.equal(sourceX(),100);
 tick(1749);assert.equal(sourceX(),200);
 tick(1750);assert.equal(sourceX(),0);
});

test('event phase uses trigger time even when its first rendered frame is delayed',async()=>{
 const {api,skin,elements,tick}=setup();
 skin.animations.wave.sequence=[
   {file:'image',rect:[100,0,100,100],duration_ms:500},
   {file:'image',rect:[200,0,100,100],duration_ms:500},
 ];
 await api.install(skin);tick(200);api.event('greeting');tick(800);
 assert.equal(elements['skin-canvas'].draws.at(-1).args[1],200);
});

test('custom event returns to idle over 160 ms with opacity-preserving blending',async()=>{
 const {api,skin,elements,tick}=setup();await api.install(skin);
 const draws=elements['skin-canvas'].draws;
 tick(0);api.event('greeting');tick(0);tick(999);tick(1000);
 assert.deepEqual(draws.map(draw=>draw.alpha),[1,0]);
 tick(1080);
 assert.deepEqual(draws.map(draw=>draw.alpha),[0.5,0.5]);
 assert.equal(draws[1].operation,'lighter');
 tick(1160);
 assert.equal(draws.length,1);
 assert.equal(draws[0].alpha,1);
 assert.equal(draws[0].operation,'source-over');
 tick(1170);
 assert.equal(draws.length,1);
 assert.equal(draws[0].alpha,1);
 assert.equal(draws[0].operation,'source-over');
});

test('state changes blend from the rendered frame and skin replacement clears old pixels',async()=>{
 const {api,skin,elements,tick}=setup();
 skin.states.talking='wave';await api.install(skin);tick(0);
 elements.stage.classList.add('talking');tick(20);
 assert.deepEqual(elements['skin-canvas'].draws.map(draw=>draw.alpha),[1,0]);
 tick(100);
 elements.stage.classList.remove('talking');tick(100);
 assert.deepEqual(elements['skin-canvas'].draws.map(draw=>draw.alpha),[1,0]);
 await api.install({...skin,id:'other'});tick(110);
 assert.equal(elements['skin-canvas'].draws.length,1);
 assert.equal(elements['skin-canvas'].draws[0].alpha,1);
});
test('unknown actions, duration bounds and skin changes',async()=>{
 const {api,skin}=setup();await api.install(skin);
 assert.equal(api.command('play_animation',{name:'missing',repeat:1},'a').error.code,'animation_not_found');
 assert.equal(api.command('play_animation',{name:'wave',repeat:31},'a').error.code,'invalid_duration');
 const play=api.command('play_event',{event:'greeting'},'a');await api.install({...skin,id:'other'});
 assert.equal(api.command('animation_status',{playback_id:play.playback_id},'a').reason,'skin_changed');
});

test('builtin plugin animation does not erase host thinking state',async()=>{
 const {api,skin,elements,tick}=setup();
 await api.install({...skin,id:'builtin',animations:{...skin.animations,happy:{duration_ms:650}}});
 elements.stage.classList.add('thinking');
 const result=api.command('play_animation',{name:'happy',repeat:2},'a');
 assert.equal(result.duration_ms,1300);
 tick(1301);
 assert.equal(elements.stage.classList.contains('thinking'),true);
 assert.equal(elements['builtin-pet'].classList.contains('happy'),false);
 assert.equal(api.command('animation_status',{playback_id:result.playback_id},'a').status,'finished');
});
