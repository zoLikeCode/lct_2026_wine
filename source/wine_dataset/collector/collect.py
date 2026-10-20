#!/usr/bin/env python3
"""Resumable collection of public wine photos. No credentials or paid APIs.

Source metadata is evidence, not a claim of independent visual verification.
Ambiguous SKU / vintage assignments are quarantined under needs_review.
"""
import argparse
import collections
import concurrent.futures as futures
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from lxml import html
from PIL import Image, ImageOps
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = str(ROOT/'catalog.csv') if (ROOT/'catalog.csv').exists() else '/Users/forthang/Downloads/Датасет/strapi_output0709.csv'
CACHE = ROOT / 'collector' / '.cache'
LOCK = threading.RLock()
NET_LOCK = threading.Lock()
HOST_NEXT = {}
DB = None
CAT = []
BY_SLUG = {}
UA = 'WineDatasetResearch/1.0 (public product image research; limited request rate)'
FRONT_KINDS = frozenset(('catalog_reference', 'bottle', 'front_label'))
FRONT_DECISIONS_PATH = ROOT/'collector'/'front_view_decisions.json'
FRONT_DECISIONS = json.loads(FRONT_DECISIONS_PATH.read_text()) if FRONT_DECISIONS_PATH.exists() else {}

def front_spec(spec):
    """Allow only front views; a visual decision overrides unreliable source captions."""
    decision=FRONT_DECISIONS.get(spec['url'])
    if decision:
        if decision['decision']!='keep':return None
        return {**spec,'kind':decision['kind']}
    caption=(spec.get('alt','')+' '+spec.get('title','')).lower()
    if re.search(r'контр[ -]?этикет|задн(?:яя|ий|юю|ей)\s+(?:сторон|вид|этикет)|back[ _-]?(?:label|view)|пробка|подарочная коробка',caption):return None
    if re.match(r'\s*(?:подарочн(?:ая|ый)\s+)?(?:коробка|упаковка|тубус|капсула|колпачок|горлышко)\b',caption):return None
    return spec if spec.get('kind') in FRONT_KINDS else None

def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def log(event, **data):
    print(json.dumps({'time': now(), 'event': event, **data}, ensure_ascii=False), flush=True)

def norm(s):
    s = unicodedata.normalize('NFKC', str(s)).lower().replace('ё','е')
    translations = {
        'cabernet sauvignon':'каберне совиньон', 'cabernet franc':'каберне фран',
        'sauvignon blanc':'совиньон блан','pinot noir':'пино нуар','pinot blanc':'пино блан',
        'pinot gris':'пино гри','chardonnay':'шардоне','riesling':'рислинг',
        'merlot':'мерло','saperavi':'саперави','aligote':'алиготе','syrah':'сира',
        'viognier':'вионье','brut':'брют','rose':'розе','reserve':'резерв',
        'fanagoria':'фанагория','massandra':'массандра','myskhako':'мысхако',
        'aristov':'аристов','chateau tamagne':'шато тамань','abrau durso':'абрау дюрсо',
        'alma valley':'альма велли','chateau pinot':'шато пино',
        'chateau de talu':'шато де талю','miroir de talu':'мируар де талю',
        'krasnostop':'красностоп','shiraz':'шираз','muscat':'мускат',
        'clairet':'клере','rouge':'руж','blanc':'блан','oenologist':'энолог',
        'reflexion':'рефлексион','uroki frantsuzskogo':'уроки французского',
    }
    for a,b in translations.items():s=s.replace(a,b)
    s=re.sub(r'[^a-zа-я0-9]+',' ',s)
    return ' '.join(s.split())

STOP=set('вино вина винодельня винодельческое имение estate winery wine белое белый красное красный розовое сухое сухой полусухое полусладкое сладкое выдержанное выдержанный игристое защищенного географического указания сортовое столовое л мл г млл урожая год года россия ооо'.split())
def tokens(s):
    s=re.sub(r'\b\d+(?:[.,]\d+)?\s*(?:мл|ml|л|litre)\b',' ',str(s),flags=re.I)
    s=re.sub(r'\b(?:19|20)\d{2}\s*г?\b',' ',s)
    s=norm(s)
    return set(t for t in s.split() if t not in STOP and (len(t)>1 or t.isdigit()))

