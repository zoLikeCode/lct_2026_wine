import sys,uuid,threading,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).parent))
import test_server
from fastapi.testclient import TestClient
import unittest

class CollectionTest(unittest.TestCase):
    setUp=test_server.ServerTest.setUp
    tearDown=test_server.ServerTest.tearDown
    def register(self,client=None,name='collector'):
        client=client or self.c
        r=client.post('/api/product/account/register',json={'username':name,'password':'Local-test-only-2026','display_name':'Коллекционер'})
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def stock(self,client=None,slug='merlo',delta=1,kind='add',key=None,**extra):
        return (client or self.c).post('/api/product/collection/stock/'+slug,json={'delta':delta,'kind':kind,'request_id':key or uuid.uuid4().hex,**extra})
    def snapshot(self,client=None):return (client or self.c).get('/api/product/collection').json()
    def seed_legacy(self,client=None,rating=None,favorite=False,quantity=0):
        """Old guest state remains migratable, but new guest mutations are forbidden."""
        client=client or self.c;client.get('/api/product/bootstrap')
        profile=hashlib.sha256(client.cookies.get('wine_profile').encode()).hexdigest()
        if rating is not None:self.app.state.product.update(profile,'merlo',{'rating':rating,'favorite':favorite})
        if quantity:self.app.state.product.collection.stock(profile,'merlo',{'delta':quantity,'kind':'add','request_id':uuid.uuid4().hex})
        return profile
    def test_legacy_preferences_and_no_invented_inventory(self):
        self.register()
        self.c.post('/api/product/preferences/merlo',json={'favorite':True,'rating':5})
        data=self.snapshot();self.assertEqual(data['counts'],{'all':1,'wish':1,'owned':0,'tried':1,'bottles':0,'liked_rated':1})
        self.assertEqual(data['items'][0]['rating'],5)
        with TestClient(self.app) as other:
            self.register(other,'other');self.assertEqual(self.snapshot(other)['items'],[])
    def test_register_preserves_guest_and_seals_guest_cookie(self):
        self.seed_legacy(rating=4,favorite=True)
        old_token=self.c.cookies.get('wine_profile');result=self.register()
        self.assertEqual(len(result['recovery_code']),39);self.assertEqual(self.snapshot()['counts']['wish'],1)
        self.assertTrue(self.c.get('/api/product/account').json()['authenticated'])
        with TestClient(self.app) as other:
            other.cookies.set('wine_profile',old_token)
            self.assertEqual(other.get('/api/product/collection').status_code,401)
            r=other.post('/api/product/account/login',json={'username':'collector','password':'Local-test-only-2026'})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.snapshot(other)['items'][0]['rating'],4)
        self.c.post('/api/product/account/logout',json={})
        self.assertEqual(self.c.get('/api/product/collection').status_code,401)
    def test_stock_idempotency_conflicting_keys_and_undo(self):
        self.register()
        key=uuid.uuid4().hex;r=self.stock(delta=3,key=key);self.assertEqual(r.status_code,200)
        self.assertTrue(self.stock(delta=3,key=key).json()['replayed']);self.assertEqual(self.stock(delta=4,key=key).status_code,409)
        opened=self.stock(delta=-1,kind='open').json()['event_id'];self.assertEqual(self.snapshot()['items'][0]['quantity'],2)
        self.assertEqual(self.stock(kind='undo',undo_of=opened).status_code,200)
        self.assertEqual(self.stock(kind='undo',undo_of=opened).status_code,409)
        self.assertEqual(self.snapshot()['items'][0]['quantity'],3)
        self.assertFalse(self.snapshot()['items'][0]['tried'])
        self.assertEqual(self.stock(delta=-4,kind='adjust').status_code,409)
        self.assertEqual(self.stock(delta=-1,kind='gift').status_code,200)
    def test_concurrent_stock_and_one_remaining_bottle(self):
        self.register()
        cookies=dict(self.c.cookies)
        self.stock(delta=1)
        def consume(i):
            with TestClient(self.app) as c:
                c.cookies.update(cookies)
                return self.stock(client=c,delta=-1,kind='open').status_code
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(consume,range(2)))
        self.assertEqual(sorted(result),[200,409]);self.assertEqual(self.snapshot()['counts']['bottles'],0)
    def test_note_version_and_foreign_list_isolation(self):
        self.register()
        ident=self.c.post('/api/product/collection/lists',json={'name':'Праздник','size':6}).json()['id']
        self.c.post('/api/product/collection/lists/'+ident,json={'action':'add','slug':'merlo','slot':0})
        self.assertEqual(self.c.post('/api/product/collection/lists/'+ident,json={'action':'add','slug':'cabernet','slot':0}).status_code,409)
        self.assertEqual(self.c.post('/api/product/collection/wines/merlo',json={'note':'К ужину','tried':True,'version':0}).status_code,200)
        self.assertEqual(self.c.post('/api/product/collection/wines/merlo',json={'note':'Старая запись','tried':True,'version':0}).status_code,409)
        with TestClient(self.app) as other:
            self.register(other,'other')
            self.assertEqual(other.post('/api/product/collection/lists/'+ident,json={'action':'delete'}).status_code,404)
            self.assertEqual(self.snapshot(other)['items'],[])
        self.c.post('/api/product/collection/lists/'+ident,json={'action':'delete'})
        self.assertEqual(self.snapshot()['items'][0]['note'],'К ужину')
    def test_login_merge_requires_choice_and_preserves_account_rating(self):
        self.seed_legacy(rating=5,favorite=True,quantity=2);self.register()
        with TestClient(self.app) as other:
            self.seed_legacy(other,rating=2,quantity=3)
            creds={'username':'collector','password':'Local-test-only-2026'}
            r=other.post('/api/product/account/login',json=creds);self.assertEqual(r.status_code,409,r.text)
            self.assertEqual(r.json()['detail']['summary']['conflicts'][0]['guest'],3)
            r=other.post('/api/product/account/login',json={**creds,'merge_guest':True,'quantity_policy':'keep'});self.assertEqual(r.status_code,200,r.text)
            wine=self.snapshot(other)['items'][0];self.assertEqual(wine['quantity'],2);self.assertEqual(wine['rating'],5)
    def test_recovery_rotates_code_and_revokes_sessions(self):
        first=self.register();old_session=self.c.cookies.get('wine_account')
        with TestClient(self.app) as other:
            r=other.post('/api/product/account/recover',json={'username':'collector','password':'New-local-password-2026','recovery_code':first['recovery_code']})
            self.assertEqual(r.status_code,200,r.text);self.assertNotEqual(r.json()['recovery_code'],first['recovery_code'])
            repeat=other.post('/api/product/account/recover',json={'username':'collector','password':'New-local-password-2026','recovery_code':first['recovery_code']})
            self.assertEqual(repeat.status_code,401)
            self.assertEqual(other.post('/api/product/account/login',json={'username':'collector','password':'New-local-password-2026'}).status_code,200)
        self.assertFalse(self.c.get('/api/product/account').json()['authenticated']);self.assertEqual(self.stock().status_code,401)
    def test_session_revoke_owner_and_csrf(self):
        self.register()
        with TestClient(self.app) as other:
            other.post('/api/product/account/login',json={'username':'collector','password':'Local-test-only-2026'})
            sessions=self.c.get('/api/product/account').json()['sessions'];self.assertEqual(len(sessions),2)
            foreign=next(s['id'] for s in sessions if not s['current'])
            self.c.post('/api/product/account/revoke',json={'session_id':foreign})
            self.assertFalse(other.get('/api/product/account').json()['authenticated'])
        self.assertEqual(self.c.post('/api/product/collection/lists',json={'name':'Bad'},headers={'Origin':'https://evil.test'}).status_code,403)
    def test_registration_validation_and_login_rate_limit(self):
        self.assertEqual(self.c.post('/api/product/account/register',json={'username':'aa','password':'short'}).status_code,422)
        self.register()
        with TestClient(self.app) as other:
            for _ in range(11):self.assertEqual(other.post('/api/product/account/login',json={'username':'collector','password':'Wrong-local-password'}).status_code,401)
            self.assertEqual(other.post('/api/product/account/login',json={'username':'collector','password':'Wrong-local-password'}).status_code,429)
    def test_wishlist_does_not_train_taste(self):
        self.register()
        before=self.c.get('/api/product/recommendations').json()
        self.c.post('/api/product/preferences/cabernet',json={'favorite':True})
        after=self.c.get('/api/product/recommendations').json()
        self.assertEqual([w['slug'] for w in before['items']],[w['slug'] for w in after['items']])
        self.assertFalse(any('Близко к сортам' in w['reason'] for w in after['items']))

