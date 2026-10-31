const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup() {
  let now = 0, callback;
  const decoded = [], closed = [];
  const element = () => ({style: {setProperty(){}}, dataset: {}, querySelectorAll(){return []}, classList: {values: new Set(), add(n){this.values.add(n)}, remove(n){this.values.delete(n)}, contains(n){return this.values.has(n)}}, getContext: () => ({clearRect(){},save(){},scale(){},translate(){},rotate(){},drawImage(){},restore(){}})});
  const elements = Object.fromEntries(['stage','pet','builtin-pet','skin-canvas'].map(id => [id, element()]));
  const context = {document:{getElementById:id=>elements[id]}, window:{dispatchEvent(){}}, Event:class{}, Image:class {async decode(){decoded.push(this.src)}}, performance:{now:()=>now}, fetch:async src=>({blob:async()=>src}), createImageBitmap:async src=>{decoded.push(src);return {close(){closed.push(src)}}}, requestAnimationFrame:f=>{callback=f}, crypto:require('node:crypto').webcrypto};
  vm.runInNewContext(fs.readFileSync('src/desktop_pet/web/skin.js','utf8'),context);
  const frame={file:'image',rect:[0,0,100,100],duration_ms:1000};
  const skin={id:'custom',name:'test',version:'1',canvas:{width:128,height:128,anchor:[64,128],bubble_anchor:[64,0],hitbox:[0,0,128,128]}, resources:{image:'data:'},animations:{idle:{duration_ms:1000,sequence:[frame]},wave:{duration_ms:1000,sequence:[frame]}},states:{idle:'idle',thinking:'idle',talking:'idle',dragging:'idle'},events:{greeting:'wave'}};
  return {api:context.window.petSkin, skin, elements, decoded, closed, flush:()=>new Promise(r=>setImmediate(r)), tick:t=>{now=t;callback(t)}};
}
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

test('plugin-only animations decode on demand and are released after playback',async()=>{
 const {api,skin,decoded,closed,flush,tick}=setup();
 const extra={file:'extra',rect:[0,0,100,100],duration_ms:1000};
 await api.install({...skin,resources:{...skin.resources,extra:'data:extra'},animations:{...skin.animations,dance:{duration_ms:1000,sequence:[extra]}}});
 assert.deepEqual(decoded,['data:']);
 const play=api.command('play_animation',{name:'dance',repeat:1},'a');
 tick(10);await flush();
 assert.deepEqual(decoded,['data:','data:extra']);
 tick(500);assert.equal(api.command('animation_status',{playback_id:play.playback_id},'a').status,'playing');
 tick(1011);assert.equal(api.command('animation_status',{playback_id:play.playback_id},'a').status,'finished');
 assert.deepEqual(closed,['data:extra']);
 api.command('play_animation',{name:'dance',repeat:1},'a');await flush();
 assert.deepEqual(decoded,['data:','data:extra','data:extra']);
});
