'use strict';
// Catalogue imagery is decorative during search; only the server response selects the final bottle.
class WineSearchReel {
 constructor(stage) {
  this.stage=stage;this.track=stage.querySelector('.reel-track');this.motion=matchMedia('(prefers-reduced-motion: reduce)');
  this.cells=[];this.running=false;this.hovered=false;this.focused=false;
  stage.addEventListener('pointerenter',e=>{if(e.pointerType==='mouse')this.hovered=true;});
  stage.addEventListener('pointerleave',()=>{this.hovered=false;});
  stage.addEventListener('focusin',()=>{this.focused=true;});
  stage.addEventListener('focusout',()=>{this.focused=false;});
  this.resizeObserver=new ResizeObserver(()=>this.render());this.resizeObserver.observe(stage);
  this.motion.addEventListener('change',()=>{cancelAnimationFrame(this.raf);if(!this.running)return;if(this.motion.matches){if(this.finishState)this.complete();else this.render();}else{this.lastTime=0;this.raf=requestAnimationFrame(t=>this.tick(t));}});
 }
 start(wines) {
  this.stop();this.pool=wines.filter(w=>w.image).slice(0,12);this.position=0;this.lastTime=0;this.running=true;this.finishState=null;
  this.stage.dataset.state='searching';this.stage.setAttribute('aria-label','Вина каталога. Поиск по вашему снимку продолжается.');
  this.track.replaceChildren();this.cells=Array.from({length:7},()=>{
   const cell=document.createElement('div');cell.className='reel-bottle';cell.setAttribute('aria-hidden','true');
   const image=document.createElement('img');image.alt='';image.decoding='async';image.draggable=false;
   image.onerror=()=>{if(!image.src.endsWith('/assets/no-bottle.svg'))image.src='/assets/no-bottle.svg';};
   cell.append(image);this.track.append(cell);return{cell,image,index:null};
  });
  this.render();if(!this.motion.matches)this.raf=requestAnimationFrame(t=>this.tick(t));
 }
 render() {
  if(!this.running||!this.cells.length)return;
  const step=parseFloat(getComputedStyle(this.stage).getPropertyValue('--reel-step'))||190;
  const center=Math.floor(this.position),finish=this.finishState;
  this.cells.forEach((slot,i)=>{
   const index=center+i-3,offset=index-this.position,distance=Math.abs(offset);
   const isWinner=finish&&index===finish.target;
   let item=isWinner?finish.wine:this.pool[((index%this.pool.length)+this.pool.length)%this.pool.length];
   if(finish&&!isWinner&&item?.slug===finish.wine.slug)item=this.pool.find(w=>w.slug!==finish.wine.slug);
   const src=item?.image||'/assets/no-bottle.svg';
   if(slot.image.getAttribute('src')!==src)slot.image.src=src;
   slot.cell.dataset.slug=item?.slug||'';
   slot.cell.dataset.winner=String(!!isWinner);
   slot.cell.style.transform=`translate3d(${offset*step}px,0,0) scale(${1-Math.min(distance,2)*.13})`;
   slot.cell.style.opacity=String(Math.max(.12,1-distance*.38));
   slot.cell.style.zIndex=String(10-Math.round(distance*2));
  });
 }
 tick(now) {
  if(!this.running)return;
  const delta=this.lastTime?Math.min(now-this.lastTime,48):0;this.lastTime=now;
  const finish=this.finishState;
  if(finish){
   if(!document.hidden){
    finish.elapsed+=delta;
    const t=Math.min(1,finish.elapsed/1700),t2=t*t,t3=t2*t;
    // Hermite easing preserves the incoming loop velocity and reaches zero velocity at the stop.
    this.position=finish.from+(finish.target-finish.from)*(-2*t3+3*t2)+(1700/920)*(t3-2*t2+t);
    if(t===1){this.stage.dataset.state='matched';if(finish.elapsed>=1980){this.complete();return;}}
   }
  }else if(!this.motion.matches&&!this.hovered&&!this.focused&&!document.hidden){this.position+=delta/920;}
  this.render();this.raf=requestAnimationFrame(t=>this.tick(t));
 }
 finish(wine) {
  if(!this.running)return Promise.resolve(false);
  return new Promise(resolve=>{
   this.finishState={wine,from:this.position,target:Math.ceil(this.position)+1,elapsed:0,resolve};
   this.stage.dataset.state='settling';this.stage.setAttribute('aria-label',`Вино из ответа: ${wine.name||wine.slug}`);
   this.render();
   if(this.motion.matches)this.complete();
  });
 }
 complete() {
  const finish=this.finishState;if(!finish)return;
  this.position=finish.target;this.stage.dataset.state='matched';this.render();
  const resolve=finish.resolve;finish.resolve=null;cancelAnimationFrame(this.raf);resolve?.(true);
 }
 stop() {
  this.running=false;cancelAnimationFrame(this.raf);this.finishState?.resolve?.(false);this.finishState=null;this.track.replaceChildren();this.cells=[];
 }
}
window.WineSearchReel=WineSearchReel;
