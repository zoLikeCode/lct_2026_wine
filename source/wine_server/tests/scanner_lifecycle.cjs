// Exercise the real camera controller with controlled media devices and minimal DOM.
// No camera hardware or network calls; covers pending permission races and track release.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync(require('node:path').join(__dirname,'../static/mobile/app.js'),'utf8');
const cameraFunctions=src.slice(src.indexOf('function scannerState('),src.indexOf('function cancelScan('))+src.slice(src.indexOf('async function startCamera('),src.indexOf("$('camera-button').onclick"));
function setup(getUserMedia,play=async()=>{}){
 const nodes=new Map();const el=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,textContent:'',dataset:{},srcObject:null,clicks:0,attrs:{},setAttribute(key,value){this.attrs[key]=value},getAttribute(key){return this.attrs[key]},prepend(){},parentElement:{prepend(){}},click(){this.clicks++},classList:{add(){},remove(){}},play});return nodes.get(id)};
 const ctx=vm.createContext({$:el,navigator:{mediaDevices:{getUserMedia}},document:{hidden:false,body:{dataset:{}}},cameraGeneration:0,cameraStarting:false,cameraWanted:true,busy:false,stream:null,currentView:'scan'});vm.runInContext(cameraFunctions,ctx);return {ctx,el};
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
function media(){const track={stopped:0,stop(){this.stopped++}};return{track,stream:{getTracks:()=>[track]}}}
(async()=>{
 let m=media(),calls=0;let {ctx,el}=setup(async()=>{calls++;return m.stream});
 await ctx.startCamera(true);assert.equal(calls,1);assert.equal(el('viewfinder').dataset.cameraState,'live');assert.equal(el('shutter-button').hidden,false);assert.equal(m.track.stopped,0);ctx.stopCamera();assert.equal(m.track.stopped,1);assert.equal(ctx.stream,null);
 ({ctx,el}=setup(async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'})}));await ctx.startCamera(true);assert.equal(el('viewfinder').dataset.cameraState,'unavailable');assert.equal(el('camera-button').disabled,false);assert.equal(ctx.cameraWanted,false);
 let d=deferred();m=media();({ctx,el}=setup(()=>d.promise));const pending=ctx.startCamera(true);ctx.currentView='collection';ctx.stopCamera();d.resolve(m.stream);await pending;assert.equal(m.track.stopped,1);assert.equal(ctx.stream,null);assert.notEqual(el('viewfinder').dataset.cameraState,'live');
 d=deferred();m=media();({ctx,el}=setup(()=>d.promise));const pendingToggle=ctx.startCamera(false);assert.equal(el('camera-button').getAttribute('aria-pressed'),'true');ctx.turnOffCamera();d.resolve(m.stream);await pendingToggle;assert.equal(ctx.cameraWanted,false);assert.equal(el('camera-button').getAttribute('aria-pressed'),'false');assert.equal(m.track.stopped,1);
 d=deferred();m=media();({ctx,el}=setup(()=>d.promise));const duplicate=ctx.startCamera(true);await ctx.startCamera(true);ctx.busy=true;ctx.stopCamera();d.resolve(m.stream);await duplicate;assert.equal(m.track.stopped,1);assert.equal(el('shutter-button').hidden,true);
 d=deferred();m=media();({ctx,el}=setup(async()=>m.stream,()=>d.promise));const playing=ctx.startCamera(true);await new Promise(r=>setImmediate(r));ctx.stopCamera();ctx.currentView='ranking';d.resolve();await playing;assert.equal(ctx.stream,null);assert.equal(el('camera').hidden,true);assert.ok(m.track.stopped>=1);
 ({ctx,el}=setup(undefined));ctx.navigator.mediaDevices=null;await ctx.startCamera(true);assert.equal(el('native-camera-file').clicks,0);assert.equal(el('viewfinder').dataset.cameraState,'unavailable');await ctx.startCamera(false);assert.equal(el('native-camera-file').clicks,1);
 ({ctx,el}=setup(async()=>{throw Error('must not request in background')}));ctx.document.hidden=true;await ctx.startCamera(true);assert.equal(ctx.cameraStarting,false);
 console.log('PASS: live capture readiness, permission denial, stale permission result, upload interruption, play race, unsupported browser, background guard');
})().catch(e=>{console.error(e);process.exitCode=1});
