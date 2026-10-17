const $ = id => document.getElementById(id);
let mode, config, wines=[], matches=[], queue=null, position=0, selected=null, activeWine=null, group=null;
let rejected=new Set(), busy=false, searchGeneration=0, previewId=null, toastTimer;
const escapeHTML = s => String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const photoURL = (kind,id,size=1000) => `/api/image/${kind}/${encodeURIComponent(id)}?size=${size}`;
async function api(url,body){
 const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Curation':'1'},body:JSON.stringify(body)});
 const text=await response.text(); let result; try{result=JSON.parse(text)}catch{result={detail:text}}
 if(!response.ok)throw Error(result.detail||'Не удалось выполнить действие'); return result;
}
function toast(text,error=false){clearTimeout(toastTimer);$('message').textContent=text;$('message').classList.toggle('error',error);$('message').hidden=false;toastTimer=setTimeout(()=>$('message').hidden=true,error?12000:4500)}
async function action(fn){if(busy)return;busy=true;document.body.classList.add('working');try{await fn()}catch(e){toast(e.message,true)}finally{busy=false;document.body.classList.remove('working')}}
function zoom(url,caption){$('zoom-image').src=url;$('zoom-caption').textContent=caption;$('zoom-dialog').showModal()}
$('close-zoom').onclick=()=>$('zoom-dialog').close();
$('zoom-dialog').onclick=e=>{if(e.target===$('zoom-dialog'))$('zoom-dialog').close()};
function rows(){return queue?.items.filter(x=>$('show-skipped').checked||x.state==='pending')||[]}
function renderSource(){
 const items=rows();position=Math.max(0,Math.min(position,items.length-1));const item=items[position];
 $('assign-count').textContent=queue?`${queue.done} / ${queue.total}`:'0 / 0';
 $('excluded-count').textContent=queue?`Пропущено: ${queue.skipped}. Не в каталоге: ${queue.out_of_catalog||0}.` : '';
 $('photo-position').textContent=item?`${position+1} из ${items.length} оставшихся`:'';
 $('source-name').textContent=item?item.name:(queue?.total===queue?.done?'Все фотографии размечены':queue?'Все оставшиеся фото отложены. Включите «Показать отложенные».':'Откройте папку');
 if(item){$('source-photo').src=photoURL('source',item.id);$('source-photo').alt=item.name}else $('source-photo').removeAttribute('src');
 $('source-zoom').disabled=!item;$('prev-photo').disabled=!item||position===0;$('skip-photo').disabled=!item;$('not-in-catalog').disabled=!item;
 $('assign-ok').disabled=!selected||!item;
}
function setQueue(q){queue=q;$('folder').value=q.root;localStorage.setItem('wine-source',q.root);position=0;renderSource()}
async function refreshQueue(){if(queue){queue=await api('/api/queue?root='+encodeURIComponent(queue.root));renderSource()}}
$('load-folder').onclick=()=>action(async()=>{setQueue(await api('/api/source',{folder:$('folder').value}));if(!$('session').value)$('session').value=queue.root.split('/').filter(Boolean).pop();toast(`Найдено фотографий: ${queue.total}`)});
$('choose-folder').onclick=()=>action(async()=>{const q=await api('/api/choose-folder',{});if(!q.cancelled){setQueue(q);$('session').value=q.root.split('/').filter(Boolean).pop()}});
$('folder').onkeydown=e=>{if(e.key==='Enter')$('load-folder').click()};
$('source-zoom').onclick=()=>{const item=rows()[position];if(item)zoom(photoURL('source',item.id,2200),item.name)};
$('show-skipped').onchange=()=>{position=0;renderSource()};
$('prev-photo').onclick=()=>{position--;renderSource()};
$('skip-photo').onclick=()=>action(async()=>{const item=rows()[position];if(!item)return;await api('/api/skip',{id:item.id,skipped:true});if($('show-skipped').checked)position++;await refreshQueue();toast('Пропущено — можно вернуться позже')});
$('not-in-catalog').onclick=()=>action(async()=>{const item=rows()[position];if(!item)return;await api('/api/skip',{id:item.id,state:'out_of_catalog'});if($('show-skipped').checked)position++;await refreshQueue();toast('Отмечено «нет в каталоге». Исходник оставлен на месте.')});
$('copy-mode').onchange=()=>{$('assign-ok').innerHTML=`${$('copy-mode').checked?'Копировать':'Перенести'} и далее <span>↵</span>`};
function showCandidate(w){
 if(previewId===w.slug)return;previewId=w.slug;
 $('candidate').innerHTML=`<div class="candidate-info">${w.reference?`<img src="${photoURL('asset',w.reference,600)}" alt="Фото из каталога: ${escapeHTML(w.name)}" id="candidate-photo">`:'<div class="empty">Фото карточки отсутствует</div>'}<span class="small muted">${escapeHTML(w.reference_label)}</span><strong>${escapeHTML(w.name)}</strong><p>${escapeHTML(w.winery)} · ${escapeHTML(w.category)}</p><p class="small">${escapeHTML(w.grapes)}</p><span class="slug">${escapeHTML(w.slug)}</span><a href="${escapeHTML(w.url)}" target="_blank" rel="noreferrer">Карточка на «Своё Вино» ↗</a></div>`;
 if(w.reference)$('candidate-photo').onclick=()=>zoom(photoURL('asset',w.reference,2200),w.name+' · '+w.slug);
 const pick=document.createElement('button');pick.className='secondary candidate-pick';pick.textContent=selected?.slug===w.slug?'Это вино выбрано':'Выбрать это вино';pick.onclick=()=>chooseWine(w);$('candidate').append(pick);
}
function chooseWine(w){selected=w;previewId=null;showCandidate(w);$('selected-name').textContent=w.name+' · '+w.winery;$('selected-slug').textContent=w.slug;document.querySelectorAll('#search-results .wine-option').forEach(b=>b.classList.toggle('active',b.dataset.slug===w.slug));renderSource()}
function renderMatches(){
 const list=$('search-results');list.replaceChildren();
 if(!matches.length){list.innerHTML='<p class="empty">Нет совпадений. Попробуйте винодельню, сорт или часть slug.</p>';return}
 matches.slice(0,100).forEach(w=>{const b=document.createElement('button');b.className='wine-option';b.dataset.slug=w.slug;b.classList.toggle('active',selected?.slug===w.slug);b.innerHTML=`<strong>${escapeHTML(w.name)}</strong><small>${escapeHTML(w.winery)} · ${escapeHTML(w.category)}</small><small>${escapeHTML(w.grapes)}</small>`;b.onmouseenter=()=>showCandidate(w);b.onfocus=()=>showCandidate(w);b.onclick=()=>chooseWine(w);list.append(b)});
 if(matches.length>100){const p=document.createElement('p');p.className='empty';p.textContent='Показаны первые 100. Уточните поиск.';list.append(p)}
}
let searchTimer;
$('search').oninput=()=>{clearTimeout(searchTimer);const generation=++searchGeneration;searchTimer=setTimeout(async()=>{try{const r=await api('/api/wines?q='+encodeURIComponent($('search').value));if(generation!==searchGeneration)return;matches=r.items;renderMatches()}catch(e){toast(e.message,true)}},160)};
$('assign-ok').onclick=()=>action(async()=>{const item=rows()[position];if(!item||!selected)return;await api('/api/assign',{id:item.id,slug:selected.slug,mode:$('copy-mode').checked?'copy':'move',session:$('session').value});await refreshQueue();toast('Сохранено в '+selected.slug)});
async function loadReviewList(){const r=await api('/api/wines?q='+encodeURIComponent($('review-search').value)+'&pending='+$('only-pending').checked);wines=r.items;renderReviewList();config=await api('/api/config');$('review-count').textContent=config.stats.confirmed.toLocaleString('ru-RU')}
function renderReviewList(){const list=$('review-list');list.replaceChildren();if(!wines.length){list.innerHTML='<p class="empty">Нет подходящих папок.</p>';return}wines.forEach(w=>{const b=document.createElement('button');b.className='wine-option';b.classList.toggle('active',activeWine?.slug===w.slug);b.dataset.slug=w.slug;b.innerHTML=`<strong>${escapeHTML(w.name)}</strong><small>${escapeHTML(w.winery)}</small><small>${w.count} фото · ${w.unreviewed} не проверено</small>`;b.onclick=()=>action(()=>openGroup(w));list.append(b)})}
async function openGroup(w){
 if(rejected.size && !confirm('Отметки в текущей папке ещё не сохранены. Перейти без сохранения?'))return;
 activeWine=w;group=await api('/api/group/'+encodeURIComponent(w.slug));rejected.clear();renderReviewList();
 $('review-heading').innerHTML=`${w.reference?`<img id="review-ref" src="${photoURL('asset',w.reference,500)}" alt="Фото карточки">`:''}<div><div class="eyebrow">${escapeHTML(w.winery)}</div><h2>${escapeHTML(w.name)}</h2><p>${escapeHTML(w.category)} · ${escapeHTML(w.grapes)}</p><a href="${escapeHTML(w.url)}" target="_blank" rel="noreferrer">Сравнить с карточкой портала ↗</a><span class="slug">${escapeHTML(w.slug)}</span></div>`;
 if(w.reference)$('review-ref').onclick=()=>zoom(photoURL('asset',w.reference,2200),w.name+' · Фото карточки портала');
 $('grid-count').textContent=`Все фотографии папки: ${group.items.length}`;
 const grid=$('photo-grid');grid.replaceChildren();
 for(const item of group.items){const tile=document.createElement('article');tile.className='photo-tile';tile.dataset.id=item.id;tile.innerHTML=`<button class="tile-image" aria-label="Увеличить ${escapeHTML(item.name)}"><img src="${photoURL('asset',item.id,700)}" loading="lazy" alt="${escapeHTML(item.name)}"></button><div class="tile-meta"><span class="badge">${item.status==='confirmed'?'Подтверждено вручную':item.status==='pending'?'Нужна проверка':'Принято ранее'}</span><span class="filename" title="${escapeHTML(item.name)}">${escapeHTML(item.name)}</span><span class="small muted">${escapeHTML(item.source)} · ${item.width} × ${item.height}</span></div><label class="inline tile-check"><input type="checkbox" aria-label="Чужое фото ${escapeHTML(item.name)}"> Это другое вино</label>`;
 if(item.shared_slugs.length){const warning=document.createElement('span');warning.className='small shared-warning';warning.textContent=`Тот же файл ещё в ${item.shared_slugs.length} slug`;warning.title=item.shared_slugs.join('\n');tile.querySelector('.tile-meta').append(warning)}
 tile.querySelector('.tile-image').onclick=()=>zoom(photoURL('asset',item.id,2200),`${activeWine.name} · ${item.name}`);
 tile.querySelector('input').onchange=e=>{if(e.target.checked)rejected.add(item.id);else rejected.delete(item.id);tile.classList.toggle('reject',e.target.checked);updateReviewAction()};grid.append(tile)}
 if(!group.items.length)grid.innerHTML='<p class="empty">В этой папке пока нет фотографий.</p>';
 updateReviewAction();
}
function updateReviewAction(){$('reject-count').textContent=rejected.size?`Чужие фото: ${rejected.size}. Подтвердить остальные: ${group.items.length-rejected.size}`:'Ни одного чужого фото не отмечено';$('save-group').textContent=rejected.size?'Сохранить проверку и далее →':'Все верно — далее →';$('save-group').disabled=!group?.items.length}
let reviewTimer;
$('review-search').oninput=()=>{clearTimeout(reviewTimer);reviewTimer=setTimeout(()=>action(loadReviewList),180)};
$('only-pending').onchange=()=>action(loadReviewList);
$('tile-size').oninput=()=>document.documentElement.style.setProperty('--tile',$('tile-size').value+'px');
async function nextGroup(){const index=wines.findIndex(w=>w.slug===activeWine?.slug);const next=wines[(index+1)%wines.length];if(next)await openGroup(next)}
$('skip-group').onclick=()=>action(nextGroup);
$('save-group').onclick=()=>action(async()=>{if(!group?.items.length)return;const oldIndex=wines.findIndex(w=>w.slug===activeWine.slug);const r=await api('/api/review',{slug:group.slug,version:group.version,reject:[...rejected]});rejected.clear();await loadReviewList();toast(`Подтверждено: ${r.confirmed}. На повторную проверку: ${r.rejected}.`);const next=wines[Math.min(oldIndex,wines.length-1)];if(next)await openGroup(next);else{$('photo-grid').innerHTML='<p class="empty">Все папки в текущей выборке проверены.</p>';$('save-group').disabled=true;$('review-heading').innerHTML='';group=null}});
$('undo').onclick=()=>action(async()=>{await api('/api/undo',{kind:mode});if(mode==='assign')await refreshQueue();else{rejected.clear();await loadReviewList();if(activeWine)await openGroup(activeWine)}toast('Последнее действие отменено')});
document.addEventListener('keydown',e=>{if(busy||$('zoom-dialog').open)return;const typing=['INPUT','TEXTAREA'].includes(document.activeElement.tagName);if(mode==='assign'&&!typing&&document.activeElement.tagName!=='BUTTON'){if(e.key==='Enter'){e.preventDefault();$('assign-ok').click()}if(e.key.toLowerCase()==='s')$('skip-photo').click()}});
window.addEventListener('beforeunload',e=>{if(rejected.size){e.preventDefault();e.returnValue=''}});
async function init(){config=await api('/api/config');mode=config.mode;$('assign-view').hidden=mode!=='assign';$('review-view').hidden=mode!=='review';$(mode+'-link').classList.add('active');document.title=mode==='assign'?'Разметка фото · Вино':'Проверка папок · Вино';$('subtitle').textContent='Фото → точный slug';if(mode==='assign'){$('folder').value=localStorage.getItem('wine-source')||config.default_source;matches=(await api('/api/wines')).items;renderMatches();setQueue(await api('/api/source',{folder:$('folder').value}));$('session').value=queue.root.split('/').filter(Boolean).pop()}else{await loadReviewList();if(wines.length)await openGroup(wines[0])}}
init().catch(e=>toast(e.message,true));
