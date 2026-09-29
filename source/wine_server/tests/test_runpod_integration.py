import json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT.parent/'wine_curation/tests'))
from test_curation import fixture
from app import create_app
from ml_client import WineMLClient,WineMLError


def candidate(slug,**fields):
    return {'slug':slug,'score':.82,'name':slug,'winery':'Тестовая винодельня',**fields}


class FakeGPU:
    def __init__(self,response=None,error=None):
        self.response=response;self.error=error;self.ready=True;self.payload=None
        self.model_version='qwen';self.catalog_size=2103

    def health(self):
        return {'ready':self.ready,'catalog_size':self.catalog_size if self.ready else 0,
                'model_version':self.model_version}

    def search(self,payload,filename='photo.jpg'):
        self.payload=payload
        if self.error:raise self.error
        return self.response


class RunPodIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root,self.camera=fixture(Path(self.tmp.name))
        self.photo=(self.camera/'IMG_01.jpg').read_bytes()
        self.env=patch.dict(os.environ,{'WINE_INFERENCE_BACKEND':'runpod'})
        self.env.start()
        self.gpu=FakeGPU()
        self.app=create_app(self.root,test=True,ml_client=self.gpu)
        self.client=TestClient(self.app)
        self.client.__enter__()
        for _ in range(50):
            if self.client.get('/health').json()['ready']:break
            time.sleep(.01)

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.env.stop()
        self.tmp.cleanup()

    def scan(self):
        return self.client.post('/api/predict',files={'image':('test.jpg',self.photo)},headers={'X-Device-ID':'qa-runpod'})

    def test_normal_response_enriches_known_wine_and_preserves_raw(self):
        raw={'slug':'merlo','no_wine':False,'card':candidate('merlo'),
             'alternatives':[candidate('cabernet'),{'slug':'unknown_1','score':.7},
                             candidate('unknown_2'),candidate('unknown_3')],
             'confidence':{'top1_score':.82,'gap_top1_top2':.05},
             'score_kind':'cosine_similarity','latency_ms':1234,
             'model_version':'qwen3-vl-8b-lora-v2-runpod-1'}
        self.gpu.response=raw
        status=self.client.get('/api/status').json()
        self.assertEqual((status['backend'],status['catalog_count']),('runpod',2103))
        self.assertEqual(self.client.post('/admin/api/login',json={'password':'test-password'}).status_code,200)
        config=self.client.get('/admin/api/config').json()
        self.assertEqual((config['backend'],config['model_version']),('runpod','qwen'))
        self.assertNotIn('WINE_ML_API_KEY',json.dumps(config))
        response=self.scan();self.assertEqual(response.status_code,200,response.text)
        body=response.json();self.assertFalse(body['no_wine']);self.assertEqual(body['wine']['slug'],'merlo')
        self.assertEqual(len(body['alternatives']),4)
        self.assertEqual(body['alternatives'][1]['image'],'/assets/no-bottle.svg')
        self.assertFalse(body['alternatives'][1]['preferences_available'])
        self.assertEqual(body['alternatives'][1]['name'],'unknown_1')
        self.assertNotIn('ml_response',body)
        self.assertTrue(self.gpu.payload.startswith(b'\xff\xd8'))
        with self.app.state.store.db() as db:
            row=db.execute('SELECT status,result FROM scans WHERE id=?',(body['id'],)).fetchone()
        saved=json.loads(row['result'])
        self.assertEqual(row['status'],'done');self.assertEqual(saved['slug'],'merlo')
        self.assertEqual(saved['ml_response'],raw)
        self.assertEqual(len(saved['top5']),5)

    def test_live_pod_shape_without_model_version_and_private_photo(self):
        raw={'slug':'merlo','no_wine':False,'card':candidate('merlo'),
             'alternatives':[candidate('cabernet'),candidate('other_1'),candidate('other_2'),candidate('other_3')],
             'confidence':{'top1_score':.82,'gap_top1_top2':.05},'latency_ms':1234}
        self.gpu.response=raw
        response=self.scan();self.assertEqual(response.status_code,200,response.text)
        body=response.json();self.assertEqual(body['model_version'],'qwen')
        self.assertEqual(body['confidence'],raw['confidence'])
        photo=self.client.get(f"/api/product/scans/{body['id']}/image")
        self.assertEqual(photo.status_code,200)
        self.assertEqual(photo.headers['content-type'],'image/jpeg')
        self.assertTrue(photo.content.startswith(b'\xff\xd8'))
        outsider=TestClient(self.app)
        self.assertEqual(outsider.get(f"/api/product/scans/{body['id']}/image").status_code,404)
        self.assertEqual(outsider.post('/api/product/assistant',json={'message':'Что это?','scan_id':body['id']}).status_code,404)
        self.assertEqual(self.client.post('/api/product/assistant',json={'message':'Что это?','scan_id':'../bad'}).status_code,422)
        with self.app.state.store.db() as db:
            saved=json.loads(db.execute('SELECT result FROM scans WHERE id=?',(body['id'],)).fetchone()['result'])
        self.assertEqual(saved['ml_response'],raw)

    def test_no_wine_is_success_and_keeps_five_candidates(self):
        raw={'slug':None,'no_wine':True,'card':None,
             'alternatives':[candidate('merlo'),candidate('cabernet'),candidate('other_1'),
                             candidate('other_2'),candidate('other_3')],
             'confidence':{'top1_score':.4,'gap_top1_top2':.03},
             'score_kind':'cosine_similarity','latency_ms':500,
             'model_version':'qwen3-vl-8b-lora-v2-runpod-1'}
        self.gpu.response=raw
        response=self.scan();self.assertEqual(response.status_code,200,response.text)
        body=response.json();self.assertTrue(body['no_wine']);self.assertIsNone(body['wine'])
        self.assertEqual(len(body['alternatives']),5)
        with self.app.state.store.db() as db:
            row=db.execute('SELECT status,result FROM scans WHERE id=?',(body['id'],)).fetchone()
        saved=json.loads(row['result'])
        self.assertEqual(row['status'],'done');self.assertIsNone(saved['slug'])
        self.assertTrue(saved['no_wine']);self.assertEqual(saved['ml_response'],raw)
        self.assertEqual(self.client.post('/admin/api/login',json={'password':'test-password'}).status_code,200)
        history=self.client.get('/admin/api/history?q=qa-runpod').json()['items'][0]
        self.assertEqual(history['id'],body['id'])
        self.assertTrue(history['result']['no_wine'])
        assigned=self.client.post('/admin/api/assign',json={'kind':'scan','id':body['id'],
                          'slug':'merlo','bounds':{'points':[[.1,.1],[.9,.9]]}})
        self.assertEqual(assigned.status_code,200,assigned.text)
        later=self.client.get('/admin/api/history?q=qa-runpod').json()['items'][0]
        self.assertEqual(later['assigned_slug'],'merlo')
        self.assertIsNone(later['result']['slug'])
        self.assertEqual(later['result']['ml_response'],raw)
        self.assertTrue(self.app.state.store.annotation(assigned.json()['asset_id'])['points'])

    def test_readiness_and_remote_errors(self):
        self.gpu.ready=False
        self.assertEqual(self.client.get('/health').status_code,503)
        self.assertEqual(self.client.get('/api/status').status_code,503)
        self.assertEqual(self.scan().status_code,503)
        self.gpu.ready=True
        self.gpu.catalog_size=2102
        self.assertEqual(self.client.get('/api/status').status_code,503)
        self.gpu.catalog_size=2103
        for code in (429,503,504):
            self.gpu.error=WineMLError(code,'Временная ошибка',retry_after='2' if code==429 else None)
            response=self.scan();self.assertEqual(response.status_code,code,response.text)
            if code==429:self.assertEqual(response.headers['retry-after'],'2')
        with self.app.state.store.db() as db:
            rows=db.execute("SELECT status FROM scans WHERE device='qa-runpod'").fetchall()
        self.assertEqual([r['status'] for r in rows],['error']*3)

    def test_invalid_slug_and_enrichment_failure_preserve_scan_state(self):
        raw={'slug':'merlo','no_wine':False,'card':candidate('merlo'),
             'alternatives':[candidate('cabernet'),candidate('other_1'),candidate('other_2'),candidate('other_3')],
             'confidence':{'top1_score':.82,'gap_top1_top2':.05},
             'latency_ms':12,'model_version':'qwen3-vl-8b-lora-v2-runpod-1'}
        self.gpu.response=raw
        with patch.object(self.app.state.product,'card',side_effect=RuntimeError('card lookup failed')):
            response=self.scan()
            self.assertEqual(response.status_code,200,response.text)
            self.assertFalse(response.json()['wine']['preferences_available'])
        with self.app.state.store.db() as db:
            first=db.execute('SELECT status,result FROM scans ORDER BY created DESC LIMIT 1').fetchone()
        self.assertEqual(first['status'],'done')
        self.assertEqual(json.loads(first['result'])['ml_response'],raw)
        self.gpu.response={**raw,'alternatives':[candidate('../bad'),*raw['alternatives'][1:]]}
        self.assertEqual(self.scan().status_code,503)
        with self.app.state.store.db() as db:
            states=[r['status'] for r in db.execute('SELECT status FROM scans')]
        self.assertEqual(sorted(states),['done','error'])


