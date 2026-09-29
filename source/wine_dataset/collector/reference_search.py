#!/usr/bin/env python3
"""Reference-guided extension of the front-label dataset. Resumable public sources."""
import argparse,collections,concurrent.futures as futures,csv,hashlib,io,json,os,re,shutil,sqlite3,sys,time,urllib.parse
from pathlib import Path
import collect as c
from expand import store
from lxml import html
from PIL import Image,ImageOps
import numpy as np

WORK=c.ROOT.parent.parent/'work'/'wine_enrichment'
WORK.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(WORK/'deps'))

def snapshot():
    target=WORK/'before.sqlite3'
    if not target.exists():
        dst=sqlite3.connect(target);c.DB.backup(dst);dst.close()
        for name in ['summary.json','manifest.csv','coverage.csv']:
            shutil.copy2(c.ROOT/name,WORK/('before_'+name))

def probe(urls):
    for url in urls:
        try:
            raw=c.page(url);d=html.fromstring(raw)
            c.log('probe',url=url,bytes=len(raw),title=d.xpath('//title/text()'),images=len(d.xpath('//img')),links=len(d.xpath('//a')))
            path=WORK/(urllib.parse.urlsplit(url).netloc+'.html');path.write_bytes(raw)
        except Exception as e:c.log('probe_error',url=url,error=str(e))

def cv():
    import cv2
    cv2.setNumThreads(1)
    return cv2

def feature(path,key):
    target=WORK/'features'/(key+'.npz');target.parent.mkdir(exist_ok=True)
    if target.exists():
        try:
            with np.load(target) as x:return x['xy'],x['des'],x['shape']
        except (ValueError,OSError,EOFError):pass
    cv2=cv()
    with Image.open(path) as im:
        im=ImageOps.exif_transpose(im).convert('RGBA');bg=Image.new('RGBA',im.size,'white');bg.alpha_composite(im);im=bg.convert('RGB')
        im.thumbnail((1200,1200));arr=np.asarray(im)
    mask=np.any(arr<235,axis=2);yy,xx=np.where(mask)
    if len(xx)>100 and mask.mean()<0.82:arr=arr[max(0,yy.min()-3):yy.max()+4,max(0,xx.min()-3):xx.max()+4]
    h,w=arr.shape[:2]
    # Ignore bottle necks/caps; standalone label crops retain their complete face.
    if h/w>1.8:arr=arr[int(h*.30):int(h*.97)]
    h,w=arr.shape[:2];scale=min(1,800/max(h,w))
    if scale<1:arr=cv2.resize(arr,(round(w*scale),round(h*scale)))
    gray=cv2.cvtColor(arr,cv2.COLOR_RGB2GRAY)
    kp,des=cv2.SIFT_create(nfeatures=650,contrastThreshold=.025).detectAndCompute(gray,None)
    xy=np.asarray([k.pt for k in kp],dtype=np.float32).reshape(-1,2)
    if des is None:des=np.zeros((0,128),np.float32)
    else:des=np.sqrt(des/(des.sum(axis=1,keepdims=True)+1e-7))
    shape=np.array(gray.shape,dtype=np.int32)
    temp=target.with_suffix('.'+str(time.time_ns())+'.tmp')
    with temp.open('wb') as f:np.savez_compressed(f,xy=xy,des=des,shape=shape)
    temp.replace(target)
    return xy,des,shape

