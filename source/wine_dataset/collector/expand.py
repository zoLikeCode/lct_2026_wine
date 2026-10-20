#!/usr/bin/env python3
"""Additional public catalog adapters. Persistent cache/state shared with collect.py."""
import argparse,base64,json,math,re,urllib.parse,threading
import concurrent.futures as futures
from lxml import html
import collect as c

def nuxt(raw):
    d=html.fromstring(raw)
    nodes=d.xpath('//script[@id="__NUXT_DATA__"]/text()')
    if not nodes:return d,None,None
    a=json.loads(nodes[0]);memo={}
    def de(i):
        if i<0:return None
        if i in memo:return memo[i]
        x=a[i]
        if isinstance(x,dict):
            result={};memo[i]=result
            result.update({k:de(v) for k,v in x.items()})
            return result
        if isinstance(x,list):
            if x and isinstance(x[0],str) and x[0] in ['Reactive','ShallowReactive','Ref','ShallowRef']:
                return de(x[1])
            result=[];memo[i]=result
            result.extend(de(v) if isinstance(v,int) else v for v in x)
            return result
        return x
    return d,a,de

def decoded(x):
    if isinstance(x,dict) and x.get('__encoded'):
        return urllib.parse.unquote(base64.b64decode(x['value']).decode())
    if isinstance(x,dict):return x.get('value','')
    return x

def parse_winestyle(raw,url):
    d,a,de=nuxt(raw)
    entry=next((de(i) for i,v in enumerate(a) if isinstance(v,dict) and 'products' in v and 'pagination' in v),None)
    if entry is None:
        entry=next((de(i) for i,v in enumerate(a) if isinstance(v,dict) and 'products' in v),None)
    if not entry:raise ValueError('No catalog products in public page data')
    pagination=next((de(i) for i,v in enumerate(a) if isinstance(v,dict) and 'totalItems' in v and 'perPage' in v),{})
    result=[]
    for p in entry['products']:
        names=[p.get('name',{}).get('value',''),p.get('secondaryName',{}).get('value','')]
        russian=next((s for s in names if re.search('[а-яА-Я]',s)),names[0])
        props={k:' '.join(str(v.get('value','')) for v in val.get('propertiesList',[])) for k,val in p.get('characteristicsMap',[])}
        attrs={'Производитель':props.get('manufacturer',props.get('producer',''))+' '+' '.join(names),
               'Линейка':props.get('brand',''),'Вид вина':props.get('type-and-sugar',''),
               'Градус':props.get('alcohol',''),'Сорт винограда':props.get('grapes',''),
               'Год производства':props.get('vintage',props.get('year',''))}
        primary=d.xpath('//a[contains(@class,"m-catalog-item__image") and contains(@href,"/'+p['code']+'.html")]//img')
        if not primary:continue
        imageurl=c.choose_src(primary[0],url)
        images=[{'url':imageurl,'kind':'bottle','alt':primary[0].get('alt','')}]
        # Resolve only image templates published by the source, at its displayed size.
        origin=urllib.parse.urlsplit(imageurl)
        for n,s in enumerate(p.get('imagesList',[])):
            if n==0:continue
            path=decoded(s['url']).replace('%w','526').replace('%h','640')
            src=urllib.parse.urljoin(origin.scheme+'://'+origin.netloc,path)
            images.append({'url':src,'kind':'unclassified_view','alt':s.get('alt','')})
        result.append({'url':urllib.parse.urljoin(url,'/products/'+p['code']+'.html'),'source':'winestyle.ru',
                       'title':russian,'name_variants':names,'attrs':attrs,'images':images})
    return result,math.ceil(pagination.get('totalItems',len(result))/max(1,pagination.get('perPage',36)))

def store(products):
    with c.LOCK:
        c.DB.executemany('INSERT OR REPLACE INTO products VALUES(?,?,?)',[(p['url'],p['source'],json.dumps(p,ensure_ascii=False)) for p in products]);c.DB.commit()

