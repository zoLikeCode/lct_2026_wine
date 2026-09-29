'use strict';
const $ = id => document.getElementById(id);
const localMode = false;
const icons = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const store = {get(key, fallback) {try {return JSON.parse(localStorage.getItem(key)) ?? fallback;} catch {return fallback;}}, set(key,value) {try {localStorage.setItem(key, JSON.stringify(value));return true;} catch {return false;}}};
let deviceId = store.get('wine-device', '');
if(!deviceId){deviceId=crypto.randomUUID();store.set('wine-device',deviceId);}
// Old personal links still open the public scanner; no access key is needed.
function clearLegacyAccess() {
  try {localStorage.removeItem('wine-access');} catch {}
  if(new URLSearchParams(location.hash.slice(1)).has('key'))history.replaceState(history.state,'',location.pathname+location.search);
}
clearLegacyAccess();
window.addEventListener('hashchange',clearLegacyAccess);
let entries = store.get('wine-history', []);
if (!Array.isArray(entries)) entries=[];
let cameraStarting=false, cameraWanted=false;
let stream = null, cameraGeneration = 0, busy = false, controller = null, installPrompt = null, currentResult = null, currentView = 'scan', job = 0;

let retryFile=null;
function notice(message,canRetry=false) {$('notice-text').textContent=message;if($('retry-scan'))$('retry-scan').hidden=!canRetry;$('notice').hidden=false;}
function openDialog(id) {$(id).showModal();document.body.classList.add('modal-open');}
function closeDialog(dialog) {dialog.close();document.body.classList.remove('modal-open');}
document.querySelectorAll('[data-close-dialog]').forEach(b=>b.addEventListener('click',()=>closeDialog(b.closest('dialog'))));
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('close',()=>document.body.classList.remove('modal-open')));
$('dismiss-notice').onclick=()=>{$('notice').hidden=true;};
if($('retry-scan'))$('retry-scan').onclick=()=>{if(retryFile)processFile(retryFile);};

function scannerState(state,message='') {
 $('viewfinder').dataset.cameraState=state;
 $('camera-feedback').textContent=message;
 $('camera-feedback').hidden=state==='live'||state==='processing'||(document.body.dataset.design==='portal'&&state==='idle');
 if($('lens-status'))$('lens-status').textContent=state==='live'?'Камера включена':state==='requesting'?'Открываем камеру':'Камера выключена';
 if($('shutter-caption'))$('shutter-caption').textContent=state==='live'?'Снять этикетку':state==='requesting'?'Открываем…':'Включить камеру';
}
function cameraToggleState(state) {
 const button=$('camera-button'),slot=$('camera-toggle-slot');
 button.setAttribute('aria-pressed',state==='idle'?'false':'true');
 button.setAttribute('aria-label',state==='live'?'Выключить камеру':state==='requesting'?'Отменить включение камеры':'Включить камеру');
 if(state==='live'){
  slot.prepend(button);slot.hidden=false;$('history-camera-button').hidden=true;
 }else{
  $('shutter-button').parentElement.prepend(button);slot.hidden=true;$('history-camera-button').hidden=false;
 }
}
function stopCamera() {
 cameraGeneration++;cameraStarting=false;
 if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;
 $('camera').srcObject=null;$('camera').hidden=true;$('shutter-button').hidden=true;
 $('camera-button').hidden=false;$('camera-button').disabled=busy;cameraToggleState('idle');
 $('camera-tip').hidden=true;$('viewfinder').classList.remove('live');
 if(!busy){$('finder-idle').hidden=false;$('capture-preview').hidden=true;scannerState('idle','Включите камеру или загрузите фото');}
}
function turnOffCamera() {cameraWanted=false;stopCamera();}
function cancelScan() {job++;controller?.abort();controller=null;setBusy(false);stopCamera();}
const views=['scan','result','history','collection','ranking','assistant'];
let currentWine=null,previousView='scan',collectionFilter='favorite',rankingMode='scans',productData={preferences:{},collection:[],recommendations:[]},wineCache=new Map(),viewJob=0,assistantAttachment=null;
function showView(name,push=true) {
 if(!views.includes(name))name='scan';const changed=currentView!==name;
 if(changed)$('notice').hidden=true;
 if(name==='assistant'&&currentView==='result')attachScan(currentResult);
 if(name!=='scan'){cameraWanted=false;if(busy)cancelScan();else stopCamera();}
 if(name==='result'&&currentView!=='result')previousView=currentView;
 currentView=name;document.body.dataset.screen=name;views.forEach(view=>$(view+'-view').hidden=view!==name);
 document.querySelectorAll('[data-view]').forEach(b=>{const active=b.dataset.view===(name==='result'?'scan':name==='history'?'collection':name);b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
 if(name==='history')renderHistory();if(name==='collection')renderCollection();if(name==='ranking')loadRanking();
 if(push&&changed)history.pushState({view:name,id:currentResult?.id,slug:currentWine?.slug},'',location.pathname);
 window.scrollTo({top:0,behavior:'instant'});
 if(name==='scan'&&changed&&cameraWanted&&!busy)startCamera(true);
}
history.replaceState({view:'scan'},'',location.pathname);
window.addEventListener('popstate',event=>{const view=event.state?.view||'scan';const entry=entries.find(x=>x.id===event.state?.id);if(view==='result'&&entry)renderResult(entry,wineCache.get(event.state?.slug)||entry.wine,false);else if(view==='result'&&wineCache.has(event.state?.slug))renderResult(null,wineCache.get(event.state.slug),false);else showView(view,false);});
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view));
$('wine-back').onclick=()=>showView(previousView==='result'?'scan':previousView);
$('rescan-top').onclick=()=>showView('scan');
$('help-button').onclick=()=>openDialog('help-dialog');
$('install-button').onclick=()=>installPrompt?installApp():openDialog('install-dialog');
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;$('native-install').hidden=false;});
async function installApp() {
  const prompt=installPrompt;if(!prompt)return;
  installPrompt=null;$('native-install').hidden=true;
  try {await prompt.prompt();const result=await prompt.userChoice;if(result.outcome==='accepted'&&$('install-dialog').open)closeDialog($('install-dialog'));}
  catch {openDialog('install-dialog');}
}
$('native-install').onclick=installApp;
window.addEventListener('appinstalled',()=>{$('install-button').hidden=true;});
if (localMode || matchMedia('(display-mode: standalone)').matches) $('install-button').hidden=true;
async function checkConnection(quiet=false) {
  const element=$('connection');if(!element)return;const text=element.querySelector('span');if(!quiet)element.className='connection';
  if(!localMode&&!navigator.onLine){element.className='connection error';text.textContent='Нет интернета · история доступна';return;}
  if(!quiet)text.textContent='Проверяем соединение';
  try {const r=await fetch('/api/status',{headers:{'X-Device-ID':deviceId},signal:AbortSignal.timeout(6000),cache:'no-store'});if(!r.ok)throw Error();const data=await r.json();if(!data.ready)throw Error();element.className='connection ready';text.textContent='Сервис доступен';}
  catch{element.className='connection error';text.textContent='Нет связи с сервером';}
}
if($('reconnect'))$('reconnect').onclick=()=>checkConnection();
window.addEventListener('online',checkConnection);window.addEventListener('offline',checkConnection);
setInterval(()=>{if(!document.hidden&&!busy)checkConnection(true);},30000);