def vintage(s):
    return set(re.findall(r'\b(?:19|20)\d{2}\b', str(s)))

def init(csv_path):
    global DB,CAT,BY_SLUG
    ROOT.mkdir(parents=True,exist_ok=True);CACHE.mkdir(parents=True,exist_ok=True)
    for d in ['images','needs_review']: (ROOT/d).mkdir(exist_ok=True)
    with open(csv_path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);fields=reader.fieldnames;CAT=list({r['Slug']:r for r in reader}.values())
    if 'portal_url' not in fields:fields.append('portal_url')
    BY_SLUG={r['Slug']:r for r in CAT}
    for r in CAT:
        slug=r['Slug']
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',slug):raise ValueError('Unsafe slug: '+slug)
        r['portal_url']='https://vino-svoe.ru/wines/'+slug
        (ROOT/'images'/slug).mkdir(exist_ok=True)
    write_csv(ROOT/'catalog.csv',CAT,fields)
    DB=sqlite3.connect(ROOT/'collector'/'state.sqlite3',check_same_thread=False,timeout=30)
    DB.row_factory=sqlite3.Row
    DB.executescript('''
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY,status TEXT,cache_path TEXT,error TEXT,updated TEXT);
      CREATE TABLE IF NOT EXISTS products(url TEXT PRIMARY KEY,source TEXT,data TEXT);
      CREATE TABLE IF NOT EXISTS profiles(slug TEXT PRIMARY KEY,data TEXT);
      CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,slug TEXT,source TEXT,page_url TEXT,image_url TEXT,
        title TEXT,kind TEXT,status TEXT,reason TEXT,path TEXT,sha256 TEXT,pixel_sha256 TEXT,phash TEXT,
        width INTEGER,height INTEGER,bytes INTEGER,vintage TEXT,validation TEXT,downloaded_at TEXT);
      CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,slug TEXT,image_url TEXT,status TEXT,error TEXT,updated TEXT);
    ''');DB.commit()

def write_csv(path, rows, fields):
    temp=path.with_suffix(path.suffix+'.'+str(os.getpid())+'.tmp')
    with temp.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    temp.replace(path)

def throttle(url):
    host=urllib.parse.urlsplit(url).netloc
    # A maximum of two starts per second on each origin.
    with NET_LOCK:
        t=time.monotonic();ready=max(t,HOST_NEXT.get(host,0));HOST_NEXT[host]=ready+0.5
    if ready>t:time.sleep(ready-t)

def request(url, max_bytes=22000000):
    url=urllib.parse.quote(url,safe=':/?&=%+@,;[]!~*\'()')
    for attempt in range(3):
        throttle(url)
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
            with urllib.request.urlopen(req,timeout=25) as response:
                if int(response.headers.get('Content-Length','0'))>max_bytes:raise ValueError('Response too large')
                raw=response.read(max_bytes+1)
                if len(raw)>max_bytes:raise ValueError('Response too large')
                return raw,response.geturl(),response.headers.get('Content-Type','')
        except urllib.error.HTTPError as e:
            if e.code in (401,403,404,410):raise
            if attempt==2:raise
            time.sleep(min(float(e.headers.get('Retry-After','3')) if e.headers.get('Retry-After','').isdigit() else 3*(attempt+1),30))
        except Exception:
            if attempt==2:raise
            time.sleep(2*(attempt+1))

