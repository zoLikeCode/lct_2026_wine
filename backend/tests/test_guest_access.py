import sys, threading, unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).parent))
import test_server

class GuestAccessTest(unittest.TestCase):
    setUp=test_server.ServerTest.setUp
    tearDown=test_server.ServerTest.tearDown

    def register(self):
        r=self.c.post('/api/product/account/register',json={'username':'access.test','password':'Access-test-only-2026'})
        self.assertEqual(r.status_code,200,r.text)

    def test_one_guest_answer_persists_and_login_unlocks(self):
        first=self.c.post('/api/product/assistant',json={'message':'Белое сухое к рыбе'})
        self.assertEqual(first.status_code,200,first.text)
        with patch.object(self.app.state.product,'assistant') as assistant:
            second=self.c.post('/api/product/assistant',json={'message':'А к сыру?'})
            self.assertEqual(second.status_code,401)
            self.assertEqual(second.json()['detail']['code'],'assistant_sign_in');assistant.assert_not_called()
        with TestClient(self.app) as reopened:
            reopened.cookies.set('wine_profile',self.c.cookies.get('wine_profile'))
            self.assertEqual(reopened.post('/api/product/assistant',json={'message':'К сыру'}).status_code,401)
        self.register()
        with patch.object(self.app.state.product,'assistant',return_value={'text':'Ответ','items':[]}):
            for _ in range(2):self.assertEqual(self.c.post('/api/product/assistant',json={'message':'К сыру'}).status_code,200)

    def test_guest_scan_stays_available_after_registering(self):
        scan=self.c.post('/api/predict',files={'image':('a.jpg',self.data)},headers={'X-Device-ID':'guest-phone'}).json()
        self.assertEqual(self.c.get('/api/product/history').status_code,401)
        self.assertEqual(self.c.get('/api/product/scans/'+scan['id']+'/image').status_code,200)
        self.register()
        self.assertEqual(self.c.get('/api/product/history').json()['items'][0]['id'],scan['id'])
        self.c.post('/api/product/account/logout',json={})
        self.assertEqual(self.c.get('/api/product/history').status_code,401)

    def test_invalid_first_query_does_not_consume_trial(self):
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':5}).status_code,422)
        self.assertEqual(self.c.post('/api/product/assistant',json={'message':'Вино к ужину'}).status_code,200)

    def test_parallel_guest_query_does_not_call_assistant_twice(self):
        self.c.get('/api/product/bootstrap');cookie=self.c.cookies.get('wine_profile')
        entered=threading.Event();finish=threading.Event()
        def answer(*args):
            entered.set();finish.wait(5);return {'text':'Ответ','items':[]}
        def first():
            with TestClient(self.app) as client:
                client.cookies.set('wine_profile',cookie)
                return client.post('/api/product/assistant',json={'message':'К ужину'})
        with patch.object(self.app.state.product,'assistant',side_effect=answer) as assistant,ThreadPoolExecutor(1) as pool:
            future=pool.submit(first)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.c.post('/api/product/assistant',json={'message':'К сыру'}).status_code,429)
            finally:finish.set()
            self.assertEqual(future.result().status_code,200);self.assertEqual(assistant.call_count,1)
