import hashlib,io,json,sys,tempfile,threading,time,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_server import fixture,FakeEngine,create_app

class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root,self.camera=fixture(Path(self.tmp.name))
        self.app=create_app(self.root,FakeEngine(),test=True);self.c=TestClient(self.app);self.c.__enter__()
        self.store=self.app.state.store;self.exports=self.app.state.exports
        self.c.post('/admin/api/login',json={'password':'test-password'})
    def tearDown(self):self.c.__exit__(None,None,None);self.tmp.cleanup()
    def options(self,**kw):return {'mode':'images','scope':'all','filter':'all',**kw}
    def wait(self,ident):
        for _ in range(500):
            job=self.exports.get(ident)
            if job['status'] in {'ready','error'}:return job
            time.sleep(.01)
        self.fail('Export did not finish')
    def build(self,**kw):
        r=self.c.post('/admin/api/exports',json=self.options(**kw));self.assertEqual(r.status_code,200,r.text)
        j=self.wait(r.json()['id']);self.assertEqual(j['status'],'ready',j)
        response=self.c.get(j['download_url']);self.assertEqual(response.status_code,200)
        z=zipfile.ZipFile(io.BytesIO(response.content));self.assertIsNone(z.testzip());return z,j
    def test_all_images_only_and_private_download(self):
        z,j=self.build();self.assertEqual(set(z.namelist()),{'images/merlo/','images/cabernet/','images/merlo/photo.jpg','images/cabernet/photo.jpg'})
        for r in self.store.rows():
            if r['path'].startswith('images/'):
                self.assertEqual(hashlib.sha256(z.read(r['path'])).hexdigest(),r['sha256'])
        self.assertEqual(j['summary']['images'],2)
        full=self.c.get(j['download_url']).content
        partial=self.c.get(j['download_url'],headers={'Range':'bytes=0-99'})
        self.assertEqual(partial.status_code,206);self.assertEqual(partial.content,full[:100])
        self.c.post('/admin/api/logout',json={})
        for url in ['/admin/api/exports','/admin/api/exports/catalog',j['download_url']]:
            self.assertEqual(self.c.get(url,headers={'Authorization':'Bearer test-scan-key'}).status_code,401)
        self.assertEqual(self.c.post('/admin/api/exports',json=self.options()).status_code,401)
    def test_unlabeled_is_independent_of_slug_review_and_negative_annotation(self):
        self.store.annotate('0',{'no_label':True})
        p=self.c.post('/admin/api/exports/preview',json=self.options(filter='unlabeled')).json()
        self.assertEqual((p['images'],p['labeled']),(1,0))
        z,_=self.build(filter='unlabeled');self.assertIn('images/cabernet/photo.jpg',z.namelist());self.assertNotIn('images/merlo/photo.jpg',z.namelist())
        z,_=self.build(mode='images_json',filter='labeled',scope='selected',slugs=['merlo','cabernet'])
        a=json.loads(z.read('annotations/merlo/photo.jpg.json'))
        self.assertTrue(a['no_label']);self.assertEqual(a['points'],[]);self.assertIsNone(a['yolo_box'])
        self.assertNotIn('images/cabernet/photo.jpg',z.namelist())
    def test_photo_json_exif_coordinates_and_no_fake_annotations(self):
        p=self.root/'images/merlo/photo.jpg';im=Image.new('RGB',(60,100),'red');exif=im.getexif();exif[274]=6;im.save(p,exif=exif)
        digest=hashlib.sha256(p.read_bytes()).hexdigest()
        with self.store.db() as db:db.execute('UPDATE images SET sha256=?,bytes=? WHERE id=?',(digest,p.stat().st_size,'0'))
        self.store.annotate('0',{'points':[[.1,.2],[.9,.8]]})
        z,j=self.build(mode='images_json')
        a=json.loads(z.read('annotations/merlo/photo.jpg.json'))
        self.assertEqual((a['width'],a['height']),(100,60));self.assertEqual(a['points_pixels'][0],[10,12])
        self.assertEqual(a['bbox_xyxy_normalized'],[.1,.2,.9,.8]);self.assertEqual(a['sha256'],digest)
        self.assertEqual(z.read(a['image_path']),p.read_bytes());self.assertNotIn('annotations/cabernet/photo.jpg.json',z.namelist())
        manifest=json.loads(z.read('_export.json'));self.assertEqual(manifest['counts']['annotations'],1);self.assertEqual(len(manifest['items']),2)
        self.assertEqual(j['summary']['unlabeled'],1)
    def test_json_only_selected_folders_and_delete_preserves_data(self):
        for ident in ['0','2']:self.store.annotate(ident,{'points':[[.1,.2],[.9,.8]]})
        before=self.store.annotation('0');pixels=(self.root/'images/merlo/photo.jpg').read_bytes()
        z,j=self.build(mode='json',filter='labeled',scope='selected',slugs=['merlo'])
        self.assertEqual(set(z.namelist()),{'annotations/merlo/','annotations/merlo/photo.jpg.json','_export.json'})
        m=json.loads(z.read('_export.json'));self.assertFalse(m['items'][0]['image_included']);self.assertEqual(m['items'][0]['image_path'],'images/merlo/photo.jpg')
        self.assertEqual(self.c.post('/admin/api/exports/'+j['id']+'/delete',json={}).status_code,200)
        self.assertEqual(self.c.get(j['download_url']).status_code,409)
        self.assertEqual(self.store.annotation('0'),before);self.assertEqual((self.root/'images/merlo/photo.jpg').read_bytes(),pixels)
    def test_snapshot_survives_concurrent_relabel_and_annotation_change(self):
        self.store.annotate('0',{'points':[[.1,.2],[.9,.8]]});original=(self.root/'images/merlo/photo.jpg').read_bytes()
        entered=threading.Event();release=threading.Event();real_zip=zipfile.ZipFile
        def pause(*args,**kwargs):
            entered.set()
            if not release.wait(5):raise RuntimeError('Test snapshot timed out')
            return real_zip(*args,**kwargs)
        with patch('exports.zipfile.ZipFile',side_effect=pause):
            job=self.exports.create(self.options(mode='images_json'))
            try:
                self.assertTrue(entered.wait(5))
                self.store.relabel('0','cabernet');self.store.annotate('0',{'no_label':True})
                self.assertEqual(self.store.annotation('0')['revision'],2)
            finally:release.set()
            ready=self.wait(job['id']);self.assertEqual(ready['status'],'ready',ready)
        with real_zip(self.exports.download(job['id'])[0]) as z:
            self.assertEqual(z.read('images/merlo/photo.jpg'),original)
            a=json.loads(z.read('annotations/merlo/photo.jpg.json'));self.assertEqual(a['revision'],1);self.assertFalse(a['no_label'])
        self.assertFalse((self.root/'images/merlo/photo.jpg').exists());self.assertTrue(self.store.annotation('0')['no_label'])
    def test_stale_annotation_or_missing_photo_fails_without_partial_download(self):
        self.store.annotate('0',{'no_label':True})
        with self.store.db() as db:db.execute("UPDATE annotations SET sha256='old' WHERE id='0'")
        job=self.exports.create(self.options(mode='images_json'));job=self.wait(job['id'])
        self.assertEqual(job['status'],'error');self.assertIn('другой версии',job['error'])
        with self.assertRaises(ValueError):self.exports.download(job['id'])
        (self.root/'images/merlo/photo.jpg').unlink()
        job=self.wait(self.exports.create(self.options())['id']);self.assertEqual(job['status'],'error')
        self.assertFalse((self.exports.folder(job['id'])/'snapshot').exists())
    def test_invalid_selection_and_expiry(self):
        for opt in [self.options(scope='selected'),self.options(scope='selected',slugs=['../escape']),self.options(mode='json'),self.options(filter='pending')]:
            self.assertEqual(self.c.post('/admin/api/exports',json=opt).status_code,409)
        z,j=self.build();self.exports.update(j['id'],expires=0);self.exports.cleanup()
        self.assertFalse(self.exports.folder(j['id']).exists());self.assertTrue((self.root/'images/merlo/photo.jpg').exists())

if __name__=='__main__':unittest.main()
