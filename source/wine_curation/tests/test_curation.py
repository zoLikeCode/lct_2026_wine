import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from curation import Store, create_app, sha, write_csv


def fixture(parent):
    root=parent/'dataset'
    (root/'collector').mkdir(parents=True)
    rows=[]
    for i,bucket in enumerate(['images','needs_review','images']):
        slug='merlo' if i<2 else 'cabernet'
        # First two have colliding names but different bytes.
        p=root/bucket/slug/'photo.jpg'
        p.parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(60,100),['red','blue','green'][i]).save(p)
        rows.append(dict(id=str(i),slug=slug,source='test',page_url='https://example.org/'+str(i),
                         image_url='https://example.org/'+str(i)+'.jpg',title=slug,kind='bottle',
                         status='accepted' if bucket=='images' else 'needs_review',reason='test_reason',
                         path=str(p.relative_to(root)),sha256=sha(p),pixel_sha256=sha(p),phash='',
                         width=60,height=100,bytes=p.stat().st_size,vintage='',validation='test',downloaded_at='2026'))
    with sqlite3.connect(root/'collector/state.sqlite3') as db:
        db.execute('CREATE TABLE images ('+','.join(k+(' INTEGER' if k in {'width','height','bytes'} else ' TEXT')+(' PRIMARY KEY' if k=='id' else '') for k in rows[0])+')')
        for r in rows:db.execute('INSERT INTO images VALUES ('+','.join('?' for _ in r)+')',list(r.values()))
    write_csv(root/'manifest.csv',rows,list(rows[0]))
    cats=[{'Slug':s,'Название вина':n,'Винодельня':'Тестовая винодельня','Категория':'Красное','Сорт винограда':n} for s,n in [('merlo','Мерло'),('cabernet','Каберне')]]
    write_csv(root/'catalog.csv',cats,list(cats[0]))
    write_csv(root/'coverage.csv',[{'slug':s,'accepted':1} for s in ['merlo','cabernet']],['slug','accepted'])
    source=parent/'Camera'
    source.mkdir()
    Image.new('RGB',(90,120),'yellow').save(source/'IMG_01.jpg')
    return root,source


class CurationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root,self.source=fixture(Path(self.temp.name))
        self.s=Store(self.root)

    def tearDown(self):self.temp.cleanup()

    def test_migration_collision_idempotence_and_status(self):
        before={r['id']:r['sha256'] for r in self.s.rows()}
        result=self.s.migrate()
        self.assertEqual(result['moved'],1)
        self.assertEqual(self.s.migrate()['moved'],0)
        paths=[r['path'] for r in self.s.rows()]
        self.assertEqual(len(set(paths)),3)
        self.assertTrue(all(p.startswith('images/') for p in paths))
        self.assertEqual(self.s.rows()[2]['status'],'needs_review')
        for r in self.s.rows():self.assertEqual(sha(self.root/r['path']),before[r['id']])
        self.assertTrue((self.root/'curation/before_curation/state.sqlite3').exists())

    def test_review_and_undo_and_stale_page(self):
        self.s.migrate()
        g=self.s.group('merlo')
        self.s.review('merlo',g['version'],['1'])
        self.assertEqual(self.s.stats()['needs_review'],1)
        with self.assertRaises(ValueError):self.s.review('merlo',g['version'],[])
        self.s.undo('review')
        self.assertEqual(self.s.stats()['needs_review'],0)
        self.assertEqual(self.s.group('merlo')['version'],g['version'])

    def test_assign_move_restart_undo(self):
        q=self.s.scan(str(self.source));ident=q['items'][0]['id']
        h=sha(self.source/'IMG_01.jpg')
        result=self.s.assign(ident,'merlo','move','shop-2026-09-21')
        self.assertFalse((self.source/'IMG_01.jpg').exists())
        self.assertEqual(sha(result['path']),h)
        resumed=Store(self.root)
        self.assertEqual(resumed.queue(str(self.source))['done'],1)
        with self.assertRaises(ValueError):resumed.assign(ident,'merlo','move','')
        resumed.undo('assign')
        self.assertEqual(sha(self.source/'IMG_01.jpg'),h)
        self.assertEqual(resumed.queue(str(self.source))['done'],0)

    def test_copy_is_independent_and_undo_keeps_bytes(self):
        q=self.s.scan(str(self.source));ident=q['items'][0]['id']
        result=self.s.assign(ident,'cabernet','copy','session')
        original=self.source/'IMG_01.jpg'
        self.assertNotEqual(original.stat().st_ino,Path(result['path']).stat().st_ino)
        self.s.undo('assign')
        self.assertTrue(original.exists())
        self.assertFalse(Path(result['path']).exists())
        self.assertEqual(len(list((self.root/'curation/undo_archive').rglob('*.jpg'))),1)

    def test_recover_after_files_moved_before_db_commit(self):
        row=self.s.rows()[-1]
        src=self.root/row['path'];dst=self.root/'images/merlo/recovered.jpg'
        m=None
        with self.s.db() as db:m=self.s.meta(db,row['id'])
        payload={'files':[{'src':str(src),'dst':str(dst),'sha':row['sha256'],'mode':'move'}],
                 'changes':[{'row_before':row,'row_after':{**row,'path':str(dst.relative_to(self.root))},'meta_before':m,'meta_after':m}]}
        with self.s.db() as db:db.execute('INSERT INTO curation_events VALUES (?,?,?,?,?)',('interrupted','migration','now','pending',json.dumps(payload)))
        self.s._files(payload['files'])
        resumed=Store(self.root)
        self.assertTrue(dst.exists())
        self.assertFalse(src.exists())
        self.assertEqual(next(r for r in resumed.rows() if r['id']==row['id'])['path'],str(dst.relative_to(self.root)))

    def test_api_security_preview_and_invalid_label(self):
        with TestClient(create_app(self.root)) as c:
            self.assertEqual(c.get('/').status_code,200)
            self.assertEqual(c.get('/api/wines?q=Мерло').json()['items'][0]['slug'],'merlo')
            self.assertEqual(c.get('/api/image/asset/0?size=300').headers['content-type'],'image/jpeg')
            self.assertEqual(c.post('/api/source',json={'folder':str(self.source)}).status_code,403)
            self.assertEqual(c.post('/api/source',json={'folder':str(self.source)},headers={'X-Curation':'1','Origin':'https://evil.test'}).status_code,403)
            q=c.post('/api/source',json={'folder':str(self.source)},headers={'X-Curation':'1'}).json()
            self.assertEqual(c.post('/api/assign',json={'id':q['items'][0]['id'],'slug':'../escape'},headers={'X-Curation':'1'}).status_code,409)
            self.assertEqual(c.get('/api/config',headers={'Host':'evil.test'}).status_code,403)

    def test_changed_source_and_dataset_source_rejected(self):
        q=self.s.scan(str(self.source));ident=q['items'][0]['id']
        Image.new('RGB',(150,100),'black').save(self.source/'IMG_01.jpg')
        with self.assertRaises(ValueError):self.s.assign(ident,'merlo','move','')
        with self.assertRaises(ValueError):self.s.scan(str(self.root/'images'))
        self.s.skip(ident,True)
        self.assertEqual(self.s.queue(str(self.source))['skipped'],1)

    def test_out_of_catalog_and_migration_undo(self):
        q=self.s.scan(str(self.source));ident=q['items'][0]['id']
        self.s.skip(ident,True,'out_of_catalog')
        self.assertEqual(self.s.queue(str(self.source))['out_of_catalog'],1)
        self.assertTrue((self.source/'IMG_01.jpg').exists())
        before={r['id']:r['path'] for r in self.s.rows()}
        self.s.migrate();self.s.undo('migration')
        self.assertEqual({r['id']:r['path'] for r in self.s.rows()},before)


if __name__=='__main__':unittest.main()
