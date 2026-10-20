'use strict';
// Taste starts with the quiz once, then opens directly on the swipe deck.
window.tasteUI=(()=>{
 const $t=id=>document.getElementById(id);
 const ENDS={body:['Лёгкое','Насыщенное'],tannin:['Мягкое','Терпкое'],sweetness:['Сухое','Сладкое'],acidity:['Округлое','Освежающее'],oak:['Свежее','Выдержанное']};
 const ORDER=['body','tannin','sweetness','acidity','oak'];
 const KIND_WORDS={red:'красных',white:'белых',rose:'розовых',sparkling:'игристых'};
 let quiz=null,passport=null,overviewPassport=null,kind='',viewJob=0,session=0;
 let step=0,picks={},quizBusy=false,quizModal=false,quizError='',guideBusy=false;
 let deck=[],deckBusy=false,deckDone=false,deckError='',saving=false,undoStack=[];
 const sheets=new Map(),seen=new Set();
 const complete=()=>!!(quiz&&passport?.quiz_complete===true&&quiz.questions.every(q=>q.options.some(o=>o.id===passport.quiz?.[q.id])));
 const errorText=e=>/fetch|network|abort|timeout/i.test(e.message)?'Проверьте соединение и попробуйте ещё раз.':e.message;
 const engraving=n=>`<span class="taste-engraving" data-art="${n}" aria-hidden="true"></span>`;
 const backIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12H5m6-6-6 6 6 6"/></svg>';
 const pos=v=>(Math.max(1,Math.min(5,v))-1)/4*100;
 function scale(axis,value,low,high){
  [low,high]=[low||ENDS[axis]?.[0]||'',high||ENDS[axis]?.[1]||''];
  const label=`${low} — ${high}: ${String(value).replace('.',',')} из 5`;
  return `<div class="taste-scale"><span class="taste-end">${esc(low)}</span><span class="taste-track" role="img" aria-label="${esc(label)}"><i class="taste-dot" style="--v:${pos(value)}%"></i></span><span class="taste-end">${esc(high)}</span></div>`;
 }
 function profileScales(axes){
  const by=Object.fromEntries((axes||[]).map(a=>[a.axis,a.value]));
  return ORDER.filter(a=>by[a]!=null).map(a=>scale(a,by[a])).join('');
 }
 function stageLabel(p){return p.stage==='confident'?'Профиль уверенный':p.stage==='learning'?'Профиль уточняется':'Профиль пока пуст';}
 function plural(n,one,few,many){const a=n%100,b=n%10;return n+' '+(a>=11&&a<=14?many:b===1?one:b>=2&&b<=4?few:many);}


 async function loadQuiz(){if(!quiz)quiz=await api('taste/quiz');return quiz;}
 async function show(){
  if(!requireAccount())return;
  const own=++viewJob,identity=session;
  $t('taste-stage').setAttribute('aria-busy','true');
  if(!passport)$t('taste-stage').innerHTML='<p class="taste-loading" role="status">Подбираем вина…</p>';
  try{
   await initPromise;const [p,q]=await Promise.all([api('taste'),api('taste/quiz')]);
   if(own!==viewJob||identity!==session)return;
   passport=p;quiz=q;render();showGuide();
   if(complete()&&!deck.length)await fillDeck();
  }catch(e){if(own===viewJob){$t('taste-stage').innerHTML=`<div class="taste-empty"><p role="alert">${esc(errorText(e))}</p><button class="cellar-primary" data-taste-retry>Повторить</button></div>`;$t('taste-stage').querySelector('button').onclick=show;}}
  finally{if(own===viewJob)$t('taste-stage').removeAttribute('aria-busy');}
 }
 function render(){
  const root=$t('taste-stage');if(!root||!quiz||!passport)return;
  $t('taste-overview-open').hidden=!complete();$t('taste-discuss-open').hidden=!complete();
  if(!complete()){
   root.className='taste-workspace is-onboarding';
   if(!root.querySelector('#taste-onboarding')){picks={...passport.quiz,...picks};step=quiz.questions.findIndex(q=>!picks[q.id]);if(step<0)step=0;root.innerHTML='<div id="taste-onboarding" class="taste-quiz-inline"></div>';}
   if(!quizModal)renderQuiz();
  }else{
   root.className='taste-workspace';
   if(!root.querySelector('#taste-swipe'))root.innerHTML='<div id="taste-swipe" class="taste-deck" aria-label="Подборка вин"></div>';
   renderSwipe();
  }
 }
 function showGuide(){
  if(currentView!=='taste'||passport?.guide_seen||document.querySelector('dialog[open]'))return;
  const guide=$t('taste-guide');
  guide.querySelector('.taste-guide-lead').textContent=complete()?'Отмечайте вина, которые вам нравятся. Каждая оценка помогает уточнить подборку.':'Начните с шести вопросов, затем отмечайте вина, которые вам нравятся.';
  guide.querySelector('.taste-guide-note').textContent=complete()?'Обе кнопки — над подборкой вин.':'Обе кнопки — над подборкой вин после анкеты.';
  guide.querySelector('[data-guide-continue]').textContent=complete()?'Понятно':'Начать';
  guide.querySelector('.taste-guide-error').textContent='';openDialog('taste-guide');
 }
 async function dismissGuide(){
  if(guideBusy)return;guideBusy=true;const identity=session,guide=$t('taste-guide');
  guide.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{await api('taste/guide',{});if(identity!==session)return;passport.guide_seen=true;closeDialog(guide);}
  catch(e){if(identity===session)guide.querySelector('.taste-guide-error').textContent=errorText(e);}
  finally{if(identity===session){guideBusy=false;guide.querySelectorAll('button').forEach(b=>b.disabled=false);}}
 }
 function discuss(){
  if(saving||quizBusy){notice('Дождитесь сохранения оценки.');return;}
  if($t('taste-overview').open)closeDialog($t('taste-overview'));
  askAboutTaste();
 }
 async function openQuiz(){
  if(!requireAccount())return;
  try{await initPromise;await loadQuiz();passport=await api('taste');}catch(e){notice(errorText(e));return;}
  const overview=$t('taste-overview');if(overview.open)closeDialog(overview);
  picks={...passport.quiz};step=0;quizError='';quizModal=true;
  renderQuiz();openDialog('taste-quiz');
 }
 function quizRoot(){return $t(quizModal?'taste-quiz':'taste-onboarding');}
 function renderQuiz(focusHeading=false){
  const root=quizRoot();if(!root||!quiz)return;
  const q=quiz.questions[step],chosen=picks[q.id],total=quiz.questions.length;
  const insight=q.options.find(o=>o.id===chosen)?.insight||'';
  const scrollTop=focusHeading?0:(root.querySelector('.quiz-composition')?.scrollTop||0);
  root.innerHTML=`<div class="quiz-top"><button class="quiz-round" data-quiz-back aria-label="Предыдущий вопрос" ${step===0||quizBusy?'disabled':''}>${backIcon}</button><span class="quiz-step">${step+1}<span> / ${total}</span></span>${quizModal?`<button class="quiz-round" data-quiz-close aria-label="Закрыть анкету" ${quizBusy?'disabled':''}>${icons('close')}</button>`:'<span class="quiz-top-note">Знакомимся со вкусом</span>'}</div><div class="quiz-progress" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${step+1}" aria-label="Вопрос ${step+1} из ${total}"><span style="width:${(step+1)/total*100}%"></span></div><div class="quiz-composition"><div class="quiz-art">${engraving(step)}</div><div class="quiz-body"><h2 id="${quizModal?'modal':'inline'}-quiz-title" tabindex="-1">${esc(q.title)}</h2><div class="quiz-options" role="radiogroup" aria-labelledby="${quizModal?'modal':'inline'}-quiz-title">${q.options.map((o,i)=>`<button class="quiz-option${o.id===chosen?' is-chosen':''}" role="radio" aria-checked="${o.id===chosen}" tabindex="${o.id===chosen||(!chosen&&i===0)?0:-1}" data-option="${esc(o.id)}" ${quizBusy?'disabled':''}><span class="quiz-label">${esc(o.label)}</span><span class="quiz-radio" aria-hidden="true"></span></button>`).join('')}</div></div></div><div class="quiz-footer"><p class="quiz-insight" aria-live="polite" aria-atomic="true">${esc(insight)}</p><p class="quiz-error" role="alert">${esc(quizError)}</p><button class="quiz-next" data-quiz-next ${!chosen||quizBusy?'disabled':''}>${quizBusy?'Сохраняем…':step===total-1?'Перейти к винам':'Дальше'}${icons('arrow')}</button></div>`;
  root.querySelector('.quiz-composition').scrollTop=scrollTop;
  const select=b=>{picks[q.id]=b.dataset.option;quizError='';renderQuiz();quizRoot().querySelector(`[data-option="${CSS.escape(b.dataset.option)}"]`)?.focus({preventScroll:true});};
  root.querySelectorAll('[data-option]').forEach(b=>{b.onclick=()=>select(b);b.onkeydown=e=>{if(!['ArrowRight','ArrowLeft','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();const buttons=[...root.querySelectorAll('[data-option]')],i=buttons.indexOf(b),offset=['ArrowRight','ArrowDown'].includes(e.key)?1:-1;select(buttons[(i+offset+buttons.length)%buttons.length]);};});
  root.querySelector('[data-quiz-back]').onclick=()=>{if(step&&!quizBusy){step--;quizError='';renderQuiz(true);}};
  root.querySelector('[data-quiz-close]')?.addEventListener('click',()=>{if(!quizBusy)closeDialog(root);});
  root.querySelector('[data-quiz-next]').onclick=advance;
  if(focusHeading)root.querySelector('h2').focus({preventScroll:true});
 }
 async function advance(){
  if(quizBusy||!picks[quiz.questions[step].id])return;
  if(step<quiz.questions.length-1){step++;renderQuiz(true);return;}
  if(!quiz.questions.every(q=>q.options.some(o=>o.id===picks[q.id])))return;
  quizBusy=true;quizError='';renderQuiz();const identity=session;
  try{
   const res=await api('taste/quiz',{answers:{...picks}});if(identity!==session)return;
   passport=res.passport;quiz.answers={...picks};deck=[];seen.clear();undoStack=[];deckDone=false;deckError='';invalidate();
   if(quizModal)closeDialog($t('taste-quiz'));
   quizModal=false;render();refreshRecommendations();
   if(currentView==='taste')await fillDeck();else showView('taste');
  }catch(e){if(identity===session){quizError=errorText(e);}}
  finally{if(identity===session){quizBusy=false;if(!complete()||quizModal)renderQuiz();}}
 }
 async function openOverview(){
  if(!complete())return;
  kind='';overviewPassport=passport;renderOverview();openDialog('taste-overview');await choose('');
 }
 async function choose(value){
  kind=value;const own=++viewJob;
  try{const p=await api('taste'+(kind?'?kind='+kind:''));if(own!==viewJob)return;overviewPassport=p;renderOverview();$t('taste-tabs').querySelector(`[data-taste-kind="${CSS.escape(value)}"]`)?.focus({preventScroll:true});if(!kind&&p.stage!=='new')loadPicks(own);}
  catch(e){notice(errorText(e));}
 }
 function renderOverview(){
  const tabs=$t('taste-tabs'),root=$t('taste-content');if(!tabs||!root)return;
  const list=[{id:'',label:'Обзор'},...((overviewPassport||passport)?.tabs||[{id:'red',label:'Красное'},{id:'white',label:'Белое'},{id:'rose',label:'Розовое'},{id:'sparkling',label:'Игристое'}])];
  tabs.innerHTML=list.map(t=>`<button data-taste-kind="${t.id}" aria-pressed="${t.id===kind}" class="${t.id===kind?'active':''}">${esc(t.label)}${t.count?` <span>${t.count}</span>`:''}</button>`).join('');
  tabs.querySelectorAll('[data-taste-kind]').forEach(b=>b.onclick=()=>choose(b.dataset.tasteKind));
  if(!passport){root.innerHTML='<p class="shelf-loading" role="status">Собираем ваш вкус…</p>';return;}
  const p=overviewPassport||passport;
  if(p.stage==='new'){root.innerHTML=`<div class="taste-empty"><h2>Пока мало данных о ${KIND_WORDS[kind]} винах</h2><p>Оцените пару ${KIND_WORDS[kind]} вин или полистайте подборку — профиль появится здесь.</p><button class="cellar-primary" data-open-swipe>Листать вина</button></div>`;bindActions(root);return;}
  const facts=[['Стили',p.styles],['Сорта',p.grapes],['Регионы',p.regions]].filter(([,v])=>v?.length);
  root.innerHTML=`<div class="taste-layout"><article class="taste-hero"><p class="taste-stage"><span class="taste-stage-dot" data-stage="${p.stage}"></span>${stageLabel(p)}</p><h2>${esc(p.summary||'Ваш вкус')}</h2><p class="taste-evidence">${[p.liked_count?plural(p.liked_count,'вино нравится','вина нравятся','вин нравятся'):'',p.swiped&&!kind?plural(p.swiped,'свайп','свайпа','свайпов'):'',p.onboarding?'анкета пройдена':''].filter(Boolean).join(' · ')}</p><div class="taste-scales">${profileScales(p.axes)}</div></article><div class="taste-side">${p.aromas.length?`<section class="taste-card"><h3>Любимые ноты</h3><ul class="taste-aromas">${p.aromas.map(a=>`<li><span>${esc(a.name)}</span><i style="--share:${Math.round(a.share*100)}%"></i></li>`).join('')}</ul></section>`:''}${facts.length?`<section class="taste-card taste-facts">${facts.map(([title,values])=>`<div><h3>${title}</h3><p>${values.map(v=>esc(v.name)).join(', ')}</p></div>`).join('')}</section>`:''}<section class="taste-card taste-actions"><button class="taste-action" data-discuss-taste><span class="taste-action-icon" aria-hidden="true">${engraving(0)}</span><span><b>Обсудить с сомелье</b><small>О вашем вкусе и новых открытиях</small></span>${icons('chevron')}</button><button class="taste-action" data-open-swipe><span class="taste-action-icon" aria-hidden="true">${engraving(0)}</span><span><b>Листать вина</b><small>Вернуться к подборке </small></span>${icons('chevron')}</button><button class="taste-action" data-open-quiz><span class="taste-action-icon" aria-hidden="true">${engraving(5)}</span><span><b>${Object.keys(p.quiz||{}).length?'Пройти анкету заново':'Пройти анкету'}</b><small>${Object.keys(p.quiz||{}).length?`Ответов: ${Object.keys(p.quiz).length} из 6`:'Шесть вопросов о кофе, шоколаде и вине'}</small></span>${icons('chevron')}</button></section></div></div>${kind?'':'<section class="taste-picks" aria-labelledby="taste-picks-title" hidden><div class="section-heading"><h2 id="taste-picks-title">Подобрано под ваш вкус</h2></div><div class="wine-grid" id="taste-picks"></div></section>'}`;
  bindActions(root);
 }

 function bindActions(root){
  root.querySelectorAll('[data-discuss-taste]').forEach(b=>b.onclick=discuss);
  root.querySelectorAll('[data-open-swipe]').forEach(b=>b.onclick=openSwipe);
  root.querySelectorAll('[data-open-quiz]').forEach(b=>b.onclick=openQuiz);
 }
 async function loadPicks(own){
  try{const data=await api('recommendations?limit=4');if(own!==viewJob||!$t('taste-overview').open)return;
   const grid=$t('taste-picks');if(!grid||!data.items.length)return;
   grid.replaceChildren();for(const w of data.items){const card=miniWine({...w,...getPreference(w.slug)},{showOpenLabel:false});card.querySelector('.mini-wine-open').onclick=()=>{closeDialog($t('taste-overview'));openWine(w);};grid.append(card);}grid.closest('section').hidden=false;
  }catch{}
 }
 function sheet(w){
  const t=w.taste||sheets.get(w.slug);
  if(w.taste)sheets.set(w.slug,w.taste);

  if(!t)return `<section class="taste-sheet is-loading" data-taste-slug="${esc(w.slug)}" aria-label="Вкус вина"></section>`;
  const m=t.match;
  const scales=t.scales.map(s=>scale(s.axis,s.value,s.low,s.high)).join('');
  const chips=[...t.aromas.map(a=>a.name),...t.notes].map(x=>`<li>${esc(x)}</li>`).join('');
  const match=!productData.account?.authenticated?'':m?`<div class="taste-match"><span class="taste-match-ring" style="--p:${m.percent}" aria-hidden="true"><b>${m.percent}%</b></span><div><p class="taste-match-title">Совпадение с вашим вкусом</p><p>${esc(m.note)}</p></div></div>`
   :`<div class="taste-match taste-match-empty"><span class="taste-match-ring" aria-hidden="true">${icons('spark')}</span><div><p class="taste-match-title">Подойдёт ли вам это вино?</p><p>Ответьте на шесть вопросов — и мы покажем совпадение с вашим вкусом.</p><button class="text-button" data-open-quiz>Пройти анкету ${icons('arrow')}</button></div></div>`;
  return `<section class="taste-sheet" aria-labelledby="taste-sheet-title"><div class="taste-sheet-head"><h2 id="taste-sheet-title">Вкус вина</h2><small>${esc(t.source)}</small></div><div class="taste-sheet-grid"><div class="taste-scales">${scales}</div><div class="taste-sheet-side">${match}${chips?`<div><h3>Ноты во вкусе и аромате</h3><ul class="taste-chips">${chips}</ul></div>`:''}${t.occasions.length?`<div><h3>Когда открыть</h3><p class="taste-occasions">${esc(t.occasions.join(', '))}</p></div>`:''}</div></div></section>`;
 }
 async function bindSheet(root){
  root.querySelectorAll('.taste-sheet [data-open-quiz]').forEach(b=>b.onclick=()=>openQuiz());
  const holder=root.querySelector('.taste-sheet.is-loading');if(!holder)return;
  const slug=holder.dataset.tasteSlug;
  try{const fresh=await api('wines/'+encodeURIComponent(slug));if(!fresh.taste)return;sheets.set(slug,fresh.taste);
   const current=root.querySelector(`.taste-sheet.is-loading[data-taste-slug="${CSS.escape(slug)}"]`);if(!current)return;
   const tmp=document.createElement('div');tmp.innerHTML=sheet({slug,taste:fresh.taste});current.replaceWith(tmp.firstElementChild);bindSheet(root);
  }catch{holder.remove();}
 }

 function invalidate(){sheets.clear();}
 function reset(){session++;viewJob++;quiz=null;passport=null;overviewPassport=null;picks={};step=0;quizModal=false;quizBusy=false;guideBusy=false;deck=[];seen.clear();undoStack=[];deckDone=false;deckBusy=false;saving=false;invalidate();['taste-quiz','taste-overview','taste-guide'].forEach(id=>{if($t(id).open)closeDialog($t(id));});$t('taste-stage').replaceChildren();$t('taste-overview-open').hidden=true;$t('taste-discuss-open').hidden=true;$t('taste-guide').querySelectorAll('button').forEach(b=>b.disabled=false);}
 async function openSwipe(){
  if($t('taste-overview').open)closeDialog($t('taste-overview'));
  if($t('taste-quiz').open)closeDialog($t('taste-quiz'));
  showView('taste');
 }
 async function fillDeck(){
  if(deckBusy||deckDone||!complete())return;deckBusy=true;deckError='';renderSwipe();const identity=session;
  try{const data=await api('taste/swipe?limit=10');if(identity!==session)return;
   const have=new Set(deck.map(w=>w.slug)),fresh=data.items.filter(w=>!have.has(w.slug)&&!seen.has(w.slug));
   fresh.forEach(remember);deck.push(...fresh);if(!fresh.length)deckDone=true;
  }catch(e){if(identity===session)deckError=errorText(e);}
  finally{if(identity===session){deckBusy=false;renderSwipe();}}
 }
 function cardMarkup(w){
  const t=w.taste||{scales:[],aromas:[],notes:[]},m=t.match;
  return `<article class="swipe-card" data-slug="${esc(w.slug)}" aria-label="${esc(w.name)}"><span class="swipe-stamp swipe-like" aria-hidden="true">Нравится</span><span class="swipe-stamp swipe-nope" aria-hidden="true">Не моё</span><div class="swipe-visual">${img(w)}${m?`<span class="swipe-match"><b>${m.percent}%</b> ваш вкус</span>`:''}</div><div class="swipe-body"><small class="swipe-winery">${esc(w.winery)}</small><h2><button data-swipe-wine>${esc(w.name)} ${icons('arrow')}</button></h2><p class="swipe-meta">${esc([w.category,w.region].filter(Boolean).join(' · '))}</p><div class="taste-scales swipe-scales">${t.scales.map(s=>scale(s.axis,s.value,s.low,s.high)).join('')}</div>${t.aromas.length?`<ul class="taste-chips">${t.aromas.slice(0,3).map(a=>`<li>${esc(a.name)}</li>`).join('')}</ul>`:''}</div><div class="swipe-rate" hidden><p>Как вам это вино?</p><div class="swipe-glasses" role="group" aria-label="Оценка от 1 до 5">${[1,2,3,4,5].map(n=>`<button data-rate="${n}" aria-label="${n} из 5"><span class="swipe-glass"><img src="/assets/portal-card/public-rating-empty.svg" alt="" width="40" height="40"><img class="swipe-glass-fill" src="/assets/portal-card/public-rating-wine-in-glass.svg" alt="" width="40" height="40"></span></button>`).join('')}</div><button class="text-button" data-rate-cancel>Отмена</button></div></article>`;
 }
 function renderSwipe(){
  const root=$t('taste-swipe');if(!root||!complete())return;
  if(!root.querySelector('.swipe-stack')){
   root.innerHTML=`<div class="swipe-toolbar"></div><div class="swipe-stack"></div><div class="swipe-controls"><button class="swipe-button swipe-button-nope" data-verdict="nope" aria-label="Не моё">${icons('close')}<span>Не моё</span></button><button class="swipe-button swipe-button-tasted" data-tasted aria-label="Пробовал — оценить">${icons('glass')}<span>Пробовал</span></button><button class="swipe-button swipe-button-like" data-verdict="like" aria-label="Нравится">${icons('heart')}<span>Нравится</span></button></div><p class="swipe-status" role="status" aria-live="polite"></p>`;
   root.querySelectorAll('[data-verdict]').forEach(b=>b.onclick=()=>commit(b.dataset.verdict));
   root.querySelector('[data-tasted]').onclick=openRating;
  }
  const stack=root.querySelector('.swipe-stack'),shown=deck.slice(0,3);
  stack.querySelectorAll('.swipe-card:not(.is-leaving)').forEach(el=>{if(!shown.some(w=>w.slug===el.dataset.slug))el.remove();});
  shown.slice().reverse().forEach((w,k)=>{
   let el=stack.querySelector(`.swipe-card[data-slug="${CSS.escape(w.slug)}"]:not(.is-leaving)`);
   if(!el){const tmp=document.createElement('div');tmp.innerHTML=cardMarkup(w);el=tmp.firstElementChild;handleBrokenImages(el);stack.prepend(el);}
   el.dataset.depth=shown.length-1-k;
  });
  const top=stack.querySelector('.swipe-card[data-depth="0"]:not(.is-leaving)');
  stack.querySelectorAll('.swipe-card').forEach(el=>el.inert=el!==top||saving);
  if(top)drag(top);
  stack.querySelector('.swipe-end')?.remove();
  if(!deck.length){stack.insertAdjacentHTML('beforeend',`<div class="swipe-end">${engraving(0)}<h2>${deckBusy?'Подбираем вина…':deckError?'Не удалось загрузить вина':'Вы всё посмотрели'}</h2>${!deckBusy?`<button class="cellar-primary" data-deck-action>${deckError?'Повторить':'Посмотреть обзор'}</button>`:''}</div>`);stack.querySelector('[data-deck-action]')?.addEventListener('click',()=>deckError?fillDeck():openOverview());}
  root.querySelectorAll('.swipe-controls button').forEach(b=>b.disabled=!top||saving);
  root.querySelector('.swipe-status').textContent=saving?'Сохраняем…':deckError;
  root.setAttribute('aria-busy',String(saving));
 }
 function drag(card){
  if(card.dataset.bound)return;card.dataset.bound='1';
  bindTasteSwipe(card,{
   canInteract:()=>!saving&&currentView==='taste'&&card.dataset.depth==='0'&&!card.inert&&!card.classList.contains('is-rating'),
   onSwipe:verdict=>commit(verdict),
   onOpen:()=>{const w=deck.find(x=>x.slug===card.dataset.slug);if(w)openWine(w);}
  });
 }
 function flyOut(card,direction){
  if(!card)return Promise.resolve();
  card.classList.add('is-submitting');card.classList.remove('is-dragging');card.inert=true;
  // Flush the released position before starting a single continuous exit transition.
  void card.offsetWidth;
  const x=direction==='up'?0:direction==='right'?1:-1;
  card.style.transform=`translate3d(${x*115}%,${direction==='up'?'-100%':'-4%'},0) rotate(${x*8}deg)`;
  card.style.opacity='0';
  return new Promise(resolve=>setTimeout(resolve,matchMedia('(prefers-reduced-motion: reduce)').matches?0:240));
 }
 function returnCard(card){
  if(!card)return;
  card.classList.remove('is-submitting');
  ['transform','opacity','--like','--nope'].forEach(name=>card.style.removeProperty(name));
 }
 function syncPreference(w){remember(w);productData.preferences[w.slug]={rating:w.rating,favorite:w.favorite};productData.collection=productData.collection.filter(x=>x.slug!==w.slug);if(w.rating||w.favorite)productData.collection.unshift(w);store.set('wine-product',productData);}
 async function commit(verdict,rating=0){
  const w=deck[0];if(!w||saving)return;const identity=session,previous={...getPreference(w.slug)};saving=true;deckError='';
  const card=$t('taste-swipe')?.querySelector('.swipe-card[data-depth="0"]:not(.is-leaving)');
  const flight=flyOut(card,rating?'up':verdict==='like'?'right':'left');renderSwipe();
  try{
   let p;
   if(rating){const fresh=await api('preferences/'+encodeURIComponent(w.slug),{rating});if(identity!==session)return;syncPreference(fresh);p=await api('taste');}
   else{const res=await api('taste/swipe',{slug:w.slug,verdict});if(identity!==session)return;p=res.passport;if(verdict==='like')syncPreference({...w,...previous,favorite:true});}
   if(identity!==session)return;
   await flight;if(identity!==session)return;passport=p;
   deck.shift();seen.add(w.slug);undoStack.push({wine:w,verdict,rating,previous});if(undoStack.length>20)undoStack.shift();
   card?.remove();invalidate();refreshRecommendations();
  }catch(e){await flight;if(identity===session){returnCard(card);deckError='Не сохранилось. '+errorText(e);}}
  finally{if(identity===session){saving=false;renderSwipe();if(deck.length<4&&!deckError)fillDeck();}}
 }
 async function undo(){
  const last=undoStack.at(-1);if(!last||saving)return;saving=true;deckError='';renderSwipe();const identity=session;
  try{
   let p;
   if(last.rating){const fresh=await api('preferences/'+encodeURIComponent(last.wine.slug),{rating:last.previous.rating||0});if(identity!==session)return;syncPreference(fresh);p=await api('taste');}
   else{const res=await api('taste/swipe',{slug:last.wine.slug,verdict:'undo'});if(identity!==session)return;p=res.passport;if(last.verdict==='like')syncPreference({...last.wine,...last.previous});}
   if(identity!==session)return;passport=p;undoStack.pop();seen.delete(last.wine.slug);deck=deck.filter(w=>w.slug!==last.wine.slug);deck.unshift(last.wine);deckDone=false;invalidate();refreshRecommendations();
  }catch(e){if(identity===session)deckError='Не удалось отменить. '+errorText(e);}
  finally{if(identity===session){saving=false;renderSwipe();}}
 }
 function openRating(){
  const card=$t('taste-swipe')?.querySelector('.swipe-card[data-depth="0"]:not(.is-leaving)');if(!card||saving)return;
  const panel=card.querySelector('.swipe-rate');panel.hidden=false;card.classList.add('is-rating');
  panel.querySelectorAll('[data-rate]').forEach(b=>{b.onclick=()=>commit('rated',Number(b.dataset.rate));b.onmouseenter=b.onfocus=()=>panel.querySelectorAll('[data-rate]').forEach(x=>x.classList.toggle('is-lit',Number(x.dataset.rate)<=Number(b.dataset.rate)));});
  panel.querySelector('[data-rate-cancel]').onclick=()=>{panel.hidden=true;card.classList.remove('is-rating');$t('taste-swipe').querySelector('[data-tasted]').focus({preventScroll:true});};
  panel.querySelector('[data-rate]').focus({preventScroll:true});
 }
 document.addEventListener('keydown',e=>{
  if(currentView!=='taste'||document.querySelector('dialog[open]')||e.target.closest('input,textarea,select,[contenteditable]')||!complete())return;
  if($t('taste-swipe')?.querySelector('.swipe-card.is-rating:not(.is-leaving)'))return;
  if(e.key==='ArrowRight'){e.preventDefault();commit('like');}else if(e.key==='ArrowLeft'){e.preventDefault();commit('nope');}
 });
 $t('taste-overview-open').onclick=openOverview;
 $t('taste-discuss-open').onclick=discuss;
 $t('taste-guide').querySelectorAll('button').forEach(b=>b.onclick=dismissGuide);
 $t('taste-guide').addEventListener('cancel',e=>{e.preventDefault();dismissGuide();});
 $t('taste-overview').querySelector('[data-overview-close]').onclick=()=>closeDialog($t('taste-overview'));
 $t('taste-quiz').addEventListener('cancel',e=>{if(quizBusy)e.preventDefault();});
 $t('taste-quiz').addEventListener('close',()=>{quizModal=false;quizError='';if(currentView==='taste')render();});
 document.querySelectorAll('[data-open-taste-quiz]').forEach(b=>b.addEventListener('click',()=>openQuiz()));
 return {show,render,openQuiz,openSwipe,sheet,bindSheet,invalidate,reset};
})();