def page(url):
    with LOCK:r=DB.execute('SELECT * FROM pages WHERE url=?',(url,)).fetchone()
    if r and r['status']=='ok' and Path(r['cache_path']).exists():return Path(r['cache_path']).read_bytes()
    try:
        raw,final,ct=request(url,max_bytes=12000000)
        path=CACHE/(hashlib.sha256(url.encode()).hexdigest()+'.html');path.write_bytes(raw)
        with LOCK:DB.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?)',(url,'ok',str(path),'',now()));DB.commit()
        return raw
    except Exception as e:
        with LOCK:DB.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?)',(url,'error','',str(e),now()));DB.commit()
        raise

def text(el):return ' '.join(el.text_content().split())

def choose_src(img,base):
    candidates=[]
    for part in img.get('srcset','').split(','):
        v=part.strip().split()
        if v:
            size=float(re.sub('[^0-9.]','',v[-1]) or 1) if len(v)>1 else 1
            candidates.append((size,v[0]))
    if candidates:return urllib.parse.urljoin(base,max(candidates)[1])
    return urllib.parse.urljoin(base,img.get('data-src') or img.get('src') or '')

def parse_svoe(raw,url,slug):
    d=html.fromstring(raw)
    titles=d.xpath('//h1');title=text(titles[0]) if titles else ''
    if not title:raise ValueError('No wine h1')
    imgs=d.xpath('//img[contains(@class,"wine-hero-block__bottle")]')
    if not imgs:imgs=d.xpath('//img[contains(@class,"wine-mobile-hero-block__bottle")]')
    seen=set();images=[]
    for i in imgs:
        src=choose_src(i,url)
        # Same Strapi asset in mobile and desktop sizes is one photograph.
        key=src.split('/uploads/')[-1]
        if not src or key in seen:continue
        seen.add(key);images.append({'url':src,'kind':'catalog_reference','alt':i.get('alt','')})
    if not images:raise ValueError('No primary product image')
    primary=d.xpath('//*[contains(@class,"wine-detail-info") or contains(@class,"wine-hero-block")]')
    body=' '.join(text(e) for e in primary)[:30000]
    return {'url':url,'source':'vino-svoe.ru','title':title,'slug':slug,'text':body,'images':images}

def parse_cigar(raw,url):
    d=html.fromstring(raw);out=[]
    for card in d.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," product-list__item ")]'):
        names=card.xpath('.//a[contains(@class,"product-list__name")]')
        if not names:continue
        title=text(names[0]);product_url=urllib.parse.urljoin(url,names[0].get('href',''))
        attrs={}
        for row in card.xpath('.//div[@class="product-params__row"]'):
            k=row.xpath('./div[@class="product-params__field"]');v=row.xpath('./div[@class="product-params__field-content"]')
            if k and v:attrs[text(k[0])]=text(v[0])
        images=[];seen=set()
        for el in card.xpath('.//*[@data-galley-item]'):
            src=urllib.parse.urljoin(url,el.get('data-galley-item'));alt=el.get('alt') or el.get('title') or title
            if src in seen:continue
            seen.add(src)
            kind='back_label' if 'контрэтикет' in alt.lower() else ('front_label' if 'этикетка' in alt.lower() else 'bottle')
            if any(s in alt.lower() for s in ['линейка вин','в магазине','ассортимент','коллекция вин']):kind='group_photo'
            images.append({'url':src,'kind':kind,'alt':alt})
        if not images:
            for img in card.xpath('.//img[@itemprop="image"]')[:1]:images.append({'url':choose_src(img,url),'kind':'bottle','alt':img.get('alt','')})
        if images:out.append({'url':product_url,'source':'cigarpro.ru','title':title,'attrs':attrs,'images':images})
    pages=[int(v) for a in d.xpath('//a/@href') for v in re.findall(r'[?&]page=(\d+)',a)]
    return out,max(pages or [1])

def image_hash(im):
    # Two grayscale difference hashes (whole frame and nonwhite foreground).
    rgba=im.convert('RGBA');background=Image.new('RGBA',rgba.size,'white');background.alpha_composite(rgba)
    rgb=background.convert('RGB');arr=np.asarray(rgb)
    mask=np.any(arr<240,axis=2)
    yy,xx=np.where(mask)
    if len(xx)>50:rgb=rgb.crop((int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1))
    small=np.asarray(rgb.convert('L').resize((17,16)))
    bits=small[:,1:]>small[:,:-1]
    value=0
    for b in bits.ravel():value=(value<<1)|int(b)
    return f'{value:064x}'

