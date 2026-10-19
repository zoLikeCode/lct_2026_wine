from unittest.mock import patch
from fastapi.testclient import TestClient
from test_shelves import ShelvesTest


class SommelierCollectionsTest(ShelvesTest):
    def setUp(self):
        super().setUp()
        product=self.app.state.product
        product.cards['merlo'].update(category='Красное сухое',pairings=['Мясо и стейки'])
        product.cards['cabernet'].update(category='Белое сухое',pairings=['Рыба и морепродукты'],grapes='Рислинг')

    def white_shelf(self, client, name='Белые к рыбе'):
        response=client.post('/api/product/collection/lists',json={'name':name,'size':0})
        self.assertEqual(response.status_code,200,response.text)
        ident=response.json()['id']
        client.post('/api/product/collection/lists/'+ident,json={'action':'add','slug':'cabernet'})
        return ident

    def test_assistant_filters_composition_and_keeps_wine_route(self):
        red=self.create('Красные к мясу');white=self.white_shelf(self.c)
        answer=self.c.post('/api/product/assistant',json={'message':'Подбери коллекцию белого сухого к рыбе'}).json()
        self.assertEqual(answer['recommendation_type'],'collection')
        self.assertEqual([x['id'] for x in answer['collections']],[white])
        self.assertEqual(answer['items'],[])
        self.assertIn('Рыба и морепродукты',answer['collections'][0]['reason'])
        self.assertEqual(answer['collections'][0]['matched_count'],1)
        ordinary=self.c.post('/api/product/assistant',json={'message':'Красное вино к стейку'}).json()
        self.assertEqual(ordinary['items'][0]['slug'],'merlo')
        self.assertNotIn('collections',ordinary)
        none=self.c.post('/api/product/assistant',json={'message':'Подбери коллекцию розового вина к рыбе'}).json()
        self.assertEqual(none['collections'],[])
        self.assertNotIn(red,[x['id'] for x in answer['collections']])

    def test_private_shelves_and_notes_do_not_leak_and_revocation_applies(self):
        private=self.white_shelf(self.c,'Секретная полка')
        public=self.white_shelf(self.c,'Белые для гостей');self.publish(public)
        self.c.post('/api/product/collection/wines/cabernet',json={'note':'Личная заметка владельца','version':0})
        shelf=self.app.state.product.collection.shelves
        result=shelf.recommendations('different-profile','Подбери коллекцию к рыбе')
        self.assertEqual([x['id'] for x in result['collections']],[public])
        self.assertNotIn('Секретная полка',str(result));self.assertNotIn('Личная заметка',str(result))
        self.assertFalse(result['collections'][0]['owner'])
        self.publish(public,'private')
        self.assertEqual(shelf.recommendations('different-profile','Подбери коллекцию')['collections'],[])
        with self.store.db() as db:owner=db.execute('SELECT profile FROM collection_lists WHERE id=?',(private,)).fetchone()[0]
        own=shelf.recommendations(owner,'Подбери коллекцию')['collections']
        self.assertEqual(len(own),1)  # Identical wine sets are one recommendation.
        self.assertTrue(own[0]['owner'])

    def test_own_scope_ratings_followup_and_partial_match(self):
        mixed=self.create('Разные вина')
        self.c.post('/api/product/collection/lists/'+mixed,json={'action':'add','slug':'cabernet'})
        with TestClient(self.app) as other:
            other.post('/api/product/account/register',json={'username':'other-collections','password':'Local-test-password-456'})
            public=self.white_shelf(other)
            other.post('/api/product/shelves/'+public+'/settings',json={'name':'Публичная белая','visibility':'public'})
        answer=self.c.post('/api/product/assistant',json={'message':'Подбери из моих коллекций к рыбе'}).json()
        self.assertEqual([x['id'] for x in answer['collections']],[mixed])
        self.assertEqual((answer['collections'][0]['matched_count'],answer['collections'][0]['wine_count']),(1,2))
        followup=self.c.post('/api/product/assistant',json={'message':'А к мясу?',
            'context':[{'role':'user','content':'Подбери коллекцию к рыбе'}]}).json()
        self.assertEqual([x['id'] for x in followup['collections']],[mixed])
        self.c.post('/api/product/preferences/cabernet',json={'rating':1})
        disliked=self.c.post('/api/product/assistant',json={'message':'Подбери коллекцию к рыбе'}).json()
        self.assertEqual(disliked['collections'],[])

    def test_collection_request_uses_catalogue_path(self):
        self.white_shelf(self.c)
        with patch.object(self.app.state.product,'conversation',side_effect=AssertionError('wrong route')):
            result=self.c.post('/api/product/assistant',json={'message':'Подбери коллекцию','intent':'collection_recommendation'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(result.json()['collections']),1)


    def test_collections_for_you_outside_the_chat(self):
        public=self.white_shelf(self.c,'Белые для гостей');self.publish(public)
        private=self.white_shelf(self.c,'Секретная полка')
        self.c.post('/api/product/preferences/cabernet',json={'rating':5})
        self.assertEqual(self.c.get('/api/product/shelves/for-you').json()['items'],[])  # own shelves are not «для вас»
        with TestClient(self.app) as other:
            newcomer=other.get('/api/product/shelves/for-you').json()
            self.assertEqual(newcomer['items'],[]);self.assertIn('анкету',newcomer['empty'])
            other.post('/api/product/preferences/cabernet',json={'rating':5})
            found=other.get('/api/product/shelves/for-you?limit=3').json()
            self.assertEqual([x['id'] for x in found['items']],[public])
            self.assertIn('Каберне',found['items'][0]['reason']);self.assertFalse(found['items'][0]['owner'])
            self.assertNotIn(private,str(found))
            other.post('/api/product/shelves/'+public+'/copy',json={})  # saved already: nothing new to suggest
            self.assertEqual(other.get('/api/product/shelves/for-you').json()['items'],[])
            self.assertEqual(other.get('/api/product/shelves/for-you?limit=99').status_code,422)

    def test_search_for_a_shelf_skips_its_wines_and_stays_private(self):
        shelf=self.white_shelf(self.c)
        found=self.c.get('/api/product/search',params={'q':'тестовая','list_id':shelf}).json()['items']
        self.assertEqual([w['slug'] for w in found],['merlo'])  # cabernet is already on the shelf
        self.assertEqual(len(self.c.get('/api/product/search',params={'q':'тестовая'}).json()['items']),2)
        with TestClient(self.app) as other:
            self.assertEqual(other.get('/api/product/search',params={'q':'тестовая','list_id':shelf}).status_code,404)
