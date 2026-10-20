'use strict';
// Taste profile: the six-question quiz, the «Мой вкус» screen, the wine swipe deck and the taste
// sheet on a wine card. Contract: docs/recsys_api.md («Анкета», «Свайп», «Вкус вина»).
window.tasteUI=(()=>{
 const $t=id=>document.getElementById(id);
 const ENDS={body:['Лёгкое','Насыщенное'],tannin:['Мягкое','Терпкое'],sweetness:['Сухое','Сладкое'],acidity:['Округлое','Освежающее'],oak:['Свежее','Выдержанное']};
 const ORDER=['body','tannin','sweetness','acidity','oak'];
 const KIND_WORDS={red:'красных',white:'белых',rose:'розовых',sparkling:'игристых'};
 let quiz=null,step=0,picks={},quizResult=null,quizBusy=false;
 let passport=null,kind='',viewJob=0;
 let deck=[],deckBusy=false,deckDone=false,undoStack=[],deckSummary=null;
 const sheets=new Map();

 // ---- shared pieces ------------------------------------------------------------------
 const pos=v=>(Math.max(1,Math.min(5,v))-1)/4*100;
 function scale(axis,value,yours,low,high){
  [low,high]=[low||ENDS[axis]?.[0]||'',high||ENDS[axis]?.[1]||''];
  const label=`${low} — ${high}: ${String(value).replace('.',',')} из 5${yours!=null?'; ваш вкус — '+String(yours).replace('.',','):''}`;
  return `<div class="taste-scale"><span class="taste-end">${esc(low)}</span><span class="taste-track" role="img" aria-label="${esc(label)}">${yours!=null?`<i class="taste-you" style="--v:${pos(yours)}%"></i>`:''}<i class="taste-dot" style="--v:${pos(value)}%"></i></span><span class="taste-end">${esc(high)}</span></div>`;
 }
 function profileScales(axes){
  const by=Object.fromEntries((axes||[]).map(a=>[a.axis,a.value]));
  return ORDER.filter(a=>by[a]!=null).map(a=>scale(a,by[a])).join('');
 }
 function stageLabel(p){return p.stage==='confident'?'Профиль уверенный':p.stage==='learning'?'Профиль уточняется':'Профиль пока пуст';}
 function plural(n,one,few,many){const a=n%100,b=n%10;return n+' '+(a>=11&&a<=14?many:b===1?one:b>=2&&b<=4?few:many);}

 // Quiz pictures: simple line drawings on a tinted circle, in the portal's ink and wine colours.
 function quizIcon(name){
  const ink='stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"';
  const glass=fill=>`<path d="M15.4 15h17.2l.6 3.5a9.2 9.2 0 0 1-18.4 0Z" fill="${fill}"/><g ${ink}><path d="M16 8h16l1.2 10.5a9.2 9.2 0 0 1-18.4 0Z"/><path d="M24 27.7V39M18 39h12"/></g>`;
  const cup=level=>`${level?`<path d="M12.5 ${level}h18v${27-level}a6.5 6.5 0 0 1-6.5 6.5h-5a6.5 6.5 0 0 1-6.5-6.5Z" fill="${level<20?'#3b2a22':'#b98a62'}"/>`:''}<g ${ink}><path d="M11 16h21v11a8 8 0 0 1-8 8h-5a8 8 0 0 1-8-8Z"/><path d="M32 19h3a4 4 0 0 1 0 8h-3"/>${level?'<path d="M18 6c-1.5 2 1.5 3 0 5M24 6c-1.5 2 1.5 3 0 5"/>':''}</g>`;
  const bar=fill=>`<rect x="14" y="8" width="20" height="32" rx="2" fill="${fill}"/><g ${ink}><rect x="14" y="8" width="20" height="32" rx="2"/><path d="M14 16h20M14 24h20M14 32h20M24 8v32" opacity=".55"/></g>`;
  const juice=fill=>`<path d="M15.3 18h17.4l-1.8 21.2h-13.8Z" fill="${fill}"/><g ${ink}><path d="M14 10h20l-2.5 30h-15Z"/><path d="M29 3l-3.5 15"/></g>`;
  const tea=cubes=>`<path d="M11.6 24h18.8v3a7.4 7.4 0 0 1-7.4 7.4h-4a7.4 7.4 0 0 1-7.4-7.4Z" fill="#c98a4f"/><g ${ink}><path d="M10 22h22v5a9 9 0 0 1-9 9h-4a9 9 0 0 1-9-9Z"/><path d="M32 24h2.5a3.5 3.5 0 0 1 0 7H31M8 40h28"/></g>${cubes>0?`<rect x="${cubes>1?13:18}" y="10" width="7" height="7" rx="1.4" fill="#fffdf8" ${ink}/>`:''}${cubes>1?'<rect x="23" y="7" width="7" height="7" rx="1.4" fill="#fffdf8" stroke="currentColor" stroke-width="1.8" transform="rotate(14 26.5 10.5)"/>':''}`;
  const anise=[0,1,2,3,4,5,6,7].map(k=>`<ellipse cx="24" cy="15.5" rx="2.8" ry="6.5" transform="rotate(${k*45} 24 24)" fill="#9a5631"/>`).join('');
  const petals=[0,1,2,3,4].map(k=>`<circle cx="24" cy="12" r="5.2" transform="rotate(${k*72} 24 19)" fill="#f2d27a"/>`).join('');
  const art={
   'glass-red':glass('#8f3d42'),'glass-white':glass('#d9a93c'),
   'glass-both':`<defs><linearGradient id="quiz-split"><stop offset=".5" stop-color="#8f3d42"/><stop offset=".5" stop-color="#d9a93c"/></linearGradient></defs>${glass('url(#quiz-split)')}`,
   'cup-black':cup(18),'cup-milk':cup(24),'cup-empty':cup(0),
   'choc-dark':bar('#5b3427'),'choc-milk':bar('#a86d45'),'choc-white':bar('#f4ead6'),
   'juice-citrus':juice('#f19b74')+`<circle cx="34" cy="11" r="5" fill="#f6d36b" ${ink}/>`,'juice-apple':juice('#e9c45d'),'juice-peach':juice('#f3b07c'),
   'tea-none':tea(0),'tea-one':tea(1),'tea-more':tea(2),
   'aroma-berries':`<circle cx="18" cy="32" r="6" fill="#8f3d42"/><circle cx="30.5" cy="34" r="6" fill="#b14d53"/><g ${ink}><path d="M18 26c2-8 6-13 12-16M30.5 28c-1-7-1-12 0-18"/></g><path d="M30 10c4-3 9-2 11 1-4 3-8 3-11-1Z" fill="#7d8c4c"/>`,
   'aroma-flowers':`${petals}<circle cx="24" cy="19" r="3.6" fill="#d9952f"/><g ${ink}><path d="M24 28v13M24 35c3-4 7-5 10-4-2 4-6 5-10 4"/></g>`,
   'aroma-spice':`${anise}<circle cx="24" cy="24" r="3.2" fill="#f1dcc0" stroke="#5b3427" stroke-width="1.4"/>`,
   'aroma-bubbles':`<path d="M19.7 12h8.6l-.8 8a3.5 3.5 0 0 1-7 0Z" fill="#ecd27a"/><g ${ink}><path d="M19 6h10l-1 14a4 4 0 0 1-8 0Z"/><path d="M24 24v14M19 38h10"/></g><g fill="none" stroke="#d9a93c" stroke-width="1.6"><circle cx="34" cy="10" r="2.2"/><circle cx="37" cy="17" r="1.5"/><circle cx="32" cy="21" r="1.2"/></g>`
  };
  return `<span class="quiz-icon" data-icon="${esc(name)}"><svg viewBox="0 0 48 48" aria-hidden="true">${art[name]||art['glass-both']}</svg></span>`;
 }

 // ---- quiz ---------------------------------------------------------------------------
 async function loadQuiz(){if(!quiz)quiz=await api('taste/quiz');return quiz;}
 async function openQuiz(first=null){
  try{await initPromise;await loadQuiz();}catch(e){notice(e.message);return;}
  picks={...(passport?.quiz||quiz.answers||{})};step=0;quizResult=null;
  if(first){picks[quiz.questions[0].id]=first;}
  openDialog('taste-quiz');renderQuiz();
 }
 function renderQuiz(){
  const root=$t('taste-quiz'),total=quiz.questions.length;
  if(quizResult){renderQuizDone(root);return;}
  const q=quiz.questions[step],chosen=picks[q.id];
  root.innerHTML=`<div class="quiz-top"><button class="quiz-round" data-quiz-back aria-label="${step?'Предыдущий вопрос':'Закрыть анкету'}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12H5m6-6-6 6 6 6"/></svg></button><span class="quiz-step" aria-live="polite">${step+1} из ${total}</span><button class="quiz-round" data-quiz-close aria-label="Закрыть анкету">${icons('close')}</button></div><div class="quiz-progress" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${step+1}" aria-label="Вопрос ${step+1} из ${total}"><span style="width:${(step+1)/total*100}%"></span></div><div class="quiz-body"><h2 id="taste-quiz-title">${esc(q.title)}</h2><div class="quiz-options" role="radiogroup" aria-labelledby="taste-quiz-title">${q.options.map(o=>`<div class="quiz-option${o.id===chosen?' is-chosen':''}"><button role="radio" aria-checked="${o.id===chosen}" data-option="${esc(o.id)}">${quizIcon(o.icon)}<span class="quiz-label">${esc(o.label)}</span><span class="quiz-radio" aria-hidden="true"></span></button>${o.id===chosen?`<p class="quiz-insight"><b>Это говорит нам о следующем:</b> ${esc(o.insight)}</p>`:''}</div>`).join('')}</div></div><div class="quiz-bottom"><button class="quiz-next" data-quiz-next ${chosen?'':'disabled'}>${step===total-1?'Готово':'Дальше'}</button><button class="quiz-skip" data-quiz-skip>Пропустить вопрос</button></div>`;
  root.setAttribute('aria-labelledby','taste-quiz-title');
  root.querySelectorAll('[data-option]').forEach(b=>b.onclick=()=>{picks[q.id]=b.dataset.option;renderQuiz();root.querySelector(`[data-option="${CSS.escape(b.dataset.option)}"]`)?.focus({preventScroll:true});});
  root.querySelector('[data-quiz-back]').onclick=()=>{if(step){step--;renderQuiz();}else closeDialog(root);};
  root.querySelector('[data-quiz-close]').onclick=()=>closeDialog(root);
  root.querySelector('[data-quiz-next]').onclick=()=>advance();
  root.querySelector('[data-quiz-skip]').onclick=()=>{delete picks[q.id];advance();};
 }
 async function advance(){
  if(step<quiz.questions.length-1){step++;renderQuiz();$t('taste-quiz').querySelector('[data-option]')?.focus({preventScroll:true});return;}
  if(quizBusy)return;
  const answers=Object.fromEntries(Object.entries(picks).filter(([q,o])=>quiz.questions.some(x=>x.id===q&&x.options.some(y=>y.id===o))));
  if(!Object.keys(answers).length){quizResult={empty:true};renderQuiz();return;}
  quizBusy=true;$t('taste-quiz').querySelectorAll('button').forEach(b=>b.disabled=true);
  try{const res=await api('taste/quiz',{answers});quiz.answers=answers;passport=res.passport;quizResult=res;invalidate();refreshRecommendations();renderQuiz();if(currentView==='taste')render();}
  catch(e){notice(e.message);renderQuiz();}
  finally{quizBusy=false;}
 }
 function renderQuizDone(root){
  const p=quizResult.passport,items=(quizResult.recommendations?.items||[]).slice(0,3);
  root.innerHTML=quizResult.empty?`<div class="quiz-top"><span></span><span class="quiz-step">Анкета</span><button class="quiz-round" data-quiz-close aria-label="Закрыть">${icons('close')}</button></div><div class="quiz-body quiz-done"><h2>Профиль пока пуст</h2><p class="quiz-lead">Без ответов вкус не угадать. Можно вернуться к анкете или просто полистать вина — каждый свайп тоже рассказывает о вкусе.</p></div><div class="quiz-bottom"><button class="quiz-next" data-quiz-swipe>Листать вина</button><button class="quiz-skip" data-quiz-restart>Пройти анкету</button></div>`
  :`<div class="quiz-top"><span></span><span class="quiz-step">Готово</span><button class="quiz-round" data-quiz-close aria-label="Закрыть">${icons('close')}</button></div><div class="quiz-body quiz-done"><span class="taste-spark" aria-hidden="true">${icons('spark')}</span><p class="eyebrow">ВАШ ВКУСОВОЙ ПРОФИЛЬ</p><h2 tabindex="-1">${esc(p.summary||'Профиль сохранён')}</h2><div class="taste-scales">${profileScales(p.axes)}</div>${items.length?`<h3 class="quiz-subtitle">Первые вина для вас</h3><div class="quiz-wines"></div>`:''}<p class="quiz-lead">Профиль будет точнее с каждой оценкой. Быстрее всего — полистать вина: вправо, если хочется попробовать, влево — если не ваше.</p></div><div class="quiz-bottom"><button class="quiz-next" data-quiz-swipe>Листать вина</button><button class="quiz-skip" data-quiz-profile>Открыть мой вкус</button></div>`;
  const list=root.querySelector('.quiz-wines');
  items.forEach(w=>{const row=wineRow(w,w.reason||w.category,()=>{closeDialog(root);openWine(w);});list.append(row);});
  root.querySelector('[data-quiz-close]').onclick=()=>closeDialog(root);
  root.querySelector('[data-quiz-swipe]').onclick=()=>{closeDialog(root);openSwipe();};
  root.querySelector('[data-quiz-profile]')?.addEventListener('click',()=>{closeDialog(root);kind='';showView('taste');});
  root.querySelector('[data-quiz-restart]')?.addEventListener('click',()=>{quizResult=null;step=0;renderQuiz();});
  root.querySelector('h2')?.focus({preventScroll:true});
 }

 // ---- «Мой вкус» screen ------------------------------------------------------------------
 async function show(){render();await load();}
 async function load(){
  const own=++viewJob;
  try{await initPromise;const p=await api('taste'+(kind?'?kind='+kind:''));if(own!==viewJob)return;passport=p;render();if(!kind&&p.stage!=='new')loadPicks(own);}
  catch(e){if(own===viewJob){$t('taste-content').innerHTML=`<p class="form-error" role="alert">Не удалось загрузить профиль. ${esc(e.message)}</p>`;}}
 }
 function choose(value){kind=value;passport=null;render();load();}
 function render(){
  const tabs=$t('taste-tabs'),root=$t('taste-content');if(!tabs||!root)return;
  const list=[{id:'',label:'Обзор'},...(passport?.tabs||[{id:'red',label:'Красное'},{id:'white',label:'Белое'},{id:'rose',label:'Розовое'},{id:'sparkling',label:'Игристое'}])];
  tabs.innerHTML=list.map(t=>`<button data-taste-kind="${t.id}" aria-pressed="${t.id===kind}" class="${t.id===kind?'active':''}">${esc(t.label)}${t.count?` <span>${t.count}</span>`:''}</button>`).join('');
  tabs.querySelectorAll('[data-taste-kind]').forEach(b=>b.onclick=()=>choose(b.dataset.tasteKind));
  if(!passport){root.innerHTML='<p class="shelf-loading" role="status">Собираем ваш вкус…</p>';return;}
  const p=passport;
  if(p.stage==='new'&&!kind){renderInvite(root);return;}
  if(p.stage==='new'){root.innerHTML=`<div class="taste-empty"><h2>Пока мало данных о ваших ${KIND_WORDS[kind]}</h2><p>Оцените пару ${KIND_WORDS[kind]} вин или полистайте подборку — профиль появится здесь.</p><button class="cellar-primary" data-open-swipe>Листать вина</button></div>`;bindActions(root);return;}
  const facts=[['Стили',p.styles],['Сорта',p.grapes],['Регионы',p.regions]].filter(([,v])=>v?.length);
  root.innerHTML=`<div class="taste-layout"><article class="taste-hero"><p class="taste-stage"><span class="taste-stage-dot" data-stage="${p.stage}"></span>${stageLabel(p)}</p><h2>${esc(p.summary||'Ваш вкус')}</h2><p class="taste-evidence">${[p.liked_count?plural(p.liked_count,'вино нравится','вина нравятся','вин нравятся'):'',p.swiped&&!kind?plural(p.swiped,'свайп','свайпа','свайпов'):'',p.onboarding?'анкета пройдена':''].filter(Boolean).join(' · ')}</p><div class="taste-scales">${profileScales(p.axes)}</div></article><div class="taste-side">${p.aromas.length?`<section class="taste-card"><h3>Любимые ноты</h3><ul class="taste-aromas">${p.aromas.map(a=>`<li><span>${esc(a.name)}</span><i style="--share:${Math.round(a.share*100)}%"></i></li>`).join('')}</ul></section>`:''}${facts.length?`<section class="taste-card taste-facts">${facts.map(([title,values])=>`<div><h3>${title}</h3><p>${values.map(v=>esc(v.name)).join(', ')}</p></div>`).join('')}</section>`:''}<section class="taste-card taste-actions"><button class="taste-action" data-open-swipe><span class="taste-action-icon" aria-hidden="true">${swipeGlyph()}</span><span><b>Листать вина</b><small>Вправо — хочу попробовать, влево — не моё. Каждый свайп уточняет профиль.</small></span>${icons('chevron')}</button><button class="taste-action" data-open-quiz><span class="taste-action-icon" aria-hidden="true">${icons('spark')}</span><span><b>${Object.keys(p.quiz||{}).length?'Пройти анкету заново':'Пройти анкету'}</b><small>${Object.keys(p.quiz||{}).length?`Ответов: ${Object.keys(p.quiz).length} из 6`:'Шесть вопросов о кофе, шоколаде и вине'}</small></span>${icons('chevron')}</button></section></div></div>${kind?'':'<section class="taste-picks" aria-labelledby="taste-picks-title" hidden><div class="section-heading"><h2 id="taste-picks-title">Подобрано под ваш вкус</h2></div><div class="wine-grid" id="taste-picks"></div></section>'}`;
  bindActions(root);
 }
 function renderInvite(root){
  const first=quiz?.questions?.[0];
  root.innerHTML=`<article class="taste-invite"><div class="taste-invite-head"><span class="taste-spark" aria-hidden="true">${icons('spark')}</span><h2>Сформируйте свой вкусовой профиль!</h2></div><p class="taste-invite-lead">Шесть простых вопросов — о кофе, шоколаде и вине. По ответам подберём вина, которые вам понравятся, и покажем, насколько каждое совпадает с вашим вкусом.</p>${first?`<p class="taste-invite-question" id="taste-invite-q">${esc(first.title)}</p><div class="quiz-options" role="group" aria-labelledby="taste-invite-q">${first.options.map(o=>`<div class="quiz-option"><button data-invite="${esc(o.id)}">${quizIcon(o.icon)}<span class="quiz-label">${esc(o.label)}</span><span class="quiz-radio" aria-hidden="true"></span></button></div>`).join('')}</div>`:`<button class="cellar-primary" data-open-quiz>Начать анкету</button>`}<button class="text-button taste-invite-swipe" data-open-swipe>Или сразу листать вина ${icons('arrow')}</button></article>`;
  root.querySelectorAll('[data-invite]').forEach(b=>b.onclick=()=>openQuiz(b.dataset.invite));
  bindActions(root);
  if(!quiz)loadQuiz().then(()=>{if(currentView==='taste'&&passport?.stage==='new'&&!kind)renderInvite(root);}).catch(()=>{});
 }
 function bindActions(root){
  root.querySelectorAll('[data-open-swipe]').forEach(b=>b.onclick=()=>openSwipe());
  root.querySelectorAll('[data-open-quiz]').forEach(b=>b.onclick=()=>openQuiz());
 }
 async function loadPicks(own){
  try{const data=await api('recommendations?limit=4');if(own!==viewJob||currentView!=='taste')return;
   const grid=$t('taste-picks');if(!grid||!data.items.length)return;
   grid.replaceChildren();for(const w of data.items){const card=miniWine({...w,...getPreference(w.slug)},{showOpenLabel:false});if(w.reason){const why=document.createElement('p');why.className='taste-why';why.textContent=w.reason;card.querySelector('.mini-copy').append(why);}grid.append(card);}
   grid.closest('section').hidden=false;
  }catch{}
 }
 function swipeGlyph(){return '<svg viewBox="0 0 24 24"><rect x="6" y="3" width="12" height="17" rx="3"/><path d="M2 9l2 3-2 3M22 9l-2 3 2 3"/></svg>';}

 // ---- taste sheet on the wine card -------------------------------------------------------
 function sheet(w){
  const t=w.taste||sheets.get(w.slug);
  if(w.taste)sheets.set(w.slug,w.taste);
  if(w.preferences_available===false)return '';
  if(!t)return `<section class="taste-sheet is-loading" data-taste-slug="${esc(w.slug)}" aria-label="Вкус вина"></section>`;
  const m=t.match,yours=m?.yours||{};
  const scales=t.scales.map(s=>scale(s.axis,s.value,yours[s.axis],s.low,s.high)).join('');
  const chips=[...t.aromas.map(a=>a.name),...t.notes].map(x=>`<li>${esc(x)}</li>`).join('');
  const match=m?`<div class="taste-match"><span class="taste-match-ring" style="--p:${m.percent}" aria-hidden="true"><b>${m.percent}%</b></span><div><p class="taste-match-title">Совпадение с вашим вкусом</p><p>${esc(m.note)}</p></div></div>`
   :`<div class="taste-match taste-match-empty"><span class="taste-match-ring" aria-hidden="true">${icons('spark')}</span><div><p class="taste-match-title">Подойдёт ли вам это вино?</p><p>Ответьте на шесть вопросов — и мы покажем совпадение с вашим вкусом.</p><button class="text-button" data-open-quiz>Пройти анкету ${icons('arrow')}</button></div></div>`;
  return `<section class="taste-sheet" aria-labelledby="taste-sheet-title"><div class="taste-sheet-head"><h2 id="taste-sheet-title">Вкус вина</h2><small>${esc(t.source)}</small></div><div class="taste-sheet-grid"><div class="taste-scales">${scales}${m?'<p class="taste-legend"><i class="taste-you"></i>ваш вкус</p>':''}</div><div class="taste-sheet-side">${match}${chips?`<div><h3>Ноты во вкусе и аромате</h3><ul class="taste-chips">${chips}</ul></div>`:''}${t.occasions.length?`<div><h3>Когда открыть</h3><p class="taste-occasions">${esc(t.occasions.join(', '))}</p></div>`:''}</div></div></section>`;
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

 // ---- swipe deck («тиндер по винам») --------------------------------------------------------
 async function openSwipe(){
  openDialog('taste-swipe');deck=[];undoStack=[];deckDone=false;renderSwipe();await fillDeck();
 }
 async function fillDeck(){
  if(deckBusy||deckDone)return;deckBusy=true;
  try{await initPromise;const data=await api('taste/swipe?limit=10');const have=new Set(deck.map(w=>w.slug));
   const fresh=data.items.filter(w=>!have.has(w.slug));fresh.forEach(remember);deck.push(...fresh);
   if(!fresh.length)deckDone=true;if(deckSummary===null)deckSummary={swiped:data.swiped,summary:passport?.summary||null,stage:passport?.stage};
  }catch(e){notice(e.message);}
  finally{deckBusy=false;renderSwipe();}
 }
 function cardMarkup(w){
  const t=w.taste||{scales:[],aromas:[],notes:[]},m=t.match,yours=m?.yours||{};
  return `<article class="swipe-card" data-slug="${esc(w.slug)}" aria-label="${esc(w.name)}"><span class="swipe-stamp swipe-like" aria-hidden="true">Хочу</span><span class="swipe-stamp swipe-nope" aria-hidden="true">Не моё</span><div class="swipe-visual">${img(w)}${m?`<span class="swipe-match"><b>${m.percent}%</b> совпадение</span>`:''}</div><div class="swipe-body"><small class="swipe-winery">${esc(w.winery)}</small><h3>${esc(w.name)}</h3><p class="swipe-meta">${esc([w.category,w.region].filter(Boolean).join(' · '))}</p><div class="taste-scales swipe-scales">${t.scales.map(s=>scale(s.axis,s.value,yours[s.axis],s.low,s.high)).join('')}</div>${t.aromas.length?`<ul class="taste-chips">${t.aromas.slice(0,3).map(a=>`<li>${esc(a.name)}</li>`).join('')}</ul>`:''}${w.reason?`<p class="swipe-reason">${esc(w.reason)}</p>`:''}</div><div class="swipe-rate" hidden><p>Как вам это вино?</p><div class="swipe-glasses" role="group" aria-label="Оценка от 1 до 5">${[1,2,3,4,5].map(n=>`<button data-rate="${n}" aria-label="${n} из 5"><span class="swipe-glass"><img src="/assets/portal-card/public-rating-empty.svg" alt="" width="40" height="40"><img class="swipe-glass-fill" src="/assets/portal-card/public-rating-wine-in-glass.svg" alt="" width="40" height="40"></span></button>`).join('')}</div><button class="text-button" data-rate-cancel>Отмена</button></div></article>`;
 }
 function renderSwipe(){
  const root=$t('taste-swipe');if(!root.open)return;
  const s=deckSummary||{};
  if(!root.querySelector('.swipe-stack')){
   root.innerHTML=`<div class="quiz-top"><button class="quiz-round" data-swipe-undo aria-label="Вернуть предыдущее вино" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg></button><div class="swipe-title"><h2 id="taste-swipe-title">Листаем вина</h2><span class="quiz-step" data-swipe-count aria-live="polite"></span></div><button class="quiz-round" data-swipe-close aria-label="Закрыть">${icons('close')}</button></div><div class="swipe-stack"></div><div class="swipe-controls"><button class="swipe-button swipe-button-nope" data-verdict="nope" aria-label="Не моё">${icons('close')}<span>Не моё</span></button><button class="swipe-button swipe-button-tasted" data-tasted aria-label="Уже пробовал(а) — оценить">${icons('glass')}<span>Пробовал(а)</span></button><button class="swipe-button swipe-button-like" data-verdict="like" aria-label="Хочу попробовать">${icons('heart')}<span>Хочу</span></button></div><p class="swipe-profile" data-swipe-profile aria-live="polite"></p>`;
   root.setAttribute('aria-labelledby','taste-swipe-title');
   root.querySelector('[data-swipe-close]').onclick=()=>closeDialog(root);
   root.querySelector('[data-swipe-undo]').onclick=()=>undo();
   root.querySelectorAll('[data-verdict]').forEach(b=>b.onclick=()=>commit(b.dataset.verdict));
   root.querySelector('[data-tasted]').onclick=()=>openRating();
  }
  const stack=root.querySelector('.swipe-stack');
  const shown=deck.slice(0,3);
  stack.querySelectorAll('.swipe-card:not(.is-leaving)').forEach(el=>{if(!shown.some(w=>w.slug===el.dataset.slug))el.remove();});
  shown.slice().reverse().forEach((w,k)=>{
   let el=stack.querySelector(`.swipe-card[data-slug="${CSS.escape(w.slug)}"]:not(.is-leaving)`);
   if(!el){const tmp=document.createElement('div');tmp.innerHTML=cardMarkup(w);el=tmp.firstElementChild;handleBrokenImages(el);stack.prepend(el);}
   el.dataset.depth=shown.length-1-k;
  });
  const top=stack.querySelector('.swipe-card[data-depth="0"]:not(.is-leaving)');
  stack.querySelectorAll('.swipe-card').forEach(el=>{el.inert=el!==top;});
  if(top)drag(top);
  if(!deck.length){
   stack.querySelector('.swipe-end')?.remove();
   stack.insertAdjacentHTML('beforeend',deckBusy?'<p class="swipe-end shelf-loading" role="status">Подбираем вина…</p>':`<div class="swipe-end"><span class="taste-spark" aria-hidden="true">${icons('spark')}</span><h3>На сегодня всё</h3><p>${deckDone?'Вы пролистали все подходящие вина. Загляните позже — подборка обновится.':'Не удалось загрузить вина.'}</p><button class="cellar-primary" data-swipe-profile-open>Посмотреть мой вкус</button></div>`);
   stack.querySelector('[data-swipe-profile-open]')?.addEventListener('click',()=>{closeDialog(root);kind='';showView('taste');});
  }else stack.querySelector('.swipe-end')?.remove();
  root.querySelectorAll('.swipe-controls button').forEach(b=>b.disabled=!top);
  root.querySelector('[data-swipe-undo]').disabled=!undoStack.length;
  root.querySelector('[data-swipe-count]').textContent=s.swiped?plural(s.swiped,'вино оценено','вина оценено','вин оценено'):'Вправо — хочу, влево — не моё';
  root.querySelector('[data-swipe-profile]').innerHTML=s.summary?`<b>${esc(stageLabel(s))}.</b> ${esc(s.summary)}`:'Каждое решение уточняет ваш вкусовой профиль.';
 }
 function drag(card){
  if(card.dataset.bound)return;card.dataset.bound='1';
  let start=null,dx=0,dy=0;
  card.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.swipe-rate')||card.classList.contains('is-rating'))return;start={x:e.clientX,y:e.clientY,t:performance.now()};dx=dy=0;card.setPointerCapture(e.pointerId);card.classList.add('is-dragging');});
  card.addEventListener('pointermove',e=>{if(!start)return;dx=e.clientX-start.x;dy=e.clientY-start.y;card.style.transform=`translate(${dx}px,${dy*.3}px) rotate(${dx/18}deg)`;card.style.setProperty('--like',Math.max(0,Math.min(1,dx/110)));card.style.setProperty('--nope',Math.max(0,Math.min(1,-dx/110)));});
  const end=()=>{if(!start)return;const fast=Math.abs(dx)/(performance.now()-start.t)>.6&&Math.abs(dx)>40;start=null;card.classList.remove('is-dragging');
   if(Math.abs(dx)>110||fast)commit(dx>0?'like':'nope');else{card.style.transform='';card.style.removeProperty('--like');card.style.removeProperty('--nope');}};
  card.addEventListener('pointerup',end);card.addEventListener('pointercancel',end);
  card.addEventListener('click',e=>{if(e.target.closest('button')||card.classList.contains('is-rating'))return;if(Math.abs(dx)<6){const w=deck.find(x=>x.slug===card.dataset.slug);if(w){closeDialog($t('taste-swipe'));openWine(w);}}});
 }
 function flyOut(card,direction){
  card.classList.add('is-leaving');card.inert=true;
  const x=direction==='up'?0:direction==='right'?1:-1;
  card.style.transform=`translate(${x*140}%,${direction==='up'?'-120%':'-4%'}) rotate(${x*24}deg)`;card.style.opacity='0';
  setTimeout(()=>card.remove(),matchMedia('(prefers-reduced-motion: reduce)').matches?0:320);
 }
 async function commit(verdict,rating=0){
  const root=$t('taste-swipe'),w=deck[0];if(!w)return;
  const card=root.querySelector(`.swipe-card[data-slug="${CSS.escape(w.slug)}"]:not(.is-leaving)`);
  deck.shift();if(card)flyOut(card,rating?'up':verdict==='like'?'right':'left');
  undoStack.push({wine:w,verdict,rating});if(undoStack.length>20)undoStack.shift();
  renderSwipe();if(deck.length<4)fillDeck();
  try{
   if(rating){await changePreference(w.slug,{rating});const p=await api('taste');deckSummary={swiped:(deckSummary?.swiped||0)+1,summary:p.summary,stage:p.stage};passport=p;}
   else{const res=await api('taste/swipe',{slug:w.slug,verdict});deckSummary={swiped:res.passport.swiped,summary:res.passport.summary,stage:res.passport.stage};passport=res.passport;
    if(verdict==='like'){productData.preferences[w.slug]={...getPreference(w.slug),favorite:true};}}
   invalidate();renderSwipe();
  }catch(e){notice(e.message);}
 }
 async function undo(){
  const last=undoStack.pop();if(!last)return;
  try{
   if(last.rating)await changePreference(last.wine.slug,{rating:0});
   else{const res=await api('taste/swipe',{slug:last.wine.slug,verdict:'undo'});deckSummary={swiped:res.passport.swiped,summary:res.passport.summary,stage:res.passport.stage};passport=res.passport;
    if(last.verdict==='like')productData.preferences[last.wine.slug]={...getPreference(last.wine.slug),favorite:false};}
   deck.unshift(last.wine);invalidate();renderSwipe();
  }catch(e){undoStack.push(last);notice(e.message);}
 }
 function openRating(){
  const card=$t('taste-swipe').querySelector('.swipe-card[data-depth="0"]:not(.is-leaving)');if(!card)return;
  const panel=card.querySelector('.swipe-rate');panel.hidden=false;card.classList.add('is-rating');
  panel.querySelectorAll('[data-rate]').forEach(b=>{b.onclick=()=>commit('rated',Number(b.dataset.rate));b.onmouseenter=b.onfocus=()=>panel.querySelectorAll('[data-rate]').forEach(x=>x.classList.toggle('is-lit',Number(x.dataset.rate)<=Number(b.dataset.rate)));});
  panel.querySelector('[data-rate-cancel]').onclick=()=>{panel.hidden=true;card.classList.remove('is-rating');card.focus?.();};
  panel.querySelector('[data-rate]').focus({preventScroll:true});
 }

 document.addEventListener('keydown',e=>{
  const root=$t('taste-swipe');if(!root?.open||e.target.closest('input,textarea'))return;
  if(root.querySelector('.swipe-card.is-rating:not(.is-leaving)'))return;
  if(e.key==='ArrowRight'){e.preventDefault();commit('like');}else if(e.key==='ArrowLeft'){e.preventDefault();commit('nope');}
 });
 $t('taste-swipe')?.addEventListener('close',()=>{deckSummary=null;if(currentView==='taste')load();refreshRecommendations();});
 document.querySelectorAll('[data-open-taste-quiz]').forEach(b=>b.addEventListener('click',()=>openQuiz()));
 return {show,render,openQuiz,openSwipe,sheet,bindSheet,invalidate};
})();