def parse_new(raw,url):
    d=html.fromstring(raw.decode('utf-8','replace'));domain=urllib.parse.urlsplit(url).netloc;products=[]
    if domain=='winehelp2.ru':
        for card in d.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," catalog-block__item ")]'):
            title=card.xpath('.//meta[@itemprop="name"]/@content');link=card.xpath('.//link[@itemprop="url"]/@href')
            imgs=card.xpath('.//a[contains(@class,"image-list__link")]//img')
            if not(title and link and imgs):continue
            src=imgs[0].get('data-big') or imgs[0].get('data-src') or imgs[0].get('src')
            if not src:continue
            title=title[0];body=c.text(card)
            types=re.findall(r'\b(?:белое|красное|розовое|оранжевое|полусухое|полусладкое|сухое|сладкое|экстра брют|брют)\b',body.lower())
            attrs={'Производитель':title,'Вид вина':' '.join(dict.fromkeys(types)),'Год производства':','.join(sorted(c.vintage(title)))}
            products.append({'url':urllib.parse.urljoin(url,link[0]),'source':domain,'title':title,'attrs':attrs,'images':[{'url':urllib.parse.urljoin(url,src),'kind':'bottle','alt':title}]})
    elif domain=='winemore.ru':
        for card in d.xpath('//article[contains(@class,"catalog-item--card-wrapper")]'):
            title=card.xpath('.//div[@class="catalog-item--card-title"]');link=card.xpath('.//div[@class="catalog-item--card-title"]/a/@href');imgs=card.xpath('.//img[@data-lazyload-src]')
            if not(title and link and imgs):continue
            name=c.text(title[0]);desc=' '.join(c.text(x) for x in card.xpath('.//div[@class="catalog-item--card-description"]'))
            products.append({'url':urllib.parse.urljoin(url,link[0]),'source':domain,'title':name,'attrs':{'Производитель':name,'Вид вина':desc,'Год производства':','.join(sorted(c.vintage(name)))},'images':[{'url':urllib.parse.urljoin(url,imgs[0].get('data-lazyload-src')),'kind':'bottle','alt':name}]})
    elif domain=='vinela.ru':
        for card in d.xpath('//div[@class="GoodsList"]'):
            imgs=card.xpath('.//div[@class="GoodsListImg"]/@style');links=card.xpath('.//a[@href]');title=card.xpath('.//*[contains(@class,"GoodsListName")]')
            if not(imgs and links):continue
            match=re.search(r'url\(\s*([^\s)]+)',imgs[0]);link=next((x for x in links if x.get('href','').startswith('/item/')),links[0])
            name=link.get('title') or c.text(link);body=' '.join(c.text(x) for x in card.xpath('.//div[@class="GoodsListRus"]'))
            if not(match and name):continue
            products.append({'url':urllib.parse.urljoin(url,link.get('href')),'source':domain,'title':name,'attrs':{'Производитель':body[:500],'Вид вина':body[:700],'Год производства':','.join(sorted(c.vintage(body)))},'images':[{'url':urllib.parse.urljoin(url,match[1]),'kind':'bottle','alt':name}]})
    elif domain=='gordienko-nikolaev.ru':
        for card in d.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," t-rec ")]'):
            imgs=card.xpath('.//img[@data-original and contains(@class,"t106__img")]');head=card.xpath('.//*[contains(@class,"t106__title")]')
            if not(imgs and head):continue
            name=c.text(head[0]);year=re.search(r'\b(2[0-6])\s*$',name)
            name=re.sub(r'\b(2[0-6])\s*$',r'20\1',name)
            body=c.text(card)
            products.append({'url':url+'#'+card.get('id'),'source':domain,'title':name,'attrs':{'Производитель':'А. Гордиенко & М. Николаев','Вид вина':body[:600],'Год производства':'20'+year[1] if year else ''},'images':[{'url':imgs[0].get('data-original'),'kind':'bottle','alt':name}]})
    elif domain=='kuban-vino.ru':
        for card in d.xpath('//a[@class="catalog-item"]'):
            title=card.xpath('.//div[contains(@class,"catalog-item__title")]');imgs=card.xpath('.//img')
            if not(title and imgs):continue
            name=c.text(title[0]);types=' '.join(c.text(e) for e in card.xpath('.//div[contains(@class,"catalog-item__tags")]'))
            products.append({'url':urllib.parse.urljoin(url,card.get('href')),'source':domain,'title':name,'attrs':{'Производитель':'Кубань-Вино','Вид вина':types,'Год производства':','.join(sorted(c.vintage(name)))},'images':[{'url':c.choose_src(imgs[0],url),'kind':'bottle','alt':name}]})
    elif domain=='myskhako.ru':
        imgs=d.xpath('//img[@class="wine-params__wine-img"]')
        if imgs:
            name=d.xpath('//title/text()')[0].strip();body=' '.join(c.text(x) for x in d.xpath('//div[@class="wine-params__right"]'))
            products.append({'url':url,'source':domain,'title':name,'attrs':{'Производитель':'Мысхако','Вид вина':body,'Год производства':','.join(sorted(c.vintage(name)))},'images':[{'url':c.choose_src(imgs[0],url),'kind':'bottle','alt':name}]})
    elif domain=='derbentwine.ru':
        imgs=d.xpath('//div[contains(concat(" ",@class," ")," good-image ")]//img');title=d.xpath('//h1')
        if imgs and title:
            name=c.text(title[0]);body=imgs[0].get('alt','')
            products.append({'url':url,'source':domain,'title':name,'attrs':{'Производитель':'Дербент Вино','Вид вина':body,'Год производства':','.join(sorted(c.vintage(name)))},'images':[{'url':c.choose_src(imgs[0],url),'kind':'bottle','alt':name}]})
    pages=[int(n) for x in d.xpath('//a/@href') for n in re.findall(r'PAGEN_\d+=(\d+)',x)]
    return products,max(pages or [1])

def crawl_new(urls,limit=0):
    for base in urls:
        try:
            first,total=parse_new(c.page(base),base);store(first)
            if limit:total=min(limit,total)
            c.log('new_source_start',url=base,pages=total,first=len(first))
            stopped=__import__('threading').Event()
            def one(n):
                if stopped.is_set():return 0
                param='PAGEN_3' if 'winehelp2.ru' in base else 'PAGEN_1'
                url=base+('?'+param+'='+str(n))
                try:
                    failed=c.DB.execute('SELECT status FROM pages WHERE url=?',(url,)).fetchone()
                    if failed and failed[0]=='error':stopped.set();return 0
                    items,_=parse_new(c.page(url),url);store(items);return len(items)
                except Exception as e:
                    if getattr(e,'code',None) in (401,403,429,503):stopped.set()
                    c.log('new_source_error',url=url,error=str(e));return 0
            count=len(first)
            with futures.ThreadPoolExecutor(max_workers=4) as workers:
                for i,n in enumerate(workers.map(one,range(2,total+1)),2):
                    count+=n
                    if i%10==0 or i==total:c.log('new_source_pages',url=base,done=i,total=total,products=count)
        except Exception as e:c.log('new_source_error',url=base,error=str(e))

