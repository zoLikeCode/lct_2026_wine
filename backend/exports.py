"""Bounded-memory, consistent dataset ZIP exports. Never changes source images."""
import hashlib,json,os,re,shutil,threading,time,uuid,zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from annotation_formats import FORMATS, SPLITS, assign_splits, write_standard

MODES={'images','images_json','json'}
FILTERS={'all','labeled','unlabeled'}
TTL=24*3600

class Exports:
    def __init__(self,store):
        self.store=store;self.root=store.work/'exports';self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='wine-export')
        with store.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS export_jobs(
                id TEXT PRIMARY KEY,status TEXT,created REAL,expires REAL,options TEXT,
                summary TEXT,done INTEGER,total INTEGER,bytes INTEGER,error TEXT,filename TEXT)''')
            interrupted=[r['id'] for r in db.execute("SELECT id FROM export_jobs WHERE status IN ('queued','building')")]
            db.execute("UPDATE export_jobs SET status='error',error='Сервер перезапущен. Создайте архив ещё раз.' WHERE status IN ('queued','building')")
        for ident in interrupted:shutil.rmtree(self.folder(ident),ignore_errors=True)
        self.cleanup()

    def close(self):self.pool.shutdown(wait=True)

    def folder(self,ident):
        if not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('Архив не найден')
        return self.root/ident

    def options(self,body):
        mode=body.get('mode','images');filter=body.get('filter','all');scope=body.get('scope','all')
        slugs=body.get('slugs',[])
        fmt=body.get('format','native');split=body.get('split','none')
        if mode not in MODES or filter not in FILTERS or scope not in {'all','selected'}:raise ValueError('Неизвестный вариант выгрузки')
        if fmt not in FORMATS or split not in SPLITS:raise ValueError('Неизвестный формат или разбиение')
        if mode=='images':fmt='native';split='none'
        if fmt=='native':split='none'
        elif filter!='labeled':raise ValueError('Для COCO и YOLO выберите фотографии с сохранённой разметкой этикетки')
        if not isinstance(slugs,list) or any(not isinstance(s,str) or s not in self.store.catalog for s in slugs):raise ValueError('Неизвестная папка вина')
        if scope=='selected' and not slugs:raise ValueError('Выберите хотя бы одну папку')
        if mode=='json' and filter!='labeled':raise ValueError('Для JSON выберите фотографии с разметкой этикетки')
        return {'scope':scope,'slugs':sorted(set(slugs)) if scope=='selected' else [],'filter':filter,'mode':mode,'format':fmt,'split':split}

    def selection(self,options):
        slugs=options['slugs'] if options['scope']=='selected' else sorted(self.store.catalog)
        allowed=set(slugs)
        with self.store.db() as db:
            annotations={r['id']:dict(r) for r in db.execute('SELECT * FROM annotations')}
            rows=[dict(r) for r in db.execute("SELECT i.*,m.state AS review_state FROM images i LEFT JOIN curation_meta m ON i.id=m.id WHERE i.path LIKE 'images/%' ORDER BY i.slug,i.path")]
        selected=[]
        for row in rows:
            if row['slug'] not in allowed:continue
            a=annotations.get(row['id'])
            if options['filter']=='labeled' and a is None:continue
            if options['filter']=='unlabeled' and a is not None:continue
            row['annotation']=a;selected.append(row)
        labeled=sum(r['annotation'] is not None for r in selected)
        photo_bytes=sum(int(r['bytes']) for r in selected) if options['mode']!='json' else 0
        summary={'folders':len(slugs),'nonempty_folders':len({r['slug'] for r in selected}),'matched_images':len(selected),
            'images':len(selected) if options['mode']!='json' else 0,'annotations':labeled if options['mode']!='images' else 0,
            'labeled':labeled,'unlabeled':len(selected)-labeled,'estimated_bytes':photo_bytes+(labeled*2500 if options['mode']!='images' else 0)+len(selected)*500}
        if options['format']!='native':
            # Reserve one converted image in addition to the expected archive.
            # Actual space is checked incrementally by the standard writer.
            sizes=[max(int(r['bytes']),int(r['annotation']['width'])*int(r['annotation']['height'])*4+65536) for r in selected]
            if options['mode']!='json':summary['estimated_bytes']=photo_bytes+max(sizes,default=0)+labeled*4000
            summary['negative_images']=sum(bool(r['annotation']['no_label']) for r in selected)
            summary['split_counts']={}
            if selected:
                mapping=assign_splits(selected,options['split'])
                for v in mapping.values():summary['split_counts'][v or 'unsplit']=summary['split_counts'].get(v or 'unsplit',0)+1
        return slugs,selected,summary

    def preview(self,body):
        options=self.options(body);_,_,summary=self.selection(options)
        return {'options':options,**summary}

    def catalog(self):
        with self.store.db() as db:
            counts={r['slug']:dict(r) for r in db.execute("SELECT i.slug,count(*) AS total,count(a.id) AS labeled FROM images i LEFT JOIN annotations a ON a.id=i.id WHERE i.path LIKE 'images/%' GROUP BY i.slug")}
        return [{'slug':slug,'name':r['Название вина'].strip(),'winery':r['Винодельня'],
            'total':counts.get(slug,{}).get('total',0),'labeled':counts.get(slug,{}).get('labeled',0)} for slug,r in self.store.catalog.items()]

    def cleanup(self):
        with self.lock,self.store.db() as db:
            old=[r['id'] for r in db.execute("SELECT id FROM export_jobs WHERE expires<? AND status NOT IN ('queued','building')",(time.time(),))]
            for ident in old:
                shutil.rmtree(self.folder(ident),ignore_errors=True)
                db.execute('DELETE FROM export_jobs WHERE id=?',(ident,))

    def create(self,body):
        options=self.options(body)
        with self.lock:
            self.cleanup();_,rows,summary=self.selection(options)
            if not rows:raise ValueError('Под выбранные условия не попало ни одного фото')
            with self.store.db() as db:
                if db.execute("SELECT 1 FROM export_jobs WHERE status IN ('queued','building')").fetchone():raise ValueError('Другой архив ещё собирается. Дождитесь его завершения.')
                if db.execute('SELECT count(*) FROM export_jobs').fetchone()[0]>=5:raise ValueError('Удалите один из прежних архивов, чтобы создать новый')
            if shutil.disk_usage(self.root).free<summary['estimated_bytes']+1024**3:raise ValueError('Недостаточно места для ZIP. Удалите прежние архивы или выберите меньше папок.')
            ident=uuid.uuid4().hex;created=time.time();date=datetime.fromtimestamp(created,timezone.utc).strftime('%Y%m%d-%H%M%S')
            name=f"wine-{options['format']}-{options['mode']}-{options['filter']}-{date}.zip"
            with self.store.db() as db:db.execute('INSERT INTO export_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (ident,'queued',created,created+TTL,json.dumps(options),json.dumps(summary),0,len(rows),0,'',name))
            self.pool.submit(self.build,ident,options)
            return self.get(ident)

    def update(self,ident,**values):
        with self.store.db() as db:db.execute('UPDATE export_jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[ident])

    def build(self,ident,options):
        folder=self.folder(ident);snapshot=folder/'snapshot';part=folder/'dataset.zip.part'
        try:
            folder.mkdir();snapshot.mkdir();self.update(ident,status='building')
            # Hard links keep bytes stable if an admin moves/rejects photos while ZIP is built.
            # The guard is released before reading the large image payloads.
            with self.store.guard():
                slugs,rows,summary=self.selection(options)
                if not rows:raise ValueError('Выборка стала пустой. Обновите параметры выгрузки.')
                if shutil.disk_usage(self.root).free<summary['estimated_bytes']+1024**3:
                    raise ValueError('Недостаточно места для ZIP. Выберите меньше папок.')
                for n,row in enumerate(rows):
                    if row['annotation'] and options['mode']!='images' and row['annotation']['sha256']!=row['sha256']:
                        raise ValueError('Границы относятся к другой версии фото: '+row['path'])
                    if options['mode']!='json' or options['format']!='native':
                        src=self.store.path(row['path']);os.link(src,snapshot/str(n))
                self.update(ident,summary=json.dumps(summary),total=len(rows))
            manifest={'schema_version':1,'snapshot_at':datetime.now(timezone.utc).isoformat(),'selection':options,
                'coordinate_system':'EXIF-oriented image; normalized x/y from top left. Apply EXIF orientation before using points.',
                'notes':['Original image bytes and EXIF are preserved.','no_label=true is a saved negative annotation, not an unannotated photo.',
                         'Slug review status is independent of label boundary annotation.'],
                'counts':summary,'folders':slugs,'items':[]}
            with zipfile.ZipFile(part,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
                if options['format']!='native':
                    write_standard(archive,rows,snapshot,options,self.store,lambda done:self.update(ident,done=done))
                else:
                    for slug in slugs:
                        if options['mode']!='json':archive.writestr('images/'+slug+'/','')
                        if options['mode']!='images':archive.writestr('annotations/'+slug+'/','')
                    for n,row in enumerate(rows):
                        image_path='images/'+row['slug']+'/'+Path(row['path']).name
                        annotation_path=None
                        if options['mode']!='json':
                            digest=hashlib.sha256()
                            with (snapshot/str(n)).open('rb') as source,archive.open(image_path,'w',force_zip64=True) as target:
                                for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk);target.write(chunk)
                            if digest.hexdigest()!=row['sha256']:raise ValueError('Изменился файл изображения: '+row['path'])
                        if row['annotation'] is not None and options['mode']!='images':
                            annotation_path='annotations/'+row['slug']+'/'+Path(row['path']).name+'.json'
                            archive.writestr(annotation_path,json.dumps(self.annotation_json(row,image_path),ensure_ascii=False,indent=2))
                        manifest['items'].append({'id':row['id'],'slug':row['slug'],'image_path':image_path,
                            'image_included':options['mode']!='json','annotation_path':annotation_path,
                            'sha256':row['sha256'],'slug_review_state':row['review_state']})
                        if (n+1)%20==0:self.update(ident,done=n+1)
                    if options['mode']!='images':archive.writestr('_export.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            final=folder/'dataset.zip';part.replace(final)
            self.update(ident,status='ready',done=len(rows),bytes=final.stat().st_size,expires=time.time()+TTL)
        except Exception as e:
            part.unlink(missing_ok=True);self.update(ident,status='error',error=str(e))
        finally:shutil.rmtree(snapshot,ignore_errors=True)

    def annotation_json(self,row,image_path):
        a=row['annotation'];points=json.loads(a['points']);width,height=a['width'],a['height']
        self.store.validate_bounds({'points':points,'no_label':bool(a['no_label'])})
        xs=[p[0] for p in points];ys=[p[1] for p in points]
        return {'schema_version':1,'id':row['id'],'slug':row['slug'],'image_path':image_path,'sha256':row['sha256'],
            'label':'front_label','width':width,'height':height,'orientation':'EXIF-applied','coordinate_system':'normalized_xy_top_left',
            'points':points,'points_pixels':[[x*width,y*height] for x,y in points],
            'bbox_xyxy_normalized':[min(xs),min(ys),max(xs),max(ys)] if points else None,
            'yolo_box':[0,(min(xs)+max(xs))/2,(min(ys)+max(ys))/2,max(xs)-min(xs),max(ys)-min(ys)] if points else None,
            'no_label':bool(a['no_label']),'revision':a['revision'],'updated':a['updated']}

    def public(self,row):
        r=dict(row);r['options']=json.loads(r['options']);r['summary']=json.loads(r['summary'])
        if r['status']=='ready':r['download_url']='/admin/api/exports/'+r['id']+'/download'
        return r

    def get(self,ident):
        self.folder(ident)
        with self.store.db() as db:r=db.execute('SELECT * FROM export_jobs WHERE id=?',(ident,)).fetchone()
        if not r:raise ValueError('Архив не найден или срок хранения истёк')
        return self.public(r)

    def list(self):
        self.cleanup()
        with self.store.db() as db:return [self.public(r) for r in db.execute('SELECT * FROM export_jobs ORDER BY created DESC')]

    def download(self,ident):
        with self.lock:
            self.cleanup();job=self.get(ident);p=self.folder(ident)/'dataset.zip'
            if job['status']!='ready' or not p.is_file():raise ValueError('Архив ещё не готов или уже удалён')
            self.update(ident,expires=time.time()+TTL)
            return p,job['filename']

    def delete(self,ident):
        with self.lock:
            job=self.get(ident)
            if job['status'] in {'queued','building'}:raise ValueError('Дождитесь завершения сборки архива')
            shutil.rmtree(self.folder(ident),ignore_errors=True)
            with self.store.db() as db:db.execute('DELETE FROM export_jobs WHERE id=?',(ident,))
            return {'ok':True}