async function startCamera(automatic=false) {
 if(busy||cameraStarting||stream||currentView!=='scan'||document.hidden)return;
 if(!navigator.mediaDevices?.getUserMedia){
  scannerState('unavailable','Загрузите фото — камера в этом браузере недоступна');
  if(!automatic)$('native-camera-file').click();
  return;
 }
 cameraWanted=true;cameraStarting=true;const generation=++cameraGeneration;
 $('camera-button').disabled=false;cameraToggleState('requesting');scannerState('requesting','Разрешите доступ к камере в браузере');
 try {
  const nextStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:2560},height:{ideal:1920}},audio:false});
  if(generation!==cameraGeneration||currentView!=='scan'||busy||document.hidden){nextStream.getTracks().forEach(t=>t.stop());return;}
  stream=nextStream;$('camera').srcObject=stream;await $('camera').play();
  if(generation!==cameraGeneration||currentView!=='scan'||busy||document.hidden){nextStream.getTracks().forEach(t=>t.stop());return;}
  $('finder-idle').hidden=true;$('capture-preview').hidden=true;$('camera').hidden=false;
  $('viewfinder').classList.add('live');$('shutter-button').hidden=false;cameraToggleState('live');
  $('camera-tip').hidden=false;scannerState('live');
 } catch(error) {
  if(generation!==cameraGeneration)return;
  stopCamera();cameraWanted=false;
  scannerState('unavailable',error.name==='NotAllowedError'?'Разрешите камеру или загрузите готовое фото':error.name==='NotFoundError'?'Камера не найдена. Можно загрузить фото':'Камера недоступна. Попробуйте загрузить фото');
 } finally {if(generation===cameraGeneration){cameraStarting=false;$('camera-button').disabled=busy;}}
}
$('camera-button').onclick=()=>cameraWanted?turnOffCamera():startCamera(false);
$('gallery-button').onclick=()=>{if(!busy)$('photo-file').click();};
$('shutter-button').onclick=async()=>{
  if(busy)return;const video=$('camera');if(!video.videoWidth){notice('Подождите, пока камера настроится.');return;}
  const canvas=document.createElement('canvas');const scale=Math.min(1,4096/Math.max(video.videoWidth,video.videoHeight));canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
  const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.96));stopCamera();if(blob)processFile(blob);
};
for(const id of ['photo-file','native-camera-file']) $(id).onchange=event=>{const file=event.target.files[0];event.target.value='';if(file)processFile(file);};
$('cancel-scan').onclick=cancelScan;
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera();else{checkConnection();if(cameraWanted&&currentView==='scan'&&!busy)startCamera(true);}});
window.addEventListener('pagehide',stopCamera);

