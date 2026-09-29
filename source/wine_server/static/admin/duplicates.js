let dupItems=[],dupGroup=null,dupIndex=0,dupChoice=null,dupPhotoId=null,dupSearchTimer,dupWineTimer,dupWineSequence=0;
async function openDuplicates(){await loadDuplicates(dupGroup?.id,dupIndex)}
async function loadDuplicates(preferred=null,index=0){
 const r=await api('duplicates?q='+encodeURIComponent($('dup-search').value));dupItems=r.items;
 $('dup-total').textContent=r.total.toLocaleString('ru-RU');$('dup-filtered').textContent='В списке: '+r.filtered.toLocaleString('ru-RU')+' · копий: '+r.copies.toLocaleString('ru-RU');$('dup-undo').disabled=!r.undo_available;
 renderDuplicateList();const found=dupItems.findIndex(x=>x.id===preferred);
 if(dupItems.length)await openDuplicate(found>=0?found:Math.min(index,dupItems.length-1));else clearDuplicate();
}
function renderDuplicateList(){
 const list=$('dup-list');list.replaceChildren();
 for(let i=0;i<dupItems.length;i++){const g=dupItems[i],b=document.createElement('button');b.className='dup-group'+(dupGroup?.id===g.id?' active':'');b.dataset.group=g.id;b.innerHTML=`<img src="${imageURL('asset',g.photo_id,160)}" alt="" loading="lazy"><span><strong>${esc(g.wines.map(w=>w.name).join(' / '))}</strong><span class="small">${esc([...new Set(g.wines.map(w=>w.winery))].join(', '))}</span><span class="small">Папок: ${g.slugs.length} · копий: ${g.copies}</span></span>`;b.onclick=()=>act(()=>openDuplicate(i));list.append(b)}
 if(!dupItems.length)list.innerHTML='<p class="empty">Подходящих групп нет.</p>';
}
function clearDuplicate(){dupGroup=null;dupChoice=null;$('dup-content').hidden=true;$('dup-empty').hidden=false;$('dup-empty').textContent=$('dup-search').value.trim()?'По этому поиску групп не найдено. Измените запрос.':'Одинаковых фото для проверки не найдено. Эталоны «Своё Вино» исключены.';$('dup-position').textContent='Нет групп для проверки';$('dup-match').textContent='';$('dup-prev').disabled=true;$('dup-next').disabled=true;$('dup-save').disabled=true}
async function openDuplicate(index){
 const item=dupItems[index];if(!item)return;dupGroup=await api('duplicates/'+encodeURIComponent(item.id));dupIndex=index;dupChoice=null;dupWineSequence++;
 $('dup-content').hidden=false;$('dup-empty').hidden=true;$('dup-position').textContent=`Группа ${index+1} из ${dupItems.length}`;
 $('dup-match').textContent=(dupGroup.match==='identical_files'?'Одинаковый файл':'Одинаковое изображение')+` · папок: ${dupGroup.slugs.length} · копий: ${dupGroup.copies}`;
 $('dup-prev').disabled=index===0;$('dup-next').disabled=dupItems.length<2;renderDuplicateList();
 $('dup-wine-search').value='';$('dup-wine-results').replaceChildren();$('dup-wine-results').hidden=true;$('dup-other-selected').replaceChildren();
 const candidates=$('dup-candidates');candidates.replaceChildren();for(const w of dupGroup.folders)candidates.append(duplicateWineCard(w));
 const copies=$('dup-copies');copies.replaceChildren();for(const f of dupGroup.files){const b=document.createElement('button');b.className='dup-copy';b.dataset.photo=f.id;b.title=f.name+' · '+f.slug;b.setAttribute('aria-label','Посмотреть копию в '+f.slug);b.innerHTML=`<img src="${imageURL('asset',f.id,160)}" alt="" loading="lazy"><span>${esc(dupGroup.folders.find(w=>w.slug===f.slug)?.name||f.slug)}</span>`;b.onclick=()=>showDuplicatePhoto(f);copies.append(b)}
 showDuplicatePhoto(dupGroup.files.find(f=>f.id===dupGroup.photo_id));updateDuplicateChoice();
}
function showDuplicatePhoto(file){dupPhotoId=file.id;$('dup-photo').src=imageURL('asset',file.id,1400);$('dup-file-name').textContent=file.name+' · '+file.source;$('dup-file-slug').textContent=file.slug;document.querySelectorAll('.dup-copy').forEach(b=>b.classList.toggle('active',b.dataset.photo===file.id))}
function duplicateWineCard(w){
 const card=document.createElement('article');card.className='panel dup-candidate';card.dataset.slug=w.slug;
 card.innerHTML=`<div class="dup-candidate-body"><div class="dup-reference">${w.reference?`<button class="dup-ref-zoom" aria-label="Увеличить другое фото ${esc(w.name)}"><img src="${imageURL('asset',w.reference,400)}" alt="Другое фото из папки ${esc(w.name)}" loading="lazy"></button>`:'<span class="small">Другого фото в папке нет</span>'}</div><div class="dup-wine-info"><button class="dup-wine-name">${esc(w.name)}</button><span class="small">${esc(w.winery)} · ${esc(w.category)}</span><span class="small">${esc(w.grapes)}</span><span class="slug">${esc(w.slug)}</span><span class="dup-choice-status">${w.copies?`Копий в этой папке: ${w.copies}`:'Выбрать эту папку'}</span><a href="${esc(w.url)}" target="_blank" rel="noreferrer">Карточка портала ↗</a></div></div>${w.reference?`<span class="small dup-reference-caption">${esc(w.reference_label||'Фото из выбранной папки')}</span>`:''}${w.other_photos?.length>1?'<div class="dup-other-photos"></div>':''}`;
 card.querySelector('.dup-wine-name').onclick=()=>chooseDuplicateWine(w);
 if(w.reference)card.querySelector('.dup-ref-zoom').onclick=()=>zoom(imageURL('asset',w.reference,2500));
 const more=card.querySelector('.dup-other-photos');if(more)for(const id of w.other_photos.slice(1)){const b=document.createElement('button');b.className='ghost';b.setAttribute('aria-label','Увеличить другой снимок '+w.name);b.innerHTML=`<img src="${imageURL('asset',id,160)}" alt="Другой снимок" loading="lazy">`;b.onclick=()=>zoom(imageURL('asset',id,2500));more.append(b)}
 return card;
}
function chooseDuplicateWine(w){
 dupChoice={slug:w.slug,name:w.name,winery:w.winery};
 if(!dupGroup.folders.some(x=>x.slug===w.slug)){$('dup-other-selected').replaceChildren(duplicateWineCard(w));$('dup-wine-results').hidden=true}
 else $('dup-other-selected').replaceChildren();
 updateDuplicateChoice();
}
function updateDuplicateChoice(){
 document.querySelectorAll('.dup-candidate').forEach(el=>{const chosen=el.dataset.slug===dupChoice?.slug;el.classList.toggle('selected',chosen);el.querySelector('.dup-wine-name').setAttribute('aria-pressed',String(chosen));const wine=dupGroup?.folders.find(w=>w.slug===el.dataset.slug);el.querySelector('.dup-choice-status').textContent=chosen?'Выбрано для сохранения':wine?`Копий в этой папке: ${wine.copies}`:'Выбрать эту папку'});
 $('dup-quarantine').classList.toggle('selected',!!dupChoice?.quarantine);$('dup-quarantine').setAttribute('aria-pressed',String(!!dupChoice?.quarantine));$('dup-save').disabled=!dupChoice;
 if(!dupChoice){$('dup-destination').textContent='Выберите правильное вино';$('dup-impact').textContent='Перенос выполняется только после сохранения.';return}
 const kept=dupChoice.quarantine?0:dupGroup.files.filter(f=>f.slug===dupChoice.slug).length||1;
 $('dup-destination').textContent=dupChoice.quarantine?'Все копии — на повторную проверку':dupChoice.name+' · '+dupChoice.winery;
 $('dup-impact').textContent=`Останется в images: ${kept}. В needs_review: ${dupGroup.copies-kept}.`;
}
$('dup-search').oninput=()=>{clearTimeout(dupSearchTimer);dupSearchTimer=setTimeout(()=>act(()=>loadDuplicates()),220)};
$('dup-wine-search').oninput=()=>{clearTimeout(dupWineTimer);const seq=++dupWineSequence,q=$('dup-wine-search').value.trim(),list=$('dup-wine-results');list.replaceChildren();list.hidden=!q;if(!q)return;list.innerHTML='<p class="small inset">Ищем в каталоге…</p>';dupWineTimer=setTimeout(async()=>{try{const r=await api('wines?q='+encodeURIComponent(q));if(seq!==dupWineSequence)return;list.replaceChildren();for(const w of r.items.slice(0,30)){const b=document.createElement('button');b.className='wine-option';b.innerHTML=`<strong>${esc(w.name)}</strong><small>${esc(w.winery)} · ${esc(w.grapes)}</small><span class="slug">${esc(w.slug)}</span>`;b.onclick=()=>chooseDuplicateWine(dupGroup.folders.find(x=>x.slug===w.slug)||w);list.append(b)}if(!r.items.length)list.innerHTML='<p class="empty">Ничего не найдено.</p>'}catch(e){toast(e.message,true)}},180)};
$('dup-quarantine').onclick=()=>{dupChoice={quarantine:true};$('dup-other-selected').replaceChildren();updateDuplicateChoice()};
$('dup-save').onclick=()=>act(async()=>{if(!dupGroup||!dupChoice)return;const index=dupIndex;const r=await api('duplicates/'+encodeURIComponent(dupGroup.id)+'/resolve',{version:dupGroup.version,slug:dupChoice.slug||null,quarantine:!!dupChoice.quarantine});toast(`Сохранено. В images: ${r.kept}; в needs_review: ${r.rejected}.`);await updateConfig();await loadDuplicates(null,index)});
$('dup-prev').onclick=()=>act(()=>openDuplicate(dupIndex-1));$('dup-next').onclick=()=>act(()=>openDuplicate((dupIndex+1)%dupItems.length));
$('dup-refresh').onclick=()=>act(openDuplicates);$('dup-zoom').onclick=()=>$('dup-photo').src&&zoom(imageURL('asset',dupPhotoId,2500));$('dup-photo').onclick=$('dup-zoom').onclick;
$('dup-undo').onclick=()=>act(async()=>{const r=await api('undo-duplicate',{});await updateConfig();await loadDuplicates(r.group);toast('Перенос отменён. Копии возвращены в прежние папки.')});
