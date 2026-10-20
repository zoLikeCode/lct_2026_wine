const test=require('node:test');
const assert=require('node:assert/strict');
const bind=require('../../dist/taste-gesture.js');

function setup(){
 const listeners=new Map(),classes=new Set(),props=new Map(),calls=[];
 let captured=null,enabled=true;
 const card={
  clientWidth:330,
  classList:{add:c=>classes.add(c),remove:c=>classes.delete(c)},
  style:{setProperty:(k,v)=>props.set(k,v),removeProperty:k=>{if(k==='transform')card.style.transform='';props.delete(k);}},
  addEventListener:(name,fn)=>listeners.set(name,fn),
  setPointerCapture:id=>{captured=id;},
  hasPointerCapture:id=>captured===id,
  releasePointerCapture:id=>{captured=null;emit('lostpointercapture',{pointerId:id});}
 };
 const body={closest:()=>null};
 const rating={closest:()=>({})};
 function emit(name,extra={}){
  const e={target:body,pointerId:1,isPrimary:true,button:0,clientX:180,clientY:180,timeStamp:0,
   detail:1,cancelable:true,preventDefault(){this.prevented=true;},stopPropagation(){},...extra};
  listeners.get(name)?.(e);return e;
 }
 bind(card,{canInteract:()=>enabled,onSwipe:v=>calls.push([v,card.style.transform]),onOpen:()=>calls.push(['open'])});
 return {card,classes,calls,emit,rating,disable:()=>{enabled=false;}};
}

test('drag continues from release position and its generated click does not open the wine',()=>{
 const h=setup();h.emit('pointerdown');
 h.emit('pointermove',{clientX:80,timeStamp:200});
 h.emit('pointerup',{clientX:70,timeStamp:250});
 assert.equal(h.calls[0][0],'nope');assert.match(h.calls[0][1],/-110px/);
 h.emit('click');assert.equal(h.calls.length,1);
 h.emit('lostpointercapture');assert.match(h.card.style.transform,/-110px/);
});
test('a tap and keyboard activation still open the wine',()=>{
 const h=setup();h.emit('pointerdown');h.emit('pointerup',{clientX:182,timeStamp:50});h.emit('click');
 h.emit('click',{detail:0});assert.deepEqual(h.calls,[['open'],['open']]);
});
test('vertical scrolling neither swipes nor opens a card',()=>{
 const h=setup();h.emit('pointerdown');h.emit('pointermove',{clientX:185,clientY:240,timeStamp:80});
 h.emit('pointerup',{clientX:185,clientY:240,timeStamp:100});h.emit('click');
 assert.deepEqual(h.calls,[]);assert.equal(h.card.style.transform,'');
});
test('short slow drag returns without a verdict, even when a click follows',()=>{
 const h=setup();h.emit('pointerdown');h.emit('pointermove',{clientX:220,timeStamp:250});
 h.emit('pointerup',{clientX:220,timeStamp:400});h.emit('click');
 assert.deepEqual(h.calls,[]);assert.equal(h.card.style.transform,'');
});
test('a deliberate short flick is accepted',()=>{
 const h=setup();h.emit('pointerdown');h.emit('pointermove',{clientX:205,timeStamp:35});
 h.emit('pointerup',{clientX:225,timeStamp:60});assert.equal(h.calls[0][0],'like');
});
test('cancelled pointer and lost capture reset the card without submitting',()=>{
 for(const event of ['pointercancel','lostpointercapture']){
  const h=setup();h.emit('pointerdown');h.emit('pointermove',{clientX:290,timeStamp:80});h.emit(event);
  h.emit('pointerup',{clientX:290,timeStamp:90});h.emit('click');
  assert.deepEqual(h.calls,[]);assert.equal(h.card.style.transform,'');
  assert.equal(h.classes.has('is-dragging'),false);
 }
});
test('a second pointer cannot release or redirect the primary gesture',()=>{
 const h=setup();h.emit('pointerdown');h.emit('pointermove',{clientX:290,timeStamp:100});
 h.emit('pointerup',{pointerId:2,isPrimary:false,clientX:0,timeStamp:110});
 assert.equal(h.calls.length,0);
 h.emit('pointerup',{clientX:290,timeStamp:150});assert.equal(h.calls[0][0],'like');
});
test('rating controls and a pending save cannot start gestures',()=>{
 const h=setup();h.emit('pointerdown',{target:h.rating});
 h.emit('pointerup',{clientX:300,timeStamp:100});assert.deepEqual(h.calls,[]);
 h.disable();h.emit('pointerdown');h.emit('pointerup',{clientX:300,timeStamp:100});h.emit('click');
 assert.deepEqual(h.calls,[]);
});
