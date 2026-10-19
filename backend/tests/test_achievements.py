import json
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_shelves import ShelvesTest
from achievements import achievement_snapshot


class AchievementsTest(ShelvesTest):
    def owner(self):
        with self.store.db() as db:
            return db.execute("SELECT profile FROM wine_accounts WHERE username='shelf-owner'").fetchone()[0]

    def scan(self, slug, version='real-model-v1', no_wine=False, profile=None, error=''):
        ident, _ = self.store.scan_start(self.data, 'test-device')
        self.store.scan_finish(ident, {'slug': slug, 'model_version': version, 'no_wine': no_wine}, error)
        self.app.state.product.record_scan(profile or self.owner(), ident)

    def test_only_real_distinct_completed_scans_unlock_rewards(self):
        product = self.app.state.product
        self.create()
        self.c.post('/api/product/preferences/merlo', json={'favorite': True, 'rating': 5})
        for version in ('design-qa-fake', 'test-only', 'preview', ''):
            self.scan('merlo', version)
        self.scan('merlo', no_wine=True)
        self.scan('merlo', error='timeout')
        self.scan('outside-catalog')
        self.scan('merlo', profile='somebody-else')
        snap = lambda: achievement_snapshot(self.store, self.owner(), product.cards)
        self.assertEqual(snap()['unique_wines'], 0)
        self.scan('merlo'); self.scan('merlo')
        first = snap()
        self.assertEqual(first['unique_wines'], 1)
        self.assertEqual(first['status'], 'Первооткрыватель')
        self.assertTrue(first['items'][0]['earned'])
        self.assertEqual(snap()['items'][0]['earned_at'], first['items'][0]['earned_at'])
        self.assertEqual(snap()['earned_count'], 1)
        self.assertFalse(achievement_snapshot(self.store, 'unused-profile', product.cards)['items'][0]['earned'])

    def test_catalogue_dimensions_and_guest_transfer(self):
        p = self.app.state.product
        p.cards['merlo'].update(winery='А', region='Крым', grapes='Мерло', category='Красное сухое')
        p.cards['cabernet'].update(winery='Б', region='Кубань', grapes='Каберне Совиньон', category='Розовое')
        p.cards['third'] = {**p.cards['merlo'], 'slug':'third','winery':'В','region':'Долина Дона','grapes':'Шардоне','category':'Белое'}
        p.cards['blend'] = {**p.cards['merlo'], 'slug':'blend','grapes':'Мерло, Сира, Мальбек'}
        for slug in ('merlo', 'cabernet', 'third', 'blend'): self.scan(slug)
        result = achievement_snapshot(self.store, self.owner(), p.cards)
        self.assertEqual(result['metrics'], dict(wines=4, grapes=3, wineries=3, regions=3, colors=3, collection_saves=0))
        self.assertEqual(result['earned_count'], 5)
        self.assertFalse(result['items'][1]['earned'])
        with self.store.db() as db:
            p.collection.merge_guest(db, self.owner(), 'transfer-target', 'keep')
        moved = achievement_snapshot(self.store, 'transfer-target', p.cards)
        self.assertEqual(moved['metrics'], result['metrics'])
        self.assertEqual(moved['earned_count'], result['earned_count'])

    def test_editorial_seed_is_idempotent_copyable_and_not_a_fake_person(self):
        shelves = self.app.state.product.collection.shelves
        config = {'collections': [{'id':'editorial-fixture','name':'Вина к ужину','description':'Из каталога',
                                   'theme':'К столу','slugs':['merlo','cabernet']}]}
        with patch('shelves.Path.read_text', return_value=json.dumps(config)):
            shelves.seed_editorial(); shelves.seed_editorial()
        result = self.c.get('/api/product/shelves').json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(self.c.get('/api/product/shelves?author=people').json()['total'], 0)
        self.assertEqual(self.c.get('/api/product/shelves?author=editorial').json()['total'], 1)
        self.assertEqual(self.c.get('/api/product/shelves?author=other').status_code, 422)
        item = result['items'][0]
        self.assertTrue(item['editorial']); self.assertFalse(item['owner'])
        self.assertEqual(item['author'], 'Редакция приложения')
        self.assertEqual(len(item['wines']), 2)
        self.assertEqual(self.c.post('/api/product/shelves/editorial-fixture/settings', json={'name':'hijack'}).status_code, 404)
        copied = self.c.post('/api/product/shelves/editorial-fixture/copy', json={}).json()['id']
        self.c.post('/api/product/collection/lists/'+copied, json={'action':'remove','slug':'merlo'})
        with patch('shelves.Path.read_text', return_value=json.dumps(config)): shelves.seed_editorial()
        self.assertEqual(len(self.c.get('/api/product/shelves/'+copied).json()['wines']), 1)
        self.assertEqual(len(self.c.get('/api/product/shelves/editorial-fixture').json()['wines']), 2)
        self.assertEqual(self.c.get('/api/product/collection').json()['achievements']['earned_count'], 0)

    def test_demo_profiles_are_explicit_isolated_and_can_be_disabled(self):
        shelves = self.app.state.product.collection.shelves
        config = {'profiles':[{'id':'fixture','name':'Анна','bio':'Пример профиля',
            'achievements':['first-label','five-labels'], 'collections':[
            {'id':'demo-fixture','name':'Два вина','description':'Пример', 'slugs':['merlo','cabernet']}]}]}
        shelves.demo_enabled = True
        with patch('shelves.Path.read_text', return_value=json.dumps(config)):
            shelves.seed_demo(); shelves.seed_demo()
        feed = self.c.get('/api/product/shelves?author=people').json()
        self.assertEqual(feed['total'], 1)
        item = feed['items'][0]
        self.assertTrue(item['demo']); self.assertEqual(item['author'], 'Анна')
        author = self.c.get('/api/product/shelves/authors/demo-fixture').json()
        self.assertTrue(author['demo']); self.assertEqual(author['status'], 'Любознательный')
        self.assertEqual(len(author['badges']), 2); self.assertEqual(len(author['collections']), 1)
        self.assertNotIn('profile', author)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM wine_accounts WHERE profile LIKE 'system:demo:%'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM collection_badges WHERE profile LIKE 'system:demo:%'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM wine_profile_scans WHERE profile LIKE 'system:demo:%'").fetchone()[0], 0)
        copied=self.c.post('/api/product/shelves/demo-fixture/copy',json={}).json()['id']
        view=self.c.get('/api/product/shelves/'+copied).json()
        self.assertFalse(view['demo']); self.assertTrue(view['owner']); self.assertEqual(view['visibility'],'private')
        self.assertEqual(self.c.get('/api/product/collection').json()['achievements']['earned_count'],0)
        shelves.demo_enabled = False
        self.assertEqual(self.c.get('/api/product/shelves?author=people').json()['total'],0)
        self.assertEqual(self.c.get('/api/product/shelves/demo-fixture').status_code,404)
        self.assertEqual(self.c.get('/api/product/shelves/authors/demo-fixture').status_code,404)
        self.assertEqual(self.c.get('/api/product/shelves/'+copied).status_code,200)

    def test_public_author_exposes_only_public_collections_and_no_private_notes(self):
        public=self.create('Для всех'); private=self.create('Личное')
        self.publish(public)
        self.c.post('/api/product/collection/wines/merlo',json={'note':'Секрет','tried':True,'version':0})
        self.scan('merlo')
        self.c.get('/api/product/collection')
        with TestClient(self.app) as other:
            response=other.get('/api/product/shelves/authors/'+public)
            self.assertEqual(response.status_code,200)
            payload=response.json()
            self.assertEqual([x['id'] for x in payload['collections']],[public])
            self.assertEqual(payload['status'],'Первооткрыватель')
            self.assertNotIn('Секрет',response.text)
            self.assertNotIn(self.owner(),response.text)
            self.assertEqual(other.get('/api/product/shelves/authors/'+private).status_code,404)
            self.publish(public,'private')
            self.assertEqual(other.get('/api/product/shelves/authors/'+public).status_code,404)


if __name__ == '__main__':
    import unittest
    unittest.main()
