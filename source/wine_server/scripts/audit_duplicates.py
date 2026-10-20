"""Read-only content audit; writes reports ONLY, never modifies images or database."""
import argparse,csv,datetime,hashlib,html,json,os,sqlite3,struct,time
from collections import Counter,defaultdict
from pathlib import Path
from PIL import Image,ImageOps

EXTENSIONS={'.jpg','.jpeg','.png','.webp','.heic','.heif','.tif','.tiff','.bmp','.avif'}
def portal(row):return row.get('source','').lower() in {'vino-svoe.ru','www.vino-svoe.ru'} or row.get('kind')=='catalog_reference'
def stats(rows,key='sha256'):
    groups=defaultdict(list)
    for row in rows:
        if row.get(key):groups[row[key]].append(row)
    dup=[g for g in groups.values() if len(g)>1]
    within=[n for g in dup for n in Counter(r['slug'] for r in g).values() if n>1]
    return {'files':len(rows),'unique_contents':len(groups),'duplicate_groups':len(dup),
        'files_in_duplicate_groups':sum(map(len,dup)),'copies_beyond_one_per_content':sum(len(g)-1 for g in dup),
        'cross_slug_groups':sum(len({r['slug'] for r in g})>1 for g in dup),
        'only_same_slug_groups':sum(len({r['slug'] for r in g})==1 for g in dup),
        'same_slug_sets':len(within),'same_slug_extra_copies':sum(n-1 for n in within),
        'redundant_bytes':sum(sum(r['bytes'] for r in g)-max(r['bytes'] for r in g) for g in dup)}
def group_records(rows,key):
    groups=defaultdict(list)
    for row in rows:
        if row.get(key):groups[row[key]].append(row)
    result=[]
    for digest,rr in groups.items():
        if len(rr)<2:continue
        if key=='pixel_sha256' and len({r['sha256'] for r in rr})==1:continue
        result.append({'hash':digest,'file_count':len(rr),'slug_count':len({r['slug'] for r in rr}),
                       'files':sorted(rr,key=lambda r:(r['bucket'],r['slug'],r['path']))})
    return sorted(result,key=lambda g:(-g['file_count'],g['hash']))
def same_bytes(a,b):
    with open(a,'rb') as x,open(b,'rb') as y:
        while True:
            xx=x.read(1024*1024);yy=y.read(1024*1024)
            if xx!=yy:return False
            if not xx:return True

