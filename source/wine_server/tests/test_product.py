import json,sys,time,unittest,urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_server
from fastapi.testclient import TestClient

class ProductTest(unittest.TestCase):
    setUp=test_server.ServerTest.setUp
    tearDown=test_server.ServerTest.tearDown
    def test_profile_isolation_and_persistent_rating_upsert(self):
        self.c.get('/api/product/bootstrap')
        r=self.c.post('/api/product/preferences/merlo',json={'rating':5,'favorite':True})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['average_rating'],5)
        self.c.post('/api/product/preferences/merlo',json={'rating':3})
        self.assertEqual(self.c.get('/api/product/wines/merlo').json()['rating_count'],1)
        with TestClient(self.app) as other:
            data=other.get('/api/product/bootstrap').json();self.assertEqual(data['collection'],[])
            wine=other.get('/api/product/wines/merlo').json();self.assertEqual(wine['rating'],0);self.assertFalse(wine['favorite']);self.assertEqual(wine['average_rating'],3)
            other.post('/api/product/preferences/merlo',json={'rating':5})
            self.assertEqual(other.get('/api/product/wines/merlo').json()['average_rating'],4)
        self.assertEqual(self.c.get('/api/product/bootstrap').json()['preferences']['merlo'],{'favorite':True,'rating':3})
        for rating in [-1,6,True,1.5,'5']:
            self.assertEqual(self.c.post('/api/product/preferences/merlo',json={'rating':rating}).status_code,422)
        self.assertEqual(self.c.post('/api/product/preferences/missing',json={'rating':5}).status_code,404)
        self.assertEqual(self.c.post('/api/product/preferences/merlo',json={'rating':5},headers={'Origin':'https://evil.test'}).status_code,403)
        self.c.post('/api/product/preferences/merlo',json={'rating':0})
        self.assertEqual(self.c.get('/api/product/wines/merlo').json()['rating_count'],1)

    def test_history_and_rankings_without_exposing_photos(self):
        self.assertEqual(self.c.get('/api/product/ranking?mode=rating').json()['items'],[])
        self.c.get('/api/product/bootstrap')
        r=self.c.post('/api/predict',files={'image':('a.jpg',self.data)},headers={'X-Device-ID':'phone'})
        scan_id=r.json()['id'];self.assertEqual(self.c.get('/api/product/history').json()['items'][0]['id'],scan_id)
        with TestClient(self.app) as other:self.assertEqual(other.get('/api/product/history').json()['items'],[])
        self.assertEqual(self.c.get('/admin/api/image/scan/'+scan_id).status_code,401)
        self.assertEqual(self.c.get('/api/product/ranking').json()['total_scans'],1)
        self.assertEqual(self.c.get('/api/product/wines/merlo/image').status_code,404)
        with self.store.db() as db:db.execute("UPDATE images SET kind='catalog_reference' WHERE id='0'")
        self.assertEqual(self.c.get('/api/product/wines/merlo/image').status_code,200)
        self.assertEqual(self.c.get('/api/product/future-private-route').status_code,401)
        self.assertEqual(self.c.get('/api/product/wines/no-such-wine/image').status_code,404)
        self.assertEqual(self.c.get('/api/product/ranking?mode=whatever').status_code,422)

    def test_no_wine_scan_remains_in_profile_history_without_affecting_recommendations(self):
        self.c.get('/api/product/bootstrap')
        response=self.c.post('/api/predict',files={'image':('a.jpg',self.data)},headers={'X-Device-ID':'qa-no-wine'})
        self.assertEqual(response.status_code,200,response.text)
        scan_id=response.json()['id']
        with self.store.db() as db:
            db.execute('UPDATE scans SET result=? WHERE id=?',
                       (json.dumps({'slug':None,'no_wine':True,'top5':[{'slug':'merlo','score':.4}],
                                    'model_version':'test-runpod'}),scan_id))
        item=self.c.get('/api/product/history').json()['items'][0]
        self.assertEqual(item['id'],scan_id)
        self.assertTrue(item['no_wine'])
        self.assertIsNone(item['wine'])
        self.assertEqual(self.c.get('/api/product/recommendations').status_code,200)
        self.assertEqual(self.c.get('/api/product/ranking').json()['total_scans'],0)
        with self.store.db() as db:
            profile=db.execute('SELECT profile FROM wine_profile_scans WHERE scan_id=?',(scan_id,)).fetchone()[0]
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                assert image_bytes.startswith(b'\xff\xd8\xff')
                return {'choices':[{'message':{'content':json.dumps({'text':'На фото не удалось подтвердить бутылку вина. Попробуйте снять этикетку крупнее.','slugs':[]})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            answer=self.app.state.product.assistant(profile,'Что на фото?',scan_id=scan_id)
        self.assertEqual(answer['mode'],'ai')
        self.assertEqual(answer['items'],[])
        pairing=self.c.post('/api/product/assistant',json={
            'message':'Что подать к нему?','scan_id':scan_id,'intent':'dish_pairing'}).json()
        self.assertEqual(pairing['recommendation_type'],'dish')
        self.assertEqual(pairing['items'],[])
        self.assertEqual(pairing['dishes'],[])
        self.assertIsNone(pairing['wine'])
        self.assertIn('Не удалось уверенно определить',pairing['text'])

    def test_recommendation_constraints_and_ai_fallback(self):
        p=self.app.state.product
        p.cards['merlo'].update(category='Красное сухое',pairings=['Мясо и стейки'])
        p.cards['cabernet'].update(category='Белое сухое',pairings=['Рыба и морепродукты'])
        for s,w in p.cards.items():p.search[s]=' '.join(str(v) for v in w.values()).lower()
        self.c.get('/api/product/bootstrap')
        r=self.c.post('/api/product/assistant',json={'message':'Белое сухое к рыбе на ужин'}).json()
        self.assertEqual(r['mode'],'catalog');self.assertEqual([x['slug'] for x in r['items']],['cabernet'])
        r=self.c.post('/api/product/assistant',json={'message':'Красное к рыбе на ужин'}).json();self.assertEqual(r['items'],[])
        self.c.post('/api/product/preferences/cabernet',json={'rating':1})
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':'Белое сухое к рыбе на ужин'}).json()['items'],[])
        with patch.dict('os.environ',{'WINE_ASSISTANT_URL':'https://example.test/api','WINE_ASSISTANT_KEY':'test','WINE_ASSISTANT_MODEL':'test'}),patch('urllib.request.urlopen',side_effect=TimeoutError):
            r=self.c.post('/api/product/assistant',json={'message':'Красное к стейку на ужин'}).json();self.assertEqual(r['mode'],'catalog');self.assertTrue(r['warning']);self.assertEqual(r['items'][0]['slug'],'merlo')
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':5}).status_code,422)
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':'x'*1601}).status_code,422)
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':'вино','context':'wrong'}).status_code,422)

    def test_invalid_ai_slug_is_never_returned(self):
        self.c.get('/api/product/bootstrap')
        data={'choices':[{'message':{'content':json.dumps({'text':'Invented','slugs':['fake-slug']})}}]}
        with patch.dict('os.environ',{'WINE_ASSISTANT_URL':'https://example.test/api','WINE_ASSISTANT_KEY':'test','WINE_ASSISTANT_MODEL':'test'}),patch('urllib.request.urlopen',return_value=BytesIO(json.dumps(data).encode())):
            r=self.c.post('/api/product/assistant',json={'message':'Вино к ужину'}).json();self.assertEqual(r['mode'],'catalog');self.assertNotIn('fake-slug',[x['slug'] for x in r['items']])

    def test_gigachat_oauth_chat_and_token_cache(self):
        requests=[]
        def fake_urlopen(request,timeout=None,context=None):
            requests.append((request,timeout,context))
            if request.full_url.endswith('/oauth'):
                return BytesIO(json.dumps({'access_token':'test-access','expires_at':int((time.time()+1800)*1000)}).encode())
            return BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps({'text':'Подойдёт мерло.','slugs':['merlo']})}}]}).encode())
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_GIGACHAT_SCOPE':'GIGACHAT_API_PERS',
             'WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch('urllib.request.urlopen',side_effect=fake_urlopen):
            self.assertEqual(self.c.get('/api/product/bootstrap').json()['assistant_mode'],'ai')
            for _ in range(2):
                result=self.c.post('/api/product/assistant',json={'message':'Вино к ужину'}).json()
                self.assertEqual(result['mode'],'ai')
                self.assertEqual(result['items'][0]['slug'],'merlo')
                self.assertIn('Советую ',result['text'])
                self.assertIn('мерло',result['text'].lower())
                self.assertGreaterEqual(len(result['text'].split()),6)
        self.assertEqual(sum(r.full_url.endswith('/oauth') for r,_,_ in requests),1)
        chat=[r for r,_,_ in requests if r.full_url.endswith('/chat/completions')]
        self.assertEqual(len(chat),2)
        self.assertEqual(json.loads(chat[0].data)['response_format']['type'],'json_schema')
        self.assertEqual(chat[0].get_header('Authorization'),'Bearer test-access')
        self.assertTrue(all(context is not None and timeout<=18 for _,timeout,context in requests))

    def test_gigachat_refreshes_rejected_token(self):
        from gigachat_client import GigaChatClient
        seen=[]
        def fake_urlopen(request,timeout=None,context=None):
            if request.full_url.endswith('/oauth'):
                token='token-'+str(1+sum(x=='oauth' for x in seen));seen.append('oauth')
                return BytesIO(json.dumps({'access_token':token,'expires_at':int((time.time()+1800)*1000)}).encode())
            seen.append(request.get_header('Authorization'))
            if len(seen)==2:raise urllib.error.HTTPError(request.full_url,401,'unauthorized',{},None)
            return BytesIO(json.dumps({'choices':[]}).encode())
        with patch('urllib.request.urlopen',side_effect=fake_urlopen):
            client=GigaChatClient('Y2lkOnRlc3Q=','cid','GIGACHAT_API_PERS')
            self.assertEqual(client.complete({'model':'GigaChat-2','messages':[]}),{'choices':[]})
        self.assertEqual(seen,['oauth','Bearer token-1','oauth','Bearer token-2'])

    def test_gigachat_uploads_scanned_photo_and_attaches_it_to_vision_request(self):
        from gigachat_client import GigaChatClient
        file_id='80e0bcd5-2b78-4fa7-8783-903995f56b4b'
        seen=[]
        def fake_urlopen(request,timeout=None,context=None):
            seen.append(request)
            if request.full_url.endswith('/oauth'):
                return BytesIO(json.dumps({'access_token':'test-access','expires_at':int((time.time()+1800)*1000)}).encode())
            if request.full_url.endswith('/files'):
                self.assertIn(b'name="purpose"',request.data)
                self.assertIn(b'general',request.data)
                self.assertIn(b'filename="scan.jpg"',request.data)
                self.assertIn(b'\xff\xd8\xff',request.data)
                return BytesIO(json.dumps({'id':file_id}).encode())
            if request.full_url.endswith('/chat/completions'):
                sent=json.loads(request.data)
                self.assertEqual(sent['model'],'GigaChat-2-Pro')
                self.assertEqual(sent['messages'][-1]['attachments'],[file_id])
                self.assertEqual(sent['response_format']['schema']['required'],['choice_id'])
                return BytesIO(json.dumps({'choices':[{'message':{'content':'{"text":"Вино найдено","slugs":["merlo"]}'}}]}).encode())
            self.assertTrue(request.full_url.endswith('/files/'+file_id+'/delete'))
            return BytesIO(b'{}')
        with patch('urllib.request.urlopen',side_effect=fake_urlopen):
            client=GigaChatClient('Y2lkOnRlc3Q=','cid','GIGACHAT_API_PERS')
            result=client.complete({'model':'GigaChat-2-Pro','messages':[{'role':'user','content':'Что на фото?'}],
                                    'response_format':{'type':'json_schema','schema':{'type':'object',
                                        'properties':{'choice_id':{'type':'string'}},'required':['choice_id']}}},
                                   image_bytes=b'\xff\xd8\xff'+b'test-photo')
        self.assertEqual(result['choices'][0]['message']['content'],'{"text":"Вино найдено","slugs":["merlo"]}')
        self.assertEqual(len(seen),4)

    def test_attached_scan_is_private_and_drives_vision_model(self):
        self.c.get('/api/product/bootstrap')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        with self.store.db() as db:
            profile=db.execute('SELECT profile FROM wine_profile_scans WHERE scan_id=?',(scan['id'],)).fetchone()[0]
        product=self.app.state.product
        self.assertTrue(product.owned_scan(profile,scan['id'])['path'].is_file())
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as rejected:
            product.owned_scan('other-profile',scan['id'])
        self.assertEqual(rejected.exception.status_code,404)
        captured={}
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                captured.update(payload=payload,image=image_bytes)
                return {'choices':[{'message':{'content':json.dumps({'text':'Это вино соответствует снимку и карточке каталога.','slugs':['merlo']})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_GIGACHAT_SCOPE':'GIGACHAT_API_PERS',
             'WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(product,'gigachat',return_value=FakeGiga()):
            response=product.assistant(profile,'Что за вино на фото?',scan_id=scan['id'])
        self.assertEqual(response['mode'],'ai')
        self.assertEqual(response['items'][0]['slug'],'merlo')
        self.assertEqual(captured['payload']['model'],'GigaChat-2-Pro')
        self.assertEqual(json.loads(captured['payload']['messages'][-1]['content'])['scan']['recognized_slug'],'merlo')
        self.assertTrue(captured['image'].startswith(b'\xff\xd8\xff'))

    def test_attached_wine_recommends_a_dish_from_its_own_catalog_pairings(self):
        self.c.get('/api/product/bootstrap')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        product=self.app.state.product
        product.cards['merlo']['pairings']=['Сыры','Мясо и стейки']
        captured={}
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                captured.update(payload=payload,image=image_bytes)
                return {'choices':[{'message':{'content':json.dumps({'choice_id':'2'})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(product,'gigachat',return_value=FakeGiga()):
            response=self.c.post('/api/product/assistant',json={
                'message':'Что приготовить к этому вину?','scan_id':scan['id'],'intent':'dish_pairing'})
        self.assertEqual(response.status_code,200,response.text)
        answer=response.json()
        self.assertEqual(answer['recommendation_type'],'dish')
        self.assertEqual(answer['wine']['slug'],'merlo')
        self.assertEqual(answer['items'],[])
        self.assertEqual(answer['dishes'],[{'name':'Стейк из говядины','source_pairing':'Мясо и стейки'}])
        self.assertEqual(answer['mode'],'ai')
        self.assertIn('пример блюда',answer['text'])
        self.assertIn('Мясо и стейки',answer['text'])
        self.assertTrue(captured['image'].startswith(b'\xff\xd8\xff'))
        self.assertEqual(captured['payload']['model'],'GigaChat-2-Pro')
        self.assertEqual(captured['payload']['response_format']['schema']['required'],['choice_id'])
        options=json.loads(captured['payload']['messages'][-1]['content'])['choices']
        self.assertEqual(options[1],{'choice_id':'2','name':'Стейк из говядины','source_pairing':'Мясо и стейки'})

    def test_recipe_followup_keeps_dish_context_and_never_recommends_wine(self):
        self.c.get('/api/product/bootstrap')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        captured={}
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                captured.update(payload=payload,image=image_bytes)
                return {'choices':[{'message':{'content':json.dumps({
                    'text':'Натрите утку солью и перцем. Запекайте при 180 °C около двух часов до готовности.'})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        request={'message':'Как приготовить?','context':['Что приготовить к этому вину?'],
                 'scan_id':scan['id'],'intent':'dish_recipe','dish_name':'Запечённая утка'}
        env['WINE_ASSISTANT_VISION_MODEL']='GigaChat-2-Pro'
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            response=self.c.post('/api/product/assistant',json=request)
        self.assertEqual(response.status_code,200,response.text)
        answer=response.json()
        self.assertEqual(answer['recommendation_type'],'dish_recipe')
        self.assertEqual(answer['mode'],'ai')
        self.assertEqual(answer['items'],[])
        self.assertEqual(answer['dishes'],[])
        self.assertIsNone(answer['wine'])
        self.assertIn('Запечённая утка',answer['text'])
        self.assertIn('Запекайте',answer['text'])
        self.assertIsNone(captured['image'])
        self.assertEqual(captured['payload']['response_format']['schema']['required'],['text'])
        self.assertEqual(captured['payload']['model'],'GigaChat-2-Pro')
        user=json.loads(captured['payload']['messages'][-1]['content'])
        self.assertEqual(user['dish_name'],'Запечённая утка')
        self.assertEqual(user['previous_queries'],['Что приготовить к этому вину?'])
        self.assertNotIn('wine_name',user)
        with self.store.db() as db:
            db.execute('UPDATE scans SET result=? WHERE id=?',
                (json.dumps({'slug':None,'no_wine':True,'top5':[]}),scan['id']))
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            no_wine_recipe=self.c.post('/api/product/assistant',json=request).json()
        self.assertEqual(no_wine_recipe['mode'],'ai')
        self.assertEqual(no_wine_recipe['items'],[])

        class BadGiga:
            def complete(self,payload,image_bytes=None):
                raise TimeoutError('private provider response must not be logged')
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=BadGiga()),self.assertLogs('product',level='WARNING') as logs:
            fallback=self.c.post('/api/product/assistant',json=request).json()
        self.assertIn('TimeoutError',logs.output[0])
        self.assertNotIn('private provider response',logs.output[0])
        self.assertEqual(fallback['mode'],'catalog')
        self.assertEqual(fallback['items'],[])
        self.assertEqual(fallback['recommendation_type'],'dish_recipe')
        self.assertIn('запекайте',fallback['text'])
        self.assertTrue(fallback['warning'])
        class WineOnlyGiga:
            def complete(self,payload,image_bytes=None):
                return {'choices':[{'message':{'content':json.dumps({
                    'text':'Сначала приготовьте блюдо по любимому рецепту; к нему рекомендую красное вино из каталога.'})}}]}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=WineOnlyGiga()):
            wine_only=self.c.post('/api/product/assistant',json=request).json()
        self.assertEqual(wine_only['mode'],'catalog')
        self.assertEqual(wine_only['items'],[])
        self.assertIn('запекайте',wine_only['text'])
        class HttpErrorGiga:
            def complete(self,payload,image_bytes=None):
                raise urllib.error.HTTPError('https://api.giga.chat/private',400,
                    'private provider error',{},None)
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=HttpErrorGiga()),self.assertLogs('product',level='WARNING') as logs:
            failed=self.c.post('/api/product/assistant',json=request).json()
        self.assertEqual(failed['mode'],'catalog')
        self.assertIn('HTTPError HTTP 400',logs.output[0])
        self.assertNotIn('private',logs.output[0])
        with TestClient(self.app) as other:
            self.assertEqual(other.post('/api/product/assistant',json=request).status_code,404)
        self.assertEqual(self.c.post('/api/product/assistant',json={**request,'dish_name':'<script>bad</script>'}).status_code,422)
        self.assertEqual(self.c.post('/api/product/assistant',json={**request,'dish_name':''}).status_code,422)

    def test_dish_pairing_failure_and_missing_pairings_never_substitute_another_wine(self):
        self.c.get('/api/product/bootstrap')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        product=self.app.state.product
        product.cards['merlo']['pairings']=['Сыры']
        class BadGiga:
            def complete(self,payload,image_bytes=None):
                return {'choices':[{'message':{'content':json.dumps({'choice_id':'unknown'})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(product,'gigachat',return_value=BadGiga()):
            answer=self.c.post('/api/product/assistant',json={
                'message':'Что подать?','scan_id':scan['id'],'intent':'dish_pairing'}).json()
        self.assertEqual(answer['mode'],'catalog')
        self.assertTrue(answer['warning'])
        self.assertEqual(answer['dishes'],[{'name':'Сырная тарелка','source_pairing':'Сыры'}])
        self.assertEqual(answer['items'],[])
        product.cards['merlo']['pairings']=['Кухни народов мира']
        no_example=self.c.post('/api/product/assistant',json={
            'message':'Что приготовить?','scan_id':scan['id'],'intent':'dish_pairing'}).json()
        self.assertEqual(no_example['dishes'],[])
        self.assertEqual(no_example['items'],[])
        self.assertIn('нет достаточно точного сочетания',no_example['text'])
        product.cards['merlo']['pairings']=[]
        self.assertEqual(self.c.post('/api/product/assistant',json={
            'message':'Что приготовить?','scan_id':scan['id'],'intent':'dish_pairing'}).json()['dishes'],[])

    def test_dish_pairing_uses_human_corrected_wine_and_preserves_other_requests(self):
        self.c.get('/api/product/bootstrap')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        product=self.app.state.product
        product.cards['merlo']['pairings']=['Мясо и стейки']
        product.cards['cabernet']['pairings']=['Блюда из птицы']
        with self.store.db() as db:
            db.execute('UPDATE scans SET assigned_slug=? WHERE id=?',('cabernet',scan['id']))
        answer=self.c.post('/api/product/assistant',json={
            'message':'Что сочетается с этим вином?','scan_id':scan['id'],'intent':'dish_pairing'}).json()
        self.assertEqual(answer['wine']['slug'],'cabernet')
        self.assertEqual(answer['dishes'],[{'name':'Запечённая курица','source_pairing':'Блюда из птицы'}])
        ordinary=self.c.post('/api/product/assistant',json={'message':'Красное вино на ужин'}).json()
        self.assertNotIn('recommendation_type',ordinary)
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':'Ужин','intent':'unknown'}).status_code,422)
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':'Что подать?','intent':'dish_pairing'}).status_code,422)
        with self.store.db() as db:
            db.execute('UPDATE scans SET assigned_slug=?,result=? WHERE id=?',('',json.dumps({'slug':'unknown-wine','no_wine':False}),scan['id']))
        missing=self.c.post('/api/product/assistant',json={
            'message':'Что подать?','scan_id':scan['id'],'intent':'dish_pairing'}).json()
        self.assertEqual(missing['items'],[])
        self.assertEqual(missing['dishes'],[])
        self.assertIsNone(missing['wine'])

    def test_general_dialogue_answers_latest_question_without_wine_cards(self):
        self.c.get('/api/product/bootstrap')
        history=[
            {'role':'user','content':'Что подать к утке?'},
            {'role':'assistant','content':'Подойдёт запечённая утка с яблоками.'},
            {'role':'user','content':'Как приготовить?'},
            {'role':'assistant','content':'Запекайте утку с яблоками при 180 °C около двух часов.'},
        ]
        captured={}
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                captured.update(payload=payload,image=image_bytes)
                return {'choices':[{'message':{'content':json.dumps({
                    'text':'Яблоки можно заменить грушами. Положите их к утке ближе к концу запекания.'})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            response=self.c.post('/api/product/assistant',json={
                'message':'А чем заменить яблоки?','context':history})
        self.assertEqual(response.status_code,200,response.text)
        answer=response.json()
        self.assertEqual(answer['recommendation_type'],'conversation')
        self.assertEqual(answer['items'],[])
        self.assertEqual(answer['mode'],'ai')
        self.assertIn('грушами',answer['text'])
        self.assertEqual(captured['payload']['model'],'GigaChat-2-Pro')
        self.assertEqual(captured['payload']['response_format']['schema']['required'],['text'])
        self.assertEqual(captured['payload']['messages'][1:-1],history)
        self.assertEqual(captured['payload']['messages'][-1],{'role':'user','content':'А чем заменить яблоки?'})
        self.assertIsNone(captured['image'])

    def test_general_questions_and_short_followups_do_not_force_wine(self):
        self.c.get('/api/product/bootstrap')
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                return {'choices':[{'message':{'content':json.dumps({
                    'text':'Танины — вещества, которые дают ощущение терпкости.'})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            for message in ('Что такое танины?','Почему?','Сколько хранить?'):
                result=self.c.post('/api/product/assistant',json={'message':message,
                    'context':[{'role':'user','content':'Как приготовить?'},
                               {'role':'assistant','content':'Запекайте утку при 180 °C.'}]}).json()
                self.assertEqual(result['recommendation_type'],'conversation')
                self.assertEqual(result['items'],[])
                self.assertEqual(result['mode'],'ai')
        for message in ('Красное к стейку','Белое сухое к рыбе','Подбери другое вино',
                        'Вино к ужину','Какое вино к пасте?'):
            result=self.c.post('/api/product/assistant',json={'message':message}).json()
            self.assertNotIn('recommendation_type',result,message)

    def test_short_wine_request_does_not_inherit_unrelated_dialogue(self):
        self.c.get('/api/product/bootstrap')
        product=self.app.state.product
        with patch.object(product,'recommendations',return_value=[]) as recommend:
            self.c.post('/api/product/assistant',json={'message':'Белое к рыбе',
                'context':[{'role':'user','content':'Как приготовить утку?'},
                           {'role':'assistant','content':'Запекайте её два часа.'}]})
            self.assertEqual(recommend.call_args.args[1],'Белое к рыбе')
            self.c.post('/api/product/assistant',json={'message':'Красное к стейку',
                'context':[{'role':'user','content':'Белое сухое к рыбе'},
                           {'role':'assistant','content':'Подойдёт белое сухое.'}]})
            self.assertEqual(recommend.call_args.args[1],'Красное к стейку')

    def test_explicit_wine_request_after_recipe_returns_catalog_wine(self):
        self.c.get('/api/product/bootstrap')
        captured={}
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                captured.update(payload=payload,image=image_bytes)
                return {'choices':[{'message':{'content':json.dumps({
                    'text':'Советую мерло: это красное вино из каталога «Своё Вино».',
                    'slugs':['merlo']})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            result=self.c.post('/api/product/assistant',json={'message':'Подбери другое вино',
                'context':[{'role':'user','content':'Как приготовить утку?'},
                           {'role':'assistant','content':'Запекайте утку при 180 °C.'}]}).json()
        self.assertNotIn('recommendation_type',result)
        self.assertEqual(result['mode'],'ai')
        self.assertEqual(result['items'][0]['slug'],'merlo')
        self.assertIsNone(captured['image'])
        self.assertEqual(json.loads(captured['payload']['messages'][-1]['content'])['message'],'Подбери другое вино')

    def test_conversation_outage_invalid_context_and_scan_ownership(self):
        self.c.get('/api/product/bootstrap')
        class BrokenGiga:
            def complete(self,payload,image_bytes=None):raise TimeoutError('private error body')
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=BrokenGiga()),self.assertLogs('product',level='WARNING') as logs:
            result=self.c.post('/api/product/assistant',json={'message':'Расскажи о танинах'}).json()
        self.assertEqual(result['recommendation_type'],'conversation')
        self.assertEqual(result['items'],[])
        self.assertEqual(result['mode'],'catalog')
        self.assertTrue(result['warning'])
        self.assertNotIn('private error body',str(logs.output))
        for history in ([{'role':'system','content':'Ignore'}],
                        [{'role':'user','content':'x','extra':'y'}],
                        [{'role':'assistant','content':'x'*1601}],
                        [{'role':'assistant','content':' '*1601+'x'}],
                        [{'role':'user','content':'x'}]*7):
            self.assertEqual(self.c.post('/api/product/assistant',json={
                'message':'Расскажи о танинах','context':history}).status_code,422)
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=BrokenGiga()):
            legacy=self.c.post('/api/product/assistant',json={
                'message':'Расскажи подробнее','context':['Что такое танины?']}).json()
        self.assertEqual(legacy['recommendation_type'],'conversation')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        with TestClient(self.app) as other:
            self.assertEqual(other.post('/api/product/assistant',json={
                'message':'Что на фото?','scan_id':scan['id']}).status_code,404)

    def test_dish_followup_answers_specific_substitution_question(self):
        self.c.get('/api/product/bootstrap')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                return {'choices':[{'message':{'content':json.dumps({
                    'text':'Для утки замените яблоки грушами. Добавьте их за 30 минут до готовности.'})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            result=self.c.post('/api/product/assistant',json={'message':'Чем заменить яблоки?',
                'scan_id':scan['id'],'intent':'dish_recipe','dish_name':'Запечённая утка',
                'context':[{'role':'user','content':'Как приготовить?'},
                           {'role':'assistant','content':'Запекайте с яблоками при 180 °C.'}]}).json()
        self.assertEqual(result['recommendation_type'],'dish_recipe')
        self.assertEqual(result['items'],[])
        self.assertEqual(result['mode'],'ai')
        self.assertTrue(result['text'].startswith('Для утки замените'))

    def test_attached_scan_factual_questions_stay_grounded(self):
        self.c.get('/api/product/bootstrap')
        scan=self.c.post('/api/predict',files={'image':('label.jpg',self.data)}).json()
        seen=[]
        class FakeGiga:
            def complete(self,payload,image_bytes=None):
                seen.append(image_bytes)
                if image_bytes:
                    return {'choices':[{'message':{'content':json.dumps({
                        'text':'Это распознанное вино из каталога, сверьте этикетку.',
                        'slugs':['merlo']})}}]}
                return {'choices':[{'message':{'content':json.dumps({
                    'text':'Вино — напиток из винограда.'})}}]}
        env={'WINE_ASSISTANT_PROVIDER':'gigachat','WINE_GIGACHAT_AUTH_KEY':'Y2lkOnRlc3Q=',
             'WINE_GIGACHAT_CLIENT_ID':'cid','WINE_ASSISTANT_MODEL':'GigaChat-2'}
        with patch.dict('os.environ',env),patch.object(self.app.state.product,'gigachat',return_value=FakeGiga()):
            for message in ('Расскажи про это вино','Почему это вино подходит?','Что за вино?'):
                result=self.c.post('/api/product/assistant',json={
                    'message':message,'scan_id':scan['id']}).json()
                self.assertNotIn('recommendation_type',result)
                self.assertEqual(result['mode'],'ai')
                self.assertEqual(result['items'][0]['slug'],'merlo')
            general=self.c.post('/api/product/assistant',json={
                'message':'Что такое вино?','scan_id':scan['id']}).json()
            storage=self.c.post('/api/product/assistant',json={
                'message':'Как хранить открытое вино?','scan_id':scan['id']}).json()
        self.assertEqual(general['recommendation_type'],'conversation')
        self.assertEqual(general['items'],[])
        self.assertEqual(storage['recommendation_type'],'conversation')
        self.assertEqual(storage['items'],[])
        self.assertEqual(storage['mode'],'ai')
        self.assertTrue(all(photo is not None for photo in seen[:3]))
        self.assertEqual(seen[3:],[None,None])

if __name__=='__main__':unittest.main()