def factories():
    crawl_new(['https://kuban-vino.ru/catalog/'])
    for base in ['https://myskhako.ru/','https://derbentwine.ru/catalog/']:
        try:
            d=html.fromstring(c.page(base).decode('utf-8','replace'));domain=urllib.parse.urlsplit(base).netloc
            links={urllib.parse.urljoin(base,s.strip()) for s in d.xpath('//a/@href') if s.startswith('/catalog/')}
            if domain=='myskhako.ru':links={s for s in links if s.endswith('.html')}
            else:
                categories=list(links)
                for url in categories:
                    if len(urllib.parse.urlsplit(url).path.strip('/').split('/'))>2:continue
                    dd=html.fromstring(c.page(url).decode('utf-8','replace'))
                    links|={urllib.parse.urljoin(base,s) for s in dd.xpath('//a/@href') if s.startswith('/catalog/') and len(s.strip('/').split('/'))>2}
                links={s for s in links if len(urllib.parse.urlsplit(s).path.strip('/').split('/'))>2}
            def one(url):
                try:
                    products,_=parse_new(c.page(url),url);store(products);return len(products)
                except Exception as e:c.log('factory_error',url=url,error=str(e));return 0
            with futures.ThreadPoolExecutor(max_workers=3) as workers:
                counts=list(workers.map(one,sorted(links)))
            c.log('factory_complete',source=domain,products=sum(counts),pages=len(links))
        except Exception as e:c.log('factory_error',url=base,error=str(e))

def references():
    refs=[]
    issues=c.ROOT/'reference_issues.csv';excluded=set()
    if issues.exists():
        with issues.open(encoding='utf-8-sig',newline='') as f:
            excluded={(r['slug'],Path(r['archive_path']).name) for r in csv.DictReader(f) if r['status']=='not_used_as_reference'}
    for r in c.DB.execute("SELECT * FROM images WHERE source='vino-svoe.ru' ORDER BY slug"):
        r=dict(r)
        if (r['slug'],Path(urllib.parse.urlsplit(r['image_url']).path).name) in excluded:continue
        if (c.ROOT/r['path']).exists():refs.append(r)
    return refs

def feature_refs():
    refs=references()
    def one(r):
        try:feature(c.ROOT/r['path'],r['sha256']);return True
        except Exception as e:c.log('feature_error',id=r['id'],error=str(e));return False
    with futures.ThreadPoolExecutor(max_workers=5) as pool:
        for i,ok in enumerate(pool.map(one,refs),1):
            if i%250==0 or i==len(refs):c.log('reference_features',done=i,total=len(refs))

def recover_refs():
    present={r['slug'] for r in references()};missing=[r for r in c.CAT if r['Slug'] not in present]
    with futures.ThreadPoolExecutor(max_workers=3) as workers:
        for slug,result in workers.map(c.svoe_one,missing):c.log('recover_reference',slug=slug,result=result)
    # Preserve the portal's structured metadata on recovered profiles.
    from audit import profiles
    profiles();c.export()

def archive_refs():
    rows=json.loads((WORK/'archive_targets.json').read_text());log=[]
    for i,r in enumerate(rows):
        asset=Path(r['archive_path']).name
        src='https://api.vino-svoe.ru/v1/img/str-api/1160/1160/resize/uploads/'+asset
        if i in [41,42,45]:
            log.append({**r,'result':'ambiguous_generic_box_without_wine_name','image_url':src});continue
        product={'url':'https://vino-svoe.ru/wines/'+r['slug'],'source':'vino-svoe.ru','title':r['name'],'slug':r['slug'],'images':[{'url':src,'kind':'catalog_reference','alt':r['name']}]}
        result=c.download_image(r['slug'],product,product['images'][0],'accepted','provided_catalog_filename_matches_unique_archive_asset; front_view_visually_checked')
        ident=hashlib.sha256((r['slug']+'\n'+src).encode()).hexdigest()
        c.DB.execute("UPDATE images SET validation='provided_csv_filename_archive_and_public_asset; front_visually_checked' WHERE id=?",(ident,));c.DB.commit()
        if result in ('accepted','existing','needs_review'):
            c.DB.execute('INSERT OR IGNORE INTO profiles VALUES(?,?)',(r['slug'],json.dumps(product,ensure_ascii=False)));c.DB.commit()
        log.append({**r,'result':result,'image_url':src});c.log('archive_reference',slug=r['slug'],result=result)
    c.write_csv(c.ROOT/'recovered_references.csv',log,['slug','name','original_filename','archive_path','image_url','result']);c.export()

def producer_targets(p,targets):
    context=c.norm(p['title']+' '+p.get('attrs',{}).get('Производитель','')+' '+p.get('attrs',{}).get('Линейка',''))
    pt=c.tokens(context)
    out=[]
    for r in targets:
        aliases=r['aliases']
        exact=any((' '+a+' ') in (' '+context+' ') for a in aliases if a)
        unique=r['unique']
        if exact or (unique and len(unique&pt)/len(unique)>=.5):out.extend(r['targets'])
    return out