def audit(root,out,pixels=True):
    started=time.time();when=datetime.datetime.now(datetime.timezone.utc).isoformat()
    out.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect((root/'collector/state.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;registered={r['path']:dict(r) for r in db.execute('SELECT * FROM images')}
    with (root/'catalog.csv').open(encoding='utf-8-sig') as f:catalog={r['Slug']:r for r in csv.DictReader(f)}
    paths=sorted(p for bucket in ['images','needs_review'] for p in (root/bucket).rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in EXTENSIONS)
    rows=[];issues=[];pixel_cache={};initial={str(p.relative_to(root)) for p in paths}
    for i,p in enumerate(paths):
        relative=str(p.relative_to(root));meta=registered.get(relative,{});parts=p.relative_to(root).parts;slug=parts[1]
        try:
            before=p.stat();digest=hashlib.sha256()
            with p.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            sh=digest.hexdigest();pixel='';width=height=0
            if pixels:
                if sh not in pixel_cache:
                    with Image.open(p) as im:
                        # Include dimensions, EXIF orientation and alpha; no resize/perceptual threshold.
                        im=ImageOps.exif_transpose(im).convert('RGBA');width,height=im.size
                        h=hashlib.sha256(struct.pack('>II',width,height));h.update(im.tobytes());pixel=h.hexdigest()
                    pixel_cache[sh]=(pixel,width,height)
                pixel,width,height=pixel_cache[sh]
            after=p.stat()
            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Файл изменился во время чтения')
            row={'path':relative,'bucket':parts[0],'slug':slug,'name':catalog.get(slug,{}).get('Название вина',''),
                 'id':meta.get('id',''),'source':meta.get('source','unregistered'),'kind':meta.get('kind',''),
                 'portal_reference':portal(meta),'page_url':meta.get('page_url',''),'sha256':sh,'pixel_sha256':pixel,
                 'bytes':after.st_size,'width':width,'height':height}
            rows.append(row)
            if meta and meta.get('sha256')!=sh:issues.append({'path':relative,'issue':'stored_sha256_mismatch'})
        except Exception as e:issues.append({'path':relative,'issue':str(e)})
        if (i+1)%500==0:print(json.dumps({'read':i+1,'total':len(paths),'elapsed_s':round(time.time()-started)}),flush=True)
    # Verify candidates byte for byte as well as by cryptographic checksum.
    by_sha=defaultdict(list)
    for row in rows:by_sha[row['sha256']].append(row)
    verified_pairs=0
    for rr in by_sha.values():
        for row in rr[1:]:
            try:
                if not same_bytes(root/rr[0]['path'],root/row['path']):raise ValueError('Содержимое различается при повторном чтении')
                verified_pairs+=1
            except Exception as e:issues.append({'path':row['path'],'issue':str(e)})
    final={str(p.relative_to(root)) for bucket in ['images','needs_review'] for p in (root/bucket).rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in EXTENSIONS}
    scopes={
        'images':[r for r in rows if r['bucket']=='images'],
        'images_without_portal':[r for r in rows if r['bucket']=='images' and not r['portal_reference']],
        'images_portal_only':[r for r in rows if r['bucket']=='images' and r['portal_reference']],
        'needs_review':[r for r in rows if r['bucket']=='needs_review'],
        'combined':rows,
    }
    result={'started_utc':when,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'dataset':str(root),'read_only':True,
            'method':'Fresh SHA256 plus byte-by-byte comparison. '+('RGBA pixels at original resolution, EXIF orientation applied, dimensions included; no perceptual hashing.' if pixels else 'Pixel comparison disabled for this pass.'),
            'files_at_start':len(paths),'files_read':len(rows),'verified_identical_file_pairs':verified_pairs,'elapsed_s':round(time.time()-started,2),
            'inventory_changed_during_run':initial!=final,'paths_added':sorted(final-initial),'paths_removed':sorted(initial-final),'issues':issues,
            'scopes':{name:{'bytes':stats(rr),'pixels':stats(rr,'pixel_sha256') if pixels else None} for name,rr in scopes.items()},
            'exact_file_groups':group_records(rows,'sha256'),'additional_pixel_groups':group_records(rows,'pixel_sha256') if pixels else []}
    (out/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    text=['# Анализ точных дублей фотографий','',f'Снимок данных: {when}. Проверено файлов: {len(rows)}. Ничего не удалено и не перенесено.','',
          'SHA-256 рассчитан заново по содержимому файлов; найденные копии дополнительно сравнены побайтово. '+('Отдельно сравнены пиксели без изменения размера, с учётом ориентации EXIF и прозрачности.' if pixels else 'Пиксели в этом проходе не сравнивались.'),'',
          '| Область | Файлов | Групп точных копий | Файлов в группах | Повторных экземпляров сверх одного | Групп между slug | Повторов внутри slug |',
          '|---|---:|---:|---:|---:|---:|---:|']
    names={'images':'images','images_without_portal':'images без эталонов портала','images_portal_only':'только эталоны портала','needs_review':'needs_review','combined':'images + needs_review'}
    for name,s in result['scopes'].items():
        m=s['bytes'];text.append('| '+names[name]+' | '+' | '.join(str(m[k]) for k in ['files','duplicate_groups','files_in_duplicate_groups','copies_beyond_one_per_content','cross_slug_groups','same_slug_extra_copies'])+' |')
    text+=['','«Повторы внутри slug» посчитаны отдельно в каждой папке. Группа может одновременно иметь копии внутри папки и в разных slug; эти колонки нельзя складывать.',
            (f"\nГрупп одинаковых пикселей с разными байтами: {len(result['additional_pixel_groups'])} (обе папки)." if pixels else "\nСравнение пикселей в этом отчёте не выполнялось."),
            '\nЭталоны портала включены в статистику анализа, но исключены из очереди ручной проверки. Это не повод удалять их.',
            f"\nОшибок/расхождений: {len(issues)}. Изменение списка файлов во время анализа: {'да' if initial!=final else 'нет'}.",
            '\nПодробности и пути: audit.json. Для просмотра и поиска: report.html.']
    (out/'REPORT.md').write_text('\n'.join(text)+'\n')
    esc=html.escape;cards=[]
    for kind,groups in [('Побайтовые копии',result['exact_file_groups']),('Совпали пиксели, байты различаются',result['additional_pixel_groups'])]:
        for group in groups:
            lines=[]
            for r in group['files']:
                lines.append('<tr><td>'+esc(r['bucket'])+'</td><td>'+esc(r['name'])+'<br><code>'+esc(r['slug'])+'</code></td><td>'+esc(r['source'])+(' · эталон' if r['portal_reference'] else '')+'</td><td><code>'+esc(r['path'])+'</code></td></tr>')
            cards.append('<details data-search="'+esc(' '.join(r['name']+' '+r['slug']+' '+r['path'] for r in group['files']).lower(),quote=True)+'"><summary>'+kind+' · файлов: '+str(group['file_count'])+' · slug: '+str(group['slug_count'])+'</summary><p><code>'+group['hash']+'</code></p><table><thead><tr><th>Папка</th><th>Вино</th><th>Источник</th><th>Путь</th></tr></thead><tbody>'+''.join(lines)+'</tbody></table></details>')
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Анализ дублей фотографий</title><style>body{font:16px/1.6 system-ui;margin:24px auto;max-width:1200px;padding:0 16px;color:#262324;background:#f8f6f2}h1{font-size:28px}pre{white-space:pre-wrap}input{width:100%;box-sizing:border-box;padding:12px;font:inherit;margin:15px 0}details{padding:14px;background:white;border:1px solid #ddd;border-radius:8px;margin:10px 0}summary{cursor:pointer;font-weight:600}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:8px;text-align:left;border-bottom:1px solid #eee;vertical-align:top;overflow-wrap:anywhere}code{font-size:11px;overflow-wrap:anywhere}th:first-child{width:80px}#result{font-size:14px}</style><h1>Анализ точных дублей</h1><p>Только анализ. Фотографии и разметка не изменялись.</p><pre>'''+esc('\n'.join(text[2:]))+'''</pre><label>Поиск по вину, slug или имени файла<input id="search" type="search" placeholder="Название вина или часть пути"></label><p id="result"></p><main>'''+''.join(cards)+'''</main><script>const q=document.getElementById('search'),items=[...document.querySelectorAll('details')];function filter(){const t=q.value.toLowerCase().trim();let n=0;for(const e of items){e.hidden=!e.dataset.search.includes(t);if(!e.hidden)n++}document.getElementById('result').textContent='Групп в списке: '+n}q.addEventListener('input',filter);filter()</script></html>'''
    (out/'report.html').write_text(page)
    print(json.dumps({k:result[k] for k in ['files_read','elapsed_s','inventory_changed_during_run','verified_identical_file_pairs','scopes']},ensure_ascii=False,indent=2),flush=True)
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--no-pixels',action='store_true');a=p.parse_args()
    audit(a.root.resolve(),a.out.resolve(),not a.no_pixels)
