"""Local, journalled dataset curation. No training or network image lookup."""
from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import unicodedata
import uuid
import webbrowser

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
import uvicorn

HERE = Path(__file__).resolve().parent
DEFAULT_DATASET = HERE.parent / 'wine_dataset'
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    EXTENSIONS |= {'.heic', '.heif'}
except ImportError:
    pass


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    temp = Path(str(path) + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def norm(s):
    return re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', s).lower().replace('ё', 'е')).strip()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.work = self.root / 'curation'
        self.work.mkdir(exist_ok=True)
        self.dbpath = self.root / 'collector/state.sqlite3'
        if not self.dbpath.exists():
            raise ValueError('Не найдена база collector/state.sqlite3')
        self.catalog = {r['Slug']: r for r in read_csv(self.root / 'catalog.csv')}
        self.lock = threading.RLock()
        self.guard_state = threading.local()
        with self.guard(), self.db() as db:
            # Back up before the first schema or metadata mutation.
            backup = self.work / 'before_curation'
            if not backup.exists():
                backup.mkdir()
                with sqlite3.connect(backup / 'state.sqlite3') as dest:
                    db.backup(dest)
                for name in ['manifest.csv', 'coverage.csv', 'README.md', 'summary.json']:
                    if (self.root / name).exists():
                        shutil.copy2(self.root / name, backup / name)
            db.executescript('''
                CREATE TABLE IF NOT EXISTS curation_meta (
                    id TEXT PRIMARY KEY, state TEXT, original_path TEXT,
                    session TEXT, reviewed_at TEXT, original_status TEXT);
                CREATE TABLE IF NOT EXISTS curation_events (
                    id TEXT PRIMARY KEY, kind TEXT, created TEXT, state TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS curation_sources (
                    id TEXT PRIMARY KEY, path TEXT, root TEXT, size INTEGER, mtime INTEGER,
                    state TEXT, assigned_slug TEXT, asset_id TEXT, session TEXT);
            ''')
            db.execute('''INSERT OR IGNORE INTO curation_meta
                SELECT id, CASE WHEN status='accepted' THEN 'legacy_accepted' ELSE 'pending' END,
                path, '', '', status FROM images''')
        self.recover()

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=60)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextlib.contextmanager
    def guard(self):
        with self.lock:
            if getattr(self.guard_state, 'active', False):
                yield
                return
            with (self.work / '.lock').open('a') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                self.guard_state.active = True
                try:
                    yield
                finally:
                    self.guard_state.active = False

    def path(self, relative):
        p = (self.root / relative).resolve()
        if not p.is_relative_to(self.root) or Path(relative).parts[0] not in {'images', 'needs_review'}:
            raise ValueError('Недопустимый путь изображения')
        return p

    def rows(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM images ORDER BY slug,id')]

    def available(self, slug, filename, bucket, reserved=None):
        if slug not in self.catalog or Path(slug).name != slug:
            raise ValueError('Неизвестный slug')
        dest = self.path(f'{bucket}/{slug}/{Path(filename).name}')
        reserved = reserved or set()
        while dest.exists() or str(dest) in reserved:
            dest = dest.with_name(dest.stem + '_' + uuid.uuid4().hex[:8] + dest.suffix)
        return dest

    def asset(self, db, ident):
        r = db.execute('SELECT * FROM images WHERE id=?', (ident,)).fetchone()
        if not r:
            raise ValueError('Изображение уже изменено или отсутствует')
        return dict(r)

    def meta(self, db, ident):
        r = db.execute('SELECT * FROM curation_meta WHERE id=?', (ident,)).fetchone()
        return dict(r) if r else None

    def _files(self, operations):
        for op in operations:
            src, dst = Path(op['src']), Path(op['dst'])
            if src == dst:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                if not src.is_file() or sha(src) != op['sha']:
                    raise ValueError(f'Исходник изменён: {src.name}')
                # Exclusive linking never overwrites a destination, even in a race.
                try:
                    os.link(src, dst)
                except OSError as exc:
                    if exc.errno != 18:  # EXDEV
                        raise
                    tmp = dst.with_name('.' + uuid.uuid4().hex + '.tmp')
                    try:
                        shutil.copy2(src, tmp)
                        if sha(tmp) != op['sha']:
                            raise ValueError('Контрольная сумма копии не совпала')
                        os.link(tmp, dst)
                    finally:
                        tmp.unlink(missing_ok=True)
            if sha(dst) != op['sha']:
                raise ValueError(f'Конфликт файла назначения: {dst.name}')
            if op['mode'] == 'move' and src.exists():
                if sha(src) != op['sha']:
                    raise ValueError('Исходный файл изменён во время переноса')
                src.unlink()
            if op['mode'] == 'copy' and src.exists() and os.stat(src).st_ino == os.stat(dst).st_ino:
                # Copies must not remain hard linked to the user's original.
                tmp = dst.with_name('.' + uuid.uuid4().hex + '.tmp')
                shutil.copy2(dst, tmp)
                tmp.replace(dst)

    def _finish(self, ident, payload):
        self._files(payload['files'])
        with self.db() as db:
            for change in payload['changes']:
                for table, key in [('images', 'row'), ('curation_meta', 'meta'), ('curation_sources', 'source'), ('scans', 'scan')]:
                    after = change.get(key + '_after')
                    before = change.get(key + '_before')
                    if after:
                        fields = list(after)
                        db.execute(f'INSERT OR REPLACE INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})', list(after.values()))
                    elif before:
                        db.execute(f'DELETE FROM {table} WHERE id=?', (before['id'],))
            if payload.get('undo_of'):
                db.execute("UPDATE curation_events SET state='undone' WHERE id=?", (payload['undo_of'],))
            db.execute("UPDATE curation_events SET state='done' WHERE id=?", (ident,))
        self.export()

    def commit(self, kind, files, changes, undo_of=None):
        ident = uuid.uuid4().hex
        payload = {'files': files, 'changes': changes, 'undo_of': undo_of}
        with self.db() as db:
            db.execute('INSERT INTO curation_events VALUES (?,?,?,?,?)',
                       (ident, kind, stamp(), 'pending', json.dumps(payload, ensure_ascii=False)))
        self._finish(ident, payload)
        return ident

    def recover(self):
        with self.guard(), self.db() as db:
            pending = list(db.execute("SELECT * FROM curation_events WHERE state='pending' ORDER BY rowid"))
            for event in pending:
                self._finish(event['id'], json.loads(event['payload']))

    def export(self):
        rows = self.rows()
        with self.db() as db:
            metas = {r['id']: dict(r) for r in db.execute('SELECT * FROM curation_meta')}
            sources = [dict(r) for r in db.execute('SELECT * FROM curation_sources ORDER BY path')]
        write_csv(self.work / 'own_labels.csv', sources,
                  ['id','path','root','size','mtime','state','assigned_slug','asset_id','session'])
        for r in rows:
            m = metas[r['id']]
            r.update(human_review=m['state'], capture_session=m['session'],
                     original_path=m['original_path'], reviewed_at=m['reviewed_at'])
        write_csv(self.root / 'manifest.csv', rows, list(rows[0]) if rows else ['id','slug','path','status'])
        previous = {r['slug']: r for r in read_csv(self.root / 'coverage.csv')}
        by_slug = {}
        for r in rows:
            by_slug.setdefault(r['slug'], []).append(r)
        covers = []
        for slug, cat in self.catalog.items():
            rr = by_slug.get(slug, [])
            p = previous.get(slug, {'slug': slug, 'name': cat['Название вина'], 'winery': cat['Винодельня']})
            p.update(accepted=sum(r['status']=='accepted' for r in rr),
                     needs_review=sum(r['status']=='needs_review' for r in rr),
                     additional_internet=sum(r['status']=='accepted' and r['source'] not in {'vino-svoe.ru','own_photo'} for r in rr),
                     files_in_images=sum(r['path'].startswith('images/') for r in rr),
                     files_in_needs_review=sum(r['path'].startswith('needs_review/') for r in rr),
                     human_confirmed=sum(r['human_review']=='confirmed' for r in rr),
                     awaiting_human_review=sum(r['human_review']=='pending' for r in rr))
            p['note'] = 'human_review_pending' if p['awaiting_human_review'] else ('target_not_reached' if p['accepted'] < 5 else '')
            covers.append(p)
        fields = list(dict.fromkeys(k for r in covers for k in r))
        write_csv(self.root / 'coverage.csv', covers, fields)
        summary = self.stats()
        tmp = self.work / 'summary.tmp'
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        tmp.replace(self.work / 'summary.json')

    def stats(self):
        with self.db() as db:
            return {'catalog': len(self.catalog),
                    'images': db.execute("SELECT count(*) FROM images WHERE path LIKE 'images/%'").fetchone()[0],
                    'needs_review': db.execute("SELECT count(*) FROM images WHERE path LIKE 'needs_review/%'").fetchone()[0],
                    'pending': db.execute("SELECT count(*) FROM curation_meta WHERE state='pending'").fetchone()[0],
                    'confirmed': db.execute("SELECT count(*) FROM curation_meta WHERE state='confirmed'").fetchone()[0],
                    'updated': stamp()}

    def migrate(self):
        with self.guard(), self.db() as db:
            files, changes, reserved = [], [], set()
            for row in self.rows():
                if not row['path'].startswith('needs_review/'):
                    continue
                src = self.path(row['path'])
                if sha(src) != row['sha256']:
                    raise ValueError(f'Контрольная сумма не совпала: {src}')
                dst = self.available(row['slug'], src.name, 'images', reserved)
                reserved.add(str(dst))
                m = self.meta(db, row['id'])
                files.append({'src': str(src), 'dst': str(dst), 'sha': row['sha256'], 'mode': 'move'})
                changes.append({'row_before': row, 'row_after': {**row,'path': str(dst.relative_to(self.root))},
                                'meta_before': m, 'meta_after': {**m,'state':'pending'}})
            event = self.commit('migration', files, changes) if files else None
            return {'moved': len(files), 'event': event, **self.stats()}

    def wines(self, q='', pending=False):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('''SELECT i.id,i.slug,i.path,i.kind,i.source,m.state
                FROM images i JOIN curation_meta m ON i.id=m.id''')]
        groups = {}
        for r in rows:
            groups.setdefault(r['slug'], []).append(r)
        result = []
        tokens = norm(q).split()
        for slug, cat in self.catalog.items():
            text = norm(' '.join([cat['Название вина'],cat['Винодельня'],cat['Категория'],cat['Сорт винограда'],slug]))
            if any(t not in text for t in tokens):
                continue
            rr = groups.get(slug, [])
            in_images = [r for r in rr if r['path'].startswith('images/')]
            count = sum(r['state'] != 'confirmed' for r in in_images)
            if pending and (not count or len(in_images) < 2):
                continue
            refs = sorted([r for r in in_images if r['state']!='rejected'],
                          key=lambda r: (r['kind'] != 'catalog_reference', r['source'] != 'vino-svoe.ru',r['id']))
            ref = refs[0] if refs else None
            result.append({'slug':slug,'name':cat['Название вина'].strip(),'winery':cat['Винодельня'],
                           'category':cat['Категория'],'grapes':cat['Сорт винограда'],
                           'url': 'https://vino-svoe.ru/wines/'+slug,'count':len(in_images),'unreviewed':count,
                           'reference':ref['id'] if ref else None,
                           'reference_label': ('Фото из карточки портала' if ref and ref['kind']=='catalog_reference' else 'Фото из текущей папки — проверьте принадлежность')})
        return sorted(result, key=lambda r: (norm(r['winery']), norm(r['name']), r['slug']))

    def group(self, slug):
        if slug not in self.catalog:
            raise ValueError('Неизвестный slug')
        with self.db() as db:
            rr = [dict(r) for r in db.execute('''SELECT i.*,m.state AS review_state FROM images i
                JOIN curation_meta m ON i.id=m.id WHERE i.slug=? AND i.path LIKE 'images/%' ORDER BY i.kind,i.id''', (slug,))]
            shared = {}
            for row in rr:
                shared[row['id']] = [r[0] for r in db.execute('SELECT DISTINCT slug FROM images WHERE sha256=? AND slug!=?', (row['sha256'],slug))]
        version = hashlib.sha256(json.dumps([(r['id'],r['path'],r['review_state']) for r in rr]).encode()).hexdigest()
        return {'slug':slug,'version':version,'items':[{'id':r['id'],'name':Path(r['path']).name,'source':r['source'],
                    'page_url':r['page_url'],'status':r['review_state'],'width':r['width'],'height':r['height'],
                    'kind':r['kind'],'reason':r['reason'],'shared_slugs':shared[r['id']]} for r in rr]}

    def review(self, slug, version, reject):
        with self.guard():
            group = self.group(slug)
            if group['version'] != version:
                raise ValueError('Папка изменена в другом окне. Обновите страницу перед сохранением.')
            ids = {r['id'] for r in group['items']}
            if not set(reject) <= ids:
                raise ValueError('Выбранное фото не принадлежит открытой папке')
            files, changes = [], []
            with self.db() as db:
                for ident in ids:
                    row = self.asset(db, ident)
                    m = self.meta(db, ident)
                    after = dict(row)
                    rejected = ident in reject
                    if rejected:
                        src = self.path(row['path'])
                        dst = self.available(slug, src.name, 'needs_review')
                        files.append({'src':str(src),'dst':str(dst),'sha':row['sha256'],'mode':'move'})
                        after['path'] = str(dst.relative_to(self.root))
                    after.update(status='needs_review' if rejected else 'accepted',
                                 validation='human_rejected' if rejected else 'human_confirmed')
                    changes.append({'row_before':row,'row_after':after,'meta_before':m,
                                    'meta_after':{**m,'state':'rejected' if rejected else 'confirmed','reviewed_at':stamp()}})
            event = self.commit('review', files, changes)
            return {'event':event,'confirmed':len(ids)-len(reject),'rejected':len(reject)}

    def scan(self, folder):
        root = Path(folder).expanduser().resolve()
        if not root.is_dir() or root == Path('/') or root == Path.home():
            raise ValueError('Выберите папку со снимками, а не весь диск или домашнюю папку')
        if root.is_relative_to(self.root) or self.root.is_relative_to(root):
            raise ValueError('Папка источника должна находиться отдельно от датасета')
        files = sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in EXTENSIONS and not any(x.startswith('.') for x in p.relative_to(root).parts))
        with self.guard(), self.db() as db:
            for p in files:
                s = p.stat()
                ident = hashlib.sha256(f'{p}|{s.st_size}|{s.st_mtime_ns}'.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO curation_sources VALUES (?,?,?,?,?,?,?,?,?)',
                           (ident,str(p),str(root),s.st_size,s.st_mtime_ns,'pending','','',root.name))
        return self.queue(str(root))

    def queue(self, root):
        root = str(Path(root).expanduser().resolve())
        with self.db() as db:
            rr = [dict(r) for r in db.execute('SELECT * FROM curation_sources WHERE root=? ORDER BY path', (root,))]
        return {'root':root,'total':len(rr),'done':sum(r['state']=='assigned' for r in rr),
                'skipped':sum(r['state']=='skipped' for r in rr),
                'out_of_catalog':sum(r['state']=='out_of_catalog' for r in rr),
                'items':[{'id':r['id'],'name':str(Path(r['path']).relative_to(root)),'state':r['state']} for r in rr if r['state']!='assigned']}

    def assign(self, ident, slug, mode, session):
        if mode not in {'move','copy'} or slug not in self.catalog:
            raise ValueError('Неверное действие или slug')
        with self.guard(), self.db() as db:
            r = db.execute('SELECT * FROM curation_sources WHERE id=?',(ident,)).fetchone()
            if not r or r['state']=='assigned':
                raise ValueError('Фото уже размечено. Обновите страницу.')
            source = dict(r)
            src = Path(r['path'])
            s = src.stat()
            if s.st_size != r['size'] or s.st_mtime_ns != r['mtime']:
                raise ValueError('Файл изменён. Заново откройте папку.')
            digest = sha(src)
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert('RGB')
                im.load()
                width,height = im.size
                pixel = hashlib.sha256(im.tobytes()+str(im.size).encode()).hexdigest()
            asset_id = uuid.uuid4().hex
            dst = self.available(slug, 'own_'+src.name, 'images')
            row = dict(id=asset_id,slug=slug,source='own_photo',page_url=src.as_uri(),image_url=src.as_uri(),
                       title=self.catalog[slug]['Название вина'],kind='bottle',status='accepted',
                       reason='manual_slug_assignment',path=str(dst.relative_to(self.root)),sha256=digest,
                       pixel_sha256=pixel,phash='',width=width,height=height,bytes=s.st_size,vintage='',
                       validation='human_confirmed',downloaded_at=stamp())
            meta = dict(id=asset_id,state='confirmed',original_path=str(src),session=session.strip() or Path(r['root']).name,
                        reviewed_at=stamp(),original_status='own_photo')
            change = {'row_before':None,'row_after':row,'meta_before':None,'meta_after':meta,
                      'source_before':source,'source_after':{**source,'state':'assigned','assigned_slug':slug,'asset_id':asset_id,'session':meta['session']}}
            event = self.commit('assign', [{'src':str(src),'dst':str(dst),'sha':digest,'mode':mode}], [change])
            return {'event':event,'slug':slug,'path':str(dst),'root':r['root']}

    def skip(self, ident, skipped, state=None):
        state = state or ('skipped' if skipped else 'pending')
        if state not in {'pending','skipped','out_of_catalog'}:
            raise ValueError('Неизвестное состояние')
        with self.guard(), self.db() as db:
            db.execute("UPDATE curation_sources SET state=? WHERE id=? AND state!='assigned'", (state,ident))
        with self.guard():
            self.export()

    def undo(self, kind):
        if kind not in {'assign','review','migration','duplicate'}:
            raise ValueError('Нельзя отменить это действие через UI')
        with self.guard(), self.db() as db:
            e = db.execute("SELECT * FROM curation_events WHERE state='done' AND kind IN ('assign','review','migration','duplicate') ORDER BY rowid DESC LIMIT 1").fetchone()
            if not e:
                raise ValueError('Нет действий для отмены')
            if e['kind'] != kind:
                raise ValueError('Последнее действие сделано в другом инструменте. Отмените его в том окне.')
            payload = json.loads(e['payload'])
            files, changes = [], []
            for f in reversed(payload['files']):
                dst, src = Path(f['src']), Path(f['dst'])
                if f['mode']=='copy':
                    # Retain copies in an undo archive; never delete image bytes.
                    dst = self.work / 'undo_archive' / e['id'] / src.name
                if dst.exists():
                    raise ValueError('Нельзя отменить: исходное имя уже занято. Файл не перезаписан.')
                files.append({'src':str(src),'dst':str(dst),'sha':f['sha'],'mode':'move'})
            for c in payload['changes']:
                for key in ['row','meta','source','scan']:
                    if c.get(key+'_after'):
                        table={'row':'images','meta':'curation_meta','source':'curation_sources','scan':'scans'}[key]
                        current=db.execute(f'SELECT * FROM {table} WHERE id=?',(c[key+'_after']['id'],)).fetchone()
                        if not current or dict(current)!=c[key+'_after']:
                            raise ValueError('Данные изменены после действия. Автоматическая отмена остановлена.')
                changes.append({k.replace('_before','_AFTER').replace('_after','_before').replace('_AFTER','_after'):v for k,v in c.items()})
            ident = self.commit('undo',files,changes,undo_of=e['id'])
            return {'event':ident,'undone':e['id']}


