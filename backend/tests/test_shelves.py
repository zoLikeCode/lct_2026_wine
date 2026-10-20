import json
import sys
import uuid
from pathlib import Path
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_server

class ShelvesTest(test_server.ServerTest):
    def setUp(self):
        super().setUp()
        self.c.post('/api/product/account/register',json={'username':'shelf-owner','password':'Local-test-password-123','display_name':'Автор'})
    def create(self, name='Моя полка'):
        r=self.c.post('/api/product/collection/lists',json={'name':name,'size':0});self.assertEqual(r.status_code,200,r.text)
        ident=r.json()['id'];self.c.post('/api/product/collection/lists/'+ident,json={'action':'add','slug':'merlo'});return ident
    def publish(self, ident, visibility='public'):
        r=self.c.post('/api/product/shelves/'+ident+'/settings',json={'name':'Полка автора','description':'К ужину','visibility':visibility})
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def test_public_private_share_revocation_and_isolation(self):
        ident=self.create()
        self.c.post('/api/product/preferences/merlo',json={'rating':5,'favorite':True})
        self.c.post('/api/product/collection/wines/merlo',json={'note':'Секретная заметка','tried':True,'version':0})
        with TestClient(self.app) as other:
            other.post('/api/product/account/register',json={'username':'reader','password':'Local-test-password-456'})
            self.assertEqual(other.get('/api/product/shelves/'+ident).status_code,404)
            self.assertEqual(other.get('/api/product/shelves').json()['total'],0)
            public=self.publish(ident);token=public['share_token']
            view=other.get('/api/product/shelves/shared/'+token).json()
            self.assertFalse(view['owner']);self.assertEqual(view['wines'][0]['rating'],0)
            self.assertNotIn('Секретная заметка',json.dumps(view,ensure_ascii=False))
            self.assertEqual(other.post('/api/product/shelves/'+ident+'/settings',json={'name':'attack','visibility':'private'}).status_code,404)
            self.assertEqual(other.post('/api/product/shelves/'+ident+'/reorder',json={'slugs':['merlo']}).status_code,404)
            self.publish(ident,'private')
            self.assertEqual(other.get('/api/product/shelves/shared/'+token).status_code,404)
            self.assertEqual(other.get('/api/product/shelves/'+ident).status_code,404)
            new=self.publish(ident);self.assertNotEqual(new['share_token'],token)
    def test_copy_is_private_independent_idempotent_and_keeps_ratings(self):
        ident=self.create();self.publish(ident)
        with TestClient(self.app) as other:
            other.post('/api/product/account/register',json={'username':'reader','password':'Local-test-password-456'})
            copied=other.post('/api/product/shelves/'+ident+'/copy',json={});self.assertEqual(copied.status_code,200,copied.text)
            new=copied.json()['id'];self.assertNotEqual(ident,new)
            self.assertEqual(other.post('/api/product/shelves/'+ident+'/copy',json={}).json()['id'],new)
            detail=other.get('/api/product/shelves/'+new).json();self.assertTrue(detail['owner']);self.assertEqual(detail['visibility'],'private')
            self.assertEqual(other.post('/api/product/shelves/'+new+'/settings',json={'name':'copy','visibility':'private'}).status_code,200)
            other.post('/api/product/preferences/merlo',json={'rating':4})
            other.post('/api/product/collection/lists/'+new,json={'action':'remove','slug':'merlo'})
            self.assertEqual(len(self.c.get('/api/product/shelves/'+ident).json()['wines']),1)
            self.assertEqual(other.get('/api/product/collection').json()['items'][0]['rating'],4)
            self.assertEqual(self.c.get('/api/product/shelves/'+new).status_code,404)
    def test_discussion_messages_replies_retry_delete_and_access(self):
        ident=self.create();self.publish(ident)
        url='/api/product/shelves/'+ident+'/comment'
        with TestClient(self.app) as other:
            self.assertEqual(other.post(url,json={'body':'anon'}).status_code,401)
            other.post('/api/product/account/register',json={'username':'other-shelf','password':'Local-test-password-456','display_name':'Гость'})
            first=other.post(url,json={'body':'Первый','client_id':'message-first-0001'});self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(other.post(url,json={'body':'Первый','client_id':'message-first-0001'}).status_code,200)
            self.assertEqual(other.post(url,json={'body':'Другой','client_id':'message-first-0001'}).status_code,409)
            other.post(url,json={'body':'Второй','client_id':'message-second-001'})
            reply=self.c.post(url,json={'body':'Ответ автора','reply_to':first.json()['id']})
            self.assertEqual(reply.status_code,200,reply.text)
            view=self.c.get('/api/product/shelves/'+ident).json();self.assertEqual(len(view['comments']),3)
            self.assertEqual(view['comments'][-1]['reply_body'],'Первый')
            self.assertFalse(view['comments'][0]['mine'])
            self.assertEqual(self.c.post(url,json={'id':first.json()['id'],'body':''}).status_code,404)
            self.assertEqual(other.post(url,json={'id':first.json()['id'],'body':''}).status_code,200)
            view=self.c.get('/api/product/shelves/'+ident).json();self.assertTrue(view['comments'][0]['deleted'])
            self.assertEqual(view['comments'][-1]['reply_body'],'Сообщение удалено')
            self.assertNotIn('profile',view['comments'][0])
            self.assertEqual(other.post(url,json={'body':''}).status_code,422)
            other_id=self.create(); self.publish(other_id)
            self.assertEqual(other.post('/api/product/shelves/'+other_id+'/comment',json={'body':'cross reply','reply_to':reply.json()['id']}).status_code,422)
            self.publish(ident,'private')
            self.assertEqual(other.get('/api/product/shelves/'+ident+'/discussion').status_code,404)
            self.assertEqual(other.post(url,json={'body':'Private attack'}).status_code,404)

    def test_discussion_migration_and_pagination(self):
        from shelves import Shelves
        ident=self.create()
        with self.store.db() as db:
            profile=db.execute('SELECT profile FROM collection_lists WHERE id=?',(ident,)).fetchone()[0]
            db.execute('INSERT INTO collection_comments VALUES(?,?,?,?,?)',(ident,profile,'Старая заметка',1,2))
            db.execute("DELETE FROM collection_schema_migrations WHERE name='discussion-v1'")
        shelves=Shelves(self.app.state.product.collection)
        initial=shelves.detail(profile,ident)['comments']; self.assertEqual(initial[0]['body'],'Старая заметка')
        Shelves(self.app.state.product.collection)
        self.assertEqual(len(shelves.detail(profile,ident)['comments']),1)
        self.c.post('/api/product/shelves/'+ident+'/comment',json={'id':initial[0]['id'],'body':''})
        Shelves(self.app.state.product.collection)
        self.assertTrue(shelves.detail(profile,ident)['comments'][0]['deleted'])
        with self.store.db() as db:
            for i in range(60):
                db.execute('INSERT INTO collection_messages(id,list_id,profile,body,created,updated) VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,ident,profile,str(i),10+i,10+i))
        last=self.c.get('/api/product/shelves/'+ident+'/discussion').json()
        self.assertEqual(last['total'],61); self.assertEqual(len(last['items']),50)
        older=self.c.get('/api/product/shelves/'+ident+'/discussion?before='+str(last['next_before'])).json()
        self.assertEqual(len(older['items']),11); self.assertIsNone(older['next_before'])
        self.assertFalse({x['id'] for x in older['items']} & {x['id'] for x in last['items']})

    def test_collection_ranking_unique_accounts_self_private_and_badges(self):
        ident=self.create();self.publish(ident)
        other_id=self.create();self.publish(other_id)
        url='/api/product/shelves/'+ident+'/copy'
        self.c.post(url,json={})
        with TestClient(self.app) as other:
            self.assertEqual(other.post(url,json={}).status_code,401)
            self.assertEqual(self.c.get('/api/product/shelves/'+ident).json()['copies'],0)
            r=other.post('/api/product/account/register',json={'username':'saver-account','password':'Local-test-password-456','display_name':'Читатель'})
            self.assertEqual(r.status_code,200,r.text)
            new=other.post(url,json={}).json()['id'];other.post(url,json={})
            self.assertEqual(self.c.get('/api/product/shelves/'+ident).json()['copies'],1)
            other.post('/api/product/collection/lists/'+new,json={'action':'delete'})
            other.post(url,json={})
            self.assertEqual(self.c.get('/api/product/shelves/'+ident).json()['copies'],1)
            ranked=self.c.get('/api/product/shelves/ranking').json()['items']
            self.assertEqual(ranked[0]['id'],ident);self.assertEqual(ranked[0]['copies'],1)
            snap=self.c.get('/api/product/collection').json()['achievements']
            badge=next(x for x in snap['items'] if x['id']=='collection-first-save')
            self.assertTrue(badge['earned']);self.assertEqual(snap['metrics']['collection_saves'],1)
            author=other.get('/api/product/shelves/authors/'+ident).json()
            self.assertIn('collection-first-save',[x['id'] for x in author['badges']])
            self.publish(ident,'private')
            self.assertNotIn(ident,[x['id'] for x in self.c.get('/api/product/shelves/ranking').json()['items']])
            self.assertTrue(next(x for x in self.c.get('/api/product/collection').json()['achievements']['items'] if x['id']=='collection-first-save')['earned'])

    def test_collection_badge_requires_one_collection_and_registered_savers(self):
        from achievements import achievement_snapshot
        a=self.create();b=self.create()
        with self.store.db() as db:
            owner=db.execute('SELECT profile FROM collection_lists WHERE id=?',(a,)).fetchone()[0]
            for i in range(10):
                profile='registered-'+str(i)
                db.execute('INSERT INTO wine_accounts VALUES(?,?,?,?,?,?,?)',(profile,profile,profile,'test','test',profile,0))
                db.execute('INSERT INTO collection_saves VALUES(?,?,?)',(profile,a if i<5 else b,0))
            db.execute('INSERT INTO collection_saves VALUES(?,?,?)',('guest-only',a,0))
            db.execute('INSERT INTO collection_saves VALUES(?,?,?)',(owner,a,0))
        snap=achievement_snapshot(self.store,owner,self.app.state.product.cards)
        self.assertEqual(snap['metrics']['collection_saves'],5)
        self.assertFalse(next(x for x in snap['items'] if x['id']=='collection-ten-saves')['earned'])
        with self.store.db() as db:
            for i in range(5,10): db.execute('INSERT INTO collection_saves VALUES(?,?,?)',('registered-'+str(i),a,0))
        snap=achievement_snapshot(self.store,owner,self.app.state.product.cards)
        self.assertTrue(next(x for x in snap['items'] if x['id']=='collection-ten-saves')['earned'])

    def test_reorder_requires_exact_members_and_survives_restart(self):
        ident=self.create();self.c.post('/api/product/collection/lists/'+ident,json={'action':'add','slug':'cabernet'})
        url='/api/product/shelves/'+ident+'/reorder'
        self.assertEqual(self.c.post(url,json={'slugs':['merlo']}).status_code,409)
        self.assertEqual(self.c.post(url,json={'slugs':['merlo','merlo']}).status_code,422)
        self.assertEqual(self.c.post(url,json={'slugs':['cabernet','merlo']}).status_code,200)
        self.assertEqual([w['slug'] for w in self.c.get('/api/product/shelves/'+ident).json()['wines']],['cabernet','merlo'])
    def test_challenge_only_distinct_completed_real_predictions(self):
        shelf=self.app.state.product.collection.shelves
        slugs=['merlo','cabernet','wine3','wine4','wine5']
        for slug in slugs[2:]:shelf.product.cards[slug]={**shelf.product.cards['merlo'],'slug':slug}
        shelf.challenge_config={'id':'test-challenge','wines':[{'slug':s,'price':10000,'source_url':'https://example.org','checked_at':'2026-10-18'} for s in slugs]}
        profile=self.c.get('/api/product/bootstrap').json()
        with self.store.db() as db:owner=db.execute("SELECT profile FROM wine_accounts WHERE username='shelf-owner'").fetchone()[0]
        def scan(slug,version='real-model-v1',no_wine=False):
            ident,p=self.store.scan_start(self.data,'test-device');self.store.scan_finish(ident,{'slug':slug,'model_version':version,'no_wine':no_wine});shelf.product.record_scan(owner,ident)
        scan('merlo','design-qa-fake');scan('cabernet',no_wine=True)
        self.assertEqual(shelf.challenge(owner)['progress'],0)
        copied=self.c.post('/api/product/shelves/challenge/copy',json={})
        self.assertEqual(copied.status_code,200,copied.text)
        ident=copied.json()['id']
        self.assertEqual(self.c.post('/api/product/shelves/challenge/copy',json={}).json()['id'],ident)
        saved=self.c.get('/api/product/shelves/'+ident).json()
        self.assertEqual(len(saved['wines']),5)
        self.assertEqual(saved['visibility'],'private')
        self.assertEqual(shelf.challenge(owner)['progress'],0)
        scan('merlo');scan('merlo');self.assertEqual(shelf.challenge(owner)['progress'],1)
        self.assertFalse(shelf.challenge(owner)['earned'])
        for s in slugs[1:]:scan(s)
        self.assertTrue(shelf.challenge(owner)['earned']);self.assertEqual(shelf.challenge(owner)['progress'],5)
        self.assertEqual(shelf.challenge('other-profile')['progress'],0)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM collection_badges').fetchone()[0],1)

    def test_large_shelf_reorder_fits_bounded_request(self):
        ident=self.create()
        with self.store.db() as db:
            owner=db.execute('SELECT profile FROM collection_lists WHERE id=?',(ident,)).fetchone()[0]
            slugs=['merlo']+['wine-'+str(i)+'-'+'x'*110 for i in range(1,100)]
            for slot,slug in enumerate(slugs[1:],1):
                db.execute('INSERT INTO collection_members VALUES(?,?,?,?,?)',(ident,owner,slug,slot,0))
        result=self.c.post('/api/product/shelves/'+ident+'/reorder',json={'slugs':slugs[::-1]})
        self.assertEqual(result.status_code,200,result.text)
        with self.store.db() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT slug FROM collection_members WHERE list_id=? ORDER BY slot',(ident,))],slugs[::-1])

    def test_wine_collections_filter_before_limit_and_rank_unique_savers(self):
        # Many unrelated public lists must not push matching ones out of the top five.
        shelves=self.app.state.product.collection.shelves
        with self.store.db() as db:
            for i in range(8):
                p='rank-user-'+str(i)
                db.execute('INSERT INTO wine_accounts VALUES(?,?,?,?,?,?,?)',(p,p,p,'test','test',p,0))
            for i in range(65):
                ident='rank-list-'+str(i); slug='merlo' if i>=58 else 'cabernet'
                db.execute('INSERT INTO collection_lists VALUES(?,?,?,?,?,?)',(ident,'rank-owner',ident,'',0,i))
                db.execute('INSERT INTO collection_list_meta(list_id,visibility,updated) VALUES(?,?,?)',(ident,'public',i))
                db.execute('INSERT INTO collection_members VALUES(?,?,?,?,?)',(ident,'rank-owner',slug,0,i))
                for saver in range(8 if i<58 else i-58):
                    db.execute('INSERT INTO collection_saves VALUES(?,?,?)',('rank-user-'+str(saver),ident,0))
            # A private collection owned by the reader is still excluded.
            owner=db.execute("SELECT profile FROM wine_accounts WHERE username='shelf-owner'").fetchone()[0]
            db.execute('UPDATE collection_lists SET profile=? WHERE id=?',(owner,'rank-list-64'))
            db.execute("UPDATE collection_list_meta SET visibility='private' WHERE list_id='rank-list-64'")
            db.execute("UPDATE collection_lists SET profile='system:demo:fixture' WHERE id='rank-list-63'")
        rows=self.c.get('/api/product/shelves/ranking?slug=merlo').json()['items']
        self.assertEqual([x['id'] for x in rows],['rank-list-'+str(i) for i in (62,61,60,59,58)])
        self.assertEqual([x['copies'] for x in rows],[4,3,2,1,0])
        self.assertTrue(all(any(w['slug']=='merlo' for w in x['wines']) for x in rows))
        self.assertTrue(all('comments' not in x and 'share_token' not in x for x in rows))
        with TestClient(self.app) as guest:
            self.assertEqual(guest.get('/api/product/shelves/ranking?slug=merlo').status_code,401)
        self.app.state.product.cards['new-wine']={**self.app.state.product.cards['merlo'],'slug':'new-wine'}
        self.assertEqual(self.c.get('/api/product/shelves/ranking?slug=new-wine').json()['items'],[])
        self.assertEqual(self.c.get('/api/product/shelves/ranking?slug=missing').status_code,404)

    def test_wine_collections_recheck_visibility_and_membership(self):
        from unittest.mock import patch
        a=self.create();self.publish(a)
        shelves=self.app.state.product.collection.shelves
        original=shelves.detail
        def changed(profile,ident):
            item=original(profile,ident);item['visibility']='private';return item
        with patch.object(shelves,'detail',side_effect=changed):
            self.assertEqual(self.c.get('/api/product/shelves/ranking?slug=merlo').json()['items'],[])
        def removed(profile,ident):
            item=original(profile,ident);item['wines']=[];return item
        with patch.object(shelves,'detail',side_effect=removed):
            self.assertEqual(self.c.get('/api/product/shelves/ranking?slug=merlo').json()['items'],[])
