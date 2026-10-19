import sys,tempfile,time,unittest,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT.parent/'wine_curation/tests'))
from test_curation import fixture
from data_store import DataStore
from product import Product

class RecsysServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root,_=fixture(Path(self.tmp.name))
        self.store=DataStore(root);self.p=Product(self.store);self.r=self.p.recs
        self.p.cards['merlo'].update(category='Красное сухое',pairings=['Мясо и стейки'])
        self.p.cards['cabernet'].update(category='Красное сухое',pairings=['Мясо и стейки'])
    def tearDown(self):self.tmp.cleanup()

    def own(self,profile,slug,quantity=1,opened=0):
        with self.store.db() as db:
            db.execute('INSERT INTO collection_entries(profile,slug,quantity,updated) VALUES(?,?,?,?)',(profile,slug,quantity,time.time()))
            for _ in range(opened):
                db.execute('INSERT INTO collection_events VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,profile,slug,-1,'open',uuid.uuid4().hex,None,time.time()))

    def test_modes_have_one_shape_and_log_impressions(self):
        for mode in ('for_you','new','cellar','refill'):
            res=self.r.endpoint('p1',mode,'',4)
            self.assertEqual(set(res),{'mode','items','request_id','empty'})
            self.assertTrue(res['items'] or res['empty'])
        with self.store.db() as db:
            n=db.execute("SELECT count(*) FROM recsys_events WHERE profile='p1' AND action='impression'").fetchone()[0]
        self.assertGreater(n,0)
        with self.assertRaises(Exception):self.r.endpoint('p1','whatever')
        with self.assertRaises(Exception):self.r.endpoint('p1','for_you','',99)

    def test_not_for_me_hides_and_counts_as_dislike_until_undone(self):
        first=self.r.endpoint('p1','for_you','',4)['items'][0]['slug']
        self.r.feedback('p1',{'slug':first,'action':'not_for_me','mode':'for_you'})
        self.assertNotIn(first,[w['slug'] for w in self.r.endpoint('p1','for_you','',4)['items']])
        self.assertLess(self.p.recsys.evidence(self.p.taste_signals('p1')[first]),0)
        self.r.feedback('p1',{'slug':first,'action':'undo'})
        self.assertNotIn(first,self.p.taste_signals('p1'))
        for bad in [{'slug':first,'action':'like'},{'slug':'missing','action':'click'},'x']:
            with self.assertRaises(Exception):self.r.feedback('p1',bad)

    def test_onboarding_validates_and_shapes_the_passport(self):
        with self.assertRaises(Exception):self.r.save_onboarding('p1',{'colors':['синее']})
        with self.assertRaises(Exception):self.r.save_onboarding('p1',{'tiles':['nope']})
        res=self.r.save_onboarding('p1',{'colors':['красное'],'sweetness':['сухое'],'tiles':['rich_oak']})
        self.assertEqual(res['answers']['tiles'],['rich_oak'])
        self.assertTrue(res['recommendations']['items'])
        passport=self.r.passport('p1')
        self.assertTrue(passport['onboarding']);self.assertEqual(passport['stage'],'learning')
        self.assertGreaterEqual({a['axis']:a['value'] for a in passport['axes']}['body'],4)
        self.assertEqual(self.r.passport('p2')['stage'],'new')

    def test_cellar_and_refill_use_the_collection(self):
        self.assertTrue(self.r.endpoint('p1','cellar')['empty'])
        self.own('p1','merlo',quantity=2)
        cellar=self.r.endpoint('p1','cellar','к мясу')['items']
        self.assertEqual([w['slug'] for w in cellar],['merlo']);self.assertTrue(cellar[0]['owned'])
        self.own('p1','cabernet',quantity=0,opened=1)
        refill=self.r.endpoint('p1','refill')['items']
        self.assertEqual(refill[0]['slug'],'cabernet');self.assertIn('купить снова',refill[0]['reason'])

    def test_assistant_answers_from_own_bottles(self):
        empty=self.p.assistant('p1','Что открыть сегодня из моей коллекции к мясу?')
        self.assertEqual(empty['recommendation_type'],'cellar');self.assertEqual(empty['items'],[])
        self.own('p1','merlo',quantity=2)
        answer=self.p.assistant('p1','Что открыть сегодня из моей коллекции к мясу?')
        self.assertEqual([w['slug'] for w in answer['items']],['merlo']);self.assertIn('Мерло',answer['text'])
        self.assertNotEqual(self.p.assistant('p1','У меня есть вопрос про танины').get('recommendation_type'),'cellar')

    def test_list_suggestions_skip_members_and_respect_size(self):
        lid=self.p.collection.save_list('p1',{'name':'Шашлыки','goal':'','size':6})['id']
        self.p.collection.list_action('p1',lid,{'action':'add','slug':'merlo'})
        res=self.r.list_suggestions('p1',lid)
        self.assertEqual(res['free'],5);self.assertNotIn('merlo',[w['slug'] for w in res['items']])
        with self.assertRaises(Exception):self.r.list_suggestions('p2',lid)

    def test_list_suggestions_follow_what_is_on_the_shelf(self):
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from test_recsys import CARDS
        from recsys import Recommender
        self.p.cards={s:{**w,'slug':s,'image':'','portal_url':''} for s,w in CARDS.items()}
        self.p.search={s:w['name'].lower() for s,w in self.p.cards.items()}
        self.p.recsys=self.r.rec=Recommender(self.p.cards)
        lid=self.p.collection.save_list('p1',{'name':'Пятница','goal':'','size':0})['id']
        for slug in ('sweet-1','sap-3'):self.p.collection.list_action('p1',lid,{'action':'add','slug':slug})
        items=self.r.list_suggestions('p1',lid,6)['items']
        slugs=[w['slug'] for w in items]
        self.assertIn('sweet-2',slugs[:2])  # a newcomer: the shelf itself decides, not the catalog order
        self.assertNotIn('sap-3-2021',slugs)  # another vintage of a wine already on the shelf
        self.assertIn('из этой коллекции',items[0]['reason'])

    def test_guest_onboarding_survives_merge(self):
        self.r.save_onboarding('guest',{'tiles':['bubbles']})
        self.r.feedback('guest',{'slug':'merlo','action':'dismiss'})
        with self.store.db() as db:self.r.merge_guest(db,'guest','account')
        self.assertEqual(self.r.answers('account')['tiles'],['bubbles'])
        self.assertIn('merlo',self.r.hidden('account'));self.assertIsNone(self.r.answers('guest'))

    RED_LOVER={'wine':'red','coffee':'black','chocolate':'dark','juice':'apple','sugar':'none','aroma':'berries'}

    def test_quiz_validates_and_builds_the_passport(self):
        options=self.r.quiz_options('p1')
        self.assertEqual(len(options['questions']),6);self.assertEqual(options['answers'],{})
        self.assertTrue(all(o['insight'] for q in options['questions'] for o in q['options']))
        for bad in [{'answers':{'wine':'blue'}},{'answers':{'nope':'red'}},{'answers':['red']},'x']:
            with self.assertRaises(Exception):self.r.save_quiz('p1',bad)
        res=self.r.save_quiz('p1',{'answers':self.RED_LOVER})
        passport=res['passport'];axes={a['axis']:a['value'] for a in passport['axes']}
        self.assertEqual(passport['stage'],'learning');self.assertEqual(passport['quiz'],self.RED_LOVER)
        self.assertGreaterEqual(axes['body'],4);self.assertGreaterEqual(axes['tannin'],4);self.assertEqual(axes['sweetness'],1)
        self.assertEqual(passport['aromas'][0]['id'],'red_berries')  # the aroma question speaks first
        self.assertIn('красные',passport['summary'])
        self.assertTrue(all(w['reason'] for w in res['recommendations']['items']))
        self.assertEqual(self.r.answers('p1')['colors'],['красное'])
        self.assertEqual(self.r.quiz_options('p1')['answers'],self.RED_LOVER)
        # a skipped question is simply absent; a retake replaces the old answers
        self.r.save_quiz('p1',{'answers':{'wine':'white','juice':'grapefruit'}})
        self.assertEqual(self.r.answers('p1')['colors'],['белое','розовое'])
        self.assertNotIn('tannin',{a['axis'] for a in self.r.passport('p1')['axes']})

    def test_passport_tabs_narrow_to_one_kind(self):
        self.r.save_quiz('p1',{'answers':self.RED_LOVER})
        self.assertTrue(self.r.passport('p1','red')['axes'])
        white=self.r.passport('p1','white')
        self.assertEqual(white['stage'],'new');self.assertEqual(white['axes'],[])
        self.assertEqual([t['id'] for t in white['tabs']],['red','white','rose','sparkling'])
        self.p.update('p1','merlo',{'rating':5})
        self.assertEqual({t['id']:t['count'] for t in self.r.passport('p1')['tabs']}['red'],1)
        with self.assertRaises(Exception):self.r.passport('p1','orange')

    def test_wine_taste_has_scales_and_a_match_only_with_a_profile(self):
        sheet=self.p.wine_detail('merlo','p1')['taste']
        self.assertEqual([s['axis'] for s in sheet['scales']],['body','tannin','sweetness','acidity'])
        self.assertTrue(all(1<=s['value']<=5 and s['low'] and s['high'] for s in sheet['scales']))
        self.assertIsNone(sheet['match'])
        self.r.save_quiz('p1',{'answers':self.RED_LOVER})
        match=self.p.wine_detail('merlo','p1')['taste']['match']
        self.assertTrue(10<=match['percent']<=99 and match['note'])
        self.assertEqual(set(match['yours']),{'body','tannin','sweetness','acidity'})
        self.p.cards['cabernet'].update(category='Белое сухое',description='Свежее, с нотами лимона')
        self.r.rec.styles[self.r.rec.index['cabernet']]=('белое','сухое',False)
        self.assertNotIn('tannin',[s['axis'] for s in self.r.wine_taste('cabernet')['scales']])

    def test_swipes_train_taste_fill_likes_and_undo_cleanly(self):
        deck=self.r.deck('p1',5)
        self.assertTrue(deck['items']);self.assertIn('taste',deck['items'][0]);self.assertEqual(deck['swiped'],0)
        first=deck['items'][0]['slug']
        res=self.r.swipe('p1',{'slug':first,'verdict':'like'})
        self.assertEqual(res['passport']['swiped'],1)
        self.assertTrue(self.p.preferences('p1')[first]['favorite'])  # «хочу попробовать» lands in Лайки
        self.assertGreater(self.p.recsys.evidence(self.p.taste_signals('p1')[first]),0)
        self.assertNotIn(first,[w['slug'] for w in self.r.deck('p1',5)['items']])
        self.r.swipe('p1',{'slug':first,'verdict':'nope'})  # changed their mind: the like goes away
        self.assertFalse(self.p.preferences('p1')[first]['favorite'])
        self.assertLess(self.p.recsys.evidence(self.p.taste_signals('p1')[first]),0)
        self.assertNotIn(first,[w['slug'] for w in self.p.recommendations('p1','',8)])
        self.r.swipe('p1',{'slug':first,'verdict':'undo'})
        self.assertEqual(self.r.swipes('p1'),{});self.assertNotIn('swipe',self.p.taste_signals('p1').get(first,{}))
        # a wine that was already liked stays liked after undo
        self.p.update('p1','merlo',{'favorite':True})
        self.r.swipe('p1',{'slug':'merlo','verdict':'like'});self.r.swipe('p1',{'slug':'merlo','verdict':'undo'})
        self.assertTrue(self.p.preferences('p1')['merlo']['favorite'])
        for bad in [{'slug':first,'verdict':'love'},{'slug':'missing','verdict':'like'},'x']:
            with self.assertRaises(Exception):self.r.swipe('p1',bad)
        with self.assertRaises(Exception):self.r.deck('p1',0)

    def test_guest_quiz_and_swipes_survive_merge(self):
        self.r.save_quiz('guest',{'answers':{'wine':'red'}})
        self.r.swipe('guest',{'slug':'merlo','verdict':'nope'})
        with self.store.db() as db:self.r.merge_guest(db,'guest','account')
        self.assertEqual(self.r.answers('account')['quiz'],{'wine':'red'})
        self.assertEqual(self.r.swipes('account'),{'merlo':'nope'});self.assertEqual(self.r.swipes('guest'),{})

if __name__=='__main__':unittest.main()
