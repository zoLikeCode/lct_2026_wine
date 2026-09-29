#!/usr/bin/env python3
"""Open manufacturer catalog adapters, using product-scoped images only."""
import argparse,concurrent.futures as futures,json,urllib.parse,re
from lxml import html
import collect as c
from expand import store

def fanagoria():
    base='https://www.fanagoria.ru'
    def one(n):
        data=json.loads(c.page(base+'/api/v1/catalog/products?page='+str(n)))['result'];products=[]
        for p in data['data']:
            if p.get('category_name')!='Вино':continue
            img=p.get('image_full') or p.get('image')
            if not img:continue
            url=base+'/collection/'+p['collection']['slug']+'/'+p['slug']
            products.append({'source':'fanagoria.ru','url':url,'title':p['name'],
                'attrs':{'Производитель':'Фанагория','Вид вина':p.get('color','')+' '+p.get('sugar_name',''),'Градус':p.get('alcohol','')},
                'images':[{'url':urllib.parse.urljoin(base,img),'kind':'bottle','alt':p['name']}]})
        store(products);return len(products)
    first=json.loads(c.page(base+'/api/v1/catalog/products?page=1'))['result']
    with futures.ThreadPoolExecutor(max_workers=4) as pool:counts=list(pool.map(one,range(1,first['last_page']+1)))
    c.log('fanagoria_catalog',products=sum(counts))

def vibes():
    base='https://vibes-wine.ru';seed=base+'/silvaner-barrel-2022/'
    raw=c.page(seed);doc=html.fromstring(raw.decode('utf-8'))
    urls={urllib.parse.urljoin(base,u) for u in doc.xpath('//a/@href') if any(k in u for k in ('silvaner','cabernet-franc','pet-nat','chardonnay','vermentino','montepulciano','pinot-grigio')) and u.startswith('/')}
    def one(url):
        try:
            d=html.fromstring(c.page(url).decode('utf-8'));heads=d.xpath('//h1');cards=d.xpath('//div[@class="product-card"]')
            if not heads or not cards:return 0
            title=c.text(heads[0]);body=c.text(cards[0]);images=[];seen=set()
            for im in cards[0].xpath('.//img[contains(@class,"wineimg") and contains(@src,"/upload/iblock/")]'):
                src=urllib.parse.urljoin(base,im.get('src'))
                # Linked gallery JPEGs on these pages depict the vineyard/place, not the bottle.
                if im.getparent().get('data-caption'):continue
                if src in seen:continue
                seen.add(src);images.append({'url':src,'kind':'bottle' if '.png' in src else 'unclassified_view','alt':title})
            attrs={'Производитель':'Vibes','Вид вина':'Белое сухое' if 'БЕЛОЕ' in body and 'СУХОЕ' in body else ''}
            store([{'url':url,'source':'vibes-wine.ru','title':title,'attrs':attrs,'images':images}]);return 1
        except Exception as e:c.log('vibes_page_error',url=url,error=str(e));return 0
    with futures.ThreadPoolExecutor(max_workers=4) as pool:counts=list(pool.map(one,sorted(urls)))
    c.log('vibes_catalog',products=sum(counts))

def soyuz():
    base='https://soyuz-vino.ru';urls={base+'/zelenayadolina',base+'/isoladelsole',base+'/collection-kubanskoeprisma'}
    for seed in [base+'/katalog',base+'/page15075476.html']:
        try:
            doc=html.fromstring(c.page(seed).decode('utf-8'))
            urls|={urllib.parse.urljoin(base,u) for u in doc.xpath('//a/@href') if any(s in u for s in ('collection-','isoladelsole','zelenayadolina','chateaushug','terrapia','stvionnet'))}
        except Exception as e:c.log('soyuz_seed_error',url=seed,error=str(e))
    def one(url):
        try:
            d=html.fromstring(c.page(url).decode('utf-8'));products=[]
            for card in d.xpath('//div[@data-product-lid and contains(@class,"t776__col")]'):
                name=card.xpath('.//*[contains(@class,"js-product-name")]');desc=card.xpath('.//*[contains(@class,"t776__descr")]');images=card.xpath('.//*[@data-original and contains(@class,"js-product-img")]')
                if not name or not images:continue
                title=c.text(name[0]);body=c.text(desc[0]) if desc else title
                quoted=re.findall('[«"]([^»"]+)[»"]',body)
                if quoted:title=quoted[0]
                can=any(url.rstrip('/').endswith(x) for x in ('/zelenayadolina','/isoladelsole','/collection-perle'))
                if can:title+=' в банке'
                attrs={'Производитель':'Союз-Вино','Вид вина':body}
                products.append({'url':url+'#product-'+card.get('data-product-lid'),'source':'soyuz-vino.ru','title':title,'attrs':attrs,
                    'images':[{'url':i.get('data-original'),'kind':'bottle','alt':title} for i in images]})
            store(products);return len(products)
        except Exception as e:c.log('soyuz_page_error',url=url,error=str(e));return 0
    with futures.ThreadPoolExecutor(max_workers=4) as pool:counts=list(pool.map(one,sorted(urls)))
    c.log('soyuz_catalog',pages=len(urls),products=sum(counts))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',choices=['fanagoria','vibes','soyuz']);a=p.parse_args();c.init(c.DEFAULT_CSV)
    if a.source=='fanagoria':fanagoria()
    elif a.source=='vibes':vibes()
    else:soyuz()