def pool_download(limit=0,plan=False):
    targets=c.build_matcher();valid={r['slug'] for r in references()}
    targets=[t for t in targets if t['Slug'] in valid]
    ocr=load_ocr();refmap={r['slug']:r for r in references()}
    for t in targets:
        t['expected_year']=t['year'] or visible_years(ocr.get(refmap[t['Slug']]['sha256'],[]))
    generic={'шато','долина','дом','усадьба','поместье','компания','завод','имение','семья','новый','свет','виноградники','вин','винодельческий','русское','российское'}
    groups={}
    for t in targets:
        aliases=t['aliases']+[c.norm(t['Винодельня'])]
        g=groups.setdefault(t['Винодельня'],{'aliases':aliases,'unique':c.tokens(' '.join(aliases))-generic,'targets':[]});g['targets'].append(t)
    targets=list(groups.values())
    existing={r['image_url']:dict(r) for r in c.DB.execute('SELECT * FROM images')}
    known_excluded={r['image_url'] for r in c.DB.execute("SELECT image_url FROM attempts WHERE status IN ('duplicate','rejected','excluded_non_front')")}
    jobs={}
    for rec in c.DB.execute('SELECT * FROM products ORDER BY source,url'):
        p=json.loads(rec['data']);ts=producer_targets(p,targets)
        py=c.vintage(p['title']+' '+p.get('attrs',{}).get('Год производства',''))
        if py:ts=[t for t in ts if not t['expected_year'] or t['expected_year']&py]
        if not ts:continue
        for s in p['images']:
            original=s;s=c.front_spec(s)
            if s is None and original['kind'] in ('unclassified_view','group_photo') and original['url'] not in c.FRONT_DECISIONS:
                # Research cache only. These may enter the dataset only after an
                # explicit saved visual front-view review, even with a strong match.
                s={**original,'kind':'bottle','requires_visual_front_check':True}
            if not s:continue
            if s['url'] in known_excluded and s['url'] not in existing:continue
            x=jobs.setdefault(s['url'],{'key':hashlib.sha256(s['url'].encode()).hexdigest(),'image_url':s['url'],'products':[],'slugs':[],'spec':s})
            x['products'].append(p);x['slugs']=sorted(set(x['slugs'])|{t['Slug'] for t in ts})
    pooldir=WORK/'pool';pooldir.mkdir(exist_ok=True)
    rows=list(jobs.values())
    # Existing candidates first; every source gets covered without a global per-slug cap.
    rows.sort(key=lambda r:(r['image_url'] not in existing,r['products'][0]['source'],r['key']))
    old=[r for r in rows if r['image_url'] in existing];new=collections.defaultdict(collections.deque)
    for r in rows:
        if r['image_url'] not in existing:new[urllib.parse.urlsplit(r['image_url']).netloc].append(r)
    rows=old
    while any(new.values()):
        for q in new.values():
            if q:rows.append(q.popleft())
    if limit:rows=rows[:limit]
    c.log('pool_plan',total=len(rows),existing=sum(r['image_url'] in existing for r in rows),new_by_source=dict(collections.Counter(r['products'][0]['source'] for r in rows if r['image_url'] not in existing)))
    if plan:return
    stopped=set();lock=__import__('threading').Lock()
    def one(r):
        cached=pooldir/(r['key']+'.img');host=urllib.parse.urlsplit(r['image_url']).netloc
        try:
            ex=existing.get(r['image_url'])
            if ex and (c.ROOT/ex['path']).exists():path=c.ROOT/ex['path'];r['sha256']=ex['sha256']
            elif cached.exists():path=cached
            else:
                with lock:
                    if host in stopped:return {**r,'error':'source_stopped_after_access_limit'}
                raw,_,_=c.request(r['image_url']);cached.write_bytes(raw);path=cached
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                r['width'],r['height']=im.size
                if max(im.size)<480 or min(im.size)<70:raise ValueError('image_too_small')
                if (im.format or '').upper() not in ['JPEG','PNG','WEBP']:raise ValueError('unsupported_format')
            r['sha256']=r.get('sha256') or hashlib.sha256(path.read_bytes()).hexdigest()
            r['path']=str(path)
            feature(path,r['sha256'])
            return r
        except Exception as e:
            if getattr(e,'code',None) in (401,403,429):
                with lock:stopped.add(host)
            return {**r,'error':str(e)}
    output=WORK/'pool.jsonl';counts=collections.Counter()
    with output.open('w') as f, futures.ThreadPoolExecutor(max_workers=8) as workers:
        for i,r in enumerate(workers.map(one,rows),1):
            f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush();counts['error' if 'error'in r else 'ok']+=1
            if i%100==0 or i==len(rows):c.log('visual_pool',done=i,total=len(rows),counts=dict(counts))