function setBusy(value) {busy=value;if(value)scannerState('processing');$('processing').hidden=!value;$('camera-button').disabled=value;$('gallery-button').disabled=value;$('shutter-button').disabled=value;}
async function imageElement(blob) {const url=URL.createObjectURL(blob);try {const img=new Image();img.src=url;await img.decode();return img;} finally {URL.revokeObjectURL(url);}}
async function prepare(blob) {
  if(blob.size>24*1024*1024)throw Error('Выберите фото размером до 24 МБ.');
  let img;try{img=await imageElement(blob);}catch{throw Error('Фото не открывается. Выберите JPEG, PNG или WebP.');}
  if(!img.naturalWidth||img.naturalWidth*img.naturalHeight>40_000_000)throw Error('Слишком большое фото. Выберите снимок до 40 млн пикселей.');
  const tiny=document.createElement('canvas');const ratio=Math.min(1,260/Math.max(img.naturalWidth,img.naturalHeight));tiny.width=Math.max(1,Math.round(img.naturalWidth*ratio));tiny.height=Math.max(1,Math.round(img.naturalHeight*ratio));tiny.getContext('2d').drawImage(img,0,0,tiny.width,tiny.height);
  // The original file goes to the backend; its EXIF orientation and private metadata are handled there.
  return{upload:blob,thumb:tiny.toDataURL('image/jpeg',.65)};
}
async function processFile(file) {
  if(busy)return;if(!localMode&&!navigator.onLine){notice('Для нового сканирования нужен интернет. История уже доступна на телефоне.');return;}
  cameraWanted=false;stopCamera();showView('scan');$('notice').hidden=true;retryFile=null;setBusy(true);const ownJob=++job;controller=new AbortController();const signal=controller.signal;const timer=setTimeout(()=>controller?.abort('timeout'),105000);
  try {
    const {upload,thumb}=await prepare(file);if(ownJob!==job)return;
    $('capture-preview').src=thumb;$('capture-preview').hidden=false;$('finder-idle').hidden=true;
    await initPromise;if(ownJob!==job)return;const data=new FormData();data.append('image',upload,upload.name||'label.jpg');const r=await fetch('/api/predict',{method:'POST',headers:{'X-Device-ID':deviceId},body:data,signal});
    let result;try{result=await r.json();}catch{const error=Error('Не удалось получить ответ сервера. Проверьте соединение.');error.retryable=r.status>=500;throw error;}
    if(!r.ok){const error=Error(typeof result.detail==='string'?result.detail:r.status===429?'Сканер занят. Повторите через несколько секунд.':r.status===503||r.status===504?'Распознавание временно недоступно. Попробуйте позже.':r.status===413?'Файл больше 24 МБ. Выберите другое фото.':'Не удалось распознать снимок.');error.retryable=[429,503,504].includes(r.status);throw error;}
    if(ownJob!==job)return;
    const entry={...result,server_id:result.id,thumb,id:crypto.randomUUID(),created_at:new Date().toISOString()};entries.unshift(entry);entries=entries.slice(0,24);
    while(entries.length>1&&!store.set('wine-history',entries))entries.pop();
    const saved=store.set('wine-history',entries);setBusy(false);renderResult(entry);refreshRecommendations();if(!saved)notice('Результат открыт, но в хранилище телефона нет места для истории.');
  } catch(error) {if(ownJob!==job)return;const canRetry=signal.aborted||error instanceof TypeError||error.retryable===true;retryFile=canRetry?file:null;notice(signal.aborted?'Ответ задержался. Попробуйте снова при устойчивом интернете.':(error instanceof TypeError?'Нет связи с распознаванием. Проверьте интернет.':error.message),canRetry);}
  finally{clearTimeout(timer);if(ownJob===job){controller=null;setBusy(false);stopCamera();}}
}