def download_image(slug, product, spec, status='accepted', reason='source_metadata_match'):
    src=spec['url'];ident=hashlib.sha256((slug+'\n'+src).encode()).hexdigest()
    spec=front_spec(spec)
    if spec is None:
        with LOCK:
            DB.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',(ident,slug,src,'excluded_non_front','front_only_dataset_policy',now()));DB.commit()
        return 'excluded_non_front'
    with LOCK:
        existing=DB.execute('SELECT * FROM images WHERE id=?',(ident,)).fetchone()
        attempt=DB.execute('SELECT * FROM attempts WHERE id=?',(ident,)).fetchone()
    if existing and (ROOT/existing['path']).exists():return 'existing'
    if attempt and attempt['status'] in ('duplicate','rejected','excluded_non_front'):return attempt['status']
    try:
        with LOCK:cached=DB.execute('SELECT path FROM images WHERE image_url=? LIMIT 1',(src,)).fetchone()
        if cached and (ROOT/cached['path']).exists():raw=(ROOT/cached['path']).read_bytes()
        else:raw,final,ct=request(src)
        with Image.open(io.BytesIO(raw)) as source:
            source.load();fmt=(source.format or '').upper();im=ImageOps.exif_transpose(source)
            w,h=im.size
            if max(w,h)<480 or min(w,h)<70:raise ValueError(f'Image too small: {w}x{h}')
            if fmt not in ['JPEG','PNG','WEBP']:raise ValueError('Unsupported image format '+fmt)
            canonical=im.convert('RGBA');sha=hashlib.sha256(raw).hexdigest()
            pixel=hashlib.sha256(str(im.size).encode()+canonical.tobytes()).hexdigest();ph=image_hash(im)
        with LOCK:
            previous=DB.execute('SELECT * FROM images WHERE slug=?',(slug,)).fetchall()
            for prev in previous:
                if prev['sha256']==sha or prev['pixel_sha256']==pixel:
                    DB.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',(ident,slug,src,'duplicate','Same pixels as '+prev['id'],now()));DB.commit();return 'duplicate'
                # Conservative near-duplicate check: same role, near-identical hash and aspect.
                if prev['kind']==spec['kind'] and abs(w/h-prev['width']/prev['height'])<0.025 and (int(ph,16)^int(prev['phash'],16)).bit_count()<=2:
                    DB.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',(ident,slug,src,'duplicate','Near-duplicate '+prev['id'],now()));DB.commit();return 'duplicate'
            cross=DB.execute('SELECT * FROM images WHERE pixel_sha256=? AND slug<>?',(pixel,slug)).fetchall()
            if cross:
                status='needs_review';reason+='; shared_image_across_slugs'
                for old in cross:
                    if old['status']!='needs_review':
                        oldpath=ROOT/old['path'];newpath=ROOT/'needs_review'/old['slug']/oldpath.name;newpath.parent.mkdir(parents=True,exist_ok=True)
                        if oldpath.exists():oldpath.replace(newpath)
                        DB.execute('UPDATE images SET path=?,status=?,reason=? WHERE id=?',(str(newpath.relative_to(ROOT)),'needs_review',old['reason']+'; shared_image_across_slugs',old['id']))
            folder='needs_review' if status=='needs_review' else 'images'
            ext={'JPEG':'jpg','PNG':'png','WEBP':'webp'}[fmt]
            dest=ROOT/folder/slug/(sha[:16]+'_'+spec['kind']+'.'+ext);dest.parent.mkdir(parents=True,exist_ok=True)
            temp=dest.with_suffix(dest.suffix+'.part');temp.write_bytes(raw);temp.replace(dest)
            row=(ident,slug,product['source'],product['url'],src,product['title'],spec['kind'],status,reason,str(dest.relative_to(ROOT)),sha,pixel,ph,w,h,len(raw),','.join(sorted(vintage(product['title']+' '+product.get('attrs',{}).get('Год производства','')))),'publisher_slug_link' if product['source']=='vino-svoe.ru' else 'automated_metadata_matching',now())
            DB.execute('INSERT OR REPLACE INTO images VALUES('+','.join('?'*len(row))+')',row)
            DB.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',(ident,slug,src,'downloaded','',now()));DB.commit()
        return status
    except Exception as e:
        status='rejected' if isinstance(e,ValueError) else 'error'
        with LOCK:DB.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',(ident,slug,src,status,str(e),now()));DB.commit()
        return status