def geometry(a,b):
    cv2=cv();axy,ad,ashape=a;bxy,bd,bshape=b
    if min(len(ad),len(bd))<8:return {'inliers':0,'matches':0,'ratio':0,'coverage':0}
    matches=cv2.BFMatcher(cv2.NORM_L2).knnMatch(ad,bd,k=2)
    good=[m for m,n in matches if m.distance<.73*n.distance]
    if len(good)<8:return {'inliers':0,'matches':len(good),'ratio':0,'coverage':0}
    src=np.float32([axy[m.queryIdx] for m in good]);dst=np.float32([bxy[m.trainIdx] for m in good])
    h,mask=cv2.findHomography(src,dst,cv2.RANSAC,4.0,maxIters=1500,confidence=.995)
    if mask is None:return {'inliers':0,'matches':len(good),'ratio':0,'coverage':0}
    inlier=mask.ravel().astype(bool);n=int(inlier.sum());points=dst[inlier]
    area=cv2.contourArea(cv2.convexHull(points)) if len(points)>2 else 0
    return {'inliers':n,'matches':len(good),'ratio':round(n/len(good),3),'coverage':round(area/float(bshape[0]*bshape[1]),4)}

def match_pool(limit=0):
    cv2=cv();refs=references();rf={r['slug']:(r,feature(c.ROOT/r['path'],r['sha256'])) for r in refs}
    lines=(WORK/'pool.jsonl').read_text().splitlines();pool=[]
    for i,line in enumerate(lines):
        try:p=json.loads(line)
        except ValueError:
            if i==len(lines)-1:break
            raise
        if 'error' not in p:pool.append(p)
    if limit:pool=pool[:limit]
    # Per producer candidate-set ANN indexes propose matches. RANSAC verifies exact label details.
    indexes={};cachelock=__import__('threading').Lock()
    cache=WORK/'rank_cache';cache.mkdir(exist_ok=True)
    def one(p):
        slugs=tuple(s for s in p['slugs'] if s in rf)
        signature=hashlib.sha256(('v1'+p['sha256']+json.dumps(p['products'],sort_keys=True)+'|'.join(s+rf[s][0]['sha256'] for s in slugs)).encode()).hexdigest()
        cached=cache/(signature+'.json')
        if cached.exists():return {**p,'ranked':json.loads(cached.read_text())}
        a=feature(p['path'],p['sha256'])
        if not slugs or len(a[1])<8:return {**p,'ranked':[]}
        with cachelock:
            if slugs not in indexes:
                matcher=cv2.FlannBasedMatcher(dict(algorithm=1,trees=4),dict(checks=40))
                arrays=[rf[s][1][1] for s in slugs];usable=[i for i,v in enumerate(arrays) if len(v)>0]
                if not usable:return {**p,'ranked':[]}
                matcher.add([arrays[i] for i in usable]);matcher.train();indexes[slugs]=(matcher,usable,__import__('threading').Lock())
            matcher,usable,mlock=indexes[slugs]
        with mlock:nearest=matcher.knnMatch(a[1],k=2)
        votes=collections.Counter()
        for pair in nearest:
            if len(pair)==2:
                m,n=pair
                if m.distance<.86*n.distance:votes[slugs[usable[m.imgIdx]]]+=1
        proposals=[s for s,_ in votes.most_common(5)]
        # Include metadata's exact proposals to avoid dropping near-identical label families.
        for prod in p['products']:
            for s,_,_ in c.match_product(prod,[targetmap[s] for s in slugs]):
                if s in rf and s not in proposals:proposals.append(s)
        scores=[{'slug':s,**geometry(a,rf[s][1])} for s in proposals[:8]]
        scores.sort(key=lambda x:(x['inliers'],x['coverage']),reverse=True)
        cached.write_text(json.dumps(scores[:4]))
        return {**p,'ranked':scores[:4]}
    targets=c.build_matcher();targetmap={r['Slug']:r for r in targets};dest=WORK/'ranked.jsonl'
    with dest.open('w') as f,futures.ThreadPoolExecutor(max_workers=5) as workers:
        for i,r in enumerate(workers.map(one,pool),1):
            f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush()
            if i%100==0 or i==len(pool):c.log('visual_rank',done=i,total=len(pool),indexes=len(indexes))

def load_ocr():
    byid={r['id']:r['sha256'] for r in c.DB.execute('SELECT id,sha256 FROM images')};ocr={}
    for path in [c.ROOT/'collector'/'ocr_annotations.jsonl',*sorted(WORK.glob('new_ocr*.jsonl'))]:
        if not path.exists():continue
        for line in path.read_text().splitlines():
            try:
                r=json.loads(line)
                if r.get('lines'):ocr[byid.get(r['id'],r['id'])]=r['lines']
            except ValueError:continue
    return ocr

def ocr_input():
    ocr=load_ocr();todo={}
    for ref in references():
        if ref['sha256'] not in ocr:todo[ref['sha256']]={'id':ref['sha256'],'path':str(c.ROOT/ref['path'])}
    for line in (WORK/'ranked.jsonl').read_text().splitlines():
        p=json.loads(line)
        if not p['ranked'] or p['ranked'][0]['inliers']<20:continue
        if p['sha256'] not in ocr:todo[p['sha256']]={'id':p['sha256'],'path':p['path']}
    (WORK/'ocr_input.json').write_text(json.dumps(list(todo.values()),ensure_ascii=False))
    c.log('ocr_input',count=len(todo),reused=len(ocr))

