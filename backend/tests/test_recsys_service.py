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

    def test_guest_onboarding_survives_merge(self):
        self.r.save_onboarding('guest',{'tiles':['bubbles']})
        self.r.feedback('guest',{'slug':'merlo','action':'dismiss'})
        with self.store.db() as db:self.r.merge_guest(db,'guest','account')
        self.assertEqual(self.r.answers('account')['tiles'],['bubbles'])
        self.assertIn('merlo',self.r.hidden('account'));self.assertIsNone(self.r.answers('guest'))

if __name__=='__main__':unittest.main()