def svoe_one(r):
    slug=r['Slug'];url='https://vino-svoe.ru/wines/'+slug
    try:
        product=parse_svoe(page(url),url,slug)
        with LOCK:
            DB.execute('INSERT OR REPLACE INTO profiles VALUES(?,?)',(slug,json.dumps(product,ensure_ascii=False)));DB.commit()
        nt=tokens(product['title']);ct=tokens(r['Название вина'])
        match=bool(nt&ct) or norm(product['title'])==norm(r['Название вина'])
        state='accepted' if match else 'needs_review'
        return slug,[download_image(slug,product,s,state,'exact_catalog_slug' if match else 'catalog_title_changed') for s in product['images']]
    except Exception as e:return slug,['page_error: '+str(e)]

def run_svoe(limit=0,workers=8):
    rows=CAT
    if limit:
        # Pilot samples different wineries, including all 13 repeated image-name groups.
        chosen=[];win=set();by_image=collections.defaultdict(list)
        for r in rows:by_image[r['Название фото']].append(r)
        for r in rows:
            if r['Винодельня'] not in win:chosen.append(r);win.add(r['Винодельня'])
            if len(chosen)>=max(limit-6,1):break
        for group in by_image.values():
            if len(group)>1:
                for r in group:
                    if r not in chosen:chosen.append(r)
                    if len(chosen)>=limit:break
            if len(chosen)>=limit:break
        rows=chosen[:limit]
    counts=collections.Counter()
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i,(slug,result) in enumerate(pool.map(svoe_one,rows),1):
            counts.update(result)
            if i%25==0 or i==len(rows):log('svoe_progress',done=i,total=len(rows),counts=dict(counts));export()

def crawl_cigar(workers=6):
    base='https://www.cigarpro.ru/drinks/wine/russian-wines/'
    first,total=parse_cigar(page(base),base)
    def one(n):
        url=base if n==1 else base+'?page='+str(n)
        try:
            products,_=parse_cigar(page(url),url)
            with LOCK:
                DB.executemany('INSERT OR REPLACE INTO products VALUES(?,?,?)',[(p['url'],p['source'],json.dumps(p,ensure_ascii=False)) for p in products]);DB.commit()
            return len(products)
        except Exception as e:log('cigar_page_error',page=n,error=str(e));return 0
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        num=0
        for i,n in enumerate(pool.map(one,range(1,total+1)),1):
            num+=n
            if i%10==0 or i==total:log('cigar_crawl',pages=i,total_pages=total,products=num)

