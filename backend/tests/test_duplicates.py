import hashlib,json,shutil,tempfile,unittest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from test_server import fixture,FakeEngine,create_app
from curation import sha,write_csv
from duplicates import Duplicates

def duplicate_fixture(parent):
    root,camera=fixture(parent)
    import csv
    with (root/'catalog.csv').open(encoding='utf-8-sig') as f:catalog=list(csv.DictReader(f))
    catalog.append({**catalog[0],'Slug':'pinot','Название вина':'Пино Нуар. Тестовая серия','Сорт винограда':'Пино Нуар'})
    write_csv(root/'catalog.csv',catalog,list(catalog[0]))
    return root,camera

def add_photo(store,ident,slug,source,filename='duplicate.jpg'):
    path=store.root/'images'/slug/filename;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,path)
    with Image.open(path) as im:
        pix=hashlib.sha256(im.convert('RGB').tobytes()).hexdigest();width,height=im.size
    with store.db() as db:
        row=dict(db.execute('SELECT * FROM images WHERE id=?',('0',)).fetchone())
        row.update(id=ident,slug=slug,path=str(path.relative_to(store.root)),sha256=sha(path),pixel_sha256=pix,width=width,height=height,bytes=path.stat().st_size)
        db.execute('INSERT INTO images VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        meta=dict(db.execute('SELECT * FROM curation_meta WHERE id=?',('0',)).fetchone());meta['id']=ident
        db.execute('INSERT INTO curation_meta VALUES ('+','.join('?' for _ in meta)+')',list(meta.values()))
    return ident

class DuplicateTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root,self.camera=duplicate_fixture(Path(self.tmp.name))
        self.app=create_app(self.root,FakeEngine(),test=True);self.c=TestClient(self.app);self.c.__enter__()
        self.s=self.app.state.store;self.d=Duplicates(self.s)
        add_photo(self.s,'3','cabernet',self.root/'images/merlo/photo.jpg')
    def tearDown(self):self.c.__exit__(None,None,None);self.tmp.cleanup()
    def rows(self):return {r['id']:r for r in self.s.rows()}
    def resolve(self,**kw):
        g=self.d.detail('0');return self.d.resolve('0',g['version'],**kw)
    def check_files(self,rows):
        for row in rows.values():self.assertEqual(sha(self.root/row['path']),row['sha256'])

    def test_grouping_search_independent_previews_and_auth(self):
        self.assertEqual(self.c.get('/admin/api/duplicates').status_code,401)
        self.c.post('/admin/api/login',json={'password':'test-password'})
        r=self.c.get('/admin/api/duplicates?q=Каберне').json();self.assertEqual((r['total'],r['filtered'],r['copies']),(1,1,2))
        self.assertEqual(self.d.list('不存在')['filtered'],0)
        g=self.d.detail('0');self.assertEqual(g['match'],'identical_files')
        candidates={w['slug']:w for w in g['folders']}
        self.assertIsNone(candidates['merlo']['reference']);self.assertEqual(candidates['cabernet']['reference'],'2')
        self.assertFalse({'0','3'} & set(candidates['cabernet']['other_photos']))

    def test_keep_existing_undo_annotations_and_collision(self):
        before=self.rows();self.s.annotate('0',{'points':[[.1,.2],[.9,.8]]});bounds=self.s.annotation('0')
        out=self.resolve(target='cabernet') # rejected merlo/photo.jpg collides with existing needs_review name
        self.assertEqual((out['kept'],out['rejected']),(1,1));after=self.rows()
        self.assertTrue(after['0']['path'].startswith('needs_review/merlo/'));self.assertNotEqual(after['0']['path'],before['1']['path'])
        self.assertEqual(after['1'],before['1']);self.assertEqual(after['2'],before['2']);self.assertEqual(self.s.annotation('0'),bounds)
        self.assertEqual(self.d.list()['total'],0);self.check_files(after)
        self.assertEqual(self.d.undo()['group'],'0');self.assertEqual(self.rows(),before);self.check_files(before)
        self.assertEqual(self.s.annotation('0'),bounds)

    def test_quarantine_preserves_all_bytes_and_roundtrip(self):
        before=self.rows();out=self.resolve(quarantine=True);self.assertEqual((out['kept'],out['rejected']),(0,2))
        self.check_files(self.rows());self.assertEqual(len(self.rows()),len(before));self.d.undo();self.assertEqual(self.rows(),before)

    def test_new_catalog_target_updates_links_but_not_prediction_and_undo(self):
        # photo 3 is the deterministic representative and has both source and scan links.
        source=self.s.scan(str(self.camera))['items'][0]['id']
        scan,_=self.s.scan_start((self.camera/'IMG_01.jpg').read_bytes(),'test-device');self.s.scan_finish(scan,{'slug':'merlo'})
        with self.s.db() as db:
            db.execute("UPDATE curation_sources SET state='assigned',asset_id='3',assigned_slug='cabernet' WHERE id=?",(source,))
            db.execute("UPDATE scans SET asset_id='3',assigned_slug='cabernet' WHERE id=?",(scan,))
            links_before={table:dict(db.execute(f'SELECT * FROM {table} WHERE id=?',(ident,)).fetchone()) for table,ident in [('curation_sources',source),('scans',scan)]}
        before=self.rows();out=self.resolve(target='pinot');self.assertEqual(out['moved'],1)
        self.assertTrue(self.rows()['3']['path'].startswith('images/pinot/'))
        with self.s.db() as db:
            self.assertEqual(db.execute('SELECT assigned_slug FROM curation_sources WHERE id=?',(source,)).fetchone()[0],'pinot')
            r=dict(db.execute('SELECT * FROM scans WHERE id=?',(scan,)).fetchone());self.assertEqual(r['assigned_slug'],'pinot');self.assertEqual(json.loads(r['result'])['slug'],'merlo')
        self.d.undo();self.assertEqual(self.rows(),before)
        with self.s.db() as db:
            for table,r in links_before.items():self.assertEqual(dict(db.execute(f'SELECT * FROM {table} WHERE id=?',(r['id'],)).fetchone()),r)

    def test_stale_and_changed_files_do_not_partially_move(self):
        g=self.d.detail('0');before=self.rows()
        with self.s.db() as db:db.execute("UPDATE curation_meta SET state='confirmed' WHERE id='3'")
        with self.assertRaisesRegex(ValueError,'изменилась'):self.d.resolve('0',g['version'],'merlo')
        self.assertEqual(self.rows(),before)
        (self.root/before['3']['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'измен'):self.resolve(target='merlo')
        self.assertEqual(self.rows(),before);self.assertTrue((self.root/before['0']['path']).exists())

    def test_only_exact_pixels_or_bytes_not_phash_or_same_slug(self):
        self.resolve(target='merlo')
        add_photo(self.s,'4','merlo',self.root/'images/merlo/photo.jpg')
        with self.s.db() as db:db.execute("UPDATE images SET phash='same-perceptual-hash'")
        self.assertEqual(self.d.list()['total'],0)
        src=self.root/'images/merlo/photo.jpg';png=self.root/'same.png'
        with Image.open(src) as im:im.save(png)
        add_photo(self.s,'5','cabernet',png,'same.png')
        groups=self.d.list();self.assertEqual(groups['total'],1);self.assertEqual(groups['items'][0]['match'],'identical_pixels')
        self.assertEqual(groups['items'][0]['copies'],3)

    def test_undo_refuses_intervening_asset_change(self):
        self.resolve(target='merlo')
        with self.s.db() as db:db.execute("UPDATE images SET validation='later edit' WHERE id='3'")
        before=self.rows()
        with self.assertRaisesRegex(ValueError,'изменены'):self.d.undo()
        self.assertEqual(self.rows(),before);self.check_files(before)

    def test_portal_references_excluded_and_stale_resolution_cannot_move_them(self):
        old=self.d.detail('0');before=self.rows()
        with self.s.db() as db:db.execute("UPDATE images SET source='vino-svoe.ru',kind='catalog_reference' WHERE id='0'")
        self.assertEqual(self.d.list()['total'],0)
        with self.assertRaisesRegex(ValueError,'изменилась'):self.d.resolve('0',old['version'],'cabernet')
        self.assertEqual(self.rows()['0']['path'],before['0']['path'])
        # Other photos of the same wine still need checking; the portal copy stays out.
        add_photo(self.s,'4','merlo',self.root/'images/merlo/photo.jpg','other.jpg')
        with self.s.db() as db:db.execute("UPDATE images SET source='external',kind='bottle' WHERE id='4'")
        g=self.d.detail('3');self.assertEqual({f['id'] for f in g['files']},{'3','4'})
        portal_before=self.rows()['0'];self.d.resolve('3',g['version'],quarantine=True)
        self.assertEqual(self.rows()['0'],portal_before);self.assertTrue((self.root/portal_before['path']).exists())

    def test_portal_source_or_reference_kind_are_each_protected(self):
        with self.s.db() as db:db.execute("UPDATE images SET kind='catalog_reference' WHERE id='0'")
        self.assertEqual(self.d.list()['total'],0)
        with self.s.db() as db:db.execute("UPDATE images SET kind='bottle',source='vino-svoe.ru' WHERE id='0'")
        self.assertEqual(self.d.list()['total'],0)

    def test_api_contract_versions_and_asset_cache(self):
        self.c.post('/admin/api/login',json={'password':'test-password'})
        g=self.c.get('/admin/api/duplicates/0').json()
        bad=self.c.post('/admin/api/duplicates/0/resolve',json={'version':g['version'],'slug':[]})
        self.assertEqual(bad.status_code,409)
        out=self.c.post('/admin/api/duplicates/0/resolve',json={'version':g['version'],'slug':'merlo'})
        self.assertEqual(out.status_code,200,out.text)
        self.assertEqual(self.c.get('/admin/api/duplicates/0').status_code,409)
        self.assertEqual(self.c.post('/admin/api/undo-duplicate',json={}).json()['group'],'0')
        page=self.c.get('/admin/');self.assertIn('duplicates.js?v=',page.text);self.assertIn('no-cache',page.headers['cache-control'])
        self.assertIn('no-cache',self.c.get('/admin/static/duplicates.js').headers['cache-control'])

if __name__=='__main__':unittest.main()
