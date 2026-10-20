'use strict';
// One pointer lifecycle for touch, pen and mouse. Vertical movement belongs to scrolling.
((root,factory)=>{
 const bind=factory();
 if(typeof module==='object'&&module.exports)module.exports=bind;
 else root.bindTasteSwipe=bind;
})(typeof window==='undefined'?null:window,()=>function bindTasteSwipe(card,{canInteract,onSwipe,onOpen}){
 let pointer=null,suppressClickUntil=0;
 const excluded=target=>target.closest('.swipe-rate,button:not([data-swipe-wine]),a,input,select,textarea');
 const now=()=>performance.now();
 const resetVisual=()=>{
  card.classList.remove('is-dragging');
  card.style.removeProperty('transform');
  card.style.removeProperty('--like');
  card.style.removeProperty('--nope');
 };
 const release=id=>{if(card.hasPointerCapture(id))card.releasePointerCapture(id);};
 const cancel=()=>{
  if(!pointer)return;
  const id=pointer.id;pointer=null;suppressClickUntil=now()+500;
  resetVisual();release(id);
 };
 const move=e=>{
  const p=pointer;if(!p||p.id!==e.pointerId)return;
  p.dx=e.clientX-p.x;p.dy=e.clientY-p.y;
  p.samples.push({x:e.clientX,t:e.timeStamp});
  p.samples=p.samples.filter(s=>e.timeStamp-s.t<=120);
  if(!p.horizontal){
   if(Math.abs(p.dy)>10&&Math.abs(p.dy)>Math.abs(p.dx)){cancel();return;}
   if(Math.abs(p.dx)<8||Math.abs(p.dx)<Math.abs(p.dy)*1.15)return;
   p.horizontal=true;suppressClickUntil=now()+500;
   card.classList.add('is-dragging');
  }
  if(e.cancelable)e.preventDefault();
  const rotation=Math.max(-8,Math.min(8,p.dx/30)),y=Math.max(-18,Math.min(18,p.dy*.12));
  card.style.transform=`translate3d(${p.dx}px,${y}px,0) rotate(${rotation}deg)`;
  card.style.setProperty('--like',Math.max(0,Math.min(1,p.dx/90)));
  card.style.setProperty('--nope',Math.max(0,Math.min(1,-p.dx/90)));
 };
 card.addEventListener('pointerdown',e=>{
  if(pointer||e.isPrimary===false||e.button!==0||!canInteract()||excluded(e.target))return;
  suppressClickUntil=0;
  pointer={id:e.pointerId,x:e.clientX,y:e.clientY,dx:0,dy:0,horizontal:false,
   samples:[{x:e.clientX,t:e.timeStamp}]};
  card.setPointerCapture(e.pointerId);
 });
 card.addEventListener('pointermove',move);
 card.addEventListener('pointerup',e=>{
  if(!pointer||pointer.id!==e.pointerId)return;
  move(e);const p=pointer;if(!p)return;
  pointer=null;release(p.id);card.classList.remove('is-dragging');
  if(!p.horizontal){resetVisual();return;} // The subsequent click handles an actual tap.
  suppressClickUntil=now()+500;
  const first=p.samples[0],velocity=first?(e.clientX-first.x)/Math.max(1,e.timeStamp-first.t):0;
  const distance=Math.max(56,Math.min(120,card.clientWidth*.22));
  const flick=Math.abs(p.dx)>=28&&Math.abs(velocity)>=.5&&Math.sign(velocity)===Math.sign(p.dx);
  if(canInteract()&&(Math.abs(p.dx)>=distance||flick)){
   // Keep the released position: the exit animation continues from here without snapping back.
   onSwipe(p.dx>0?'like':'nope');
  }else resetVisual();
 });
 card.addEventListener('pointercancel',cancel);
 card.addEventListener('lostpointercapture',cancel);
 card.addEventListener('dragstart',e=>e.preventDefault());
 card.addEventListener('click',e=>{
  if(excluded(e.target))return;
  e.preventDefault();e.stopPropagation();
  if(!canInteract()||(e.detail!==0&&now()<suppressClickUntil))return;
  onOpen();
 },true);
 return {cancel,resetVisual};
});