WINERY_ALIASES={
    'Кубань-Вино':['кубань вино','шато тамань','аристов','высокий берег','chateau tamagne','kuban vino'],
    'Золотая Балка':['золотая балка','балаклава','zb wine','зб вайн'],
    'Валерий Захарьин':['валерий захарьин','захарьин','дом захарьиных'],
    'Винодельня Бюрнье':['бюрнье','burnier'],
    'Винодельня Братьев Мельниковых':['братьев мельниковых','мельниковы'],
    'Дербентский завод игристых вин':['дербентский завод игристых вин','ди каспико','дербент'],
    'Дербентская винодельческая компания':['дербентская винодельческая компания','дербент вино','дербент'],
    'Шато Пино':['шато пино','chateau pinot'],
    'Абрау-Дюрсо':['абрау дюрсо','abrau durso'],
    'Новый Свет. Дом шампанских вин':['новый свет'],
    'Поместье Голубицкое':['голубицкое','golubitskoe'],
    'Усадьба Дивноморское':['дивноморское','divnomorskoe'],
    'Усадьба Маркотх':['маркотх','markotkh'],
    'Вина Арпачина':['арпачин','arpachin'],
    'Виноградники Гай-Кодзора':['гай кодзор','gai kodzor'],
    'Имение Сикоры':['сикоры','sikory'],
    'Mantra Estate':['mantra','мантра'],
    'Château Le Grand Vostock':['le grand vostock','ле гран восток'],
    'Шато АЛВИСА':['алвиса','alvisa','cantiani','кантиани'],
    'ЗМВ Коктебель':['коктебель'],
}

def sugar(s):
    n=norm(s)
    for val in ['экстра брют','полусладкое','полусухое','сладкое','сухое','брют']:
        if val in n:return val
    return ''

def build_matcher():
    profiles={r['slug']:json.loads(r['data']) for r in DB.execute('SELECT * FROM profiles')}
    out=[]
    for r in CAT:
        winery=r['Винодельня'];profile=profiles.get(r['Slug'],{})
        meta=profile.get('metadata',{})
        aliases=WINERY_ALIASES.get(winery,[winery])
        nt=tokens(r['Название вина']);wt=tokens(winery)
        aliases=list(set([norm(a) for a in aliases]+[norm(winery)]))
        data={**r,'name_tokens':nt,'winery_tokens':wt,'aliases':aliases,'year':vintage(r['Название вина']+' '+r['Slug']),
              'sugar':sugar(r['Название вина']+' '+r['Slug'].replace('-',' ').replace('polusuhoe','полусухое').replace('polusladkoe','полусладкое').replace('sladkoe','сладкое').replace('suhoe','сухое').replace('bryut','брют'))}
        if not data['sugar']:data['sugar']=sugar(meta.get('category',{}).get('name',''))
        data['alcohol']=meta.get('alcohol')
        out.append(data)
    return out

def match_product(p,targets):
    title=p['title'];attrs=p.get('attrs',{});context=norm(title+' '+attrs.get('Производитель','')+' '+attrs.get('Линейка',''))
    # Some retailers repeat the complete name first in English, then in Russian.
    russian_name=re.split(r'\bВино\s+',title,maxsplit=1)
    scoring_title=russian_name[-1] if len(russian_name)>1 and re.search('[a-zA-Z]',russian_name[0]) else title
    pt=tokens(scoring_title+' '+attrs.get('Линейка',''));py=vintage(title+' '+attrs.get('Год производства',''))
    cat=norm(attrs.get('Вид вина',''));ps=sugar(attrs.get('Вид вина','')+' '+title)
    candidates=[]
    for r in targets:
        # Producer evidence must be present before fuzzy name scoring.
        producer=any((' '+a+' ') in (' '+context+' ') for a in r['aliases'] if a)
        if not producer:continue
        nt=r['name_tokens']-r['winery_tokens']
        if not nt:nt=r['name_tokens']
        if not nt:continue
        overlap=nt&pt;coverage=len(overlap)/len(nt)
        if coverage<0.65:continue
        if r['year'] and py and r['year']!=py:continue
        rc=norm(r['Категория'])
        if cat and rc not in cat:continue
        if r['sugar'] and ps and r['sugar']!=ps:continue
        extra=pt-nt-r['winery_tokens']-tokens(' '.join(r['aliases']))
        score=coverage-0.025*len(extra)
        if r['year'] and py==r['year']:score+=0.04
        pa=re.findall(r'\d+(?:[.,]\d+)?',attrs.get('Градус',''))
        alcohol_mismatch=False
        if r.get('alcohol') and len(pa)==1:
            try:alcohol_mismatch=abs(float(r['alcohol'])-float(pa[0].replace(',','.')))>0.2
            except (ValueError,TypeError):pass
        candidates.append((score,r,coverage,extra,alcohol_mismatch))
    candidates.sort(key=lambda x:x[0],reverse=True)
    if not candidates:return []
    best=candidates[0];gap=best[0]-(candidates[1][0] if len(candidates)>1 else 0)
    results=[]
    for score,r,coverage,extra,alcohol_mismatch in candidates[:3]:
        if best[0]-score>0.08:continue
        certain=coverage==1 and not extra and not alcohol_mismatch and score>=0.88 and gap>=0.075 and (not r['year'] or py==r['year'])
        why=f'producer_and_name; token_coverage={coverage:.3f}; score={score:.3f}; margin={gap:.3f}'
        if r['year'] and not py:why+='; vintage_unconfirmed'
        if gap<0.075:why+='; multiple_catalog_candidates'
        if extra:why+='; extra_name_tokens='+','.join(sorted(extra))
        if alcohol_mismatch:why+='; alcohol_differs_from_catalog'
        if not certain:why+='; manual_review_required'
        results.append((r['Slug'],'accepted' if certain else 'needs_review',why))
        if certain:break
    return results