def visible_years(lines):
    years=set()
    for x in lines:
        t=x['text'].strip().strip(' -–—·•.,:;()[]«»')
        if x.get('confidence',0)<.5:continue
        if re.search(r'(основан|since|est\.?|founded|с\s+19|более|сенной|столет)',t,re.I):continue
        if re.fullmatch(r'(?:19[89]\d|20[012]\d)(?:\s*г\.?)?',t) or re.search(r'(урожай|vintage)\s*(?:19|20)\d{2}',t,re.I):years|=c.vintage(t)
    return years

def grape_terms(value):
    n=c.norm(value).replace('шираз','сира').replace('гриджо','гри').replace('гриджио','гри')
    known={'каберне','совиньон','фран','мерло','саперави','шардоне','рислинг','рисланер','пино','нуар','гри','блан','сира','вионье','ркацители','сибирьковый','алиготе','коломбар','кокур','марселан','красностоп','мускат','цимлянский','гевюрцтраминер','траминер','албариньо','мальбек','санджовезе','темпранильо','глера','арени','пти','вердо'}
    return set(n.split())&known

def metadata_check(p,target,ref,ocr):
    expected=c.vintage(target['Название вина']+' '+target['Slug']) or visible_years(ocr.get(ref['sha256'],[]))
    actual=visible_years(ocr.get(p['sha256'],[]));flags=[]
    if expected and actual and not(expected&actual):flags.append('visible_year_conflict')
    txt=' '.join(x['text'] for x in ocr.get(p['sha256'],[]) if x.get('confidence',0)>=.4)
    reftext=' '.join(x['text'] for x in ocr.get(ref['sha256'],[]) if x.get('confidence',0)>=.4)
    reference_terms=c.tokens(target['Название вина']+' '+ref.get('title','')+' '+reftext)
    nt=target['name_tokens']-target['winery_tokens']
    if not nt:nt=target['name_tokens']
    cover=[];evidence=[]
    for product in p['products']:
        attrs=product.get('attrs',{});name=product['title']+' '+attrs.get('Линейка','')
        years=c.vintage(product['title']+' '+attrs.get('Год производства',''))
        if expected and years and not(expected&years):continue
        cat=c.norm(attrs.get('Вид вина',''));targetcat=c.norm(target['Категория'])
        colors={v for v in ['белое','красное','розовое','оранжевое'] if v in cat}
        if colors and targetcat not in colors and not(targetcat=='оранжевое' and 'белое'in colors):continue
        ps=c.sugar(cat)
        if target['sugar'] and ps and target['sugar']!=ps:continue
        expected_grapes=grape_terms(target.get('Сорт винограда',''))
        source_grapes=grape_terms(attrs.get('Сорт винограда',''))
        if expected_grapes and source_grapes and not source_grapes<=expected_grapes:continue
        if 'магнум' in c.norm(name) and 'магнум' not in c.norm(target['Название вина']):continue
        if re.search(r'\b(?:в банке|в пакете|тетрапак|bag in box)\b',c.norm(name)) and not re.search(r'\b(?:банке|банка|пакет|тетрапак|bag in box)\b',c.norm(target['Название вина'])):continue
        def blanc_style(value):
            n=c.norm(value)
            if re.search(r'блан (?:де|de) (?:нуар|нуаров|noir|noirs)',n):return 'noirs'
            if re.search(r'блан (?:де|de) (?:блан|бланов|blancs)',n):return 'blancs'
            return ''
        target_style=blanc_style(target['Название вина']+' '+reftext);source_style=blanc_style(name)
        if target_style and source_style and target_style!=source_style:continue
        critical={'резерв','reserve','гранд','grand','кагор','портвейн','ледяное','айсвайн','100','101'}
        if ((c.tokens(name)&critical)-reference_terms):continue
        if expected and not(years or actual):continue
        coverage=len(nt&c.tokens(name+' '+txt))/max(1,len(nt))
        cover.append(coverage);evidence.append(product)
    if not evidence:flags.append('metadata_or_vintage_unconfirmed')
    if not cover or max(cover)<.8:flags.append('name_details_unconfirmed')
    # Preserve variant digits such as Blush 1/2/3 and 100/101 shades.
    numbers={s for s in nt if s.isdigit()}
    combined=c.tokens(txt+' '+' '.join(x['title'] for x in evidence))
    if numbers and not numbers<=combined:flags.append('series_number_unconfirmed')
    return flags,evidence[0] if evidence else p['products'][0],expected,actual

