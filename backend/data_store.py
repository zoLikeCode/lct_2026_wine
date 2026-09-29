import sys,json,uuid,io,hashlib,time
from pathlib import Path
from PIL import Image,ImageOps
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'wine_curation'))
from curation import Store,stamp,sha

class DataStore(Store):
    def __init__(self,root):
        super().__init__(root)
        self.media=self.root.parent/'wine_media';self.media.mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY,created TEXT,device TEXT,path TEXT,
              status TEXT,result TEXT,error TEXT,asset_id TEXT,assigned_slug TEXT);
            CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,name TEXT,created TEXT,root TEXT);
            CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY,sha256 TEXT,points TEXT,
              no_label INTEGER,width INTEGER,height INTEGER,updated TEXT,revision INTEGER);
            CREATE TABLE IF NOT EXISTS annotation_history(id INTEGER PRIMARY KEY,asset_id TEXT,before_json TEXT,after_json TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS admin_sessions(token_hash TEXT PRIMARY KEY,expires REAL);
            CREATE INDEX IF NOT EXISTS scan_created ON scans(created);
            CREATE INDEX IF NOT EXISTS image_sha ON images(sha256);
            ''')

    def image_path(self,kind,ident):
        with self.db() as db:
            if kind=='asset':return self.path(self.asset(db,ident)['path'])
            if kind=='scan':
                r=db.execute('SELECT path FROM scans WHERE id=?',(ident,)).fetchone()
                if not r:raise ValueError('Скан не найден')
                p=Path(r['path'])
            elif kind=='source':
                r=db.execute('SELECT * FROM curation_sources WHERE id=?',(ident,)).fetchone()
                if not r:raise ValueError('Снимок не найден')
                if r['state']=='assigned':return self.path(self.asset(db,r['asset_id'])['path'])
                p=Path(r['path'])
            else:raise ValueError('Неизвестный тип фото')
            if not p.resolve().is_relative_to(self.media.resolve()):raise ValueError('Исходник находится на другом компьютере')
            return p

    def scan_start(self,data,device):
        ident=uuid.uuid4().hex;folder=self.media/'scans';folder.mkdir(exist_ok=True)
        p=folder/(ident+'.jpg');temporary=folder/(ident+'.tmp')
        # The stored, EXIF-oriented image is the one sent to inference. Re-encoding
        # strips camera metadata; the separate thumbnail is made by the browser.
        with Image.open(io.BytesIO(data)) as source:
            image=ImageOps.exif_transpose(source)
            image.thumbnail((4096,4096),Image.Resampling.LANCZOS)
            if image.mode in {'RGBA','LA'} or 'transparency' in image.info:
                rgba=image.convert('RGBA')
                rgb=Image.new('RGB',rgba.size,'white')
                rgb.paste(rgba,mask=rgba.getchannel('A'))
            else:rgb=image.convert('RGB')
            # A noisy 4096 px image can exceed the GPU API's 24 MB upload limit.
            for quality in (95,92,88):
                output=io.BytesIO();rgb.save(output,'JPEG',quality=quality,subsampling=0)
                if output.tell()<=24*1024*1024:break
            else:raise ValueError('Фото после обработки больше 24 МБ')
            try:
                temporary.write_bytes(output.getvalue())
                temporary.replace(p)
            finally:temporary.unlink(missing_ok=True)
        try:
            with self.db() as db:db.execute('INSERT INTO scans VALUES(?,?,?,?,?,?,?,?,?)',(ident,stamp(),device,str(p),'processing','{}','','',''))
        except Exception:
            p.unlink(missing_ok=True)
            raise
        return ident,p

    def scan_finish(self,ident,result=None,error=''):
        with self.db() as db:db.execute('UPDATE scans SET status=?,result=?,error=? WHERE id=?',('error' if error else 'done',json.dumps(result or {},ensure_ascii=False),error,ident))

    def history(self,offset=0,limit=40,device=None,q=''):
        condition='1=1';args=[]
        if device:condition+=' AND device=?';args.append(device)
        if q:condition+=' AND (result LIKE ? OR assigned_slug LIKE ? OR device LIKE ?)';args.extend(['%'+q+'%']*3)
        with self.db() as db:
            total=db.execute('SELECT count(*) FROM scans WHERE '+condition,args).fetchone()[0]
            rr=[dict(r) for r in db.execute('SELECT * FROM scans WHERE '+condition+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',args+[min(limit,100),max(offset,0)])]
        for r in rr:r['result']=json.loads(r['result']);r.pop('path',None)
        return {'items':rr,'total':total,'offset':offset}

    def batch(self,name):
        ident=uuid.uuid4().hex;root=self.media/'uploads'/ident;root.mkdir(parents=True)
        with self.db() as db:db.execute('INSERT INTO uploads VALUES(?,?,?,?)',(ident,name[:200],stamp(),str(root)))
        return ident

    def upload(self,ident,name,data):
        with self.db() as db:r=db.execute('SELECT * FROM uploads WHERE id=?',(ident,)).fetchone()
        if not r:raise ValueError('Папка загрузки не найдена')
        filename=Path(name.replace('\\','/')).name
        if not filename or len(filename)>180:filename='photo.jpg'
        with Image.open(io.BytesIO(data)) as im:im.verify()
        p=Path(r['root'])/(uuid.uuid4().hex[:12]+'_'+filename);p.write_bytes(data)
        stat=p.stat();source_id=hashlib.sha256(f'{p}|{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()
        with self.guard(),self.db() as db:
            db.execute('INSERT INTO curation_sources VALUES(?,?,?,?,?,?,?,?,?)',
                (source_id,str(p),r['root'],stat.st_size,stat.st_mtime_ns,'pending','','',ident))
        return {'ok':True}

    def batches(self):
        with self.db() as db:rr=[dict(r) for r in db.execute('SELECT * FROM uploads ORDER BY created DESC')]
        for r in rr:
            with self.db() as db:
                r['total']=db.execute('SELECT count(*) FROM curation_sources WHERE root=?',(r['root'],)).fetchone()[0]
                r['done']=db.execute("SELECT count(*) FROM curation_sources WHERE root=? AND state='assigned'",(r['root'],)).fetchone()[0]
            r.pop('root')
        return rr

    def batch_items(self,ident):
        with self.db() as db:
            r=db.execute('SELECT root FROM uploads WHERE id=?',(ident,)).fetchone()
            if not r:raise ValueError('Папка не найдена')
            items=[dict(x) for x in db.execute('SELECT id,path,state,assigned_slug,asset_id FROM curation_sources WHERE root=? ORDER BY path',(r['root'],))]
        for x in items:x['name']=Path(x.pop('path')).name.split('_',1)[-1]
        items.sort(key=lambda x:(x['name'].casefold(),x['id']))
        return items

    def validate_bounds(self,bounds):
        if bounds is None:return None
        points=bounds.get('points',[]);empty=bool(bounds.get('no_label'))
        if not empty:
            if len(points) not in {2,4}:raise ValueError('Выделите прямоугольник или четыре угла этикетки')
            if any(len(p)!=2 or any(not isinstance(v,(int,float)) or not 0<=v<=1 for v in p) for p in points):raise ValueError('Границы должны находиться внутри изображения')
            if len(points)==2:
                (x,y),(x2,y2)=points;points=[[min(x,x2),min(y,y2)],[max(x,x2),min(y,y2)],[max(x,x2),max(y,y2)],[min(x,x2),max(y,y2)]]
            area=abs(sum(points[i][0]*points[(i+1)%4][1]-points[(i+1)%4][0]*points[i][1] for i in range(4)))/2
            if area<.00005:raise ValueError('Выделенная область слишком мала или углы пересекаются')
            crosses=[]
            for i in range(4):
                a,b,c=points[i],points[(i+1)%4],points[(i+2)%4]
                crosses.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
            if not (all(v>0 for v in crosses) or all(v<0 for v in crosses)):
                raise ValueError('Отметьте четыре угла по порядку, без пересечения сторон')
        return {'points':[] if empty else points,'no_label':empty}

    def annotate(self,ident,bounds,revision=None):
        b=self.validate_bounds(bounds)
        if b is None:raise ValueError('Границы не заданы')
        with self.guard(),self.db() as db:
            row=self.asset(db,ident);p=self.path(row['path'])
            before=db.execute('SELECT * FROM annotations WHERE id=?',(ident,)).fetchone()
            before=dict(before) if before else None
            current=before['revision'] if before else 0
            if revision is not None and int(revision)!=current:raise ValueError('Разметка изменена в другом окне. Обновите фото.')
            with Image.open(p) as im:w,h=ImageOps.exif_transpose(im).size
            after={'id':ident,'sha256':row['sha256'],'points':json.dumps(b['points']),'no_label':int(b['no_label']),'width':w,'height':h,'updated':stamp(),'revision':current+1}
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?,?,?,?,?,?,?)',list(after.values()))
            db.execute('INSERT INTO annotation_history(asset_id,before_json,after_json,created) VALUES(?,?,?,?)',(ident,json.dumps(before),json.dumps(after),stamp()))
        return self.annotation(ident)

    def annotation(self,ident):
        with self.db() as db:r=db.execute('SELECT * FROM annotations WHERE id=?',(ident,)).fetchone()
        if not r:return {'points':[],'no_label':False,'revision':0}
        d=dict(r);d['points']=json.loads(d['points']);return d

    def annotation_selection(self,filter='unlabeled',slug='',photo_set='all'):
        if filter not in {'unlabeled','labeled','all'}:raise ValueError('Неизвестный фильтр разметки')
        if photo_set not in {'real','all'}:raise ValueError('Неизвестная подборка фотографий')
        clauses=["i.path LIKE 'images/%'"];args=[]
        if slug:clauses.append('i.slug=?');args.append(slug)
        with self.db() as db:
            rows=[dict(r) for r in db.execute('''SELECT i.id,i.slug,i.path,i.sha256,a.revision FROM images i
                LEFT JOIN annotations a ON a.id=i.id AND a.sha256=i.sha256 WHERE '''+' AND '.join(clauses)+' ORDER BY i.slug,i.path',args)]
        if photo_set=='real':
            selection_path=self.root/'collector'/'real_photo_selection.json'
            if not selection_path.exists():raise ValueError('Подборка реальных фотографий ещё не загружена')
            selection=json.loads(selection_path.read_text(encoding='utf-8'))
            # Follow the image identity across slug corrections, but never reuse the
            # visual classification after the image bytes have changed.
            real={(r['image_id'],r['sha256']) for r in selection['items']}
            rows=[r for r in rows if (r['id'],r['sha256']) in real]
        labeled=sum(r['revision'] is not None for r in rows)
        items=[r for r in rows if filter=='all' or (r['revision'] is not None)==(filter=='labeled')]
        return {'items':items,'summary':{'total':len(rows),'labeled':labeled,'unlabeled':len(rows)-labeled,
                                       'selected':len(items),'photo_set':photo_set,'slug':slug}}

    def annotation_items(self,filter='unlabeled',slug='',photo_set='all'):
        return self.annotation_selection(filter,slug,photo_set)['items']

    def assign_source(self,ident,slug,bounds=None):
        b=self.validate_bounds(bounds)
        with self.guard(),self.db() as db:
            r=db.execute('SELECT * FROM curation_sources WHERE id=?',(ident,)).fetchone()
            if not r:raise ValueError('Фото не найдено')
            if r['state']=='assigned':
                asset=self.relabel(r['asset_id'],slug)
            else:
                self.assign(ident,slug,'move',r['session'])
                with self.db() as fresh:asset=fresh.execute('SELECT asset_id FROM curation_sources WHERE id=?',(ident,)).fetchone()[0]
        if b is not None:self.annotate(asset,b)
        return {'asset_id':asset,'slug':slug}

    def relabel(self,ident,slug):
        with self.db() as db:row=self.asset(db,ident);meta=self.meta(db,ident)
        if slug==row['slug'] and row['path'].startswith('images/'):return ident
        src=self.path(row['path']);dst=self.available(slug,src.name,'images')
        after={**row,'slug':slug,'title':self.catalog[slug]['Название вина'],'path':str(dst.relative_to(self.root)),'status':'accepted','validation':'human_confirmed'}
        self.commit('relabel',[{'src':str(src),'dst':str(dst),'sha':row['sha256'],'mode':'move'}],
                    [{'row_before':row,'row_after':after,'meta_before':meta,'meta_after':{**meta,'state':'confirmed','reviewed_at':stamp()}}])
        with self.db() as db:
            db.execute('UPDATE curation_sources SET assigned_slug=? WHERE asset_id=?',(slug,ident))
            db.execute('UPDATE scans SET assigned_slug=? WHERE asset_id=?',(slug,ident))
        self.export();return ident

    def assign_scan(self,ident,slug,bounds=None):
        b=self.validate_bounds(bounds)
        with self.guard(),self.db() as db:
            r=db.execute('SELECT * FROM scans WHERE id=?',(ident,)).fetchone()
            if not r:raise ValueError('Скан не найден')
            source_id='scan_'+ident
            previous=db.execute('SELECT asset_id FROM curation_sources WHERE id=? AND state=\'assigned\'',(source_id,)).fetchone()
            if r['asset_id'] or previous:asset=self.relabel(r['asset_id'] or previous['asset_id'],slug)
            else:
                p=Path(r['path']);s=p.stat()
                with self.db() as fresh:
                    fresh.execute('INSERT OR IGNORE INTO curation_sources VALUES(?,?,?,?,?,?,?,?,?)',(source_id,str(p),str(p.parent),s.st_size,s.st_mtime_ns,'pending','','','scan:'+ident))
                self.assign(source_id,slug,'copy','scan:'+ident)
                with self.db() as fresh:asset=fresh.execute('SELECT asset_id FROM curation_sources WHERE id=?',(source_id,)).fetchone()[0]
            with self.db() as fresh:fresh.execute('UPDATE scans SET asset_id=?,assigned_slug=? WHERE id=?',(asset,slug,ident))
        if b is not None:self.annotate(asset,b)
        return {'asset_id':asset,'slug':slug}