def run_retail(workers=8,limit=0,source=''):
    targets=build_matcher();products=[json.loads(r['data']) for r in DB.execute('SELECT * FROM products')]
    if source:products=[p for p in products if p['source']==source]
    jobs=[];mapping=[];per_slug=collections.Counter()
    for p in products:
        for slug,status,reason in match_product(p,targets):
            mapping.append({'slug':slug,'title':p['title'],'url':p['url'],'status':status,'reason':reason})
            for spec in p['images']:
                if front_spec(spec) is None:continue
                if per_slug[slug]>=25:break
                jobs.append((slug,p,spec,status,reason));per_slug[slug]+=1
    write_csv(ROOT/'collector'/('source_matches'+('_'+source if source else '')+'.csv'),mapping,['slug','title','url','status','reason'])
    if limit:jobs=jobs[:limit]
    log('retail_queue',products=len(products),matched_products=len(mapping),slugs=len(per_slug),images=len(jobs))
    counts=collections.Counter()
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i,result in enumerate(pool.map(lambda j:download_image(*j),jobs),1):
            counts[result]+=1
            if i%50==0 or i==len(jobs):log('retail_progress',done=i,total=len(jobs),counts=dict(counts));export()

def export():
    with LOCK:
        rows=[dict(r) for r in DB.execute('SELECT * FROM images ORDER BY slug,source,id')]
        attempts=[dict(r) for r in DB.execute('SELECT * FROM attempts')]
    fields=['id','slug','path','status','kind','source','page_url','image_url','title','vintage','reason','validation','width','height','bytes','sha256','pixel_sha256','phash','downloaded_at']
    write_csv(ROOT/'manifest.csv',rows,fields)
    count=collections.defaultdict(collections.Counter)
    for r in rows:
        if r['kind'] not in FRONT_KINDS:raise ValueError('Run front_only.py before exporting legacy non-front images')
        key='review' if r['status']=='needs_review' else 'accepted'
        count[r['slug']][key]+=1
        if r['source']!='vino-svoe.ru' and key=='accepted':count[r['slug']]['additional']+=1
    errs=collections.defaultdict(list)
    for r in attempts:
        if r['status']=='error':errs[r['slug']].append(r['error'])
    cover=[]
    page_errors={r['url']:r['error'] for r in DB.execute('SELECT url,error FROM pages WHERE status="error"')}
    for r in CAT:
        c=count[r['Slug']];reason='target_not_reached' if c['accepted']<5 else ''
        if not c['accepted']:reason='no_confirmed_image'+('; candidates_require_review' if c['review'] else '')
        if errs[r['Slug']]:reason+='; download_errors'
        pe=page_errors.get('https://vino-svoe.ru/wines/'+r['Slug'])
        if pe:reason+='; publisher_page='+pe
        cover.append({'slug':r['Slug'],'name':r['Название вина'],'winery':r['Винодельня'],'accepted':c['accepted'],'additional_internet':c['additional'],'needs_review':c['review'],'target':5,'note':reason,'portal_url':'https://vino-svoe.ru/wines/'+r['Slug']})
    coverage_fields=['slug','name','winery','accepted','additional_internet','needs_review','target','note','portal_url']
    coverage_path=ROOT/'coverage.csv'
    if coverage_path.exists():
        with coverage_path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            extra=[k for k in (reader.fieldnames or []) if k not in coverage_fields]
            previous={r['slug']:r for r in reader} if extra else {}
        for r in cover:
            for k in extra:r[k]=previous.get(r['slug'],{}).get(k,'')
        coverage_fields+=extra
    write_csv(coverage_path,cover,coverage_fields)
    stats={'view_policy':'front_only','catalog_positions':len(CAT),'downloaded_files':len(rows),'accepted':sum(c['accepted'] for c in cover),'additional_internet':sum(c['additional_internet'] for c in cover),'needs_review':sum(c['needs_review'] for c in cover),'coverage':{'0':sum(c['accepted']==0 for c in cover),'1':sum(c['accepted']==1 for c in cover),'2-4':sum(2<=c['accepted']<=4 for c in cover),'5+':sum(c['accepted']>=5 for c in cover)},'bytes':sum(r['bytes'] for r in rows),'updated_at':now()}
    temp=ROOT/('summary.json.'+str(os.getpid())+'.tmp');temp.write_text(json.dumps(stats,ensure_ascii=False,indent=2));temp.replace(ROOT/'summary.json')
    write_csv(ROOT/'download_attempts.csv',attempts,['id','slug','image_url','status','error','updated'])
    return stats

