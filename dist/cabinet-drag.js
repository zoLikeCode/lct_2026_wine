'use strict';
/* Pointer, long-press touch and keyboard all share the same reversible reorder transaction. */
window.WineShelfDrag={attach(scene,{commit}){
 const track=scene.querySelector('.cellar-track'),status=scene.querySelector('.cellar-save-status');
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 let pending=null,active=null,hold=0,raf=0,saving=false,disposed=false,suppressUntil=0;
 const items=()=>[...track.querySelectorAll('.cellar-slot')];
 const say=text=>{if(status)status.textContent=text;};
 const renumber=()=>items().forEach((el,i)=>{const label=el.querySelector('.cellar-position');if(label)label.textContent=String(i+1).padStart(2,'0');});
 const stopPending=()=>{clearTimeout(hold);hold=0;pending=null;};
 const originalOrder=()=>items().map(el=>el.dataset.slug);
 const restore=order=>{const bySlug=new Map(items().map(el=>[el.dataset.slug,el]));order.forEach(slug=>{if(bySlug.has(slug))track.append(bySlug.get(slug));});renumber();};
 function moveTo(index){
  if(!active)return;const all=items(),from=all.indexOf(active.item);index=Math.max(0,Math.min(all.length-1,index));if(from===index)return;
  const before=new Map(all.map(el=>[el,el.getBoundingClientRect()]));
  const anchor=all[index];if(index<from)track.insertBefore(active.item,anchor);else track.insertBefore(active.item,anchor.nextSibling);
  renumber();if(active.keyboard){const r=active.item.querySelector('.cellar-bottle-stage img').getBoundingClientRect();active.ghost.style.left=r.left+'px';active.ghost.style.top=(r.top-22)+'px';}if(!reduced.matches)items().forEach(el=>{if(el===active.item)return;const old=before.get(el),r=el.getBoundingClientRect(),dx=old.left-r.left,dy=old.top-r.top;if(dx||dy)el.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:'translate(0,0)'}],{duration:220,easing:'cubic-bezier(.2,.7,.2,1)'});});
  say('Место '+(index+1)+' из '+all.length);
 }
 function hoverAt(x,y){if(!active)return;const all=items();let index=0,distance=Infinity;all.forEach((el,i)=>{const r=el.getBoundingClientRect(),d=Math.hypot(x-r.left-r.width/2,y-r.top-r.height/2);if(d<distance){distance=d;index=i;}});moveTo(index);}
 function paint(){
  if(!active||active.keyboard)return;
  const dx=active.x-active.startX,dy=active.y-active.startY;
  active.ghost.style.transform=`translate3d(${dx}px,${dy-22}px,0) rotate(${reduced.matches?0:Math.max(-5,Math.min(5,dx/40))}deg) scale(1.04)`;
 }
 function edgeScroll(){
  if(!active||active.keyboard)return;const edge=90,bottom=window.innerHeight-90;let speed=0;
  if(active.y<edge)speed=-Math.min(12,(edge-active.y)/edge*12);
  else if(active.y>bottom)speed=Math.min(12,(active.y-bottom)/edge*12);
  if(speed){window.scrollBy(0,speed);hoverAt(active.x,active.y);}raf=requestAnimationFrame(edgeScroll);
 }
 function begin(item,x,y,keyboard=false){
  if(saving||active||disposed)return;stopPending();
  const image=item.querySelector('.cellar-bottle-stage img');if(!image)return;const r=image.getBoundingClientRect();
  const ghost=document.createElement('div');ghost.className='cellar-drag-ghost';ghost.setAttribute('aria-hidden','true');const copy=image.cloneNode();copy.removeAttribute('alt');copy.style.height='100%';ghost.append(copy);Object.assign(ghost.style,{left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'});document.body.append(ghost);
  active={item,ghost,order:originalOrder(),startX:x,startY:y,x,y,keyboard};item.classList.add('is-dragged');scene.classList.add('is-dragging');document.body.classList.add('cellar-dragging');item.querySelector('.cellar-grip')?.setAttribute('aria-pressed','true');
  say(keyboard?'Стрелки · Enter · Esc':'Отпустите на новом месте');suppressUntil=performance.now()+600;paint();if(!keyboard)raf=requestAnimationFrame(edgeScroll);
 }
 async function finish(cancel=false){
  stopPending();if(!active)return;const drag=active;active=null;cancelAnimationFrame(raf);drag.ghost.remove();drag.item.classList.remove('is-dragged');scene.classList.remove('is-dragging');document.body.classList.remove('cellar-dragging');drag.item.querySelector('.cellar-grip')?.setAttribute('aria-pressed','false');suppressUntil=performance.now()+500;
  if(cancel){restore(drag.order);say('Перестановка отменена');return;}
  const next=originalOrder();if(next.every((s,i)=>s===drag.order[i])){say('');return;}
  saving=true;scene.classList.add('is-saving');scene.setAttribute('aria-busy','true');say('Сохраняем…');
  try{await commit(next);if(!disposed)say('Порядок сохранён');}
  catch(error){if(!disposed){restore(drag.order);say('Не сохранено. Повторите.');notice(error.message||'Не удалось сохранить порядок');}}
  finally{saving=false;scene.classList.remove('is-saving');scene.removeAttribute('aria-busy');}
 }
 function target(event){const el=event.target.closest('.cellar-bottle,.cellar-grip');return el&&track.contains(el)?el.closest('.cellar-slot'):null;}
 const pointerDown=ev=>{if(ev.pointerType==='touch'||ev.button!==0||saving||active)return;const item=target(ev);if(!item)return;pending={item,x:ev.clientX,y:ev.clientY,id:ev.pointerId};ev.preventDefault();};
 const pointerMove=ev=>{if(ev.pointerType==='touch')return;if(active&&!active.keyboard){ev.preventDefault();active.x=ev.clientX;active.y=ev.clientY;paint();hoverAt(ev.clientX,ev.clientY);return;}if(pending&&Math.hypot(ev.clientX-pending.x,ev.clientY-pending.y)>6){const p=pending;begin(p.item,p.x,p.y);active.x=ev.clientX;active.y=ev.clientY;paint();hoverAt(ev.clientX,ev.clientY);}};
 const pointerUp=ev=>{if(ev.pointerType!=='touch')finish();};
 const pointerCancel=ev=>{if(ev.pointerType!=='touch')finish(true);};
 const touchStart=ev=>{if(saving||active)return;if(ev.touches.length!==1){stopPending();return;}const item=target(ev);if(!item)return;const t=ev.touches[0];pending={item,x:t.clientX,y:t.clientY,id:t.identifier};if(ev.target.closest('.cellar-grip')){ev.preventDefault();begin(item,t.clientX,t.clientY);}else hold=setTimeout(()=>{if(pending)begin(pending.item,pending.x,pending.y);},300);};
 const touchMove=ev=>{if(active&&!active.keyboard){if(ev.touches.length!==1){finish(true);return;}ev.preventDefault();const t=ev.touches[0];active.x=t.clientX;active.y=t.clientY;paint();hoverAt(t.clientX,t.clientY);}else if(pending){const t=[...ev.touches].find(t=>t.identifier===pending.id);if(!t||Math.hypot(t.clientX-pending.x,t.clientY-pending.y)>8)stopPending();}};
 const touchEnd=()=>finish();const touchCancel=()=>finish(true);
 const click=ev=>{if(performance.now()<suppressUntil||saving){ev.preventDefault();ev.stopImmediatePropagation();}};
 const preventNative=ev=>{if(target(ev))ev.preventDefault();};
 const key=ev=>{
  if(ev.key==='Escape'&&(active||pending)){ev.preventDefault();finish(true);return;}
  const bottle=ev.target.closest('.cellar-bottle');if(!bottle||!track.contains(bottle)||saving||!ev.altKey||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(ev.key))return;
  ev.preventDefault();const item=bottle.closest('.cellar-slot'),r=item.getBoundingClientRect(),all=items(),i=all.indexOf(item),cols=parseInt(getComputedStyle(track).getPropertyValue('--shelf-columns'))||1;
  begin(item,r.left+r.width/2,r.top,true);
  const delta=ev.key==='ArrowLeft'?-1:ev.key==='ArrowRight'?1:ev.key==='ArrowUp'?-cols:cols;
  moveTo(ev.key==='Home'?0:ev.key==='End'?all.length-1:i+delta);finish();item.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});bottle.focus({preventScroll:true});
 };
 const blur=()=>finish(true);const visibility=()=>{if(document.hidden)finish(true);};
 track.addEventListener('pointerdown',pointerDown);document.addEventListener('pointermove',pointerMove,{passive:false});document.addEventListener('pointerup',pointerUp);document.addEventListener('pointercancel',pointerCancel);
 track.addEventListener('touchstart',touchStart,{passive:false});track.addEventListener('touchmove',touchMove,{passive:false});track.addEventListener('touchend',touchEnd);track.addEventListener('touchcancel',touchCancel);
 track.addEventListener('click',click,true);track.addEventListener('dragstart',preventNative);track.addEventListener('contextmenu',preventNative);document.addEventListener('keydown',key);window.addEventListener('blur',blur);document.addEventListener('visibilitychange',visibility);
 return()=>{disposed=true;finish(true);track.removeEventListener('pointerdown',pointerDown);document.removeEventListener('pointermove',pointerMove);document.removeEventListener('pointerup',pointerUp);document.removeEventListener('pointercancel',pointerCancel);track.removeEventListener('touchstart',touchStart);track.removeEventListener('touchmove',touchMove);track.removeEventListener('touchend',touchEnd);track.removeEventListener('touchcancel',touchCancel);track.removeEventListener('click',click,true);track.removeEventListener('dragstart',preventNative);track.removeEventListener('contextmenu',preventNative);document.removeEventListener('keydown',key);window.removeEventListener('blur',blur);document.removeEventListener('visibilitychange',visibility);};
}};