def decisions():
    refs={r['slug']:r for r in references()};targets={t['Slug']:t for t in c.build_matcher()};ocr=load_ocr();out=[]
    override_file=c.ROOT/'collector'/'reference_visual_overrides.json'
    overrides=json.loads(override_file.read_text()) if override_file.exists() else {}
    for line in (WORK/'ranked.jsonl').read_text().splitlines():
        p=json.loads(line);rank=p['ranked']
        if not rank or rank[0]['inliers']<20:continue
        valid=[]
        for candidate in rank:
            flags,product,expected,actual=metadata_check(p,targets[candidate['slug']],refs[candidate['slug']],ocr)
            valid.append((candidate,flags,product,expected,actual))
        # Metadata can resolve a year variant within the geometric shortlist.
        eligible=[x for x in valid if not x[1]]
        choice=eligible[0] if eligible else valid[0]
        best,flags,product,expected,actual=choice;flags=list(flags)
        others=[x[0]['inliers'] for x in eligible if x[0]['slug']!=best['slug']]
        runner=max(others or [0]);margin=round(best['inliers']/max(1,runner),3)
        if best['inliers']<35 or best['ratio']<.45 or best['coverage']<.045:flags.append('weak_label_geometry')
        if runner and (margin<1.65 or best['inliers']-runner<15):flags.append('similar_catalog_labels')
        # Reference sharing is unresolved even when two catalogue cards have different names.
        ref=refs[best['slug']]
        if 'shared_image_across_slugs' in ref['reason']:flags.append('ambiguous_reference')
        if p['spec'].get('requires_visual_front_check') and c.FRONT_DECISIONS.get(p['image_url'],{}).get('decision')!='keep':flags.append('front_view_not_reviewed')
        override=overrides.get(p['key'],{})
        if override.get('decision')=='review':flags+=override.get('reasons',['manual_review_required'])
        out.append({**p,'choice':best,'reference_path':str(c.ROOT/ref['path']),'reference_url':ref['image_url'],'reference_sha256':ref['sha256'],'product':product,'decision':'review' if flags else 'accept','flags':flags,'margin':margin,'expected_year':sorted(expected),'visible_year':sorted(actual)})
    (WORK/'decisions.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out))
    c.log('visual_decisions',counts=dict(collections.Counter(x['decision'] for x in out)),sources=dict(collections.Counter(x['product']['source'] for x in out if x['decision']=='accept')))

def sheets(limit=120,rows=None):
    from PIL import ImageDraw,ImageFont
    import random
    if rows is None:rows=[json.loads(x) for x in (WORK/'decisions.jsonl').read_text().splitlines()]
    rows=[x for x in rows if x['decision']=='accept'];random.Random(943).shuffle(rows)
    if limit:
        chosen=[];sources=collections.defaultdict(list)
        for r in rows:sources[r['product']['source']].append(r)
        per=max(4,limit//max(1,len(sources)))
        for s,items in sorted(sources.items()):chosen.extend(items[:per])
        used={r['key'] for r in chosen}
        for r in sorted(rows,key=lambda x:(x['choice']['inliers'],x['margin'])):
            if len(chosen)>=limit:break
            if r['key'] not in used:chosen.append(r);used.add(r['key'])
        rows=chosen
    (WORK/'review_sample.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',15)
    for start in range(0,len(rows),12):
        sheet=Image.new('RGB',(1440,1200),'white');draw=ImageDraw.Draw(sheet)
        for j,r in enumerate(rows[start:start+12]):
            x=(j%4)*360;y=(j//4)*400
            for k,path in enumerate([r['reference_path'],r['path']]):
                with Image.open(path) as im:
                    im=ImageOps.exif_transpose(im).convert('RGBA');bg=Image.new('RGBA',im.size,'white');bg.alpha_composite(im);rgb=bg.convert('RGB')
                    arr=np.asarray(rgb);yy,xx=np.where(np.any(arr<235,axis=2))
                    if len(xx)>100:rgb=rgb.crop((int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1))
                    rgb.thumbnail((172,290));sheet.paste(rgb,(x+k*180+(172-rgb.width)//2,y+26))
            label=f'{start+j}: {r["choice"]["inliers"]} pts / {r["margin"]}  {r["product"]["source"]}'
            draw.text((x+4,y+4),label,fill='black',font=font)
            title=c.BY_SLUG[r['choice']['slug']]['Винодельня']+' / '+c.BY_SLUG[r['choice']['slug']]['Название вина']
            for n in range(0,min(len(title),108),36):draw.text((x+4,y+325+(n//36)*19),title[n:n+36],fill='black',font=font)
        sheet.save(WORK/f'review_{start//12:02}.jpg')
    c.log('review_sheets',pairs=len(rows))

def apply_decisions():
    from audit import move
    rows=[json.loads(x) for x in (WORK/'decisions.jsonl').read_text().splitlines()]
    overrides_path=c.ROOT/'collector'/'reference_visual_overrides.json'
    overrides=json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    verified_path=c.ROOT/'collector'/'reference_decisions.json'
    verified=json.loads(verified_path.read_text()) if verified_path.exists() else {}
    sha_assign=collections.defaultdict(set)
    for r in rows:
        if r['decision']=='accept':sha_assign[r['sha256']].add(r['choice']['slug'])
    stats=collections.Counter();report=[]
    accepted_pixels=collections.defaultdict(set)
    for x in c.DB.execute('SELECT pixel_sha256,slug FROM images WHERE status="accepted"'):accepted_pixels[x['pixel_sha256']].add(x['slug'])
    original_request=c.request
    raw_by_url={r['image_url']:r['path'] for r in rows}
    def cached_request(url,*args,**kwargs):
        path=raw_by_url.get(url)
        if path and Path(path).exists():return Path(path).read_bytes(),url,'application/octet-stream'
        return original_request(url,*args,**kwargs)
    c.request=cached_request
    try:
        for i,r in enumerate(rows,1):
            slug=r['choice']['slug'];flags=list(r['flags']);decision=r['decision'];key=r['key']
            if key in overrides:
                flags+=overrides[key].get('reasons',[])
                if overrides[key]['decision']!='accept':decision='review'
            if len(sha_assign[r['sha256']])>1:decision='review';flags.append('one_image_multiple_visual_assignments')
            result='not_added';ident=hashlib.sha256((slug+'\n'+r['image_url']).encode()).hexdigest()
            if decision=='accept':
                reason='reference_label_geometry_and_metadata; inliers='+str(r['choice']['inliers'])+'; coverage='+str(r['choice']['coverage'])+'; margin='+str(r['margin'])
                existing_conflicts=c.DB.execute('SELECT slug FROM images WHERE sha256=? AND slug<>? AND status="accepted"',(r['sha256'],slug)).fetchall()
                if existing_conflicts:
                    decision='review';flags.append('image_already_accepted_for_other_slug')
            if decision=='accept':
                result=c.download_image(slug,r['product'],r['spec'],'accepted',reason)
                current=c.DB.execute('SELECT * FROM images WHERE id=?',(ident,)).fetchone()
                # A second URL may be byte-identical to a previously quarantined image.
                # Promote that existing file, preserving both URLs in the comparison log.
                if not current and result=='duplicate':
                    current=c.DB.execute('SELECT * FROM images WHERE slug=? AND sha256=?',(slug,r['sha256'])).fetchone()
                    if current and current['source']=='vino-svoe.ru':current=None
                if current:
                    conflicts=c.DB.execute('SELECT slug FROM images WHERE pixel_sha256=? AND slug<>? AND status="accepted"',(current['pixel_sha256'],slug)).fetchall()
                    if conflicts or (accepted_pixels[current['pixel_sha256']]-{slug}):
                        decision='review';flags.append('pixels_already_accepted_for_other_slug')
                        move(dict(current),'needs_review','; '.join(flags),'reference_match_requires_review')
                    else:
                        move(dict(current),'accepted',reason,'reference_label_geometry_plus_metadata_and_ocr')
                        accepted_pixels[current['pixel_sha256']].add(slug)
                        verified[current['id']]={'slug':slug,'sha256':r['sha256'],'reference_sha256':r['reference_sha256'],'reference_url':'https://vino-svoe.ru/wines/'+slug,'reference_image_url':r['reference_url'],'candidate_image_url':r['image_url'],'inliers':r['choice']['inliers'],'coverage':r['choice']['coverage'],'margin':r['margin'],'expected_year':r['expected_year'],'visible_year':r['visible_year'],'checked_at':c.now()}
                        result='accepted'
            stats[result]+=1
            current=c.DB.execute('SELECT path,status FROM images WHERE id=?',(ident,)).fetchone()
            report.append({'slug':slug,'reference_page':'https://vino-svoe.ru/wines/'+slug,'source':r['product']['source'],'page_url':r['product']['url'],'image_url':r['image_url'],'reference_image_url':r['reference_url'],'inliers':r['choice']['inliers'],'inlier_ratio':r['choice']['ratio'],'label_coverage':r['choice']['coverage'],'margin':r['margin'],'decision':decision,'reason':'; '.join(flags),'expected_year':','.join(r['expected_year']),'visible_year':','.join(r['visible_year']),'result':result,'path':current['path'] if current else ''})
            if i%100==0:c.DB.commit();c.log('apply_reference_matches',done=i,total=len(rows),counts=dict(stats))
    finally:c.request=original_request
    c.DB.commit();verified_path.write_text(json.dumps(verified,ensure_ascii=False,indent=2))
    fields=['slug','reference_page','source','page_url','image_url','reference_image_url','inliers','inlier_ratio','label_coverage','margin','decision','reason','expected_year','visible_year','result','path']
    c.write_csv(c.ROOT/'reference_matches.csv',report,fields);c.export()
    c.log('reference_matches_applied',counts=dict(stats))

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['probe','crawl','factories','refs','recover-refs','archive-refs','pool','pool-plan','match','ocr-input','decide','sheets','apply']);p.add_argument('--url',action='append',default=[]);p.add_argument('--limit',type=int,default=0);a=p.parse_args()
    c.init(c.DEFAULT_CSV);snapshot()
    if a.stage=='probe':probe(a.url)
    elif a.stage=='crawl':crawl_new(a.url,a.limit)
    elif a.stage=='factories':factories()
    elif a.stage=='refs':feature_refs()
    elif a.stage=='recover-refs':recover_refs()
    elif a.stage=='archive-refs':archive_refs()
    elif a.stage=='pool':pool_download(a.limit)
    elif a.stage=='pool-plan':pool_download(a.limit,True)
    elif a.stage=='match':match_pool(a.limit)
    elif a.stage=='ocr-input':ocr_input()
    elif a.stage=='decide':decisions()
    elif a.stage=='sheets':sheets(a.limit)
    elif a.stage=='apply':apply_decisions()

if __name__=='__main__':main()
