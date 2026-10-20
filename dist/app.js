const sharedCollectionToken=new URLSearchParams(location.search).get('collection');
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

let retryFile=null, scanPreviewURL=null;
const searchReel=new WineSearchReel($('wine-reel'));
$('open-scan-result').onclick=()=>searchReel.complete();
function notice(message,canRetry=false) {$('notice-text').textContent=message;if($('retry-scan'))$('retry-scan').hidden=!canRetry;$('notice').hidden=false;}
function openDialog(id) {$(id).showModal();document.body.classList.add('modal-open');}
function closeDialog(dialog) {dialog.close();document.body.classList.toggle('modal-open',!!document.querySelector('dialog[open]'));}
document.querySelectorAll('[data-close-dialog]').forEach(b=>b.addEventListener('click',()=>closeDialog(b.closest('dialog'))));
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('close',()=>document.body.classList.toggle('modal-open',!!document.querySelector('dialog[open]'))));
$('dismiss-notice').onclick=()=>{$('notice').hidden=true;};
if($('retry-scan'))$('retry-scan').onclick=()=>{if(retryFile)processFile(retryFile);};

function scannerState(state,message='') {
 $('viewfinder').dataset.cameraState=state;
 $('camera-feedback').textContent=message;
 $('camera-feedback').hidden=state==='live'||state==='processing'||state==='idle';
 if($('lens-status'))$('lens-status').textContent=state==='live'?'Камера включена':state==='requesting'?'Открываем камеру':'Камера выключена';
 if($('shutter-caption'))$('shutter-caption').textContent=state==='live'?'Снять этикетку':state==='requesting'?'Открываем…':'Включить камеру';
}
function cameraToggleState(state) {
 const button=$('camera-button'),slot=$('camera-toggle-slot');
 button.setAttribute('aria-pressed',state==='idle'?'false':'true');
 button.querySelector('use').setAttribute('href',state==='live'||state==='requesting'?'#i-close':'#i-camera');
 button.setAttribute('aria-label',state==='live'?'Выключить камеру':state==='requesting'?'Отменить включение камеры':'Включить камеру');
 if(state==='live'){
  slot.prepend(button);slot.hidden=false;$('history-camera-button').hidden=true;
 }else{
  $('shutter-button').parentElement.prepend(button);slot.hidden=true;$('history-camera-button').hidden=!productData.account?.authenticated;
 }
}
function stopCamera() {
 cameraGeneration++;cameraStarting=false;
 if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;
 $('camera').srcObject=null;$('camera').hidden=true;$('shutter-button').hidden=true;
 $('camera-button').hidden=false;$('camera-button').disabled=busy;cameraToggleState('idle');
 $('camera-tip').hidden=true;$('viewfinder').classList.remove('live');
 if(!busy){if(scanPreviewURL){URL.revokeObjectURL(scanPreviewURL);scanPreviewURL=null;}$('capture-preview').removeAttribute('src');$('finder-idle').hidden=false;$('capture-preview').hidden=true;scannerState('idle','Включите камеру или загрузите фото');}
}
function turnOffCamera() {cameraWanted=false;stopCamera();}
function cancelScan() {job++;controller?.abort();controller=null;setBusy(false);stopCamera();$('camera-button').focus({preventScroll:true});}
const views=['scan','result','history','collection','taste','ranking','assistant','account'];
let accountReady=false;
let currentWine=null,previousView='scan',collectionFilter='favorite',rankingMode='scans',productData={preferences:{},collection:[],recommendations:[]},wineCache=new Map(),viewJob=0,assistantAttachment=null;
function syncAccountControls(account){
 const authenticated=!!account?.authenticated;productData.account=account||{authenticated:false};
 const label=$('header-account-label');label.textContent=authenticated?account.username:'Логин';label.parentElement.title=authenticated?account.username:'Войти в аккаунт';label.parentElement.setAttribute('aria-label',authenticated?'Личный кабинет: '+account.username:'Войти в аккаунт');
 document.querySelectorAll('[data-view="history"]').forEach(button=>button.hidden=!authenticated||(button.id==='history-camera-button'&&!!stream));
 $('catalog-history').hidden=!authenticated;
 if(authenticated)document.querySelectorAll('.chat-auth-actions').forEach(actions=>actions.parentElement.replaceChildren('Вы вошли. Можно продолжить разговор с сомелье.'));
 if(!authenticated&&['history','taste','collection','ranking'].includes(currentView))showView('account',false);
}
function requireAccount(){
 if(productData.account?.authenticated)return true;
 window.collectionUI?.signIn();return false;
}
function showView(name,push=true) {
 if(['collection','taste','ranking','history'].includes(name)){
  if(!accountReady){initPromise.then(()=>{if(accountReady)showView(name,push);});return;}
  if(!productData.account?.authenticated)name='account';
 }
 if(!views.includes(name))name='scan';if(name==='history'&&!productData.account?.authenticated)name='account';const changed=currentView!==name;
 if(changed)$('notice').hidden=true;
 if(name==='assistant'&&currentView==='result')attachScan(currentResult);
 if(name!=='scan'){cameraWanted=false;if(busy)cancelScan();else stopCamera();}
 if(name==='result'&&currentView!=='result')previousView=currentView;
 currentView=name;document.body.dataset.screen=name;views.forEach(view=>$(view+'-view').hidden=view!==name);
 document.querySelectorAll('[data-view]').forEach(b=>{const active=b.dataset.view===(name==='result'?'scan':name==='history'?'collection':name);b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
 if(name==='history'){renderHistory();window.collectionUI?.loadHistory();}if(name==='collection')renderCollection();if(name==='ranking')loadRanking();if(name==='account')window.collectionUI?.account();if(name==='collection')window.collectionUI?.refresh();if(name==='taste')window.tasteUI?.show();
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
  scannerState('unavailable',error.name==='NotAllowedError'?'Разрешите камеру или загрузите готовое фото вина':error.name==='NotFoundError'?'Камера не найдена. Можно загрузить фото':'Камера недоступна. Попробуйте загрузить фото');
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
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&currentView==='scan'){if(busy)cancelScan();else if(cameraWanted)turnOffCamera();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera();else{checkConnection();if(cameraWanted&&currentView==='scan'&&!busy)startCamera(true);}});
window.addEventListener('pagehide',stopCamera);

function scanPhase(phase) {
 $('viewfinder').dataset.scanPhase=phase;
 $('processing-title').textContent=phase==='prepare'?'Готовим снимок':'Ищем ваше вино';
 $('processing-detail').textContent=phase==='prepare'?'Сейчас начнём поиск':'Сравниваем этикетку с каталогом';
 if(phase==='search'){
  const displaySlugs=["avtohtonnoe-vino-kryma-beloe-suhoe", "aligote-barrel-2024", "fanagoriya-100-ottenkov-krasnogo-kaberne-kaberne-sovinon-krasnoe-suhoe-135", "usadba-rodnoe-gnezdo-4-elements-kaberne-sovinon-krasnoe-suhoe-15", "vinodelnya-vedernikov-5050-tsimlyanskiy-sibirkovyy-ekstra-bryut-beloe-12", "shato-pino-agpoezdnik-beloe-shardone-suhoe-13", "olymp-winery-adagum-cabernet-franc-kaberne-fran-krasnoe-suhoe-11", "agora-rosa-viva-cabernet-franc", "chteau-le-grand-vostock-aligote-aligote-beloe-suhoe-125", "vino-nebo-aligote-sur-lie-aligote-beloe-suhoe-127", "alma-valley-locantita-merlot-cabernet-franc", "amelia"];
  const wines=displaySlugs.map(slug=>({slug,image:portalCardData[slug]?.bottleImage})).filter(w=>w.image);
  if(!wines.length)wines.push(...(productData.recommendations||[]).filter(w=>w.image).slice(0,12));
  searchReel.start(wines);
 }
}
function setBusy(value) {
 busy=value;
 if(value){
  scannerState('processing');scanPhase('prepare');
  $('search-match-title').textContent='Вина каталога';$('search-match-detail').textContent='Ваше вино появится в центре';
  $('cancel-scan').hidden=false;$('open-scan-result').hidden=true;
 }else{delete $('viewfinder').dataset.scanPhase;searchReel.stop();}
 $('processing').hidden=!value;
 const controls=document.querySelector('.capture-controls');controls.inert=value;
 $('camera-button').disabled=value;$('gallery-button').disabled=value;$('shutter-button').disabled=value;
 if(value)$('cancel-scan').focus({preventScroll:true});
}
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
    scanPreviewURL=URL.createObjectURL(upload);$('capture-preview').src=scanPreviewURL;$('capture-preview').hidden=false;$('finder-idle').hidden=true;
    await initPromise;if(ownJob!==job)return;scanPhase('search');const data=new FormData();data.append('image',upload,upload.name||'label.jpg');const r=await fetch('/api/predict',{method:'POST',headers:{'X-Device-ID':deviceId},body:data,signal});
    let result;try{result=await r.json();}catch{const error=Error('Не удалось получить ответ сервера. Проверьте соединение.');error.retryable=r.status>=500;throw error;}
    if(!r.ok){const error=Error(typeof result.detail==='string'?result.detail:r.status===429?'Сканер занят. Повторите через несколько секунд.':r.status===503||r.status===504?'Распознавание временно недоступно. Попробуйте позже.':r.status===413?'Файл больше 24 МБ. Выберите другое фото.':'Не удалось распознать снимок.');error.retryable=[429,503,504].includes(r.status);throw error;}
    if(ownJob!==job)return;
    const entry={...result,server_id:result.id,thumb,id:crypto.randomUUID(),created_at:new Date().toISOString()};entries.unshift(entry);entries=entries.slice(0,24);
    while(entries.length>1&&!store.set('wine-history',entries))entries.pop();
    const saved=store.set('wine-history',entries);clearTimeout(timer);
    if(entry.wine?.slug&&entry.no_wine!==true){
     $('viewfinder').dataset.scanPhase='found';$('processing-title').textContent='Нашли в каталоге';$('processing-detail').textContent='Открываем карточку вашего вина';
     $('search-match-title').textContent=entry.wine.name||entry.wine.slug;$('search-match-detail').textContent=entry.wine.winery||'';
     $('cancel-scan').hidden=true;$('open-scan-result').hidden=false;$('open-scan-result').focus({preventScroll:true});
     await searchReel.finish({...entry.wine,image:portalCardData[entry.wine.slug]?.bottleImage||entry.wine.image||'/assets/no-bottle.svg'});
     if(ownJob!==job)return;
    }
    setBusy(false);renderResult(entry);$('wine-card').classList.add('scan-arrival');refreshRecommendations();if(!saved)notice('Результат открыт, но в хранилище телефона нет места для истории.');
  } catch(error) {if(ownJob!==job)return;const canRetry=signal.aborted||error instanceof TypeError||error.retryable===true;retryFile=canRetry?file:null;notice(signal.aborted?'Ответ задержался. Попробуйте снова при устойчивом интернете.':(error instanceof TypeError?'Нет связи с распознаванием. Проверьте интернет.':error.message),canRetry);}
  finally{clearTimeout(timer);if(ownJob===job){controller=null;setBusy(false);stopCamera();}}
}

