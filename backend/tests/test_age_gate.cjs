const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../dist/age-gate.js'),'utf8');
function mount(saved){
 const events={},buttonEvents={},values=new Map(saved?[['wine-age-confirmed-v1','yes']]:[]);let opened=0,closed=0;
 const dialog={addEventListener:(n,f)=>events[n]=f,showModal:()=>opened++,close:()=>closed++},button={addEventListener:(n,f)=>buttonEvents[n]=f};
 vm.runInNewContext(source,{localStorage:{getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)},document:{getElementById:id=>id==='age-dialog'?dialog:button,querySelector:()=>null,body:{classList:{add(){},toggle(){}}}}});
 return {events,buttonEvents,values,get opened(){return opened},get closed(){return closed}};
}
const first=mount(false);assert.equal(first.opened,1);let cancelled=false;first.events.cancel({preventDefault(){cancelled=true}});assert.equal(cancelled,true);assert.equal(first.closed,0);first.buttonEvents.click();assert.equal(first.values.get('wine-age-confirmed-v1'),'yes');assert.equal(first.closed,1);assert.equal(mount(true).opened,0);console.log('Age notice: first visit, Escape, confirmation persistence and repeat visit passed.');
