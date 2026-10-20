import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import test_server
from recsys import Recommender

class SimilarWinesTest(unittest.TestCase):
    setUp=test_server.ServerTest.setUp
    tearDown=test_server.ServerTest.tearDown
    def test_catalog_neighbours_exclude_other_styles_and_same_label(self):
        product=self.app.state.product;base={**product.cards['merlo'],'category':'Красное сухое','grapes':'Мерло','name':'Исходное','winery':'Автор'}
        product.cards={s:{**base,'slug':s,'name':name,'winery':winery,'category':color} for s,name,winery,color in [
            ('source','Исходное','Автор','Красное сухое'),('near','Похожее','Другая винодельня','Красное сухое'),
            ('copy','Исходное','Автор','Красное сухое'),('white','Белое','Другая винодельня','Белое сухое'),
            ('distant','Терпкое','Третья','Красное сухое'),('unknown','Без характеристик','Четвёртая','Красное сухое')]}
        values={'body':2,'tannin':2,'sweetness':1,'acidity':3,'oak':1,'aromas':['red_fruit']}
        profiles={s:dict(values) for s in product.cards};profiles['distant']={a:5 for a in values if a!='aromas'};profiles['unknown']={}
        product.recsys=Recommender(product.cards,profiles=profiles)
        found=product.similar_wines('source')
        self.assertEqual([w['slug'] for w in found],['near'])
        self.assertFalse(found[0]['preferences_available']);self.assertIn('сорт',found[0]['reason'])
        self.assertEqual(product.similar_wines('unknown'),[])