def create_app(root=DEFAULT_DATASET, mode='assign'):
    store = Store(root)
    app = FastAPI(docs_url=None, redoc_url=None)
    app.state.store = store
    app.mount('/static', StaticFiles(directory=HERE/'static'), name='static')

    @app.middleware('http')
    async def local_only(request:Request, call_next):
        host = request.headers.get('host','').split(':')[0]
        if host not in {'127.0.0.1','localhost','testserver'}:
            return Response('Local access only',status_code=403)
        if request.method=='POST':
            origin = request.headers.get('origin')
            if request.headers.get('x-curation')!='1' or (origin and origin != str(request.base_url).rstrip('/')):
                return Response('Invalid origin',status_code=403)
        try:
            return await call_next(request)
        except (ValueError, OSError) as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':str(exc)},status_code=409)

    @app.get('/')
    def home():
        return FileResponse(HERE/'static/index.html')

    @app.get('/api/config')
    def config():
        return {'mode':mode,'dataset':str(store.root),'default_source':'/Users/forthang/Downloads/Camera','stats':store.stats()}

    @app.get('/api/wines')
    def wines(q:str='', pending:bool=False):
        return {'items':store.wines(q,pending)}

    @app.get('/api/group/{slug}')
    def group(slug:str):
        return store.group(slug)

    @app.post('/api/review')
    def review(body:dict):
        return store.review(body['slug'],body['version'],body.get('reject',[]))

    @app.post('/api/source')
    def source(body:dict):
        return store.scan(body['folder'])

    @app.post('/api/choose-folder')
    def choose():
        try:
            p=subprocess.run(['osascript','-e','POSIX path of (choose folder with prompt "Выберите папку со снимками вина")'],capture_output=True,text=True,timeout=120)
            if p.returncode:
                return {'cancelled':True}
            return store.scan(p.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise ValueError('Введите путь к папке в поле вручную')

    @app.get('/api/queue')
    def queue(root:str):
        return store.queue(root)

    @app.post('/api/assign')
    def assign(body:dict):
        return store.assign(body['id'],body['slug'],body.get('mode','move'),body.get('session',''))

    @app.post('/api/skip')
    def skip(body:dict):
        store.skip(body['id'],body.get('skipped',True),body.get('state'))
        return {'ok':True}

    @app.post('/api/undo')
    def undo(body:dict):
        if body.get('kind') not in {'assign','review'}:
            raise ValueError('Массовый перенос отменяется только отдельной командой')
        return store.undo(body['kind'])

    @app.get('/api/image/{kind}/{ident}')
    def image_file(kind:str, ident:str, size:int=1000):
        size = min(2200,max(100,size))
        with store.db() as db:
            if kind=='asset':
                row=store.asset(db,ident)
                p=store.path(row['path'])
            elif kind=='source':
                r=db.execute('SELECT * FROM curation_sources WHERE id=?',(ident,)).fetchone()
                if not r:
                    raise HTTPException(404,'Фото не найдено')
                p=Path(r['path'])
                if r['state']=='assigned' and not p.exists():
                    p=store.path(store.asset(db,r['asset_id'])['path'])
            else:
                raise HTTPException(404)
        key=hashlib.sha256(f'{p}|{p.stat().st_mtime_ns}|{size}'.encode()).hexdigest()
        cache=store.work/'previews'
        cache.mkdir(exist_ok=True)
        dest=cache/(key+'.jpg')
        if not dest.exists():
            with Image.open(p) as im:
                im=ImageOps.exif_transpose(im).convert('RGBA')
                im.thumbnail((size,size))
                bg=Image.new('RGBA',im.size,'white')
                bg.alpha_composite(im)
                b=io.BytesIO()
                bg.convert('RGB').save(b,'JPEG',quality=90)
                temp=dest.with_suffix('.'+uuid.uuid4().hex+'.tmp')
                temp.write_bytes(b.getvalue())
                temp.replace(dest)
        return FileResponse(dest,media_type='image/jpeg',headers={'Cache-Control':'private,max-age=3600'})

    return app


def main(mode):
    parser=argparse.ArgumentParser(description='Разметка фото вина' if mode=='assign' else 'Проверка папок вина')
    parser.add_argument('--dataset',type=Path,default=DEFAULT_DATASET)
    parser.add_argument('--port',type=int,default=8093 if mode=='assign' else 8094)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--migrate-review',action='store_true')
    parser.add_argument('--undo-migration',action='store_true')
    args=parser.parse_args()
    if args.migrate_review:
        print(json.dumps(Store(args.dataset).migrate(),ensure_ascii=False,indent=2))
        return
    if args.undo_migration:
        print(json.dumps(Store(args.dataset).undo('migration'),ensure_ascii=False,indent=2))
        return
    app=create_app(args.dataset,mode)
    url=f'http://127.0.0.1:{args.port}'
    if not args.no_browser:
        threading.Timer(1.5,lambda:webbrowser.open(url)).start()
    print(url,flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port)
