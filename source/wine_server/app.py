import asyncio,re,base64,hashlib,hmac,io,json,logging,math,os,secrets,time,warnings,threading
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request,HTTPException,UploadFile,File,Form
from fastapi.responses import FileResponse,JSONResponse,Response,HTMLResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image,ImageOps,UnidentifiedImageError
from data_store import DataStore
from exports import Exports
from duplicates import Duplicates
from product import Product
from ml_client import WineMLClient,WineMLError

ROOT=Path(__file__).resolve().parent
MOBILE_ROOT=Path(os.environ.get('WINE_FRONTEND_DIST',ROOT/'static/mobile')).resolve()
DATASET=Path(os.environ.get('WINE_DATASET',ROOT.parent/'wine_dataset'))
MAX_BYTES=24*1024*1024
RUNPOD_MODEL_VERSION='qwen'
RUNPOD_CATALOG_COUNT=2103

def normalize_ml_result(raw):
    """Keep the GPU response intact and provide fields used by existing readers."""
    if not isinstance(raw,dict) or type(raw.get('no_wine')) is not bool:
        raise WineMLError(503,'Модель вернула некорректный ответ. Повторите позже.')
    no_wine=raw['no_wine'];slug=raw.get('slug');main=raw.get('card');alternatives=raw.get('alternatives')
    if (no_wine and (slug is not None or main is not None)) or (not no_wine and (not isinstance(slug,str) or not slug or not isinstance(main,dict) or main.get('slug')!=slug)):
        raise WineMLError(503,'Модель вернула некорректный ответ. Повторите позже.')
    if not isinstance(alternatives,list) or len(alternatives)!=(5 if no_wine else 4):
        raise WineMLError(503,'Модель вернула некорректный ответ. Повторите позже.')
    ranked=alternatives if no_wine else [main,*alternatives]
    if any(not isinstance(item,dict) or not isinstance(item.get('slug'),str) or
           re.fullmatch(r'[a-z0-9_][a-z0-9_-]{0,159}',item['slug']) is None for item in ranked):
        raise WineMLError(503,'Модель вернула некорректный ответ. Повторите позже.')
    def unit_score(value):
        return type(value) in (int,float) and math.isfinite(value) and 0<=value<=1
    if any(not unit_score(item.get('score')) for item in ranked):
        raise WineMLError(503,'Модель вернула некорректный ответ. Повторите позже.')
    confidence=raw.get('confidence')
    if not isinstance(confidence,dict) or not all(unit_score(confidence.get(key)) for key in ('top1_score','gap_top1_top2')):
        raise WineMLError(503,'Модель вернула некорректный ответ. Повторите позже.')
    latency=raw.get('latency_ms')
    version=raw.get('model_version') or RUNPOD_MODEL_VERSION
    if (not isinstance(version,str) or not version or len(version)>160 or
            type(latency) not in (int,float) or not math.isfinite(latency) or latency<0):
        raise WineMLError(503,'Модель вернула некорректный ответ. Повторите позже.')
    return {'slug':slug,'top5':[{'slug':item['slug'],'score':item.get('score')} for item in ranked],
            'no_wine':no_wine,'confidence':{key:confidence[key] for key in ('top1_score','gap_top1_top2')},
            'model_version':version,'latency_ms':raw['latency_ms'],
            'ml_response':raw}

def preview_image(path,bounds):
    with Image.open(path) as source:
        source.draft('RGB',bounds)
        im=ImageOps.exif_transpose(source);im.thumbnail(bounds)
        if im.mode in {'RGBA','LA'} or 'transparency' in im.info:
            rgba=im.convert('RGBA');im=Image.new('RGB',rgba.size,'white');im.paste(rgba,mask=rgba.getchannel('A'))
        else:im=im.convert('RGB')
        return im