def parse_cru(raw,url):
    d=html.fromstring(raw);products=[]
    for card in d.xpath('//div[@data-entity="item"]'):
        title=card.xpath('.//a[@class="category-catalog-item__title"]')
        if not title:continue
        attrs={}
        for prop in card.xpath('.//*[contains(@class,"category-catalog-item__property") and not(contains(@class,"property-"))]'):
            k=prop.xpath('.//*[contains(@class,"property-name")]');v=prop.xpath('.//*[contains(@class,"property-value")]')
            if k and v:attrs[c.text(k[0]).rstrip(':')]=' '.join(c.text(x) for x in v)
        images=[]
        main=card.xpath('.//img[@class="category-catalog-item__img"]')
        if main:images.append({'url':c.choose_src(main[0],url),'kind':'bottle','alt':main[0].get('alt','')})
        for img in card.xpath('.//img[@data-galley-item]'):
            alt=img.get('alt','');kind='back_label' if 'контрэтикет' in alt.lower() else ('front_label' if 'этикетка' in alt.lower() else 'unclassified_view')
            if 'пробка' in alt.lower():kind='accessory'
            images.append({'url':urllib.parse.urljoin(url,img.get('data-galley-item')),'kind':kind,'alt':alt})
        attrs['Вид вина']=attrs.get('Цвет','')+' '+attrs.get('Сахар','').split('|')[0]
        attrs['Год производства']=attrs.get('Год','')
        attrs['Производитель']=attrs.get('Производитель',attrs.get('Бренд',''))
        attrs['Линейка']=attrs.get('Коллекция','')
        if images:products.append({'url':urllib.parse.urljoin(url,title[0].get('href')),'source':'cru.ru','title':c.text(title[0]),'attrs':attrs,'images':images})
    pages=[int(x) for u in d.xpath('//a/@href') for x in re.findall(r'PAGEN_1=(\d+)',u)]
    return products,max(pages or [1])

def crawl_cru(base,workers,limit):
    products,total=parse_cru(c.page(base),base);store(products)
    if limit:total=min(total,limit)
    c.log('cru_start',base=base,pages=total)
    def one(n):
        url=base+'?PAGEN_1='+str(n)
        try:
            products,_=parse_cru(c.page(url),url);store(products);return len(products)
        except Exception as e:c.log('cru_error',url=url,error=str(e));return 0
    count=len(products)
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i,n in enumerate(pool.map(one,range(2,total+1)),2):
            count+=n
            if i%20==0 or i==total:c.log('cru_crawl',page=i,pages=total,products=count)

def crawl_rbc(workers=4):
    base='https://wine.rbc.ru/api/v1/wines/?limit=100&offset='
    first=json.loads(c.page(base+'0'));total=math.ceil(first['count']/100)
    def one(n):
        data=json.loads(c.page(base+str(n*100)));products=[]
        for p in data['results']:
            card=p['wine_card'];winery=card.get('manufacturer',{})
            attrs={'Производитель':winery.get('name_given',''),'Вид вина':card.get('color','')+' '+card.get('sugar',''),
                   'Год производства':str(p.get('age') or ''),'Сорт винограда':p.get('grapes_text','')}
            stem='/vino/p/' if card.get('type')=='Тихое' else '/igristoe-vino/p/'
            url='https://wine.rbc.ru'+stem+card['url']+'/#record-'+str(p['id'])
            if p.get('image'):products.append({'url':url,'source':'wine.rbc.ru','title':card.get('name_given',p['name_catalog']),
                'attrs':attrs,'source_record_id':p['id'],'source_api_url':base+str(n*100),
                'images':[{'url':p['image'],'kind':'bottle','alt':card.get('name_given','')}]})
        store(products);return len(products)
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i,n in enumerate(pool.map(one,range(total)),1):
            if i%10==0 or i==total:c.log('rbc_crawl',pages=i,total=total)

def crawl_winestyle(base,workers,limit):
    products,total=parse_winestyle(c.page(base),base);store(products)
    if limit:total=min(total,limit)
    c.log('winestyle_start',base=base,pages=total)
    stopped=threading.Event()
    def one(n):
        if stopped.is_set():return 0
        url=base+'?page='+str(n)
        try:
            products,_=parse_winestyle(c.page(url),url);store(products);return len(products)
        except Exception as e:
            if getattr(e,'code',None) in (401,403,429):stopped.set()
            c.log('winestyle_error',url=url,error=str(e));return 0
    count=len(products)
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i,n in enumerate(pool.map(one,range(2,total+1)),2):
            count+=n
            if i%20==0 or i==total:c.log('winestyle_crawl',page=i,pages=total,products=count)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='https://winestyle.ru/wine/russia/');p.add_argument('--workers',type=int,default=6);p.add_argument('--limit',type=int,default=0);args=p.parse_args()
    c.init(c.DEFAULT_CSV)
    if 'wine.rbc.ru' in args.url:crawl_rbc(args.workers)
    elif 'cru.ru' in args.url:crawl_cru(args.url,args.workers,args.limit)
    else:crawl_winestyle(args.url,args.workers,args.limit)
