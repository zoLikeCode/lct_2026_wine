import io,json,os,sys,tempfile,time,unittest
from unittest.mock import patch
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT.parent/'wine_curation/tests'))
from test_curation import fixture
from app import create_app
from data_store import DataStore

class FakeEngine:
    slugs=['merlo','cabernet'];version='test-only'
    def predict(self,image):
        return {'slug':'merlo','top5':[{'slug':'merlo','score':.8},{'slug':'cabernet','score':.7}],
                'model_version':self.version,'latency_ms':1}

class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root,self.camera=fixture(Path(self.tmp.name))
        self.app=create_app(self.root,FakeEngine(),test=True)
        self.c=TestClient(self.app);self.c.__enter__();self.store=self.app.state.store
        self.data=(self.camera/'IMG_01.jpg').read_bytes()
        for _ in range(50):
            if self.c.get('/health').json()['ready']:break
            time.sleep(.01)
    def tearDown(self):self.c.__exit__(None,None,None);self.tmp.cleanup()
    def login(self):self.assertEqual(self.c.post('/admin/api/login',json={'password':'test-password'}).status_code,200)
    def upload(self):
        self.login();b=self.c.post('/admin/api/batches',json={'name':'Camera'}).json()['id']
        for name in ['first.jpg','second.jpg']:
            self.assertEqual(self.c.post(f'/admin/api/batches/{b}/upload',files={'image':(name,self.data,'image/jpeg')}).status_code,200)
        return b,self.c.get('/admin/api/batches/'+b).json()['items']
    def test_private_media_auth_and_cross_origin(self):
        for url in ['/admin/api/history','/admin/api/image/asset/0','/admin/api/wines','/admin/api/config']:
            self.assertEqual(self.c.get(url).status_code,401)
        self.assertEqual(self.c.post('/admin/api/login',json={'password':'test-password'},headers={'Origin':'https://evil.test'}).status_code,403)
        self.login();self.assertEqual(self.c.get('/admin/api/image/asset/0').headers['content-type'],'image/jpeg')
        self.assertEqual(self.c.get('/collector/state.sqlite3').status_code,404)
        self.c.post('/admin/api/logout',json={});self.assertEqual(self.c.get('/admin/api/history').status_code,401)
    def test_public_scanner_needs_no_key_or_login(self):
        status=self.c.get('/api/status')
        self.assertEqual(status.status_code,200)
        self.assertTrue(status.json()['ready'])
        self.assertEqual(status.headers['cache-control'],'no-store')
        for headers in ({},{'Authorization':'Bearer expired-key'}):
            result=self.c.post('/api/predict',files={'image':('wine.jpg',self.data)},headers=headers)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['wine']['slug'],'merlo')
            self.assertEqual(self.c.get('/admin/api/history').status_code,401)
        self.assertEqual(self.c.post('/api/predict',files={'image':('bad.jpg',b'broken')}).status_code,422)
        self.assertEqual(self.c.post('/api/predict',files={'image':('wine.jpg',self.data)},headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.c.get('/api/private-future-route').status_code,401)
        self.login()
        self.assertEqual(self.c.get('/admin/api/config').json()['scanner_url'],'http://testserver/')
    def test_server_starts_without_scanner_secret(self):
        with patch.dict(os.environ,{'WINE_ADMIN_PASSWORD':'test-password','WINE_SCAN_KEY':''}):
            app=create_app(self.root,FakeEngine())
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/status').status_code,200)
                self.assertEqual(client.get('/admin/api/history').status_code,401)
    def test_assignment_back_correction_and_annotations_survive(self):
        batch,items=self.upload();ident=items[0]['id'];initial=len(self.store.rows())
        payload={'kind':'source','id':ident,'slug':'merlo','bounds':{'points':[[.1,.2],[.9,.8]]}}
        first=self.c.post('/admin/api/assign',json=payload);self.assertEqual(first.status_code,200,first.text)
        asset=first.json()['asset_id'];before=self.store.annotation(asset)
        self.assertEqual(len(self.store.rows()),initial+1)
        current=self.c.get('/admin/api/batches/'+batch).json()['items'];self.assertEqual(len(current),2)
        self.assertEqual(next(x for x in current if x['id']==ident)['assigned_slug'],'merlo')
        payload.update(slug='cabernet',bounds=None)
        again=self.c.post('/admin/api/assign',json=payload);self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(again.json()['asset_id'],asset);self.assertEqual(len(self.store.rows()),initial+1)
        self.assertEqual(self.store.annotation(asset),before)
        row=next(x for x in self.store.rows() if x['id']==asset)
        self.assertTrue(row['path'].startswith('images/cabernet/'));self.assertTrue((self.root/row['path']).exists())
        self.assertTrue((self.camera/'IMG_01.jpg').exists())
        fresh=DataStore(self.root);self.assertEqual(next(x for x in fresh.batch_items(batch) if x['id']==ident)['assigned_slug'],'cabernet')
    def test_scan_not_added_automatically_and_manual_prediction_preserved(self):
        initial=len(self.store.rows());headers={'X-Device-ID':'my-android'}
        result=self.c.post('/api/predict',files={'image':('wine.jpg',self.data)},headers=headers)
        self.assertEqual(result.status_code,200,result.text);ident=result.json()['id']
        self.assertEqual(len(self.store.rows()),initial)
        self.login();entry=self.c.get('/admin/api/history?q=my-android').json()['items'][0]
        self.assertEqual(entry['result']['slug'],'merlo');self.assertEqual(entry['assigned_slug'],'')
        original=self.store.image_path('scan',ident);original_bytes=original.read_bytes()
        out=self.c.post('/admin/api/assign',json={'kind':'scan','id':ident,'slug':'cabernet'})
        self.assertEqual(out.status_code,200,out.text);self.assertEqual(len(self.store.rows()),initial+1)
        self.assertEqual(original.read_bytes(),original_bytes)
        entry=self.c.get('/admin/api/history').json()['items'][0]
        self.assertEqual(entry['result']['slug'],'merlo');self.assertEqual(entry['assigned_slug'],'cabernet')
        self.c.post('/admin/api/assign',json={'kind':'scan','id':ident,'slug':'merlo'})
        self.assertEqual(len(self.store.rows()),initial+1)
    def test_bounds_filters_rotation_revision_and_rejection(self):
        self.login();p=self.root/'images/merlo/photo.jpg'
        im=Image.new('RGB',(60,100),'blue');exif=im.getexif();exif[274]=6;im.save(p,exif=exif)
        from curation import sha
        with self.store.db() as db:db.execute('UPDATE images SET sha256=? WHERE id=?',(sha(p),'0'))
        b={'points':[[.1,.2],[.8,.2],[.9,.9],[.2,.8]],'revision':0}
        out=self.c.post('/admin/api/boundaries/0',json=b)
        self.assertEqual(out.status_code,200,out.text);self.assertEqual((out.json()['width'],out.json()['height']),(100,60))
        self.assertEqual(self.c.post('/admin/api/boundaries/0',json=b).status_code,409)
        self.assertEqual(len(self.c.get('/admin/api/boundaries?filter=labeled').json()['items']),1)
        self.assertEqual(len(self.c.get('/admin/api/boundaries?filter=unlabeled').json()['items']),1)
        self.assertEqual(len(self.c.get('/admin/api/boundaries?filter=all').json()['items']),2)
        self.assertEqual(self.c.post('/admin/api/boundaries/2',json={'points':[[0,0],[1,1],[1,0],[0,1]]}).status_code,409)
        g=self.store.group('merlo');self.store.review('merlo',g['version'],['0'])
        self.assertEqual(len(self.c.get('/admin/api/boundaries?filter=labeled').json()['items']),0)
        self.assertTrue(self.store.image_path('asset','0').is_file())
        self.assertTrue(str(self.store.image_path('asset','0')).find('needs_review')>=0)
    def test_corrupt_upload_and_invalid_slug_do_not_change_files(self):
        b,items=self.upload();initial=len(self.store.rows())
        self.assertEqual(self.c.post('/api/predict',files={'image':('bad.jpg',b'broken')}).status_code,422)
        self.assertEqual(self.c.post('/admin/api/assign',json={'kind':'source','id':items[0]['id'],'slug':'../escape'}).status_code,409)
        self.assertEqual(len(self.store.rows()),initial)
        self.assertEqual(self.store.batch_items(b)[0]['state'],'pending')

    def test_real_boundaries_membership_counts_and_relabel(self):
        self.login()
        row=next(r for r in self.store.rows() if r['id']=='0')
        selection=self.root/'collector/real_photo_selection.json'
        selection.write_text(json.dumps({'items':[{'image_id':'0','sha256':row['sha256']}]}))
        url='/admin/api/boundaries?photo_set=real'
        data=self.c.get(url).json()
        self.assertEqual([r['id'] for r in data['items']],['0'])
        self.assertEqual((data['summary']['total'],data['summary']['labeled'],data['summary']['unlabeled']),(1,0,1))
        self.assertEqual(self.c.get('/admin/api/boundaries?filter=all').json()['summary']['total'],2)
        self.assertEqual(self.c.get(url+'&slug=cabernet').json()['summary']['total'],0)
        saved=self.c.post('/admin/api/boundaries/0',json={'points':[[.1,.1],[.9,.9]],'revision':0})
        self.assertEqual(saved.status_code,200,saved.text)
        data=self.c.get(url).json()
        self.assertEqual(data['items'],[])
        self.assertEqual((data['summary']['total'],data['summary']['labeled'],data['summary']['unlabeled']),(1,1,0))
        self.assertEqual([r['id'] for r in self.c.get(url+'&filter=labeled').json()['items']],['0'])
        self.store.relabel('0','cabernet')
        self.assertEqual(self.c.get(url+'&filter=all&slug=cabernet').json()['summary']['total'],1)
        g=self.store.group('cabernet');self.store.review('cabernet',g['version'],['0'])
        self.assertEqual(self.c.get(url+'&filter=all').json()['summary']['total'],0)

    def test_real_boundaries_do_not_guess_missing_or_changed_membership(self):
        self.login();url='/admin/api/boundaries?photo_set=real'
        self.assertEqual(self.c.get(url).status_code,409)
        path=self.root/'collector/real_photo_selection.json'
        path.write_text(json.dumps({'items':[{'image_id':'0','sha256':'old-image-version'}]}))
        self.assertEqual(self.c.get(url).json()['summary']['total'],0)
        self.assertEqual(self.c.get('/admin/api/boundaries?photo_set=unknown').status_code,409)
        self.assertEqual(self.c.get('/admin/api/boundaries?filter=unknown').status_code,409)

if __name__=='__main__':unittest.main()