def create_app(dataset=DATASET,engine=None,test=False,ml_client=None):
    backend=os.environ.get('WINE_INFERENCE_BACKEND','local').strip().lower()
    if backend not in {'local','runpod'}:raise ValueError('WINE_INFERENCE_BACKEND must be local or runpod')
    cookie_secure=not test and os.environ.get('WINE_COOKIE_SECURE','1')!='0'
    store=DataStore(dataset)
    product=Product(store)
    password=os.environ.get('WINE_ADMIN_PASSWORD','test-password' if test else '')
    if not password:raise RuntimeError('Configure WINE_ADMIN_PASSWORD')
    exports=Exports(store)
    duplicates=Duplicates(store)
    gate=asyncio.Semaphore(1);preview_gate=threading.Semaphore(2);login_attempts={}
    @asynccontextmanager
    async def lifespan(app):
        with store.db() as db:
            db.execute("UPDATE scans SET status='error',error='Сервер перезапущен во время обработки' WHERE status='processing'")
        async def load():
            try:
                if backend=='runpod':
                    app.state.ml_client=ml_client or WineMLClient()
                elif engine is not None:app.state.engine=engine
                else:
                    from inference import Engine
                    app.state.engine=await asyncio.to_thread(Engine)
                if backend=='local':app.state.ready=True
            except Exception as e:
                app.state.error=str(e);print('Model startup failed:',e,flush=True)
        task=asyncio.create_task(load())
        yield
        await task
        await asyncio.to_thread(exports.close)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.store=store;app.state.ready=False;app.state.error='';app.state.backend=backend
    app.state.engine=None;app.state.ml_client=None
    app.state.exports=exports
    app.state.product=product

    def admin(request):
        token=request.cookies.get('wine_admin','')
        if not token:return False
        with store.db() as db:r=db.execute('SELECT expires FROM admin_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return bool(r and r['expires']>time.time())
    public_scanner_routes={('GET','/api/status'),('POST','/api/predict')}

    @app.middleware('http')
    async def security(request,call_next):
        path=request.url.path
        if request.method=='POST':
            origin=request.headers.get('origin')
            host=request.headers.get('host','')
            if origin and origin not in {'https://'+host,'http://'+host}:
                return JSONResponse({'detail':'Недопустимый источник запроса'},status_code=403)
            length=request.headers.get('content-length','0')
            if not length.isdigit() or int(length)>MAX_BYTES+65536:return JSONResponse({'detail':'Файл больше 24 МБ'},status_code=413)
        if request.method=='POST' and path.startswith('/api/product/') and int(request.headers.get('content-length','0'))>8192:return JSONResponse({'detail':'Запрос слишком большой'},status_code=413)
        if path.startswith('/admin/api/') and path!='/admin/api/login' and not admin(request):
            return JSONResponse({'detail':'Войдите в админку'},status_code=401)
        public_product=bool((request.method=='GET' and re.fullmatch(r'/api/product/(bootstrap|search|ranking|recommendations|history|wines/[^/]+(?:/image)?|scans/[^/]+/image)',path)) or (request.method=='POST' and re.fullmatch(r'/api/product/(assistant|preferences/[^/]+)',path)))
        if path.startswith('/api/') and (request.method,path) not in public_scanner_routes and not public_product and not admin(request):
            return JSONResponse({'detail':'Войдите в админку'},status_code=401)
        new_profile=None
        if (public_product and not re.fullmatch(r'/api/product/wines/[^/]+/image',path)) or path=='/api/predict':
            request.state.profile,new_profile=product.profile(request)
        try:response=await call_next(request)
        except (ValueError,OSError,KeyError) as e:response=JSONResponse({'detail':str(e)},status_code=409)
        if new_profile:response.set_cookie('wine_profile',new_profile,secure=cookie_secure,httponly=True,samesite='lax',max_age=365*86400)
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
           'Permissions-Policy':'camera=(self), microphone=(self), geolocation=()',
           'X-Frame-Options':'DENY'})
        if '/api/' in path:response.headers['Cache-Control']='no-store'
        elif path.startswith('/admin/static/') or path in {'/app.js','/app.css','/manifest.webmanifest','/chat.css'}:response.headers['Cache-Control']='no-cache'
        return response

    def readiness():
        if backend=='local':
            return {'ready':app.state.ready,'backend':'local',
                    'catalog_count':len(app.state.engine.slugs) if app.state.ready else 0,
                    'model_version':app.state.engine.version if app.state.ready else ''}
        if app.state.ml_client is None:
            return {'ready':False,'backend':'runpod','catalog_count':0,'model_version':''}
        try:
            remote=app.state.ml_client.health()
            ready=(remote.get('ready') is True and remote.get('catalog_size')==RUNPOD_CATALOG_COUNT)
            app.state.ready=ready
            return {'ready':ready,'backend':'runpod','catalog_count':remote['catalog_size'] if ready else 0,
                    'model_version':remote.get('model_version') or RUNPOD_MODEL_VERSION if ready else ''}
        except (WineMLError,ValueError,TypeError):
            app.state.ready=False
            return {'ready':False,'backend':'runpod','catalog_count':0,'model_version':''}

    @app.get('/health')
    def health():
        result=readiness()
        return JSONResponse(result,status_code=200 if result['ready'] or backend=='local' else 503)

    @app.post('/admin/api/login')
    def login(request:Request,body:dict):
        ip=request.client.host;now=time.time();recent=[t for t in login_attempts.get(ip,[]) if now-t<300]
        if len(recent)>=10:raise HTTPException(429,'Повторите вход через пять минут')
        if not hmac.compare_digest(str(body.get('password','')).encode(),password.encode()):
            login_attempts[ip]=recent+[now];raise HTTPException(401,'Неверный пароль')
        login_attempts.pop(ip,None);token=secrets.token_urlsafe(32)
        with store.db() as db:
            db.execute('DELETE FROM admin_sessions WHERE expires<?',(now,))
            db.execute('INSERT INTO admin_sessions VALUES(?,?)',(hashlib.sha256(token.encode()).hexdigest(),now+30*86400))
        response=JSONResponse({'ok':True})
        response.set_cookie('wine_admin',token,secure=cookie_secure,httponly=True,samesite='strict',max_age=30*86400)
        return response

    @app.post('/admin/api/logout')
    def logout(request:Request):
        with store.db() as db:db.execute('DELETE FROM admin_sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get('wine_admin','').encode()).hexdigest(),))
        r=JSONResponse({'ok':True});r.delete_cookie('wine_admin');return r

    @app.get('/admin/api/config')
    def config(request:Request):return {'stats':store.stats(),**readiness(), 'batches':store.batches(),
        'scanner_url':str(request.base_url)}

    @app.get('/admin/api/wines')
    def wines(q:str='',pending:bool=False):return {'items':store.wines(q,pending)}

    @app.get('/admin/api/duplicates')
    def duplicate_list(q:str=''):return duplicates.list(q)

    @app.get('/admin/api/duplicates/{ident}')
    def duplicate_detail(ident:str):return duplicates.detail(ident)

    @app.post('/admin/api/duplicates/{ident}/resolve')
    def duplicate_resolve(ident:str,body:dict):
        return duplicates.resolve(ident,body['version'],body.get('slug'),body.get('quarantine',False))

    @app.post('/admin/api/undo-duplicate')
    def duplicate_undo():return duplicates.undo()

    @app.get('/admin/api/history')
    def history(offset:int=0,q:str=''):return store.history(offset,q=q)

    @app.post('/admin/api/batches')
    def batch(body:dict):return {'id':store.batch(body.get('name','Мои фото'))}

    @app.post('/admin/api/batches/{ident}/upload')
    async def upload(ident:str,image:UploadFile=File(...)):
        data=await image.read(MAX_BYTES+1);await image.close();validate_image(data)
        return await asyncio.to_thread(store.upload,ident,image.filename or 'photo.jpg',data)

    @app.get('/admin/api/batches/{ident}')
    def items(ident:str):return {'items':store.batch_items(ident)}

    @app.post('/admin/api/assign')
    def assign(body:dict):
        if body['kind']=='scan':return store.assign_scan(body['id'],body['slug'],body.get('bounds'))
        if body['kind']=='source':return store.assign_source(body['id'],body['slug'],body.get('bounds'))
        raise ValueError('Неизвестный вид снимка')

    @app.get('/admin/api/group/{slug}')
    def group(slug:str):return store.group(slug)

    @app.post('/admin/api/review')
    def review(body:dict):return store.review(body['slug'],body['version'],body.get('reject',[]))

    @app.post('/admin/api/undo-review')
    def undo_review():
        with store.guard(),store.db() as db:
            last=db.execute("SELECT payload FROM curation_events WHERE state='done' AND kind='review' ORDER BY rowid DESC LIMIT 1").fetchone()
            changes=json.loads(last['payload'])['changes'] if last else []
            slug=changes[0]['row_after']['slug'] if changes else ''
            result=store.undo('review');return {**result,'slug':slug}

    @app.get('/admin/api/boundaries')
    def boundaries(filter:str='unlabeled',slug:str='',photo_set:str='all'):
        return store.annotation_selection(filter,slug,photo_set)

    @app.get('/admin/api/boundaries/{ident}')
    def boundary(ident:str):return store.annotation(ident)

    @app.post('/admin/api/boundaries/{ident}')
    def save_boundary(ident:str,body:dict):return store.annotate(ident,body,body.get('revision'))

    @app.get('/admin/api/export/boundaries')
    def export():
        with store.db() as db:rows=[dict(r) for r in db.execute('''SELECT a.*,i.path,i.slug FROM annotations a JOIN images i ON a.id=i.id''')]
        for r in rows:
            r['points']=json.loads(r['points'])
            if r['points']:
                xx=[p[0] for p in r['points']];yy=[p[1] for p in r['points']]
                r['yolo_box']=[0,(min(xx)+max(xx))/2,(min(yy)+max(yy))/2,max(xx)-min(xx),max(yy)-min(yy)]
        return JSONResponse({'coordinate_system':'EXIF-oriented image, normalized x/y from top-left','label':'front_label','items':rows},headers={'Content-Disposition':'attachment; filename=label_boundaries.json'})

    @app.get('/admin/api/exports/catalog')
    def export_catalog():return {'items':exports.catalog()}

    @app.post('/admin/api/exports/preview')
    def export_preview(body:dict):return exports.preview(body)

    @app.get('/admin/api/exports')
    def export_list():return {'items':exports.list()}

    @app.post('/admin/api/exports')
    def export_create(body:dict):return exports.create(body)

    @app.get('/admin/api/exports/{ident}/download')
    def export_download(ident:str):
        path,filename=exports.download(ident)
        return FileResponse(path,filename=filename,media_type='application/zip')

    @app.post('/admin/api/exports/{ident}/delete')
    def export_delete(ident:str):return exports.delete(ident)

    @app.get('/admin/api/image/{kind}/{ident}')
    def photo(kind:str,ident:str,size:int=1200):
        p=store.image_path(kind,ident);size=min(2500,max(100,size))
        cache=store.work/'server_previews';cache.mkdir(exist_ok=True)
        key=hashlib.sha256(f'{p}:{p.stat().st_mtime_ns}:{size}:white-v2'.encode()).hexdigest();dest=cache/(key+'.jpg')
        if not dest.exists():
            with preview_gate:
                if not dest.exists():
                    im=preview_image(p,(size,size));out=io.BytesIO();im.save(out,'JPEG',quality=90)
                    temp=dest.with_suffix('.'+secrets.token_hex(4)+'.tmp');temp.write_bytes(out.getvalue());temp.replace(dest)
        return FileResponse(dest,media_type='image/jpeg')

    def fallback_card(slug,ml_card):
        source=ml_card if isinstance(ml_card,dict) else {}
        def safe(key,default='',limit=500):return str(source.get(key) or default)[:limit]
        portal='https://vino-svoe.ru/wines/'+slug
        return {'slug':slug,'name':safe('name',slug),'winery':safe('winery'),
                    'category':safe('category'),'region':safe('region'),
                    'grapes':safe('grape'),'description':safe('description',limit=3000),
                    'alcohol':None,'temperature':'','pairings':[],
                    'portal_url':portal if source.get('url')==portal else '',
                    'image':'/assets/no-bottle.svg','average_rating':None,'rating_count':0,'scan_count':0,
                    'rating':0,'favorite':False,'preferences_available':False}

    def card(slug,profile=None,ml_card=None):
        if slug not in product.cards:return fallback_card(slug,ml_card)
        try:
            with store.db() as db:
                ref=db.execute("SELECT id,path FROM images WHERE slug=? AND (kind='catalog_reference' OR (source='vino-svoe.ru' AND status='accepted')) ORDER BY kind='catalog_reference' DESC LIMIT 1",(slug,)).fetchone()
            picture=None
            if ref:
                try:
                    im=preview_image(store.path(ref['path']),(320,500));out=io.BytesIO();im.save(out,'JPEG',quality=80)
                    picture='data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()
                except OSError:pass
            return {**product.card(slug,profile),'image':picture or product.cards[slug]['image'],'preferences_available':True}
        except Exception:
            if ml_card is None:raise
            logging.getLogger(__name__).exception('Product card enrichment failed for %s',slug)
            return fallback_card(slug,ml_card)

    @app.get('/api/status')
    def status():
        result=readiness()
        if not result['ready']:raise HTTPException(503,'Модель недоступна. Повторите через минуту.')
        return result

    @app.post('/api/predict')
    async def predict(request:Request,image:UploadFile=File(...)):
        if not (await asyncio.to_thread(readiness))['ready']:raise HTTPException(503,'Модель недоступна. Повторите через минуту.')
        data=await image.read(MAX_BYTES+1);await image.close();validate_image(data)
        device=request.headers.get('x-device-id','unknown')[:80]
        try:await asyncio.wait_for(gate.acquire(),timeout=.2)
        except asyncio.TimeoutError:raise HTTPException(429,'Сканер занят. Повторите через несколько секунд.')
        ident=None;result=None;finished=False
        try:
            ident,p=await asyncio.to_thread(store.scan_start,data,device)
            def run():
                if backend=='runpod':
                    if p.stat().st_size>MAX_BYTES:raise WineMLError(413,'Фото после обработки больше 24 МБ. Выберите другое фото.')
                    return normalize_ml_result(app.state.ml_client.search(p.read_bytes()))
                with Image.open(p) as im:return app.state.engine.predict(im.convert('RGB'))
            result=await asyncio.to_thread(run)
            store.scan_finish(ident,result);finished=True
            product.record_scan(request.state.profile,ident)
            if backend=='runpod':
                raw=result['ml_response'];raw_cards=raw['alternatives'] if result['no_wine'] else [raw['card'],*raw['alternatives']]
                ml_cards={item['slug']:item for item in raw_cards}
            else:ml_cards={}
            response={'id':ident,'no_wine':bool(result.get('no_wine',False)),
                      'wine':None if result.get('no_wine') else card(result['slug'],request.state.profile,ml_cards.get(result['slug'])),
                      'alternatives':[card(r['slug'],request.state.profile,ml_cards.get(r['slug'])) for r in result['top5'][0 if result.get('no_wine') else 1:]],
                      'model_version':result['model_version'],'latency_ms':result['latency_ms'],
                      'confidence':result.get('confidence'),'match_is_verified':False}
            return response
        except WineMLError as e:
            if ident and not finished:store.scan_finish(ident,result,error=e.message)
            raise HTTPException(e.status_code,e.message,headers={'Retry-After':e.retry_after} if e.retry_after else None)
        except ValueError as e:
            if ident and not finished:store.scan_finish(ident,result,error=str(e))
            if str(e)=='Фото после обработки больше 24 МБ':raise HTTPException(413,str(e))
            raise HTTPException(503,'Ошибка обработки. Попробуйте ещё раз; обработанные снимки доступны в истории.')
        except Exception as e:
            if ident and not finished:store.scan_finish(ident,result,error=str(e))
            raise HTTPException(503,'Ошибка обработки. Попробуйте ещё раз; обработанные снимки доступны в истории.')
        finally:gate.release()


    @app.get('/api/product/bootstrap')
    def product_bootstrap(request:Request):
        profile=request.state.profile;prefs=product.preferences(profile)
        return {'preferences':prefs,'collection':product.cards_for([s for s,p in prefs.items() if p['favorite'] or p['rating']],profile),
                'recommendations':product.recommendations(profile,limit=4),'assistant_mode':'ai' if product.ai_enabled else 'catalog',
                'catalog_count':len(product.cards)}

    @app.get('/api/product/search')
    def product_search(request:Request,q:str=''):
        return {'items':product.search_catalog(q[:160],request.state.profile)}

    @app.get('/api/product/wines/{slug}')
    def product_wine(slug:str,request:Request):return product.card(slug,request.state.profile)

    @app.get('/api/product/wines/{slug}/image')
    def product_image(slug:str):
        product.require_slug(slug)
        with store.db() as db:
            ref=db.execute("SELECT path FROM images WHERE slug=? AND (kind='catalog_reference' OR (source='vino-svoe.ru' AND status='accepted')) ORDER BY kind='catalog_reference' DESC LIMIT 1",(slug,)).fetchone()
        if not ref:raise HTTPException(404,'Нет фото в каталоге')
        p=store.path(ref['path'])
        if not p.exists():raise HTTPException(404,'Нет фото в каталоге')
        cache=store.work/'server_previews';cache.mkdir(exist_ok=True)
        key=hashlib.sha256(f'{p}:{p.stat().st_mtime_ns}:product-640-v1'.encode()).hexdigest();dest=cache/(key+'.jpg')
        if not dest.exists():
            with preview_gate:
                if not dest.exists():
                    im=preview_image(p,(640,900));out=io.BytesIO();im.save(out,'JPEG',quality=88)
                    temp=dest.with_suffix('.'+secrets.token_hex(4)+'.tmp');temp.write_bytes(out.getvalue());temp.replace(dest)
        return FileResponse(dest,media_type='image/jpeg')

    @app.post('/api/product/preferences/{slug}')
    def product_preference(slug:str,body:dict,request:Request):return product.update(request.state.profile,slug,body)

    @app.get('/api/product/ranking')
    def product_ranking(request:Request,mode:str='scans'):return product.ranking(mode,request.state.profile)

    @app.get('/api/product/recommendations')
    def product_recommendations(request:Request):return {'items':product.recommendations(request.state.profile)}

    @app.get('/api/product/history')
    def product_history(request:Request):return {'items':product.history(request.state.profile)}

    @app.get('/api/product/scans/{scan_id}/image')
    def product_scan_image(scan_id:str,request:Request):
        product.owned_scan(request.state.profile,scan_id)
        p=store.image_path('scan',scan_id)
        if not p.exists():raise HTTPException(404,'Фото скана не найдено')
        cache=store.work/'server_previews';cache.mkdir(exist_ok=True)
        key=hashlib.sha256(f'{p}:{p.stat().st_mtime_ns}:scan-480-v1'.encode()).hexdigest();dest=cache/(key+'.jpg')
        if not dest.exists():
            with preview_gate:
                if not dest.exists():
                    im=preview_image(p,(480,640));out=io.BytesIO();im.save(out,'JPEG',quality=85)
                    temp=dest.with_suffix('.'+secrets.token_hex(4)+'.tmp');temp.write_bytes(out.getvalue());temp.replace(dest)
        return FileResponse(dest,media_type='image/jpeg')

    @app.post('/api/product/assistant')
    async def product_assistant(body:dict,request:Request):
        context=body.get('context',[])
        if not isinstance(context,list):raise HTTPException(422,'Некорректный контекст')
        return await asyncio.to_thread(product.assistant,request.state.profile,body.get('message',''),context,body.get('scan_id'),body.get('intent'),body.get('dish_name'))

    @app.get('/admin')
    @app.get('/admin/')
    def admin_page():
        folder=ROOT/'static/admin';html=(folder/'index.html').read_text()
        for name in ('app.js','exports.js','duplicates.js','style.css'):
            version=hashlib.sha256((folder/name).read_bytes()).hexdigest()[:12]
            html=html.replace(f'/admin/static/{name}"',f'/admin/static/{name}?v={version}"')
        return HTMLResponse(html,headers={'Cache-Control':'no-cache'})
    app.mount('/admin/static',StaticFiles(directory=ROOT/'static/admin'),name='admin-static')

    def mobile_page():
        folder=MOBILE_ROOT;html=(folder/'index.html').read_text()
        for name in ('app.js', 'chat.css', 'app.css'):
            version=hashlib.sha256((folder/name).read_bytes()).hexdigest()[:12]
            html=html.replace(f'/{name}"',f'/{name}?v={version}"')
        return HTMLResponse(html,headers={'Cache-Control':'no-cache'})

    @app.get('/')
    def home():return mobile_page()

    @app.get('/old-design')
    @app.get('/old-design/')
    def old_design():return RedirectResponse('/',status_code=308)
    @app.get('/sw.js')
    def sw():return FileResponse(MOBILE_ROOT/'sw.js',media_type='application/javascript',headers={'Cache-Control':'no-cache','Service-Worker-Allowed':'/'})
    app.mount('/',StaticFiles(directory=MOBILE_ROOT),name='mobile-static')
    return app

def validate_image(data):
    if len(data)>MAX_BYTES:raise HTTPException(413,'Файл больше 24 МБ')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if im.width*im.height>40_000_000:raise ValueError('Слишком большое разрешение')
                im.verify()
    except (OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise HTTPException(422,'Не удалось прочитать изображение')

if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ.get('PORT','8100')),proxy_headers=True,forwarded_allow_ips='127.0.0.1')
