import unittest
from unittest.mock import patch
import csv,sqlite3,tempfile
from pathlib import Path
import collect as c

class MatcherTests(unittest.TestCase):
    def target(self,slug,name,year=None):
        return {'Slug':slug,'Название вина':name,'Винодельня':'Фанагория','Категория':'Красное','name_tokens':c.tokens(name),'winery_tokens':c.tokens('Фанагория'),'aliases':['фанагория'],'year':set(year or []),'sugar':'сухое'}
    def test_series_numbers_preserved(self):
        self.assertNotEqual(c.tokens('100 оттенков'),c.tokens('101 оттенок'))
        self.assertIn('1',c.tokens('Blush #1'))
        self.assertNotIn('75',c.tokens('Вино 0.75 л'))
    def test_vintage_rejects_other_year(self):
        p={'title':'Фанагория Саперави 2024 0.75л','attrs':{'Вид вина':'Красное сухое','Год производства':'2024'}}
        targets=[self.target('a','Саперави, 2023',['2023']),self.target('b','Саперави, 2024',['2024'])]
        got=c.match_product(p,targets)
        self.assertEqual(got[0][0],'b');self.assertEqual(got[0][1],'accepted')
    def test_missing_vintage_is_review(self):
        p={'title':'Фанагория Саперави 0.75л','attrs':{'Вид вина':'Красное сухое'}}
        self.assertEqual(c.match_product(p,[self.target('a','Саперави, 2023',['2023'])])[0][1],'needs_review')
    def test_no_producer_no_match(self):
        p={'title':'Массандра Саперави','attrs':{}}
        self.assertEqual(c.match_product(p,[self.target('a','Саперави')]),[])
    def test_equal_candidates_are_review(self):
        p={'title':'Фанагория Саперави','attrs':{}}
        got=c.match_product(p,[self.target('a','Саперави'),self.target('b','Саперави')])
        self.assertTrue(all(r[1]=='needs_review' for r in got))
    def test_other_series_not_accepted(self):
        p={'title':'Фанагория Резерв Саперави','attrs':{'Вид вина':'Красное сухое'}}
        got=c.match_product(p,[self.target('a','Саперави')])
        self.assertEqual(got[0][1],'needs_review')
    def test_conflicting_alcohol_review(self):
        p={'title':'Фанагория Саперави','attrs':{'Вид вина':'Красное сухое','Градус':'14%'}}
        target=self.target('a','Саперави');target['alcohol']=12
        self.assertEqual(c.match_product(p,[target])[0][1],'needs_review')

class FrontOnlyTests(unittest.TestCase):
    def test_unclassified_requires_visual_decision(self):
        spec={'url':'https://example.test/side.jpg','kind':'unclassified_view','alt':'Вино'}
        with patch.object(c,'FRONT_DECISIONS',{}):self.assertIsNone(c.front_spec(spec))
        with patch.object(c,'FRONT_DECISIONS',{spec['url']:{'decision':'keep','kind':'front_label'}}):
            self.assertEqual(c.front_spec(spec)['kind'],'front_label')

    def test_mislabelled_front_and_packaging_are_blocked(self):
        with patch.object(c,'FRONT_DECISIONS',{}):
            for alt in ('Контрэтикетка вина','Коробка Вино Массандра','подарочная упаковка вино рислинг','Back label'):
                self.assertIsNone(c.front_spec({'url':'https://example.test/a.jpg','kind':'bottle','alt':alt}))
            self.assertIsNotNone(c.front_spec({'url':'https://example.test/b.jpg','kind':'bottle','alt':'Вино в подарочной упаковке'}))

    def test_rejected_views_never_access_network_or_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);csvfile=root/'input.csv'
            with csvfile.open('w') as f:
                writer=csv.DictWriter(f,fieldnames=['Slug']);writer.writeheader();writer.writerow({'Slug':'sample'})
            old_db=c.DB;oldcat=c.CAT;oldby=c.BY_SLUG
            try:
                with patch.object(c,'ROOT',root),patch.object(c,'CACHE',root/'collector/.cache'),patch.object(c,'FRONT_DECISIONS',{}),patch.object(c,'request',side_effect=AssertionError('Unexpected network')):
                    c.init(csvfile)
                    for kind in ('back_label','accessory','group_photo','unclassified_view'):
                        spec={'url':'https://example.test/'+kind+'.jpg','kind':kind}
                        for repeat in range(2):self.assertEqual(c.download_image('sample',{},spec),'excluded_non_front')
                    self.assertEqual(c.DB.execute('SELECT count(*) FROM images').fetchone()[0],0)
                    self.assertEqual(c.DB.execute('SELECT count(*) FROM attempts').fetchone()[0],4)
                    self.assertFalse((root/'auxiliary').exists())
                    self.assertFalse(list(root.rglob('*.jpg')))
                    c.DB.close()
            finally:c.DB=old_db;c.CAT=oldcat;c.BY_SLUG=oldby

if __name__=='__main__':unittest.main()