def verify():
    errors=[];rows=[dict(r) for r in DB.execute('SELECT * FROM images')]
    def check(r):
        p=ROOT/r['path']
        try:
            if r['kind'] not in FRONT_KINDS:raise ValueError('Non-front image role')
            if front_spec({'url':r['image_url'],'kind':r['kind']}) is None:raise ValueError('Image excluded by front-view policy')
            if Path(r['path']).parts[0] not in ('images','needs_review'):raise ValueError('Invalid image folder')
            raw=p.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=r['sha256']:raise ValueError('checksum mismatch')
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
                if ImageOps.exif_transpose(im).size!=(r['width'],r['height']):raise ValueError('Image dimensions differ from manifest')
        except Exception as e:return {'path':r['path'],'error':str(e)}
    with futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i,error in enumerate(pool.map(check,rows),1):
            if error:errors.append(error)
            if i%1000==0:log('verify_progress',checked=i,total=len(rows),errors=len(errors))
    actual={str(p.relative_to(ROOT)) for base in ('images','needs_review','auxiliary') for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix.lower() in ('.jpg','.jpeg','.png','.webp')}
    registered={r['path'] for r in rows}
    report={'view_policy':'front_only','checked_files':len(rows),'errors':errors,'unregistered_image_files':sorted(actual-registered),'image_directories':sum(p.is_dir() for p in (ROOT/'images').iterdir()),'expected_directories':len(CAT),'checked_at':now()}
    (ROOT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));log('verified',**report)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['pilot','svoe','cigar-crawl','retail','export','verify']);parser.add_argument('--csv',default=DEFAULT_CSV);parser.add_argument('--workers',type=int,default=8);parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--source',default='')
    args=parser.parse_args();init(args.csv)
    if args.stage=='pilot':run_svoe(args.limit or 30,args.workers)
    elif args.stage=='svoe':run_svoe(args.limit,args.workers)
    elif args.stage=='cigar-crawl':crawl_cigar(args.workers)
    elif args.stage=='retail':run_retail(args.workers,args.limit,args.source)
    elif args.stage=='verify':verify()
    log('summary',**export())

if __name__=='__main__':main()