class ClientTest(unittest.TestCase):
    def test_public_pod_health_and_no_bearer_header(self):
        client=WineMLClient('https://model.example',key='')
        class HealthResponse:
            status_code=200
            def raise_for_status(self):pass
            def json(self):return {'status':'ok','backend':'qwen','catalog_size':2103,'no_wine_threshold':None}
        with patch('ml_client.requests.get',return_value=HealthResponse()):
            self.assertEqual(client.health()['model_version'],'qwen')
        class SearchResponse:
            status_code=200
            def raise_for_status(self):pass
            def json(self):return {'slug':'wine'}
        with patch('ml_client.requests.post',return_value=SearchResponse()) as post:
            client.search(b'jpeg-data')
        self.assertNotIn('Authorization',post.call_args.kwargs['headers'])

    def test_key_stays_in_server_header_and_busy_is_mapped(self):
        client=WineMLClient('https://model.example','private-test-key')
        class Response:
            status_code=429
        with patch('ml_client.requests.post',return_value=Response()) as post:
            with self.assertRaises(WineMLError) as caught:client.search(b'jpeg-data')
        self.assertEqual(caught.exception.status_code,429)
        self.assertEqual(post.call_args.args[0],'https://model.example/v1/search')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'],'Bearer private-test-key')
        self.assertNotIn('private-test-key',str(caught.exception))

    def test_timeout_is_explicit(self):
        client=WineMLClient('https://model.example','private-test-key')
        with patch('ml_client.requests.post',side_effect=requests.Timeout):
            with self.assertRaises(WineMLError) as caught:client.search(b'jpeg-data')
        self.assertEqual(caught.exception.status_code,504)

    def test_auth_and_server_failures_do_not_expose_upstream_body(self):
        client=WineMLClient('https://model.example','private-test-key')
        for code in (401,503):
            class Response:
                status_code=code
            with patch('ml_client.requests.post',return_value=Response()):
                with self.assertRaises(WineMLError) as caught:client.search(b'jpeg-data')
            self.assertEqual(caught.exception.status_code,503)
            self.assertNotIn('private-test-key',str(caught.exception))


if __name__=='__main__':unittest.main()
