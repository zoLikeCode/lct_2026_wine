import unittest
import reference_search as r

class IdentityGuards(unittest.TestCase):
    def target(self,name='Каберне Совиньон',slug='cabernet-2023'):
        return {'Название вина':name,'Slug':slug,'Категория':'Красное','sugar':'сухое','name_tokens':r.c.tokens(name),'winery_tokens':{'тест'}}
    def product(self,title='Каберне Совиньон',year='2023'):
        return {'sha256':'candidate','products':[{'title':title,'attrs':{'Год производства':year,'Вид вина':'Красное сухое'}}]}
    def ref(self):return {'sha256':'ref','title':'Каберне Совиньон'}
    def test_wrong_year_not_accepted(self):
        flags,*_=r.metadata_check(self.product(year='2024'),self.target(),self.ref(),{})
        self.assertIn('metadata_or_vintage_unconfirmed',flags)
    def test_photo_year_overrides_good_page_year(self):
        flags,*_=r.metadata_check(self.product(),self.target(),self.ref(),{'candidate':[{'text':'2024','confidence':1}]})
        self.assertIn('visible_year_conflict',flags)
    def test_reserve_is_not_regular(self):
        flags,*_=r.metadata_check(self.product(title='Каберне Совиньон Резерв'),self.target(),self.ref(),{})
        self.assertIn('metadata_or_vintage_unconfirmed',flags)
    def test_numbered_series(self):
        flags,*_=r.metadata_check(self.product(title='101 оттенок Саперави'),self.target(name='100 оттенков Саперави'),self.ref(),{})
        self.assertIn('series_number_unconfirmed',flags)
    def test_missing_required_year(self):
        flags,*_=r.metadata_check(self.product(year=''),self.target(),self.ref(),{})
        self.assertIn('metadata_or_vintage_unconfirmed',flags)
    def test_matching_details(self):
        flags,*_=r.metadata_check(self.product(),self.target(),self.ref(),{})
        self.assertEqual(flags,[])
    def test_founding_date_not_vintage(self):
        self.assertEqual(r.visible_years([{'text':'Since 1990','confidence':1}]),set())
    def test_vintage_with_label_decoration(self):
        self.assertEqual(r.visible_years([{'text':'- 2022 -','confidence':1}]),{'2022'})
    def test_explicit_grape_conflict(self):
        product=self.product();product['products'][0]['attrs']['Сорт винограда']='Мерло'
        target=self.target();target['Сорт винограда']='Каберне Совиньон'
        flags,*_=r.metadata_check(product,target,self.ref(),{})
        self.assertIn('metadata_or_vintage_unconfirmed',flags)
    def test_canned_variant_requires_confirmation(self):
        flags,*_=r.metadata_check(self.product(title='Каберне Совиньон в банке'),self.target(),self.ref(),{})
        self.assertIn('metadata_or_vintage_unconfirmed',flags)
    def test_blanc_de_blanc_is_not_blanc_de_noir(self):
        flags,*_=r.metadata_check(self.product(title='Блан де Блан Брют Натюр'),self.target(name='Блан де Нуар Брют Натюр'),self.ref(),{})
        self.assertIn('metadata_or_vintage_unconfirmed',flags)

if __name__=='__main__':unittest.main()
