let exportCatalog=[],exportSelected=new Set(),exportSequence=0,exportTimer,exportPoll,exportJobs=[],exportPreview=null;
const exportScope=()=>document.querySelector('[name=export-scope]:checked').value;
const exportOptions=()=>({mode:$('export-mode').value,filter:$('export-filter').value,scope:exportScope(),format:$('export-format').value,split:$('export-split').value,slugs:exportScope()==='selected'?[...exportSelected]:[]});
const exportBytes=n=>n>=1024**3?(n/1024**3).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ГБ':n>=1024**2?(n/1024**2).toLocaleString('ru-RU',{maximumFractionDigits:1})+' МБ':Math.max(1,Math.round(n/1024))+' КБ';
const exportModeName={images:'Фотографии',images_json:'Фото + разметка',json:'Только разметка'};
const exportFormatName={native:'Исходный JSON',coco:'COCO JSON',yolo_detect:'YOLO · рамки',yolo_segment:'YOLO · контуры'};
const exportFilterName={all:'все фото',labeled:'с разметкой',unlabeled:'без разметки'};
const exportNorm=s=>String(s).toLocaleLowerCase('ru-RU').replaceAll('ё','е');
async function openExports(){exportCatalog=(await api('exports/catalog')).items;exportCatalog.sort((a,b)=>(a.winery+' '+a.name).localeCompare(b.winery+' '+b.name,'ru'));syncExportFormat();renderExportFolders();await refreshExportPreview();await loadExportJobs()}
function renderExportFolders(){
 $('export-folder-picker').hidden=exportScope()!=='selected';$('export-selected-count').textContent='Выбрано: '+exportSelected.size;
 const tokens=exportNorm($('export-search').value).trim().split(/\s+/).filter(Boolean);
 const found=exportCatalog.filter(w=>tokens.every(t=>exportNorm(w.name+' '+w.winery+' '+w.slug).includes(t))),list=$('export-folders');list.replaceChildren();
 for(const w of found.slice(0,100)){const label=document.createElement('label');label.className='export-folder';label.innerHTML=`<input type="checkbox" value="${esc(w.slug)}" ${exportSelected.has(w.slug)?'checked':''}><span><strong>${esc(w.name)}</strong><span class="small">${esc(w.winery)} · ${w.total} фото · ${w.labeled} с разметкой</span><span class="slug">${esc(w.slug)}</span></span>`;label.querySelector('input').onchange=e=>{e.target.checked?exportSelected.add(w.slug):exportSelected.delete(w.slug);renderExportSelection();scheduleExportPreview()};list.append(label)}
 if(!found.length)list.innerHTML='<p class="empty">Папки не найдены. Измените запрос.</p>';
 $('export-search-count').textContent=found.length>100?`Показаны первые 100 из ${found.length}. Уточните поиск.`:`Найдено папок: ${found.length}`;renderExportSelection();
}
function renderExportSelection(){
 $('export-selected-count').textContent='Выбрано: '+exportSelected.size;$('export-clear').disabled=!exportSelected.size;const chips=$('export-chips');chips.replaceChildren();
 for(const slug of exportSelected){const w=exportCatalog.find(x=>x.slug===slug),b=document.createElement('button');b.type='button';b.className='export-chip';b.textContent=(w?.name||slug)+' ×';b.title=w?.winery||slug;b.setAttribute('aria-label','Убрать папку '+(w?.name||slug));b.onclick=()=>{exportSelected.delete(slug);renderExportFolders();scheduleExportPreview()};chips.append(b)}
}
function scheduleExportPreview(){clearTimeout(exportTimer);exportSequence++;exportPreview=null;$('export-build').disabled=true;$('export-preview-status').textContent='Считаем файлы…';exportTimer=setTimeout(refreshExportPreview,180)}
async function refreshExportPreview(){
 const seq=++exportSequence;exportPreview=null;$('export-build').disabled=true;
 if(exportScope()==='selected'&&!exportSelected.size){for(const id of ['folders','images','json','bytes'])$('export-count-'+id).textContent='—';$('export-preview-status').textContent='Выберите одну или несколько папок в списке.';return}
 $('export-preview-status').textContent='Считаем файлы…';
 try{const p=await api('exports/preview',exportOptions());if(seq!==exportSequence)return;exportPreview=p;$('export-count-folders').textContent=p.folders.toLocaleString('ru-RU');$('export-count-images').textContent=p.images.toLocaleString('ru-RU');$('export-count-json').textContent=p.annotations.toLocaleString('ru-RU');$('export-count-bytes').textContent=exportBytes(p.estimated_bytes);
 $('export-preview-status').textContent=!p.matched_images?'В этой выборке нет подходящих фото.':p.options.mode==='images_json'&&p.unlabeled?`${p.unlabeled} фото пока без границ: для них JSON не создаётся.`:p.folders>p.nonempty_folders?`Пустых папок в выборке: ${p.folders-p.nonempty_folders}.`:p.options.split==='grouped'?`Train: ${p.split_counts.train||0} · valid: ${p.split_counts.valid||0} · test: ${p.split_counts.test||0}.`:'Параметры готовы. Можно собирать архив.';exportBuildState();
 }catch(e){if(seq===exportSequence){for(const id of ['folders','images','json','bytes'])$('export-count-'+id).textContent='—';$('export-preview-status').textContent=e.message}}
}
function exportBuildState(){$('export-build').disabled=!exportPreview?.matched_images||exportJobs.some(j=>['queued','building'].includes(j.status))}
async function loadExportJobs(){
 clearTimeout(exportPoll);exportJobs=(await api('exports')).items;const list=$('export-jobs');list.replaceChildren();
 if(!exportJobs.length)list.innerHTML='<p class="empty">Здесь появятся подготовленные ZIP-архивы.</p>';
 for(const j of exportJobs){const row=document.createElement('article');row.className='export-job';const active=['queued','building'].includes(j.status);row.innerHTML=`<div class="export-job-description"><strong>${exportModeName[j.options.mode]}${j.options.mode==='images'?'':' · '+exportFormatName[j.options.format||'native']} · ${exportFilterName[j.options.filter]}</strong><span class="small">${j.summary.folders} папок · ${j.summary.images} фото · ${j.summary.annotations} размеченных · ${new Date(j.created*1000).toLocaleString('ru-RU')}</span>${active?`<progress max="${Math.max(j.total,1)}" value="${j.done}"></progress><span class="small">${j.status==='queued'?'Ожидает сборки':'Собираем ZIP'} · ${j.done} / ${j.total}</span>`:j.status==='ready'?`<span class="small">Готово · ${exportBytes(j.bytes)} · доступен до ${new Date(j.expires*1000).toLocaleString('ru-RU')}</span>`:`<p class="export-job-error">${esc(j.error)}</p>`}</div><div class="export-job-actions">${j.status==='ready'?`<a class="button" href="${j.download_url}" download="${esc(j.filename)}">Скачать ZIP</a>`:''}${!active?'<button class="ghost export-delete">Удалить ZIP</button>':''}</div>`;const remove=row.querySelector('.export-delete');if(remove)remove.onclick=()=>act(async()=>{await api('exports/'+j.id+'/delete',{});await loadExportJobs();toast('Архив удалён. Фотографии и разметка сохранены.')});list.append(row)}
 exportBuildState();if(exportJobs.some(j=>['queued','building'].includes(j.status)))exportPoll=setTimeout(()=>{if(currentTab==='export'&&!document.hidden)loadExportJobs().catch(e=>toast(e.message,true))},2000);
}
$('export-search').oninput=renderExportFolders;$('export-clear').onclick=()=>{exportSelected.clear();renderExportFolders();scheduleExportPreview()};
document.querySelectorAll('[name=export-scope]').forEach(r=>r.onchange=()=>{renderExportFolders();scheduleExportPreview()});
function syncExportFormat(){
 const photos=$('export-mode').value==='images',standard=!photos&&$('export-format').value!=='native';
 $('export-format').disabled=photos;$('export-split').disabled=!standard;
 $('export-filter').disabled=standard||$('export-mode').value==='json';
 if($('export-filter').disabled)$('export-filter').value='labeled';
 $('export-format-help').textContent=photos?'Фотографии сохраняют исходные имена и папки вин.':!standard?'Исходный формат сохраняет координаты, slug и версию разметки.':
 'Один класс: лицевая этикетка. Неразмеченные фото исключены. Без разбиения — для импорта; 70/15/15 — для обучения и проверки.';
 $('export-archive-note').textContent=photos?'Исходные фотографии останутся на сервере.':!standard?'JSON содержит углы этикетки и ссылку на соответствующее фото.':
 ($('export-mode').value==='json'?'В архиве только разметка. Фотографии добавляются отдельно; соответствие имён — в _export.json.':'Поворот фото учтён. Связь имён файлов со slug сохранена в _export.json.');
}
$('export-mode').onchange=$('export-format').onchange=$('export-split').onchange=()=>{syncExportFormat();scheduleExportPreview()};
$('export-filter').onchange=scheduleExportPreview;
$('export-build').onclick=()=>act(async()=>{if(!exportPreview)return;$('export-build').disabled=true;try{await api('exports',exportOptions());await loadExportJobs();toast('Сборка началась. Готовый ZIP появится ниже.')}finally{exportBuildState()}});
$('export-refresh').onclick=()=>act(loadExportJobs);
$('open-exports').onclick=()=>act(async()=>{const slug=$('bounds-slug').value;exportSelected=new Set(slug?[slug]:[]);document.querySelector(`[name=export-scope][value=${slug?'selected':'all'}]`).checked=true;$('export-mode').value='images_json';$('export-format').value='coco';$('export-filter').value='labeled';syncExportFormat();await tab('export')});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&currentTab==='export')loadExportJobs().catch(e=>toast(e.message,true))});