const esc=escapeHTML;
async function api(path,body){const r=await fetch('/api/product/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(path==='assistant'?30000:12000)});let data;try{data=await r.json();}catch{throw Error('Сервис не ответил. Попробуйте ещё раз.');}if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Не удалось сохранить. Попробуйте ещё раз.');return data;}
function remember(w){wineCache.set(w.slug,w);return w;}
function img(w,cls=''){return `<img class="${cls}" src="${esc(w.image||'/assets/no-bottle.svg')}" alt="${esc(w.name)}" loading="lazy">`;}
function handleBrokenImages(root){root.querySelectorAll('img').forEach(im=>im.addEventListener('error',()=>{if(!im.src.endsWith('/assets/no-bottle.svg')){im.src='/assets/no-bottle.svg';}},{once:true}));}
function empty(icon,title,text,action){return `<div class="empty-state">${icons(icon)}<h2>${esc(title)}</h2><p>${esc(text)}</p>${action?`<button class="button primary" data-empty-scan>Сканировать вино ${icons('arrow')}</button>`:''}</div>`;}
function bindEmpty(root){root.querySelectorAll('[data-empty-scan]').forEach(b=>b.onclick=()=>showView('scan'));}
function miniWine(w){remember(w);const el=document.createElement('article');el.className='mini-wine';el.innerHTML=`<button class="mini-wine-open"><div class="mini-visual">${img(w)}</div><small>${esc(w.winery)}</small><h3>${esc(w.name)}</h3><small>${esc(w.category)} · ${esc(w.region)}</small><p class="mini-meta">${w.rating?`Ваша оценка: ${w.rating} / 5`:w.average_rating?`★ ${w.average_rating.toFixed(1)} · оценок: ${w.rating_count}`:esc(w.grapes)}</p></button><button class="mini-save" aria-label="${w.favorite?'Убрать из избранного':'В избранное'}: ${esc(w.name)}" aria-pressed="${!!w.favorite}">${icons('heart')}</button>`;el.querySelector('.mini-wine-open').onclick=()=>openWine(w);el.querySelector('.mini-save').onclick=async(e)=>{await changePreference(w.slug,{favorite:!getPreference(w.slug).favorite},e.currentTarget);};handleBrokenImages(el);return el;}
function getPreference(slug){return productData.preferences[slug]||{rating:0,favorite:false};}
async function changePreference(slug,values,button){if(button)button.disabled=true;try{await initPromise;const w=await api('preferences/'+encodeURIComponent(slug),values);remember(w);productData.preferences[slug]={rating:w.rating,favorite:w.favorite};productData.collection=productData.collection.filter(x=>x.slug!==slug);if(w.rating||w.favorite)productData.collection.unshift(w);store.set('wine-product',productData);if(currentWine?.slug===slug){currentWine=w;updatePersonal(w);}renderDiscovery();if(currentView==='collection')renderCollection();updatePreferenceCopy();refreshRecommendations();notice(values.rating===0?'Оценка снята':values.rating?'Ваша оценка сохранена':w.favorite?'Вино в избранном':'Вино убрано из избранного');}catch(e){notice(e.message);}finally{if(button)button.disabled=false;}}
function updatePersonal(w){const el=$('wine-personal');if(!el)return;el.hidden=w.preferences_available===false;if(el.hidden)return;const p=getPreference(w.slug);el.innerHTML=`<div><span class="rating-label">${p.rating?'Ваша оценка — '+p.rating+' из 5':'Как вам это вино?'}</span><div class="star-rating" role="group" aria-label="Оценить вино от 1 до 5">${[1,2,3,4,5].map(n=>`<button class="star-button ${n<=p.rating?'selected':''}" data-rating="${n}" aria-label="Оценить на ${n} из 5" aria-pressed="${n===p.rating}">${icons('star')}</button>`).join('')}${p.rating?'<button class="rating-clear" id="clear-rating">Снять</button>':''}</div></div><button id="save-wine" class="save-wine" aria-pressed="${p.favorite}">${icons('heart')}${p.favorite?'В избранном':'В избранное'}</button>`;el.querySelectorAll('[data-rating]').forEach(b=>b.onclick=()=>changePreference(w.slug,{rating:Number(b.dataset.rating)},b));if($('clear-rating'))$('clear-rating').onclick=e=>changePreference(w.slug,{rating:0},e.currentTarget);$('save-wine').onclick=e=>changePreference(w.slug,{favorite:!getPreference(w.slug).favorite},e.currentTarget);}
function renderAlternatives(entry,wine){
 const noWine=entry?.no_wine===true;
 const cards=[...(entry?.wine?[entry.wine]:[]),...(Array.isArray(entry?.alternatives)?entry.alternatives:[])];
 const others=cards.filter(item=>item?.slug&&item.slug!==wine?.slug);
 const details=$('alternatives');details.hidden=!others.length;details.open=false;
 details.querySelector('summary').firstChild.textContent=noWine?'Ближайшие варианты ':'Другие похожие вина ';
 details.querySelector('.muted').textContent=noWine?'Это похожие вина из каталога, а не подтверждённое распознавание. Сверьте этикетку.':'Если название или год не совпали с бутылкой, проверьте другие варианты.';
 $('alternatives-count').textContent=others.length;$('alternative-list').replaceChildren();
 others.forEach(item=>$('alternative-list').append(wineRow(item,item.category,()=>openWine(item,entry))));
}
function renderResult(entry,wine=entry?.wine,push=true){
 if(!wine){
  if(!entry)return;
  currentResult=entry;currentWine=null;
  const noWine=entry.no_wine===true;
  $('wine-card').innerHTML=`<div class="empty-state no-wine-result">${entry.thumb?`<img class="no-wine-photo" src="${esc(entry.thumb)}" alt="Ваш снимок">`:icons('scan')}<h1 id="result-title">${noWine?'Не удалось распознать вино':'Карточка недоступна'}</h1>${noWine?'':'<p>Результат сохранён в истории, но карточку сейчас не удалось открыть.</p>'}<div class="result-actions"><button class="button primary" id="no-wine-rescan">Снять ещё раз ${icons('arrow')}</button>${entry.server_id?'<button class="button secondary" id="ask-about-scan">Спросить помощника</button>':''}</div></div>`;
  $('no-wine-rescan').onclick=()=>showView('scan');if($('ask-about-scan'))$('ask-about-scan').onclick=()=>{showView('assistant');$('assistant-input').value='Помоги определить вино на фото';$('assistant-input').focus();};renderAlternatives(entry,null);
  showView('result',push);$('result-title').setAttribute('tabindex','-1');$('result-title').focus({preventScroll:true});return;
 }
 currentResult=entry;const w=remember({...wine,name:wine.name||wine.slug||'Вино из каталога',winery:wine.winery||'',category:wine.category||'',region:wine.region||'',grapes:wine.grapes||'',description:wine.description||'',pairings:Array.isArray(wine.pairings)?wine.pairings:[],portal_url:wine.portal_url||'',average_rating:Number.isFinite(wine.average_rating)?wine.average_rating:null,rating_count:Number.isFinite(wine.rating_count)?wine.rating_count:0,...getPreference(wine.slug)});currentWine=w;
 const facts=[['Сорт винограда',w.grapes],['Регион',w.region],['Температура подачи',w.temperature],['Крепость',w.alcohol?String(w.alcohol)+'%':'']].filter(x=>x[1]);
 $('wine-card').innerHTML=`<div class="wine-card"><div class="wine-visual">${img(w)}<span class="wine-stage-word" aria-hidden="true">СВОЁ</span>${entry?.thumb?`<img class="photo-thumb" src="${esc(entry.thumb)}" alt="Ваш снимок">`:''}</div><div class="wine-info"><p class="eyebrow">${esc(w.winery)}</p><h1 id="result-title">${esc(w.name)}</h1><div class="traits"><span class="trait">${esc(w.category)}</span>${w.average_rating?`<span class="trait">★ ${w.average_rating.toFixed(1)} · оценок: ${w.rating_count}</span>`:''}</div>${entry?.server_id?`<button id="ask-about-scan" class="scan-assistant-link">Обсудить снимок с помощником ${icons('arrow')}</button>`:''}<dl class="wine-facts">${facts.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl><div id="wine-personal" class="wine-personal"></div>${w.portal_url?`<a class="source-link" href="${esc(w.portal_url)}" target="_blank" rel="noopener noreferrer">Карточка на «Своё Вино» ↗</a>`:''}</div></div><div class="wine-extra"><section class="wine-description"><h2>Характер<br><em>в бокале.</em></h2><p>${esc(w.description||'Описание пока не добавлено в каталог.')}</p></section></div>`;
 const recognizedScan=!!entry&&!entry.no_wine&&entry.wine?.slug===w.slug;
 if(recognizedScan){
  const card=$('wine-card').querySelector('.wine-card');card.classList.add('scan-result-card');
  const panel=document.createElement('section');panel.className='result-pairing';panel.setAttribute('aria-label','Сочетания с найденным вином');
  const kicker=document.createElement('p');kicker.className='result-pairing-kicker';kicker.textContent='ИДЕЯ ДЛЯ СТОЛА';panel.append(kicker);
  const names=w.pairings.map(value=>String(value).trim()).filter(Boolean);
  const heading=document.createElement('h2');heading.textContent=names.length?'С этим вином сочетаются':'Что подать к этому вину?';panel.append(heading);
  if(names.length){
   const list=document.createElement('ul');list.className='pairing-preview';
   for(const name of names.slice(0,3)){const item=document.createElement('li');item.textContent=name;list.append(item);}
   panel.append(list);
  }else{const empty=document.createElement('p');empty.className='pairing-empty';empty.textContent='В карточке вина пока нет сочетаний. Помощник проверит, можно ли предложить конкретное блюдо.';panel.append(empty);}
  const source=document.createElement('p');source.className='pairing-source';source.textContent=names.length?'Сочетания из карточки портала «Своё Вино». Помощник поможет выбрать конкретное блюдо.':'Помощник учтёт это вино и ваш снимок.';panel.append(source);
  const button=$('ask-about-scan');if(button){button.textContent='Подобрать блюдо с помощником';panel.append(button);}
  card.querySelector('.wine-info .traits').after(panel);
 }
 updatePersonal(w);handleBrokenImages($('wine-card'));
 if($('ask-about-scan'))$('ask-about-scan').onclick=()=>{showView('assistant');const name=String(wine?.name||'').trim();$('assistant-input').value=recognizedScan?(name?'Что приготовить к «'+name+'»?':'Что приготовить к этому вину?'):'Помоги определить вино на фото';$('assistant-input').focus();};
 renderAlternatives(entry,w);showView('result',push);$('result-title').setAttribute('tabindex','-1');$('result-title').focus({preventScroll:true});
}
async function openWine(w,entry=null){const own=++viewJob;renderResult(entry,w);if(w.preferences_available===false)return;try{const fresh=await api('wines/'+encodeURIComponent(w.slug));if(own===viewJob&&currentView==='result'&&currentWine.slug===w.slug)renderResult(entry,fresh,false);}catch{if(!w.description)notice('Не удалось обновить карточку. Сохранённые данные доступны.');}}
function wineRow(w,meta,action){remember(w);const b=document.createElement('button');b.className='wine-row';b.innerHTML=`${img(w)}<span class="row-copy"><small>${esc(w.winery)}</small><b>${esc(w.name||w.slug||'Вино из каталога')}</b><small>${esc(meta)}</small></span>${icons('chevron')}`;b.onclick=action;handleBrokenImages(b);return b;}
function historyNoWineRow(entry,date){const b=document.createElement('button');b.className='wine-row';b.innerHTML=`<img src="${esc(entry.thumb||'/assets/no-bottle.svg')}" alt="Ваш снимок"><span class="row-copy"><small>Сканирование</small><b>${entry.no_wine?'Не удалось распознать вино':'Карточка недоступна'}</b><small>${esc(date)}</small></span>${icons('chevron')}`;b.onclick=()=>renderResult(entry);handleBrokenImages(b);return b;}
function renderHistory(){const list=$('history-list');list.replaceChildren();$('clear-history').hidden=!entries.length;if(!entries.length){list.innerHTML=empty('history','Первое открытие ещё впереди','Снимите этикетку — найденная карточка появится здесь.',true);bindEmpty(list);return;}for(const entry of entries){const date=new Date(entry.created_at).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});list.append(entry.wine?.slug?wineRow(entry.wine,date,()=>openWine(entry.wine,entry)):historyNoWineRow(entry,date));}}
$('clear-history').onclick=()=>openDialog('clear-dialog');$('confirm-clear').onclick=()=>{entries=[];store.set('wine-history',entries);closeDialog($('clear-dialog'));renderHistory();};
async function refreshRecommendations(){try{const data=await api('recommendations');productData.recommendations=data.items;store.set('wine-product',productData);renderDiscovery();}catch{}}
function renderDiscovery(){const personalized=Object.values(productData.preferences).some(x=>x.favorite||x.rating>=4)||entries.some(x=>x.wine?.slug);$('discovery-title').textContent=personalized?'На основе ваших предпочтений':'Следующее открытие';$('discovery-eyebrow').textContent=personalized?'ВАШ ВКУС — НАШ ОРИЕНТИР':'ЗНАКОМСТВО С КАТАЛОГОМ';$('discovery-list').replaceChildren();for(const w of productData.recommendations.slice(0,4))$('discovery-list').append(miniWine({...w,...getPreference(w.slug)}));if(!productData.recommendations.length)$('discovery-list').innerHTML=empty('glass','Каталог скоро появится','Проверьте соединение с сервером.',false);}
function renderCollection(){const prefs=productData.preferences;$('favorite-count').textContent=Object.values(prefs).filter(x=>x.favorite).length;$('rated-count').textContent=Object.values(prefs).filter(x=>x.rating).length;const list=$('collection-list');list.replaceChildren();document.querySelectorAll('[data-collection]').forEach(b=>b.classList.toggle('active',b.dataset.collection===collectionFilter));const ws=productData.collection.filter(w=>collectionFilter==='favorite'?getPreference(w.slug).favorite:getPreference(w.slug).rating).map(w=>({...w,...getPreference(w.slug)}));if(collectionFilter==='rated')ws.sort((a,b)=>b.rating-a.rating);for(const w of ws)list.append(miniWine(w));if(!ws.length){list.innerHTML=empty(collectionFilter==='favorite'?'heart':'star',collectionFilter==='favorite'?'У каждого есть своё.':'Вкус — дело личное.',collectionFilter==='favorite'?'Сохраните вино с помощью сердечка. Или найдите любимое в каталоге выше.':'Поставьте вину от 1 до 5 звёзд — и помощник узнает ваш вкус.',true);bindEmpty(list);}}
document.querySelectorAll('[data-collection]').forEach(b=>b.onclick=()=>{collectionFilter=b.dataset.collection;renderCollection();});
let searchJob=0,searchTimer;
async function searchWines(){const q=$('wine-search').value.trim(),own=++searchJob,root=$('search-results');root.hidden=!q;if(!q)return;root.innerHTML='<p class="search-result-title">Ищем в каталоге…</p>';try{await initPromise;const data=await api('search?q='+encodeURIComponent(q));if(own!==searchJob)return;root.innerHTML=`<p class="search-result-title">${data.items.length?'Найденные вина · '+data.items.length:'Не нашли такое вино. Попробуйте название винодельни или сорт.'}</p><div class="search-results-list"></div>`;data.items.forEach(w=>root.lastElementChild.append(wineRow(w,w.category+' · '+w.grapes,()=>openWine(w))));}catch(e){if(own===searchJob)root.textContent=e.message;}}
$('catalog-search').onsubmit=e=>{e.preventDefault();clearTimeout(searchTimer);searchWines();};$('wine-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(searchWines,350);};
let rankJob=0;
async function loadRanking(){const own=++rankJob;document.querySelectorAll('[data-ranking]').forEach(b=>b.classList.toggle('active',b.dataset.ranking===rankingMode));$('ranking-list').innerHTML='<p class="muted">Собираем рейтинг…</p>';try{await initPromise;const data=await api('ranking?mode='+rankingMode);if(own!==rankJob)return;$('ranking-total').textContent=rankingMode==='scans'?`Всего сканов: ${data.total_scans}`:`Всего оценок: ${data.total_ratings}`;const root=$('ranking-list');root.replaceChildren();if(!data.items.length){root.innerHTML=empty(rankingMode==='scans'?'scan':'star',rankingMode==='scans'?'Первый скан — за вами.':'Первая оценка — за вами.',rankingMode==='scans'?'Здесь появятся вина, которые сканируют чаще всего.':'Поставьте оценку в карточке вина, чтобы начать общий рейтинг.',true);bindEmpty(root);}data.items.forEach((w,i)=>{const b=document.createElement('button');b.className='ranking-row';b.innerHTML=`<span class="rank-number">${String(i+1).padStart(2,'0')}</span>${img(w)}<span class="row-copy"><small>${esc(w.winery)}</small><b>${esc(w.name)}</b><small>${esc(w.category)}</small></span><span class="rank-value">${rankingMode==='scans'?w.scan_count:w.average_rating.toFixed(1)}<small>${rankingMode==='scans'?'сканирований':'из 5 · оценок: '+w.rating_count}</small></span>`;b.onclick=()=>openWine(w);handleBrokenImages(b);root.append(b);});}catch(e){if(own===rankJob)$('ranking-list').innerHTML=empty('info','Не удалось загрузить рейтинг',e.message,false);}}
document.querySelectorAll('[data-ranking]').forEach(b=>b.onclick=()=>{rankingMode=b.dataset.ranking;loadRanking();});
function updatePreferenceCopy(){if(!$('preference-copy'))return;const n=Object.values(productData.preferences).filter(x=>x.favorite||x.rating>=4).length;$('preference-copy').textContent=n?`Вин в основе подбора: ${n}. Учитываем избранное, высокие оценки и вашу историю сканов.`:'Сохраняйте вина и ставьте оценки — подбор станет ближе к вашему вкусу.';}
const chatContext=[];let chatBusy=false,lastDish=null;
function isRecipeFollowup(message){
 return /рецепт|ингредиент|приготов|готов(?:ить|к|ят|ится)|пошагов|пожар|запеч|запека|свар|туш(?:ить|и)|испеч|выпека|маринова|сколько\s+(?:минут|времени)|что\s+нужно\s+для|какие\s+продукты|как\s+(?:это|его|ее|её)?\s*сделать/i.test(message);
}
function renderAssistantAttachment(){
 const box=$('assistant-attachment');box.hidden=!assistantAttachment;
 $('assistant-input').placeholder=assistantAttachment?.intent==='dish_pairing'?'Какое блюдо приготовить к этому вину?':assistantAttachment?'Спросите о снимке…':lastDish?'Например, как приготовить это блюдо?':'Например, красное к стейку…';
 const welcome=$('chat-messages').querySelector('.assistant-welcome');
 if(welcome){welcome.querySelector('h2').textContent=assistantAttachment?.intent==='dish_pairing'?'Что приготовить к этому вину?':assistantAttachment?'Поможем разобраться со снимком':'Какое вино ищем?';welcome.querySelector('p').textContent=assistantAttachment?.intent==='dish_pairing'?'Фото вина уже прикреплено. Спросите о блюде или отправьте готовый запрос.':assistantAttachment?'Фото уже прикреплено. Спросите, что на нём.':'Напишите, что вам нравится, или что сегодня на ужин.';welcome.querySelector('.prompt-list').hidden=!!assistantAttachment;}
 if(!assistantAttachment)return;
 $('attachment-image').src=assistantAttachment.preview;
 $('attachment-label').textContent=assistantAttachment.name||'Снимок скана';
}
function attachScan(entry){
 if(!entry?.server_id)return;
 if(lastDish?.scanId!==entry.server_id)lastDish=null;
 assistantAttachment={scanId:entry.server_id,preview:entry.thumb||'/api/product/scans/'+encodeURIComponent(entry.server_id)+'/image',name:entry.wine?.name||'Снимок скана'};
 assistantAttachment.intent=!entry.no_wine&&entry.wine?.slug===currentWine?.slug?'dish_pairing':null;
 renderAssistantAttachment();
}
function removeAssistantAttachment(){assistantAttachment=null;renderAssistantAttachment();}
$('remove-attachment').onclick=removeAssistantAttachment;
async function askAssistant(message){
 const attachment=assistantAttachment;
 message=message.trim()||(attachment?.intent==='dish_pairing'?'Что приготовить к этому вину?':attachment?'Помоги определить вино на фото.':'');
 if(chatBusy||!message)return;
 const recipeFollowup=!!lastDish&&isRecipeFollowup(message)&&(!attachment||attachment.scanId===lastDish.scanId);
 const intent=recipeFollowup?'dish_recipe':attachment?.intent==='dish_pairing'?'dish_pairing':null;
 const scanId=recipeFollowup?lastDish.scanId:attachment?.scanId;
 chatBusy=true;$('send-button').disabled=true;$('voice-button').disabled=true;$('assistant-input').value='';
 const root=$('chat-messages');root.querySelector('.assistant-welcome')?.remove();
 const turn=document.createElement('div');turn.className='chat-turn';
 turn.innerHTML=`<div class="chat-user">${attachment?`<img class="chat-attached-photo" src="${esc(attachment.preview)}" alt="Прикреплённый снимок этикетки">`:''}<span>${esc(message)}</span></div><div class="chat-answer">Подбираем вино…</div>`;
 if(intent==='dish_pairing')turn.querySelector('.chat-answer').textContent='Подбираем блюдо…';
 if(intent==='dish_recipe')turn.querySelector('.chat-answer').textContent='Готовим рецепт…';
 root.append(turn);root.scrollTop=root.scrollHeight;
 try{
  await initPromise;
  const data=await api('assistant',{message,context:chatContext,...(scanId?{scan_id:scanId}:{}),...(intent?{intent}:{}),...(recipeFollowup?{dish_name:lastDish.name}:{})});
  chatContext.push(message);if(chatContext.length>3)chatContext.shift();
  if(intent==='dish_pairing'){
   const name=String(data.dishes?.[0]?.name||'').trim();lastDish=data.recommendation_type==='dish'&&name&&scanId?{scanId,name}:null;
  }else if(!recipeFollowup)lastDish=null;
  const recipeAnswer=intent==='dish_recipe'||data.recommendation_type==='dish_recipe';
  if(data.recommendation_type==='dish')turn.classList.add('dish-turn');
  if(recipeAnswer)turn.classList.add('recipe-turn');
  turn.querySelector('.chat-answer').innerHTML=`<p>${esc(data.text)}</p>`;
  if(data.warning){const warning=document.createElement('p');warning.className='muted';warning.textContent=data.warning;turn.append(warning);}
  if(!recipeAnswer&&data.recommendation_type==='dish'&&Array.isArray(data.dishes)&&data.dishes.length){
   const panel=document.createElement('section');panel.className='chat-dish';
   const kicker=document.createElement('p');kicker.className='chat-dish-kicker';kicker.textContent='БЛЮДО К ВАШЕМУ ВИНУ';panel.append(kicker);
   if(data.wine?.name){const context=document.createElement('p');context.className='chat-dish-context';context.textContent='К вину «'+data.wine.name+'»';panel.append(context);}
   for(const dish of data.dishes){
    const title=document.createElement('h3');title.textContent=dish.name||'Блюдо к вашему вину';panel.append(title);
    if(dish.source_pairing){const source=document.createElement('p');source.className='chat-dish-source';source.textContent='Сочетание в каталоге: '+dish.source_pairing;panel.append(source);}
   }
   turn.append(panel);
  }
  for(const w of (data.recommendation_type==='dish'||recipeAnswer?[]:(data.items||[]))){const result=document.createElement('div');result.className='chat-result';result.append(wineRow(w,w.category,()=>openWine(w)));const reason=document.createElement('p');reason.className='reason';reason.textContent=w.reason;result.append(reason);turn.append(result);}
  if(assistantAttachment===attachment)removeAssistantAttachment();else if(!assistantAttachment)renderAssistantAttachment();
 }catch(e){turn.querySelector('.chat-answer').textContent=e.message+' Запрос сохранён в поле ввода — можно отправить ещё раз.';$('assistant-input').value=message;}
 finally{chatBusy=false;$('send-button').disabled=false;$('voice-button').disabled=false;root.scrollTop=Math.max(0,root.scrollTop+turn.getBoundingClientRect().top-root.getBoundingClientRect().top);}
}
$('assistant-form').onsubmit=e=>{e.preventDefault();askAssistant($('assistant-input').value.trim());};$('assistant-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();askAssistant($('assistant-input').value.trim());}};document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>askAssistant(b.dataset.prompt));
let speech=null;
$('voice-button').onclick=()=>{const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Speech){notice('В этом браузере голосовой ввод недоступен. Используйте микрофон клавиатуры телефона или введите запрос.');$('assistant-input').focus();return;}if(speech){speech.stop();return;}speech=new Speech();speech.lang='ru-RU';speech.interimResults=true;speech.continuous=false;const before=$('assistant-input').value;speech.onstart=()=>{$('voice-button').classList.add('recording');$('voice-button').setAttribute('aria-label','Остановить запись');$('voice-status').textContent='Слушаю… Нажмите, чтобы остановить';};speech.onresult=e=>{$('assistant-input').value=(before+' '+Array.from(e.results).map(r=>r[0].transcript).join(' ')).trim().slice(0,1600);};speech.onerror=e=>{notice(e.error==='not-allowed'?'Разрешите микрофон в настройках браузера или напишите запрос.':'Не удалось распознать речь. Попробуйте ещё раз или напишите запрос.');};speech.onend=()=>{speech=null;$('voice-button').classList.remove('recording');$('voice-button').setAttribute('aria-label','Продиктовать запрос');$('voice-status').textContent='Проверьте текст и нажмите отправить';};try{speech.start();}catch{speech=null;notice('Голосовой ввод не запустился. Можно написать запрос.');}};
document.addEventListener('visibilitychange',()=>{if(document.hidden)speech?.stop();});
async function initializeProduct(){const cached=store.get('wine-product',null);if(cached){productData=cached;renderDiscovery();updatePreferenceCopy();}try{const data=await api('bootstrap');productData=data;store.set('wine-product',data);data.collection.forEach(remember);renderDiscovery();updatePreferenceCopy();if($('assistant-mode'))$('assistant-mode').textContent=data.assistant_mode==='ai'?'ИИ-помощник · рекомендации по каталогу':'Подбор по каталогу';}catch{if(!cached){$('discovery-list').innerHTML=empty('info','Каталог пока недоступен','Проверьте интернет и обновите страницу.',false);}}}
const initPromise=initializeProduct();
// Opening the scanner leaves the camera off until the visitor taps its control.
cameraToggleState('idle');
scannerState('idle','Включите камеру или загрузите фото');
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
checkConnection();
