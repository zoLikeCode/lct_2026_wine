"""Private collections and durable accounts; additive SQLite schema, no ML changes."""
import hashlib,hmac,json,re,secrets,sqlite3,time,uuid
from fastapi import HTTPException
from fastapi.responses import JSONResponse

SESSION_COOKIE='wine_account'
SESSION_TTL=30*86400

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def password_hash(value,salt=None):
    salt=salt or secrets.token_hex(16)
    key=hashlib.scrypt(value.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,maxmem=64*1024*1024).hex()
    return salt+':'+key

def password_matches(value,encoded):
    return hmac.compare_digest(password_hash(value,encoded.split(':')[0]),encoded)

def text(value,max_length,field):
    if not isinstance(value,str) or len(value)>max_length:raise HTTPException(422,'Проверьте поле «'+field+'»')
    return value.strip()

def valid_password(value):
    if not isinstance(value,str) or not 10<=len(value)<=256:raise HTTPException(422,'Пароль должен содержать от 10 до 256 символов')
    return value

class Collection:
    def __init__(self,product):
        self.product=product;self.store=product.store
        with self.store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS collection_entries(profile TEXT,slug TEXT,quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),tried INTEGER DEFAULT 0,note TEXT DEFAULT '',version INTEGER DEFAULT 0,updated REAL,PRIMARY KEY(profile,slug));
            CREATE TABLE IF NOT EXISTS collection_lists(id TEXT PRIMARY KEY,profile TEXT,name TEXT,goal TEXT DEFAULT '',size INTEGER DEFAULT 0,created REAL);
            CREATE INDEX IF NOT EXISTS collection_list_owner ON collection_lists(profile);
            CREATE TABLE IF NOT EXISTS collection_members(list_id TEXT,profile TEXT,slug TEXT,slot INTEGER,created REAL,PRIMARY KEY(list_id,slug),UNIQUE(list_id,slot));
            CREATE TABLE IF NOT EXISTS collection_events(id TEXT PRIMARY KEY,profile TEXT,slug TEXT,delta INTEGER,kind TEXT,request_id TEXT,undo_of TEXT,created REAL,UNIQUE(profile,request_id),UNIQUE(profile,undo_of));
            CREATE TABLE IF NOT EXISTS wine_accounts(id TEXT PRIMARY KEY,username TEXT UNIQUE,display_name TEXT,password_hash TEXT,recovery_hash TEXT,profile TEXT UNIQUE,created REAL);
            CREATE TABLE IF NOT EXISTS wine_account_sessions(id TEXT PRIMARY KEY,account TEXT,created REAL,last_seen REAL,expires REAL,label TEXT);
            CREATE INDEX IF NOT EXISTS account_sessions_owner ON wine_account_sessions(account);
            CREATE TABLE IF NOT EXISTS wine_auth_limits(key TEXT PRIMARY KEY,started REAL,attempts INTEGER);
            CREATE TABLE IF NOT EXISTS wine_guest_assistant(profile TEXT PRIMARY KEY,state TEXT,token TEXT,updated REAL);
            ''')
        from shelves import Shelves
        self.shelves=Shelves(self)
        # Equal-cost password verification for unknown usernames.
        self.dummy=password_hash(secrets.token_urlsafe(24))

    def session(self,request):
        token=request.cookies.get(SESSION_COOKIE,'')
        if not token or len(token)>100:return None
        with self.store.db() as db:
            r=db.execute('SELECT a.id,a.username,a.display_name,a.profile,s.id AS session_id FROM wine_account_sessions s JOIN wine_accounts a ON a.id=s.account WHERE s.id=? AND s.expires>?',(digest(token),time.time())).fetchone()
            if r:db.execute('UPDATE wine_account_sessions SET last_seen=? WHERE id=?',(time.time(),r['session_id']))
        return dict(r) if r else None

    def account(self,request):
        s=self.session(request)
        if not s:return {'authenticated':False}
        with self.store.db() as db:
            sessions=[{'id':r['id'],'label':r['label'],'created':r['created'],'last_seen':r['last_seen'],'current':r['id']==s['session_id']} for r in db.execute('SELECT * FROM wine_account_sessions WHERE account=? AND expires>? ORDER BY last_seen DESC',(s['id'],time.time()))]
        return {'authenticated':True,'username':s['username'],'display_name':s['display_name'],'sessions':sessions}

    def claim_guest_assistant(self,request):
        if self.session(request):return None
        now=time.time();token=secrets.token_hex(16);profile=request.state.profile
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state,updated FROM wine_guest_assistant WHERE profile=?',(profile,)).fetchone()
            if row and row['state']=='used':
                raise HTTPException(401,{'code':'assistant_sign_in','message':'Чтобы продолжить разговор с сомелье, войдите или создайте аккаунт.'})
            if row and now-row['updated']<120:
                raise HTTPException(429,'Первый ответ ещё готовится. Дождитесь его.')
            db.execute('INSERT OR REPLACE INTO wine_guest_assistant VALUES(?,?,?,?)',(profile,'pending',token,now))
        return token

    def finish_guest_assistant(self,profile,token,success):
        if token is None:return
        with self.store.db() as db:
            if success:db.execute("UPDATE wine_guest_assistant SET state='used',updated=? WHERE profile=? AND token=?",(time.time(),profile,token))
            else:db.execute('DELETE FROM wine_guest_assistant WHERE profile=? AND token=?',(profile,token))

    def rate_limit(self,request,username):
        now=time.time();ip=request.client.host if request.client else 'unknown'
        keys=[('ip:'+digest(ip),40),('user:'+digest(username),12)]
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM wine_auth_limits WHERE started<?',(now-900,))
            for key,limit in keys:
                r=db.execute('SELECT attempts FROM wine_auth_limits WHERE key=?',(key,)).fetchone()
                if r and r['attempts']>=limit:raise HTTPException(429,'Слишком много попыток. Попробуйте через 15 минут')
            for key,_ in keys:db.execute('INSERT INTO wine_auth_limits VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1',(key,now))

    def credentials(self,body):
        username=text(body.get('username',''),32,'Логин').lower()
        if not re.fullmatch('[a-z0-9][a-z0-9_.-]{2,31}',username):raise HTTPException(422,'Логин: 3–32 латинские буквы, цифры, точка, дефис или подчёркивание')
        return username,valid_password(body.get('password',''))

    def new_session(self,db,account,request):
        token=secrets.token_urlsafe(32);now=time.time()
        ua=request.headers.get('user-agent','')
        label=('Android' if 'Android' in ua else 'iPhone / iPad' if 'iPhone' in ua or 'iPad' in ua else 'Mac' if 'Mac' in ua else 'Windows' if 'Windows' in ua else 'Браузер')
        db.execute('DELETE FROM wine_account_sessions WHERE expires<?',(now,))
        db.execute('INSERT INTO wine_account_sessions VALUES(?,?,?,?,?,?)',(digest(token),account,now,now,now+SESSION_TTL,label))
        return token

    def response(self,token,secure,**extra):
        response=JSONResponse({'ok':True,**extra})
        response.set_cookie(SESSION_COOKIE,token,max_age=SESSION_TTL,httponly=True,secure=secure,samesite='lax')
        response.delete_cookie('wine_profile',secure=secure,httponly=True,samesite='lax')
        return response

    def register(self,request,body,secure):
        if self.session(request):raise HTTPException(409,'Вы уже вошли в аккаунт')
        username,password=self.credentials(body);self.rate_limit(request,username)
        name=text(body.get('display_name',''),60,'Имя') or username
        recovery=secrets.token_hex(16);encoded=password_hash(password)
        try:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                ident=uuid.uuid4().hex
                db.execute('INSERT INTO wine_accounts VALUES(?,?,?,?,?,?,?)',(ident,username,name,encoded,digest(recovery),request.state.profile,time.time()))
                token=self.new_session(db,ident,request)
        except sqlite3.IntegrityError:raise HTTPException(409,'Этот логин уже занят. Выберите другой')
        return self.response(token,secure,recovery_code='-'.join(recovery[i:i+4] for i in range(0,32,4)))

    def guest_summary(self,profile,target):
        if profile==target:return {'count':0,'conflicts':[]}
        with self.store.db() as db:
            account=db.execute('SELECT 1 FROM wine_accounts WHERE profile=?',(profile,)).fetchone()
            if account:return {'count':0,'conflicts':[]}
        snapshot=self.snapshot(profile);other={x['slug']:x for x in self.snapshot(target)['items']}
        conflicts=[{'slug':w['slug'],'name':w['name'],'guest':w['quantity'],'account':other[w['slug']]['quantity']} for w in snapshot['items'] if w['quantity'] and other.get(w['slug'],{}).get('quantity')]
        with self.store.db() as db:scans=db.execute('SELECT count(*) FROM wine_profile_scans WHERE profile=?',(profile,)).fetchone()[0]
        return {'count':len(snapshot['items']),'lists':len(snapshot['lists']),'scans':scans,'conflicts':conflicts}

    def merge_guest(self,db,source,target,quantity_policy):
        # Existing account ratings/notes win; bottle conflicts require an explicit policy.
        for r in db.execute('SELECT * FROM wine_preferences WHERE profile=?',(source,)).fetchall():
            db.execute('''INSERT INTO wine_preferences VALUES(?,?,?,?,?) ON CONFLICT(profile,slug) DO UPDATE SET favorite=max(favorite,excluded.favorite),rating=CASE WHEN rating=0 THEN excluded.rating ELSE rating END,updated=excluded.updated''',(target,r['slug'],r['rating'],r['favorite'],r['updated']))
        for r in db.execute('SELECT * FROM collection_entries WHERE profile=?',(source,)).fetchall():
            old=db.execute('SELECT * FROM collection_entries WHERE profile=? AND slug=?',(target,r['slug'])).fetchone()
            qty=r['quantity']+(old['quantity'] if old else 0) if quantity_policy=='sum' else (old['quantity'] if old and old['quantity'] else r['quantity'])
            if qty>999:raise HTTPException(409,'После объединения больше 999 бутылок одного вина')
            note=old['note'] if old and old['note'] else r['note']
            db.execute('INSERT INTO collection_entries VALUES(?,?,?,?,?,?,?) ON CONFLICT(profile,slug) DO UPDATE SET quantity=excluded.quantity,tried=max(tried,excluded.tried),note=excluded.note,version=version+1,updated=excluded.updated',(target,r['slug'],qty,r['tried'],note,1,time.time()))
            delta=qty-(old['quantity'] if old else 0)
            if delta:
                ident=uuid.uuid4().hex
                db.execute('INSERT INTO collection_events VALUES(?,?,?,?,?,?,?,?)',(ident,target,r['slug'],delta,'merge',ident,None,time.time()))
        # Guest quantity operations describe an old balance. Do not make them
        # undoable against the merged balance; retain them under the archived id.
        for table in ('collection_comments','collection_copies','collection_saves','collection_badges'):
            db.execute('UPDATE OR IGNORE '+table+' SET profile=? WHERE profile=?',(target,source))
            db.execute('DELETE FROM '+table+' WHERE profile=?',(source,))
        for table in ('collection_lists','collection_members','collection_messages','wine_profile_scans'):db.execute('UPDATE '+table+' SET profile=? WHERE profile=?',(target,source))
        self.product.recs.merge_guest(db,source,target)
        db.execute('DELETE FROM wine_preferences WHERE profile=?',(source,));db.execute('DELETE FROM collection_entries WHERE profile=?',(source,))
        db.execute('DELETE FROM wine_profiles WHERE id=?',(source,))

    def login(self,request,body,secure):
        if self.session(request):raise HTTPException(409,'Сначала выйдите из текущего аккаунта')
        username,password=self.credentials(body);self.rate_limit(request,username)
        with self.store.db() as db:r=db.execute('SELECT * FROM wine_accounts WHERE username=?',(username,)).fetchone()
        if not password_matches(password,r['password_hash'] if r else self.dummy) or not r:raise HTTPException(401,'Неверный логин или пароль')
        summary=self.guest_summary(request.state.profile,r['profile'])
        if summary['count'] or summary.get('lists') or summary.get('scans'):
            if type(body.get('merge_guest')) is not bool:raise HTTPException(409,{'code':'guest_merge','summary':summary})
            if body['merge_guest'] and summary['conflicts'] and body.get('quantity_policy') not in ('keep','sum'):raise HTTPException(409,{'code':'guest_merge','summary':summary})
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if body.get('merge_guest') and request.state.profile!=r['profile']:
                # Account-owned profiles may never be merged by a guest request.
                if db.execute('SELECT 1 FROM wine_accounts WHERE profile=?',(request.state.profile,)).fetchone():raise HTTPException(409,'Обновите страницу')
                self.merge_guest(db,request.state.profile,r['profile'],body.get('quantity_policy','keep'))
            token=self.new_session(db,r['id'],request)
        response=self.response(token,secure)
        # Explicitly retain the anonymous collection for later use after sign-out.
        if body.get('merge_guest') is False:
            response.raw_headers=[h for h in response.raw_headers if not (h[0]==b'set-cookie' and h[1].startswith(b'wine_profile='))]
        return response

    def recover(self,request,body,secure):
        username,password=self.credentials(body);self.rate_limit(request,username)
        code=text(body.get('recovery_code',''),100,'Код восстановления').replace('-','').lower()
        with self.store.db() as db:r=db.execute('SELECT * FROM wine_accounts WHERE username=?',(username,)).fetchone()
        if not r or not hmac.compare_digest(digest(code),r['recovery_hash']):raise HTTPException(401,'Проверьте логин и код восстановления')
        encoded=password_hash(password);new=secrets.token_hex(16)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed=db.execute('UPDATE wine_accounts SET password_hash=?,recovery_hash=? WHERE id=? AND recovery_hash=?',(encoded,digest(new),r['id'],r['recovery_hash'])).rowcount
            if not changed:raise HTTPException(409,'Код уже использован')
            db.execute('DELETE FROM wine_account_sessions WHERE account=?',(r['id'],))
        # Recovery does not silently consume an existing guest collection.
        return {'ok':True,'recovery_code':'-'.join(new[i:i+4] for i in range(0,32,4)),'sign_in_required':True}

    def snapshot(self,profile):
        prefs=self.product.preferences(profile)
        with self.store.db() as db:
            pref_times={r['slug']:r['updated'] for r in db.execute('SELECT slug,updated FROM wine_preferences WHERE profile=?',(profile,))}
            entries={r['slug']:dict(r) for r in db.execute('SELECT * FROM collection_entries WHERE profile=?',(profile,))}
            lists=[dict(r) for r in db.execute('SELECT id,name,goal,size,created FROM collection_lists WHERE profile=? ORDER BY created DESC',(profile,))]
            members=[dict(r) for r in db.execute('SELECT list_id,slug,slot FROM collection_members WHERE profile=? ORDER BY slot,created',(profile,))]
            events=[dict(r) for r in db.execute('SELECT e.id,e.slug,e.delta,e.kind,e.created,EXISTS(SELECT 1 FROM collection_events u WHERE u.profile=e.profile AND u.undo_of=e.id) AS undone FROM collection_events e WHERE e.profile=? AND e.undo_of IS NULL ORDER BY e.created DESC LIMIT 20',(profile,))]
        slugs={s for s,p in prefs.items() if p['favorite'] or p['rating']} | {s for s,e in entries.items() if e['quantity'] or e['tried'] or e['note']} | {m['slug'] for m in members}
        cards=self.product.cards_for(sorted(slugs),profile)
        for w in cards:
            e=entries.get(w['slug'],{});w.update(quantity=e.get('quantity',0),tried=bool(e.get('tried') or w['rating']),note=e.get('note',''),version=e.get('version',0),updated=max(e.get('updated',0),pref_times.get(w['slug'],0)),lists=[m['list_id'] for m in members if m['slug']==w['slug']])
        for l in lists:l['members']=[m for m in members if m['list_id']==l['id']]
        snapshot={'items':cards,'lists':lists,'events':events,'counts':{'all':len(cards),'wish':sum(w['favorite'] for w in cards),'owned':sum(w['quantity']>0 for w in cards),'tried':sum(w['tried'] for w in cards),'bottles':sum(w['quantity'] for w in cards)}}
        return self.shelves.augment(profile,snapshot)

    def ensure_entry(self,db,profile,slug):db.execute('INSERT OR IGNORE INTO collection_entries(profile,slug,updated) VALUES(?,?,?)',(profile,slug,time.time()))

    def edit(self,profile,slug,body):
        self.product.require_slug(slug)
        note=text(body.get('note',''),1500,'Заметка');tried=body.get('tried',False);version=body.get('version')
        if type(tried) is not bool or type(version) is not int:raise HTTPException(422,'Некорректные данные')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');self.ensure_entry(db,profile,slug)
            if db.execute('UPDATE collection_entries SET note=?,tried=?,version=version+1,updated=? WHERE profile=? AND slug=? AND version=?',(note,int(tried),time.time(),profile,slug,version)).rowcount!=1:raise HTTPException(409,'Вино уже изменено на другом устройстве. Обновите коллекцию')
        return {'ok':True}

    def stock(self,profile,slug,body):
        self.product.require_slug(slug);key=text(body.get('request_id',''),80,'Идентификатор операции')
        if not re.fullmatch('[a-zA-Z0-9_-]{16,80}',key):raise HTTPException(422,'Нужен идентификатор операции')
        kind=body.get('kind');delta=body.get('delta');undo=body.get('undo_of')
        if kind not in ('add','open','gift','adjust','undo'):raise HTTPException(422,'Неизвестное действие')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM collection_events WHERE profile=? AND request_id=?',(profile,key)).fetchone()
            if old:
                if old['slug']!=slug or old['kind']!=kind or (old['undo_of'] or None)!=(undo or None) or (kind!='undo' and old['delta']!=delta):raise HTTPException(409,'Идентификатор уже использован для другого действия')
                return {'ok':True,'event_id':old['id'],'replayed':True}
            if kind=='undo':
                e=db.execute('SELECT * FROM collection_events WHERE id=? AND profile=? AND slug=? AND undo_of IS NULL',(undo,profile,slug)).fetchone()
                if not e:raise HTTPException(404,'Операция не найдена')
                if db.execute('SELECT 1 FROM collection_events WHERE profile=? AND undo_of=?',(profile,undo)).fetchone():raise HTTPException(409,'Операция уже отменена')
                delta=-e['delta']
            if type(delta) is not int or not -999<=delta<=999 or delta==0:raise HTTPException(422,'Количество: от 1 до 999')
            if (kind=='add' and delta<1) or (kind in ('open','gift') and delta!=-1):raise HTTPException(422,'Некорректное изменение количества')
            self.ensure_entry(db,profile,slug)
            current=db.execute('SELECT quantity FROM collection_entries WHERE profile=? AND slug=?',(profile,slug)).fetchone()[0]
            if not 0<=current+delta<=999:raise HTTPException(409,'Недостаточно бутылок или превышен предел 999')
            db.execute('UPDATE collection_entries SET quantity=quantity+?,version=version+1,updated=? WHERE profile=? AND slug=?',(delta,time.time(),profile,slug))
            ident=uuid.uuid4().hex;db.execute('INSERT INTO collection_events VALUES(?,?,?,?,?,?,?,?)',(ident,profile,slug,delta,kind,key,undo if kind=='undo' else None,time.time()))
        return {'ok':True,'event_id':ident}

    def save_list(self,profile,body):
        name=text(body.get('name',''),80,'Название подборки');goal=text(body.get('goal',''),100,'Повод');size=body.get('size',0)
        if not name or type(size) is not int or size not in (0,6,12,24):raise HTTPException(422,'Укажите название и размер подборки')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM collection_lists WHERE profile=?',(profile,)).fetchone()[0]>=40:raise HTTPException(422,'Можно создать до 40 подборок')
            ident=uuid.uuid4().hex;db.execute('INSERT INTO collection_lists VALUES(?,?,?,?,?,?)',(ident,profile,name,goal,size,time.time()))
        return {'id':ident}

    def list_action(self,profile,ident,body):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM collection_lists WHERE id=? AND profile=?',(ident,profile)).fetchone()
            if not row:raise HTTPException(404,'Подборка не найдена')
            action=body.get('action')
            if action=='delete':
                db.execute('DELETE FROM collection_messages WHERE list_id=?',(ident,))
                db.execute('DELETE FROM collection_members WHERE list_id=?',(ident,));db.execute('DELETE FROM collection_lists WHERE id=?',(ident,));db.execute('DELETE FROM collection_list_meta WHERE list_id=?',(ident,));db.execute('DELETE FROM collection_comments WHERE list_id=?',(ident,));db.execute('DELETE FROM collection_copies WHERE list_id=?',(ident,))
            elif action=='rename':
                name=text(body.get('name',''),80,'Название')
                if not name:raise HTTPException(422,'Введите название')
                db.execute('UPDATE collection_lists SET name=? WHERE id=?',(name,ident))
            elif action in ('add','remove'):
                slug=body.get('slug');self.product.require_slug(slug)
                if action=='remove':db.execute('DELETE FROM collection_members WHERE list_id=? AND slug=?',(ident,slug))
                else:
                    existing=db.execute('SELECT 1 FROM collection_members WHERE list_id=? AND slug=?',(ident,slug)).fetchone()
                    if existing:return {'ok':True}
                    used={r[0] for r in db.execute('SELECT slot FROM collection_members WHERE list_id=?',(ident,))}
                    if len(used)>= (row['size'] or 200):raise HTTPException(409,'Все места заполнены')
                    slot=body.get('slot')
                    if slot is None:slot=next(i for i in range(row['size'] or 200) if i not in used)
                    if type(slot) is not int or slot<0 or slot>=(row['size'] or 200) or slot in used:raise HTTPException(409,'Это место уже занято')
                    db.execute('INSERT INTO collection_members VALUES(?,?,?,?,?)',(ident,profile,slug,slot,time.time()))
            else:raise HTTPException(422,'Неизвестное действие')
        return {'ok':True}

    def routes(self,app,secure):
        self.shelves.routes(app)
        from fastapi import Request
        @app.get('/api/product/account')
        def account(request:Request):return self.account(request)
        @app.post('/api/product/account/{action}')
        def account_action(action:str,body:dict,request:Request):
            if action=='register':return self.register(request,body,secure)
            if action=='login':return self.login(request,body,secure)
            if action=='recover':return self.recover(request,body,secure)
            session=self.session(request)
            if not session:raise HTTPException(401,'Войдите в аккаунт')
            if action=='password':
                self.rate_limit(request,session['username'])
                current=valid_password(body.get('current_password',''));new=valid_password(body.get('new_password',''))
                with self.store.db() as db:row=db.execute('SELECT password_hash FROM wine_accounts WHERE id=?',(session['id'],)).fetchone()
                if not password_matches(current,row['password_hash']):raise HTTPException(403,'Текущий пароль не подходит')
                encoded=password_hash(new);recovery=secrets.token_hex(16)
                with self.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    changed=db.execute('UPDATE wine_accounts SET password_hash=?,recovery_hash=? WHERE id=? AND password_hash=?',(encoded,digest(recovery),session['id'],row['password_hash'])).rowcount
                    if not changed:raise HTTPException(409,'Пароль уже изменён. Войдите снова')
                    db.execute('DELETE FROM wine_account_sessions WHERE account=? AND id!=?',(session['id'],session['session_id']))
                return {'ok':True,'recovery_code':'-'.join(recovery[i:i+4] for i in range(0,32,4))}
            with self.store.db() as db:
                if action=='logout':
                    db.execute('DELETE FROM wine_account_sessions WHERE id=?',(session['session_id'],))
                    response=JSONResponse({'ok':True});response.delete_cookie(SESSION_COOKIE,secure=secure,httponly=True,samesite='lax');return response
                if action=='revoke':
                    db.execute('DELETE FROM wine_account_sessions WHERE id=? AND account=?',(body.get('session_id'),session['id']))
                elif action=='name':
                    name=text(body.get('display_name',''),60,'Имя')
                    if not name:raise HTTPException(422,'Введите имя')
                    db.execute('UPDATE wine_accounts SET display_name=? WHERE id=?',(name,session['id']))
                else:raise HTTPException(404,'Действие не найдено')
            return {'ok':True}
        @app.get('/api/product/collection')
        def collection(request:Request):return self.snapshot(request.state.profile)
        @app.post('/api/product/collection/wines/{slug}')
        def edit(slug:str,body:dict,request:Request):return self.edit(request.state.profile,slug,body)
        @app.post('/api/product/collection/stock/{slug}')
        def stock(slug:str,body:dict,request:Request):return self.stock(request.state.profile,slug,body)
        @app.post('/api/product/collection/lists')
        def lists(body:dict,request:Request):return self.save_list(request.state.profile,body)
        @app.post('/api/product/collection/lists/{ident}')
        def list_action(ident:str,body:dict,request:Request):return self.list_action(request.state.profile,ident,body)