if __name__=='__main__':unittest.main()

class ExtraCollectionTest(CollectionTest):
    def test_merge_sum_has_one_safe_reversible_event(self):
        self.seed_legacy(quantity=2);self.register()
        with TestClient(self.app) as other:
            self.seed_legacy(other,quantity=3)
            result=other.post('/api/product/account/login',json={'username':'collector','password':'Local-test-only-2026','merge_guest':True,'quantity_policy':'sum'})
            self.assertEqual(result.status_code,200,result.text)
            snapshot=self.snapshot(other);self.assertEqual(snapshot['counts']['bottles'],5)
            events=snapshot['events'];self.assertEqual(len(events),2)
            event=next(e for e in events if e['kind']=='merge')
            self.assertEqual(self.stock(client=other,kind='undo',undo_of=event['id']).status_code,200)
            self.assertEqual(self.snapshot(other)['counts']['bottles'],2)
    def test_keep_guest_collection_survives_logout(self):
        self.register()
        with TestClient(self.app) as other:
            legacy=self.seed_legacy(other,quantity=2)
            self.assertEqual(other.post('/api/product/account/login',json={'username':'collector','password':'Local-test-only-2026','merge_guest':False}).status_code,200)
            self.assertEqual(self.snapshot(other)['counts']['bottles'],0)
            other.post('/api/product/account/logout',json={})
            self.assertEqual(other.get('/api/product/collection').status_code,401)
            self.assertEqual(self.app.state.product.collection.snapshot(legacy)['counts']['bottles'],2)
    def test_password_change_verifies_password_and_revokes_other_device(self):
        self.register()
        with TestClient(self.app) as other:
            other.post('/api/product/account/login',json={'username':'collector','password':'Local-test-only-2026'})
            r=self.c.post('/api/product/account/password',json={'current_password':'Wrong-local-password','new_password':'Updated-local-password'})
            self.assertEqual(r.status_code,403)
            r=self.c.post('/api/product/account/password',json={'current_password':'Local-test-only-2026','new_password':'Updated-local-password'})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['recovery_code']),39)
            self.assertFalse(other.get('/api/product/account').json()['authenticated'])
            self.assertTrue(self.c.get('/api/product/account').json()['authenticated'])