const esc=escapeHTML;
async function api(path,body){const r=await fetch('/api/product/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(path==='assistant'?30000:12000)});let data;try{data=await r.json();}catch{throw Error('Сервис не ответил. Попробуйте ещё раз.');}if(!r.ok){const error=Error(typeof data.detail==='string'?data.detail:data.detail?.message||'Не удалось сохранить. Попробуйте ещё раз.');error.code=data.detail?.code;error.status=r.status;throw error;}return data;}
function remember(w){wineCache.set(w.slug,w);return w;}
function img(w,cls=''){return `<img class="${cls}" src="${esc(portalCardData[w.slug]?.bottleImage||w.image||'/assets/no-bottle.svg')}" alt="${esc(w.name)}" loading="lazy">`;}
function handleBrokenImages(root){root.querySelectorAll('img').forEach(im=>im.addEventListener('error',()=>{if(!im.src.endsWith('/assets/no-bottle.svg')){im.src='/assets/no-bottle.svg';}},{once:true}));}
function empty(icon,title,text,action){return `<div class="empty-state">${icons(icon)}<h2>${esc(title)}</h2><p>${esc(text)}</p>${action?`<button class="button primary" data-empty-scan>Сканировать вино ${icons('arrow')}</button>`:''}</div>`;}
function bindEmpty(root){root.querySelectorAll('[data-empty-scan]').forEach(b=>b.onclick=()=>showView('scan'));}
function miniWine(w,{showOpenLabel=true}={}){
 remember(w);const el=document.createElement('article');el.className='mini-wine';
 const rating=w.rating?`<span class="mini-rating" aria-label="Ваша оценка ${w.rating} из 5"><img src="/assets/portal-card/public-rating.svg" alt="" width="20" height="24"><b>${w.rating}</b><small>моя оценка</small></span>`:'';
 el.innerHTML=`<button class="mini-wine-open" aria-label="Открыть вино: ${esc(w.name)}"><div class="mini-visual">${img(w)}${rating}</div><div class="mini-copy"><small class="mini-producer">${esc(w.winery)}</small><h3>${esc(w.name)}</h3><small class="mini-category">${esc(w.category)}${w.region?' · '+esc(w.region):''}</small><p class="mini-meta">${esc(w.grapes)}</p>${showOpenLabel?`<span class="mini-open-label">О вине ${icons('arrow')}</span>`:''}</div></button><button class="mini-save" aria-label="${w.favorite?'Убрать лайк':'Нравится'}: ${esc(w.name)}" aria-pressed="${!!w.favorite}">${icons('heart')}</button>`;
 el.querySelector('.mini-wine-open').onclick=()=>openWine(w);el.querySelector('.mini-save').onclick=async(e)=>{await changePreference(w.slug,{favorite:!getPreference(w.slug).favorite},e.currentTarget);};handleBrokenImages(el);return el;
}
function getPreference(slug){return productData.preferences[slug]||{rating:0,favorite:false};}
const preferencePending=new Set();
async function changePreference(slug,values,button){if(!requireAccount())return;if(preferencePending.has(slug))return;preferencePending.add(slug);if(button)button.disabled=true;if(currentWine?.slug===slug)document.querySelectorAll("#wine-personal button,#save-wine").forEach(b=>b.disabled=true);try{await initPromise;const w=await api('preferences/'+encodeURIComponent(slug),values);remember(w);productData.preferences[slug]={rating:w.rating,favorite:w.favorite};if(values.rating!==undefined)window.tasteUI?.invalidate();productData.collection=productData.collection.filter(x=>x.slug!==slug);if(w.rating||w.favorite)productData.collection.unshift(w);store.set('wine-product',productData);if(currentWine?.slug===slug){currentWine=w;updatePersonal(w);}renderDiscovery();if(window.collectionUI)await window.collectionUI.refresh();if(currentView==='collection')renderCollection();updatePreferenceCopy();refreshRecommendations();notice(values.rating===0?'Оценка снята':values.rating?'Ваша оценка сохранена':w.favorite?'Лайк сохранён':'Лайк убран');}catch(e){notice(e.message);}finally{preferencePending.delete(slug);if(button)button.disabled=false;if(currentWine?.slug===slug)document.querySelectorAll("#wine-personal button,#save-wine").forEach(b=>b.disabled=false);}}
let portalCardData={};
async function loadPortalCardData(){try{const r=await fetch('/assets/portal-card-data.json?v=24',{signal:AbortSignal.timeout(6000)});if(r.ok)portalCardData=await r.json();}catch{}}
const portalGlyph=name=>{
 const paths={temperature:'<path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z"/>',percent:'<path d="m5 19 14-14"/><circle cx="7" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',dishes:'<path d="m16 2-2.3 2.3a3 3 0 0 0 0 4.2l1.8 1.8a3 3 0 0 0 4.2 0L22 8M19 5 12 12M15 15l7 7M3 3l12 12-2 2-7-7a5 5 0 0 1-3-7ZM3 22l6-6"/>',region:'<path d="m3 19 6-8 4 5 3-4 5 7M3 21h18"/><circle cx="17" cy="5" r="2"/>',grape:'<circle cx="9" cy="8" r="3"/><circle cx="15" cy="8" r="3"/><circle cx="12" cy="13" r="3"/><circle cx="12" cy="18" r="3"/><path d="M12 5V2l5 1"/>'};
 return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]||paths.grape}</svg>`;
};
function portalDetail(label,value,image,kind,extra=''){
 if(!value&&!extra)return '';
 const visual=image?`<img src="${esc(image)}" alt="" width="44" height="44">`:kind==='color'?'<span class="portal-color"></span>':portalGlyph(kind);
 return `<div class="portal-detail"><div class="portal-detail-visual">${visual}</div><div><dt>${esc(label)}</dt><dd>${esc(value)}</dd>${extra?`<dd>${esc(extra)}</dd>`:''}</div></div>`;
}
function portalCardMarkup(w,entry){
 const d=portalCardData[w.slug]||{};
 const facts=portalDetail('Регион',w.region,d.regionImage,'region')+portalDetail('Сорт винограда',w.grapes,d.grapeImage,'grape')+portalDetail('Категория и цвет',w.category,null,'color',d.color);
 const dishes=(d.dishes?.length?d.dishes:w.pairings.map(name=>({name}))).filter(x=>x.name);
 const metric=(label,value,kind)=>value?`<div class="portal-metric"><span class="portal-metric-icon">${portalGlyph(kind)}</span><div><p>${label}</p><strong>${esc(value)}</strong></div></div>`:'';
 const metrics=metric('Температура<br>подачи',w.temperature?.replace(/\s*°C/,'°C'),'temperature')+metric('Крепость<br>вина',w.alcohol?String(w.alcohol)+'%':'','percent')+(dishes.length?`<div class="portal-dishes ${dishes.length>2?'portal-dishes-many':''}"><div class="portal-dishes-heading"><span class="portal-metric-icon">${portalGlyph('dishes')}</span><p>Сочетание<br>с&nbsp;блюдами</p></div><ul>${dishes.map(x=>`<li>${x.image?`<img src="${esc(x.image)}" alt="" width="72" height="72" loading="lazy">`:`<span class="portal-dish-fallback">${icons('food')}</span>`}<span>${esc(x.name)}</span></li>`).join('')}</ul></div>`:'');
 const manufacturer=d.manufacturerUrl?`<a href="${esc(d.manufacturerUrl)}" target="_blank" rel="noopener noreferrer">${esc(w.winery)}</a>`:`<span>${esc(w.winery)}</span>`;
 return `<article class="portal-wine" aria-labelledby="result-title"><div class="portal-hero"><div class="portal-info"><div class="portal-title"><h1 id="result-title">${esc(w.name)}</h1><div class="portal-manufacturer">${manufacturer}</div><button class="portal-rating-badge" id="wine-rating-link" aria-label="Перейти к оценке вина"><img src="/assets/portal-card/public-rating.svg" width="24" height="24" alt=""><span id="wine-rating-summary"></span>${icons('chevron')}</button></div><dl class="portal-facts portal-facts-desktop">${facts}</dl></div><div class="portal-landscape">${d.vineyardImage?`<img class="portal-grapes-background" src="${esc(d.vineyardImage)}" alt="Виноград: ${esc(w.grapes)}" loading="eager">`:''}<div class="portal-metrics">${metrics}</div></div><div class="portal-bottle">${img({...w,image:d.bottleImage||w.image},'portal-bottle-image')}</div></div><section id="wine-personal" class="portal-personal" aria-label="Ваша оценка вина"></section><div class="portal-mobile-details"><div class="portal-mobile-overview"><dl class="portal-facts">${facts}</dl>${d.vineyardImage?`<img class="portal-mobile-landscape" src="${esc(d.vineyardImage)}" alt="Виноград: ${esc(w.grapes)}" loading="lazy">`:''}</div><div class="portal-metrics">${metrics}</div></div><div class="portal-description"><p>${esc(w.description||'Описание пока не добавлено в каталог.')}</p><div class="portal-card-actions"><button id="save-wine" class="portal-save" aria-pressed="false">${icons('heart')}<span>Нравится</span></button>${w.portal_url?`<a href="${esc(w.portal_url)}" target="_blank" rel="noopener noreferrer">Карточка на «Своё Вино» ↗</a>`:''}</div>${entry?.server_id?`<button id="ask-about-scan" class="portal-assistant">${icons('spark')}Подобрать блюдо с помощником ${icons('arrow')}</button>`:''}${entry?.thumb?`<details class="portal-scan-photo"><summary>Ваш снимок</summary><img src="${esc(entry.thumb)}" alt="Ваш снимок этикетки"></details>`:''}</div>${window.tasteUI?.sheet(w)||''}${similarWineMarkup(w)}</article>`;
}
function similarWineMarkup(w){
 if(!w.similar?.length)return '';
 return `<section class="wine-similar" aria-labelledby="wine-similar-title"><h2 id="wine-similar-title">Это вино похоже на</h2><div class="wine-similar-list">${w.similar.map(x=>`<button class="wine-similar-item" data-similar-slug="${esc(x.slug)}">${img(x)}<span><small>${esc(x.winery)}</small><strong>${esc(x.name)}</strong><small>${esc(x.reason)}</small></span>${icons('chevron')}</button>`).join('')}</div></section>`;
}
function updatePersonal(w){
 const el=$('wine-personal');if(!el)return;
 const available=!!productData.account?.authenticated&&w.preferences_available!==false;el.hidden=!available;
 const p=getPreference(w.slug);
 const active=document.activeElement;const focused=active?.closest('#wine-personal')?active.dataset.rating||active.id:null;
 if(available){
  el.innerHTML=`<h2>Поставь свою оценку</h2><div class="portal-glass-rating" role="group" aria-label="Оценить вино от 1 до 5">${[1,2,3,4,5].map(n=>`<button class="portal-glass ${n<=p.rating?'is-filled':''}" data-rating="${n}" aria-label="Оценить на ${n} из 5" aria-pressed="${n===p.rating}"><span><img class="portal-glass-empty" src="/assets/portal-card/public-rating-empty.svg" alt="" width="48" height="48"><img class="portal-glass-fill" src="/assets/portal-card/public-rating-wine-in-glass.svg" alt="" width="48" height="48"></span></button>`).join('')}</div><div class="portal-rating-feedback"><span aria-live="polite">${p.rating?'Ваша оценка — '+p.rating+' из 5':'Нажмите на бокал'}</span><button id="clear-rating" ${p.rating?'':'hidden'}>Снять оценку</button></div>`;
  el.querySelectorAll('[data-rating]').forEach(b=>b.onclick=()=>changePreference(w.slug,{rating:Number(b.dataset.rating)},b));
  $('clear-rating').onclick=e=>changePreference(w.slug,{rating:0},e.currentTarget);
  if(focused){const target=focused==='clear-rating'?$('clear-rating'):el.querySelector(`[data-rating="${focused}"]`);if(target&&!target.hidden)target.focus({preventScroll:true});}
 }
 const save=$('save-wine');if(save){save.hidden=!available;save.setAttribute('aria-pressed',String(p.favorite));save.querySelector('span').textContent=p.favorite?'Нравится ✓':'Нравится';save.onclick=e=>changePreference(w.slug,{favorite:!getPreference(w.slug).favorite},e.currentTarget);}
 window.collectionUI?.attach(w);
 const ratingSummary=$('wine-rating-summary');if(ratingSummary)ratingSummary.textContent=w.average_rating?`Рейтинг в приложении ${Number(w.average_rating).toLocaleString('ru-RU',{maximumFractionDigits:2})}`:'Оценить вино';
 const ratingLink=$('wine-rating-link');if(ratingLink){ratingLink.hidden=!available;ratingLink.title=w.rating_count?`Оценок в приложении: ${w.rating_count}`:'Поставьте первую оценку';ratingLink.onclick=()=>{el.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});el.querySelector('[data-rating]')?.focus({preventScroll:true});};}
}

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
 wineCollectionsJob++;$('wine-collections').hidden=true;$('wine-collections').replaceChildren();
 $('wine-card').classList.remove('scan-arrival');
 if(!wine){
  if(!entry)return;
  currentResult=entry;currentWine=null;
  const noWine=entry.no_wine===true;
  $('wine-card').innerHTML=`<div class="empty-state no-wine-result">${entry.thumb?`<img class="no-wine-photo" src="${esc(entry.thumb)}" alt="Ваш снимок">`:icons('scan')}<h1 id="result-title">${noWine?'Не удалось распознать вино':'Карточка недоступна'}</h1>${noWine?'':'<p>Результат сохранён в истории, но карточку сейчас не удалось открыть.</p>'}<div class="result-actions"><button class="button primary" id="no-wine-rescan">Снять ещё раз ${icons('arrow')}</button>${entry.server_id?'<button class="button secondary" id="ask-about-scan">Спросить помощника</button>':''}</div></div>`;
  $('no-wine-rescan').onclick=()=>showView('scan');if($('ask-about-scan'))$('ask-about-scan').onclick=()=>{showView('assistant');$('assistant-input').value='Помоги определить вино на фото';$('assistant-input').focus();};renderAlternatives(entry,null);
  showView('result',push);$('result-title').setAttribute('tabindex','-1');$('result-title').focus({preventScroll:true});return;
 }
 currentResult=entry;const w=remember({...wine,name:wine.name||wine.slug||'Вино из каталога',winery:wine.winery||'',category:wine.category||'',region:wine.region||'',grapes:wine.grapes||'',description:wine.description||'',pairings:Array.isArray(wine.pairings)?wine.pairings:[],portal_url:wine.portal_url||'',average_rating:Number.isFinite(wine.average_rating)?wine.average_rating:null,rating_count:Number.isFinite(wine.rating_count)?wine.rating_count:0,...getPreference(wine.slug)});currentWine=w;
 $('wine-card').innerHTML=portalCardMarkup(w,entry);
 const recognizedScan=!!entry&&!entry.no_wine&&entry.wine?.slug===w.slug;
 const gradient=portalCardData[w.slug]?.gradient;if(gradient&&/^linear-gradient\([#0-9a-zA-Z.,%()\s-]+\)$/.test(gradient))$('wine-card').querySelectorAll('.portal-color').forEach(el=>el.style.background=gradient);
 updatePersonal(w);$('wine-card').querySelectorAll('[data-similar-slug]').forEach(b=>b.onclick=()=>openWine(w.similar.find(x=>x.slug===b.dataset.similarSlug)));handleBrokenImages($('wine-card'));window.tasteUI?.bindSheet($('wine-card'));
 $('wine-card').querySelectorAll('.portal-grapes-background,.portal-mobile-landscape,.portal-detail-visual img,.portal-dishes img').forEach(im=>im.addEventListener('error',()=>{im.hidden=true;},{once:true}));
 if($('ask-about-scan'))$('ask-about-scan').onclick=()=>{showView('assistant');const name=String(wine?.name||'').trim();$('assistant-input').value=recognizedScan?(name?'Что приготовить к «'+name+'»?':'Что приготовить к этому вину?'):'Помоги определить вино на фото';$('assistant-input').focus();};
 renderAlternatives(entry,w);showView('result',push);loadWineCollections(w);$('result-title').setAttribute('tabindex','-1');$('result-title').focus({preventScroll:true});
}
let wineCollectionsJob=0;
const wineCollectionRequests=new Map();
async function loadWineCollections(w){
 const job=++wineCollectionsJob,root=$('wine-collections');
 if(!productData.account?.authenticated||!w.slug||w.preferences_available===false)return;
 try{
  let pending=wineCollectionRequests.get(w.slug);
  if(!pending){pending=api('shelves/ranking?slug='+encodeURIComponent(w.slug));wineCollectionRequests.set(w.slug,pending);pending.finally(()=>{if(wineCollectionRequests.get(w.slug)===pending)wineCollectionRequests.delete(w.slug);}).catch(()=>{});}
  const result=await pending;
  if(job!==wineCollectionsJob||currentView!=='result'||currentWine?.slug!==w.slug)return;
  root.replaceChildren();if(!result.items.length){root.hidden=true;return;}
  root.innerHTML='<h2 id="wine-collections-title">Есть в коллекциях</h2><div class="wine-collection-list"></div>';
  for(const list of result.items){
   const button=document.createElement('button');button.className='wine-collection-link';button.setAttribute('aria-label','Открыть коллекцию «'+list.name+'»');
   button.innerHTML=`<span class="wine-collection-bottles" aria-hidden="true">${list.wines.slice(0,3).map(w=>img(w)).join('')}</span><span class="wine-collection-copy"><strong>${esc(list.name)}</strong><span>${esc(list.author)}</span>${list.copies?`<small>${list.copies} ${({one:'сохранение',few:'сохранения',many:'сохранений',other:'сохранения'}[new Intl.PluralRules('ru').select(list.copies)])}</small>`:''}</span><span class="wine-collection-arrow" aria-hidden="true">↗</span>`;
   button.onclick=()=>window.collectionUI.openFromWine(list.id);handleBrokenImages(button);root.lastElementChild.append(button);
  }
  root.hidden=false;
 }catch{
  if(job!==wineCollectionsJob||currentView!=='result'||currentWine?.slug!==w.slug)return;
  root.innerHTML='<p class="muted">Не удалось загрузить коллекции. <button class="text-button">Повторить</button></p>';root.hidden=false;root.querySelector('button').onclick=()=>loadWineCollections(w);
 }
}
async function openWine(w,entry=null){const own=++viewJob;await initPromise;if(own!==viewJob)return;renderResult(entry,w);try{const fresh=await api('wines/'+encodeURIComponent(w.slug));if(own===viewJob&&currentView==='result'&&currentWine.slug===w.slug)renderResult(entry,fresh,false);}catch{if(!w.description)notice('Не удалось обновить карточку. Сохранённые данные доступны.');}}
function wineRow(w,meta,action){remember(w);const b=document.createElement('button');b.className='wine-row';b.innerHTML=`${img(w)}<span class="row-copy"><small>${esc(w.winery)}</small><b>${esc(w.name||w.slug||'Вино из каталога')}</b><small>${esc(meta)}</small></span>${icons('chevron')}`;b.onclick=action;handleBrokenImages(b);return b;}
function historyNoWineRow(entry,date){const b=document.createElement('button');b.className='wine-row';b.innerHTML=`<img src="${esc(entry.thumb||'/assets/no-bottle.svg')}" alt="Ваш снимок"><span class="row-copy"><small>Сканирование</small><b>${entry.no_wine?'Не удалось распознать вино':'Карточка недоступна'}</b><small>${esc(date)}</small></span>${icons('chevron')}`;b.onclick=()=>renderResult(entry);handleBrokenImages(b);return b;}
function renderHistory(){const list=$('history-list');list.replaceChildren();$('clear-history').hidden=!entries.length;if(!entries.length){list.innerHTML=empty('history','Первое открытие ещё впереди','Снимите этикетку — найденная карточка появится здесь.',true);bindEmpty(list);return;}for(const entry of entries){const date=new Date(entry.created_at).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});list.append(entry.wine?.slug?wineRow(entry.wine,date,()=>openWine(entry.wine,entry)):historyNoWineRow(entry,date));}}
$('clear-history').onclick=()=>openDialog('clear-dialog');$('confirm-clear').onclick=()=>{entries=[];store.set('wine-history',entries);closeDialog($('clear-dialog'));renderHistory();};
async function refreshRecommendations(){try{const data=await api('recommendations');productData.recommendations=data.items;store.set('wine-product',productData);renderDiscovery();}catch{}}
function renderDiscovery(){if(!$('discovery-list'))return;const personalized=Object.values(productData.preferences).some(x=>x.rating>=4);$('discovery-title').textContent=personalized?'В вашем вкусе':'Откройте новое';$('discovery-eyebrow').textContent=personalized?'ВАШ ВКУС — НАШ ОРИЕНТИР':'ЗНАКОМСТВО С КАТАЛОГОМ';$('discovery-list').replaceChildren();for(const w of productData.recommendations.slice(0,4))$('discovery-list').append(miniWine({...w,...getPreference(w.slug)}));if(!productData.recommendations.length)$('discovery-list').innerHTML=empty('glass','Каталог скоро появится','Проверьте соединение с сервером.',false);}
function renderCollection(){window.collectionUI?.render();}
let searchJob=0,searchTimer;
async function searchWines(){if(window.collectionUI){return window.collectionUI.search();}const q=$('wine-search').value.trim(),own=++searchJob,root=$('search-results');root.hidden=!q;if(!q)return;root.innerHTML='<p class="search-result-title">Ищем в каталоге…</p>';try{await initPromise;const data=await api('search?q='+encodeURIComponent(q));if(own!==searchJob)return;root.innerHTML=`<div class="search-result-heading"><p class="search-result-title">${data.items.length?'Найденные вина · '+data.items.length:'Не нашли такое вино. Попробуйте название винодельни или сорт.'}</p><button class="text-button" id="close-search" aria-label="Закрыть результаты поиска">${icons('close')}</button></div><div class="search-results-list"></div>`;$('close-search').onclick=()=>{$('wine-search').value='';searchWines();$('wine-search').focus();};data.items.forEach(w=>root.lastElementChild.append(wineRow(w,w.category+' · '+w.grapes,()=>openWine(w))));}catch(e){if(own===searchJob)root.textContent=e.message;}}
$('catalog-search').onsubmit=e=>{e.preventDefault();clearTimeout(searchTimer);searchWines();};$('wine-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(searchWines,350);};
let rankJob=0;
async function loadRanking(){
 const own=++rankJob,mode=rankingMode;
 document.querySelectorAll('[data-ranking]').forEach(b=>{b.classList.toggle('active',b.dataset.ranking===mode);b.setAttribute('aria-pressed',String(b.dataset.ranking===mode));});
 $('ranking-total').textContent='';$('ranking-list').innerHTML='<p class="muted" role="status">Собираем рейтинг…</p>';
 try{
  await initPromise;const data=await api(mode==='collections'?'shelves/ranking':'ranking?mode='+mode);if(own!==rankJob)return;
  $('ranking-total').textContent=mode==='collections'?'':mode==='scans'?`Всего сканов: ${data.total_scans}`:`Всего оценок: ${data.total_ratings}`;
  const root=$('ranking-list');root.replaceChildren();
  if(!data.items.length){root.innerHTML=empty(mode==='collections'?'heart':mode==='scans'?'scan':'star',mode==='collections'?'Здесь появятся коллекции':mode==='scans'?'Первый скан — за вами.':'Первая оценка — за вами.',mode==='collections'?'Опубликуйте подборку, которую захотят сохранить.':mode==='scans'?'Здесь появятся вина, которые сканируют чаще всего.':'Поставьте оценку в карточке вина, чтобы начать общий рейтинг.',mode!=='collections');bindEmpty(root);}
  data.items.forEach((w,i)=>{
   const b=document.createElement('button');b.className='ranking-row'+(mode==='collections'?' ranking-collection-row':'');
   if(mode==='collections'){
    b.setAttribute('aria-label','Открыть коллекцию «'+w.name+'»');
    b.innerHTML=`<span class="rank-number">${w.copies?String(i+1).padStart(2,'0'):'—'}</span><span class="ranking-bottles" aria-hidden="true">${w.wines.slice(0,3).map(w=>img(w)).join('')}</span><span class="row-copy"><small>${esc(w.author)}</small><b>${esc(w.name)}</b><small>${w.wines.length} ${({one:'вино',few:'вина',many:'вин',other:'вина'}[new Intl.PluralRules('ru').select(w.wines.length)])}</small></span><span class="rank-value">${w.copies}<small>${({one:'сохранение',few:'сохранения',many:'сохранений',other:'сохранения'}[new Intl.PluralRules('ru').select(w.copies)])}</small></span>`;
    b.onclick=()=>window.collectionUI.openRanking(w.id);
   }else{
    b.innerHTML=`<span class="rank-number">${String(i+1).padStart(2,'0')}</span>${img(w)}<span class="row-copy"><small>${esc(w.winery)}</small><b>${esc(w.name)}</b><small>${esc(w.category)}</small></span><span class="rank-value">${mode==='scans'?w.scan_count:w.average_rating.toFixed(1)}<small>${mode==='scans'?({one:'сканирование',few:'сканирования',many:'сканирований',other:'сканирования'}[new Intl.PluralRules('ru').select(w.scan_count)]):'из 5 · оценок: '+w.rating_count}</small></span>`;b.onclick=()=>openWine(w);
   }
   handleBrokenImages(b);root.append(b);
  });
 }catch(e){if(own===rankJob)$('ranking-list').innerHTML=empty('info','Не удалось загрузить рейтинг',e.message,false);}
}
document.querySelectorAll('[data-ranking]').forEach(b=>b.onclick=()=>{rankingMode=b.dataset.ranking;loadRanking();});
function updatePreferenceCopy(){if(!$('preference-copy'))return;const n=Object.values(productData.preferences).filter(x=>x.rating>=4).length;$('preference-copy').textContent=n?`Вин в основе подбора: ${n}. Учитываем вина, которым вы поставили 4–5.`:'Сохраняйте вина и ставьте оценки — подбор станет ближе к вашему вкусу.';}
const chatContext=[];let chatBusy=false,lastDish=null,conversationScanId=null;
const chatWelcomeTemplate=$('chat-messages').firstElementChild.cloneNode(true);
function resetAssistantConversation(){chatContext.length=0;lastDish=null;conversationScanId=null;$('chat-messages').replaceChildren(chatWelcomeTemplate.cloneNode(true));}
function isRecipeFollowup(message){
 return /рецепт|ингредиент|приготов|готов(?:ить|к|ят|ится)|пошагов|пожар|запеч|запека|свар|туш(?:ить|и)|испеч|выпека|маринова|сколько\s+(?:минут|времени)|что\s+нужно\s+для|какие\s+продукты|как\s+(?:это|его|ее|её)?\s*сделать/i.test(message);
}
function asksForDishWithWine(message){
 const query=message.toLowerCase();
 return /с\s+чем\s+сочета|(?:какие|какое|что).{0,30}(?:блюд|закуск|гарнир)/.test(query)||
  /(?:блюд|закус|ед[ыу]|гарнир|приготов|подат|сочета|сервир|ужин|к столу)/.test(query)&&
  /(?:вин[оауеым]|к\s*[«"]|к\s+нему|с\s+ним)/.test(query);
}
function asksAboutScan(message){
 return /фото|сним|скан|этикет|распозн|бутылк|(?:это|этого|этом|этому|этим)\s+вин|что\s+за\s+вин|какое\s+это\s+вин/i.test(message);
}
function asksForWineRecommendation(message){
 const query=message.toLowerCase();
 return /(?:подбер|посовет|порекоменд|найд|выбер|предлож|советуешь).{0,90}(?:вин[оа]|бутылк|красн|бел|розов|игрист)/.test(query)||
  /(?:какое|какие).{0,20}вин[оа].{0,50}(?:купить|взять|выбрать|посовет|порекоменд)/.test(query)||
  /(?:ищу|хочу).{0,30}вин[оа]/.test(query);
}
function asksForCollectionRecommendation(message){
 return /(?:подбер|посовет|порекоменд|покаж|найд|выбер|предлож|ищ|хоч|нуж|какую|какие).{0,90}(?:коллекц|подборк|полк|набор)|(?:коллекц|подборк|полк|набор)\S*\s+(?:к|для|под|на)\s/i.test(message)||/^(?:коллекции|подборки)$/i.test(message.trim());
}
function addToListButton(listId,w){
 const button=document.createElement('button');button.type='button';button.className='text-button chat-add-to-list';button.textContent='+ В коллекцию';
 button.onclick=async()=>{button.disabled=true;try{await api('collection/lists/'+encodeURIComponent(listId),{action:'add',slug:w.slug});button.textContent='Добавлено в коллекцию ✓';window.collectionUI?.refresh();}catch(error){button.disabled=false;notice(error.message);}};
 return button;
}
function collectionRecommendationCard(shelf){
 const card=document.createElement('article');card.className='chat-collection';
 const count=Number(shelf.wine_count)||0,matched=Number(shelf.matched_count)||0;
 const noun={one:'вино',few:'вина',many:'вин',other:'вина'}[new Intl.PluralRules('ru').select(count)];
 card.innerHTML=`<div class="chat-collection-bottles" aria-hidden="true">${(shelf.wines||[]).slice(0,4).map(w=>img(w)).join('')}</div><div class="chat-collection-copy"><span class="chat-collection-source">${shelf.owner?'Ваша коллекция':'Из сообщества'} · ${count} ${noun}</span><h3>${esc(shelf.name)}</h3><p>${esc(shelf.reason)}</p>${matched<count?`<small>Под запрос подходят ${matched} из ${count} вин</small>`:''}<button class="text-button">Открыть коллекцию ${icons('arrow')}</button></div>`;
 card.querySelector('button').onclick=()=>window.collectionUI?.openRecommendation(shelf.id,shelf.owner);
 handleBrokenImages(card);return card;
}
function renderAssistantAttachment(){
 const box=$('assistant-attachment');box.hidden=!assistantAttachment;
 const shelf=assistantAttachment?.intent==='collection_fill';
 const taste=assistantAttachment?.intent==='taste_discussion';
 $('assistant-input').placeholder=shelf?'Что добавить в коллекцию?':assistantAttachment?.intent==='dish_pairing'?'Какое блюдо приготовить?':assistantAttachment?'Спросите о снимке…':lastDish?'Вопрос о блюде или рецепте…':'Спросите о вине или блюде…';
 const welcome=$('chat-messages').querySelector('.assistant-welcome');
 if(welcome){welcome.querySelector('h2').textContent=shelf?'Подберём вина в коллекцию':assistantAttachment?.intent==='dish_pairing'?'Что приготовить к этому вину?':assistantAttachment?'Поможем разобраться со снимком':'Чем помочь?';welcome.querySelector('p').textContent=shelf?'Коллекция «'+assistantAttachment.name+'» прикреплена: сомелье видит её вина и повод. Спросите, чем её дополнить, или отправьте готовый запрос.':assistantAttachment?.intent==='dish_pairing'?'Фото вина уже прикреплено. Спросите о блюде или отправьте готовый запрос.':assistantAttachment?'Фото уже прикреплено. Спросите, что на нём.':'Подберём вино, блюдо или коллекцию под ваш вкус.';welcome.querySelector('.prompt-list').hidden=!!assistantAttachment;}
 if(taste){
  $('assistant-input').placeholder='Что хотите узнать о своём вкусе?';
  if(welcome){welcome.querySelector('h2').textContent='Обсудим ваш вкус';welcome.querySelector('p').textContent='Почему вам нравятся эти вина, чем они похожи и что попробовать дальше.';}
 }
 if(!assistantAttachment)return;
 $('attachment-image').src=assistantAttachment.preview;
 $('attachment-image').alt=shelf?'Бутылка из коллекции':'Прикреплённый снимок этикетки';
 $('attachment-label').textContent=assistantAttachment.name||'Снимок скана';
 box.querySelector('strong').textContent=shelf?'Коллекция':'Фото этикетки';
 box.querySelector('.attachment-disclosure').textContent=shelf?'Состав коллекции будет отправлен помощнику':'Фото будет отправлено помощнику';
 $('remove-attachment').setAttribute('aria-label',shelf?'Убрать прикреплённую коллекцию':'Убрать прикреплённое фото');
 box.classList.toggle('is-taste',taste);
 if(taste){
  $('attachment-image').alt='';
  box.querySelector('strong').textContent='Мой вкус';
  $('attachment-label').textContent='Анкета, оценки и свайпы';
  box.querySelector('.attachment-disclosure').textContent='Сомелье учитывает ваши текущие предпочтения';
  $('remove-attachment').setAttribute('aria-label','Убрать профиль вкуса');
 }
}
function attachScan(entry){
 if(!entry?.server_id)return;
 if(conversationScanId!==entry.server_id){chatContext.length=0;lastDish=null;}
 conversationScanId=entry.server_id;
 if(lastDish?.scanId!==entry.server_id)lastDish=null;
 assistantAttachment={scanId:entry.server_id,preview:entry.thumb||'/api/product/scans/'+encodeURIComponent(entry.server_id)+'/image',name:entry.wine?.name||'Снимок скана'};
 assistantAttachment.intent=!entry.no_wine&&entry.wine?.slug===currentWine?.slug?'dish_pairing':null;
 renderAssistantAttachment();
}
// «Подобрать с сомелье» on one of the user's collections: the collection stays attached for the
// whole conversation (follow-ups like «а белое?» stay about it) until the user removes it.
function askAboutCollection(list){
 if(assistantAttachment?.listId!==list.id){chatContext.length=0;lastDish=null;}
 assistantAttachment={listId:list.id,name:list.name,intent:'collection_fill',preview:list.wines?.[0]?(portalCardData[list.wines[0].slug]?.bottleImage||list.wines[0].image):'/assets/no-bottle.svg'};
 showView('assistant');renderAssistantAttachment();
 $('assistant-input').value='Что добавить в эту коллекцию?';$('assistant-input').focus();
}
function askAboutTaste(){
 if(chatBusy){notice('Дождитесь ответа сомелье, затем откройте обсуждение вкуса.');return;}
 if(assistantAttachment?.intent!=='taste_discussion')resetAssistantConversation();
 assistantAttachment={intent:'taste_discussion',name:'Мой вкус',preview:'/assets/achievements/atlas.webp'};
 showView('assistant');renderAssistantAttachment();
 $('assistant-input').value='Расскажи о моём вкусе';
 $('assistant-input').focus({preventScroll:true});
}
function removeAssistantAttachment(){assistantAttachment=null;renderAssistantAttachment();}
$('remove-attachment').onclick=removeAssistantAttachment;
async function askAssistant(message){
 const attachment=assistantAttachment,forCollection=attachment?.intent==='collection_fill',forTaste=attachment?.intent==='taste_discussion';
 message=message.trim()||(forTaste?'Расскажи о моём вкусе.':forCollection?'Что добавить в эту коллекцию?':attachment?.intent==='dish_pairing'?'Что приготовить к этому вину?':attachment?'Помоги определить вино на фото.':'');
 if(chatBusy||!message)return;
 const collectionSwitch=!forTaste&&!forCollection&&asksForCollectionRecommendation(message);
 const wineSwitch=!forTaste&&!forCollection&&!collectionSwitch&&asksForWineRecommendation(message);
 const recipeFollowup=!forTaste&&!forCollection&&!collectionSwitch&&!wineSwitch&&!!lastDish&&isRecipeFollowup(message)&&(!attachment||attachment.scanId===lastDish.scanId);
 const dishPairing=!forTaste&&!forCollection&&!!attachment&&!collectionSwitch&&!wineSwitch&&!recipeFollowup&&asksForDishWithWine(message);
 const usedAttachment=!forTaste&&!forCollection&&!!attachment&&!collectionSwitch&&(dishPairing||asksAboutScan(message)||wineSwitch);
 const intent=forTaste?'taste_discussion':forCollection?'collection_fill':collectionSwitch?'collection_recommendation':recipeFollowup?'dish_recipe':dishPairing?'dish_pairing':null;
 const scanId=recipeFollowup?lastDish.scanId:usedAttachment?attachment.scanId:null;
 const previousContext=wineSwitch?[]:chatContext.slice(-6).map(turn=>({...turn}));
 const request={message,context:previousContext,...(scanId?{scan_id:scanId}:{}),...(intent?{intent}:{}),...(forCollection?{list_id:attachment.listId}:{}),...(recipeFollowup?{dish_name:lastDish.name}:{})};
 // Keep the whole JSON request below the backend's 8192-byte body limit.
 const encoder=new TextEncoder();
 while(encoder.encode(JSON.stringify(request)).length>7600&&previousContext.length){
  if(previousContext[0].content.length>100)previousContext[0].content=previousContext[0].content.slice(0,Math.floor(previousContext[0].content.length/2));
  else previousContext.shift();
 }
 chatBusy=true;$('send-button').disabled=true;$('voice-button').disabled=true;$('assistant-input').value='';
 const root=$('chat-messages');root.querySelector('.assistant-welcome')?.remove();
 const turn=document.createElement('div');turn.className='chat-turn';
 turn.innerHTML=`<div class="chat-user">${usedAttachment?`<img class="chat-attached-photo" src="${esc(attachment.preview)}" alt="Прикреплённый снимок этикетки">`:''}<span>${esc(message)}</span></div><div class="chat-answer">${wineSwitch?'Подбираем вино…':'Готовим ответ…'}</div>`;
 if(intent==='dish_pairing')turn.querySelector('.chat-answer').textContent='Подбираем блюдо…';
 if(intent==='dish_recipe')turn.querySelector('.chat-answer').textContent='Готовим рецепт…';
 if(collectionSwitch)turn.querySelector('.chat-answer').textContent='Подбираем коллекции…';
 if(forCollection)turn.querySelector('.chat-answer').textContent='Подбираем вина в коллекцию…';
 root.append(turn);root.scrollTop=root.scrollHeight;
 try{
  await initPromise;
  const data=await api('assistant',request);
  const wineAnswer=!data.recommendation_type||data.recommendation_type==='wine';
  if(wineSwitch||wineAnswer)chatContext.length=0;
  chatContext.push({role:'user',content:message.slice(0,600)},{role:'assistant',content:String(data.text||'').slice(0,600)});
  if(chatContext.length>6)chatContext.splice(0,chatContext.length-6);
  if(data.recommendation_type==='dish'){
   const name=String(data.dishes?.[0]?.name||'').trim();
   if(name&&scanId)lastDish={scanId,name};
  }else if(wineSwitch||wineAnswer||data.recommendation_type==='collection')lastDish=null;
  const recipeAnswer=data.recommendation_type==='dish_recipe';
  if(data.recommendation_type==='dish')turn.classList.add('dish-turn');
  if(recipeAnswer)turn.classList.add('recipe-turn');
  if(data.recommendation_type==='conversation')turn.classList.add('conversation-turn');
  turn.querySelector('.chat-answer').innerHTML=`<p>${esc(data.text)}</p>`;
  if(data.warning){const warning=document.createElement('p');warning.className='muted';warning.textContent=data.warning;turn.append(warning);}
  if(data.recommendation_type==='collection'){
   turn.classList.add('collection-turn');
   for(const shelf of data.collections||[])turn.append(collectionRecommendationCard(shelf));
  }
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
  for(const w of (['dish','dish_recipe','conversation'].includes(data.recommendation_type)?[]:(data.items||[]))){const result=document.createElement('div');result.className='chat-result';result.append(wineRow(w,w.category,()=>openWine(w)));const reason=document.createElement('p');reason.className='reason';reason.textContent=w.reason;result.append(reason);if(data.list_id)result.append(addToListButton(data.list_id,w));turn.append(result);}
  if(usedAttachment&&assistantAttachment===attachment)removeAssistantAttachment();else renderAssistantAttachment();
 }catch(e){const answer=turn.querySelector('.chat-answer');answer.textContent=e.message;if(['assistant_sign_in','account_required'].includes(e.code)){const actions=document.createElement('div');actions.className='chat-auth-actions';for(const [mode,label] of [['login','Войти'],['register','Создать аккаунт']]){const button=document.createElement('button');button.type='button';button.className=mode==='login'?'cellar-primary':'text-button';button.textContent=label;button.onclick=()=>window.collectionUI?.signIn(mode);actions.append(button);}answer.append(actions);}else answer.append(' Запрос сохранён в поле ввода — можно отправить ещё раз.');$('assistant-input').value=message;}
 finally{chatBusy=false;$('send-button').disabled=false;$('voice-button').disabled=false;root.scrollTop=Math.max(0,root.scrollTop+turn.getBoundingClientRect().top-root.getBoundingClientRect().top);}
}
$('assistant-form').onsubmit=e=>{e.preventDefault();askAssistant($('assistant-input').value.trim());};$('assistant-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();askAssistant($('assistant-input').value.trim());}};$('chat-messages').addEventListener('click',event=>{const button=event.target.closest('[data-prompt]');if(button)askAssistant(button.dataset.prompt);});
let speech=null;
$('voice-button').onclick=()=>{const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Speech){notice('В этом браузере голосовой ввод недоступен. Используйте микрофон клавиатуры телефона или введите запрос.');$('assistant-input').focus();return;}if(speech){speech.stop();return;}speech=new Speech();speech.lang='ru-RU';speech.interimResults=true;speech.continuous=false;const before=$('assistant-input').value;speech.onstart=()=>{$('voice-button').classList.add('recording');$('voice-button').setAttribute('aria-label','Остановить запись');$('voice-status').textContent='Слушаю… Нажмите, чтобы остановить';};speech.onresult=e=>{$('assistant-input').value=(before+' '+Array.from(e.results).map(r=>r[0].transcript).join(' ')).trim().slice(0,1600);};speech.onerror=e=>{notice(e.error==='not-allowed'?'Разрешите микрофон в настройках браузера или напишите запрос.':'Не удалось распознать речь. Попробуйте ещё раз или напишите запрос.');};speech.onend=()=>{speech=null;$('voice-button').classList.remove('recording');$('voice-button').setAttribute('aria-label','Продиктовать запрос');$('voice-status').textContent='Проверьте текст и нажмите отправить';};try{speech.start();}catch{speech=null;notice('Голосовой ввод не запустился. Можно написать запрос.');}};
document.addEventListener('visibilitychange',()=>{if(document.hidden)speech?.stop();});
async function initializeProduct(){const cached=null;accountReady=false;try{const data=await api('bootstrap');productData=data;accountReady=true;syncAccountControls(data.account);store.set('wine-product',data);data.collection.forEach(remember);renderDiscovery();updatePreferenceCopy();if($('assistant-mode'))$('assistant-mode').textContent=data.assistant_mode==='ai'?'ИИ-помощник · рекомендации по каталогу':'Подбор по каталогу';}catch{accountReady=true;productData.account={authenticated:false};if(!cached&&$('discovery-list')){$('discovery-list').innerHTML=empty('info','Каталог пока недоступен','Проверьте интернет и обновите страницу.',false);}}}
const initPromise=Promise.all([initializeProduct(),loadPortalCardData()]);
// Opening the scanner leaves the camera off until the visitor taps its control.
cameraToggleState('idle');
scannerState('idle','Включите камеру или загрузите фото');
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
checkConnection();
