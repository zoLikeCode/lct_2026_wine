import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from recsys import Recommender

def wine(name,category,grapes,description,winery,region='Кубань',pairings=()):
    return {'name':name,'category':category,'grapes':grapes,'description':description,'winery':winery,
            'region':region,'pairings':list(pairings),'alcohol':13,'temperature':'16-18 °C'}

CARDS={
    'sap-1':wine('Саперави Резерв','Красное сухое','Саперави','Ежевика, черника, танины, дуб','A'),
    'sap-2':wine('Саперави Классика','Красное сухое','Саперави','Черника и ежевика, бархатные танины','B'),
    'sap-3':wine('Саперави Амфора','Красное сухое','Саперави','Ежевика, вишня, плотные танины','C'),
    'sap-3-2021':wine('Саперави Амфора 2021','Красное сухое','Саперави','Ежевика, вишня, плотные танины','C'),
    'cab':wine('Каберне','Красное сухое','Каберне Совиньон','Смородина, перец, танины','D'),
    'brut-1':wine('Брют Белое','Белое брют','Шардоне','Цветы, бриошь, яблоко','E',pairings=['Морепродукты']),
    'brut-2':wine('Брют Резерв','Белое брют','Шардоне, Пино Нуар','Бриошь, груша, цветы','F',pairings=['Морепродукты']),
    'brut-3':wine('Экстра Брют','Белое экстра брют','Шардоне','Яблоко, цитрус, бриошь','G'),
    'sweet-1':wine('Мускат','Белое полусладкое','Мускат','Мёд, персик, абрикос','H'),
    'sweet-2':wine('Мускат десертный','Белое сладкое','Мускат','Мёд, абрикос, персик','I'),
    'rose':wine('Розе','Розовое сухое','Пино Нуар','Клубника, малина','J'),
}

class RecommenderTest(unittest.TestCase):
    def setUp(self):self.r=Recommender(CARDS)

    def feed(self,signals,limit):
        score,source,conf,penalty=self.r.personal(signals)
        items=[(10*conf*score[i]-6*penalty[i],s,'') for s,i in self.r.index.items() if s not in signals]
        pos,_=self.r.profile(signals);shares={}
        for i,w in pos.items():shares[self.r.interest(i)]=shares.get(self.r.interest(i),0)+w
        total=sum(shares.values());shares={k:v/total*conf for k,v in shares.items()};shares['explore']=(1-conf)/2
        interests={s:(self.r.interest(source[self.r.index[s]]) if source[self.r.index[s]] is not None and
                      self.r.sim(self.r.index[s],source[self.r.index[s]])>=.45 else 'explore') for _,s,_ in items}
        return [s for _,s,_ in self.r.diversify(items,limit,interests=interests,shares=shares)]

    def test_similar_wines_are_closer_than_other_styles(self):
        i=self.r.index
        self.assertGreater(self.r.sim(i['sap-1'],i['sap-2']),self.r.sim(i['sap-1'],i['brut-1']))
        self.assertGreater(self.r.sim(i['brut-1'],i['brut-2']),self.r.sim(i['brut-1'],i['sweet-1']))

    def test_two_tastes_both_reach_the_feed(self):
        got=self.feed({'sap-1':{'rating':5},'sap-2':{'rating':5},'brut-1':{'rating':4}},4)
        self.assertTrue(any(s.startswith('sap') for s in got),got)
        self.assertTrue(any(s.startswith('brut') for s in got),got)

    def test_disliked_style_is_pushed_away(self):
        score,_,_,penalty=self.r.personal({'sweet-1':{'rating':1},'brut-1':{'rating':5}})
        i=self.r.index
        self.assertGreater(penalty[i['sweet-2']],penalty[i['brut-2']])

    def test_duplicates_are_shown_once(self):
        got=self.feed({'sap-1':{'rating':5}},6)
        self.assertLessEqual(len({'sap-3','sap-3-2021'}&set(got)),1,got)

    def test_evidence_order(self):
        e=Recommender.evidence
        self.assertGreater(e({'rating':5}),e({'opened':1}))
        self.assertGreater(e({'opened':1}),e({'quantity':1}))
        self.assertEqual(e({'favorite':True}),0);self.assertEqual(e({'scanned':True}),0)
        self.assertEqual(e({'gifted':1}),0)
        self.assertLess(e({'rating':2,'favorite':True}),0)

    def test_reason_names_the_liked_wine(self):
        i=self.r.index
        line=self.r.why(i['sap-2'],i['sap-1'],{'sap-1':{'rating':5}})
        self.assertIn('Саперави Резерв',line);self.assertIn('5',line)

    def test_cold_start_spreads_styles(self):
        items=[(1.0,s,'') for s in CARDS]
        interests={s:self.r.interest(self.r.index[s]) for s in CARDS}
        got=self.r.diversify(items,4,interests=interests,shares=self.r.style_shares())
        self.assertGreaterEqual(len({self.r.interest(self.r.index[s]) for _,s,_ in got}),3)

if __name__=='__main__':unittest.main()
