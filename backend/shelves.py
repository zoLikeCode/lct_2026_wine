"""Named shelves, explicit publication, independent copies and scan-earned badges."""
import json
import secrets
import time
import uuid
import re
import os
from pathlib import Path
from fastapi import HTTPException, Request
from collection import text
from product import normalized

EDITORIAL_PROFILE = 'system:editorial-collections'


class Shelves:
    def __init__(self, collection):
        self.collection = collection
        self.store = collection.store
        self.product = collection.product
        with self.store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS collection_list_meta(
              list_id TEXT PRIMARY KEY,visibility TEXT NOT NULL DEFAULT 'private',
              share_token TEXT UNIQUE,description TEXT NOT NULL DEFAULT '',source_id TEXT,
              updated REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS collection_comments(
              list_id TEXT,profile TEXT,body TEXT NOT NULL,created REAL,updated REAL,
              PRIMARY KEY(list_id,profile));
            CREATE TABLE IF NOT EXISTS collection_copies(
              profile TEXT,source_id TEXT,list_id TEXT UNIQUE,PRIMARY KEY(profile,source_id));
            CREATE TABLE IF NOT EXISTS collection_badges(
              profile TEXT,challenge_id TEXT,title TEXT,earned REAL,PRIMARY KEY(profile,challenge_id));
            CREATE TABLE IF NOT EXISTS collection_demo_authors(
              profile TEXT PRIMARY KEY,name TEXT NOT NULL,bio TEXT NOT NULL,achievements TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collection_saves(
              profile TEXT,source_id TEXT,created REAL NOT NULL,PRIMARY KEY(profile,source_id));
            CREATE TABLE IF NOT EXISTS collection_messages(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,list_id TEXT NOT NULL,
              profile TEXT NOT NULL,body TEXT NOT NULL,reply_to TEXT,created REAL NOT NULL,
              updated REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS collection_messages_list ON collection_messages(list_id,seq);
            CREATE TABLE IF NOT EXISTS collection_schema_migrations(name TEXT PRIMARY KEY);
            ''')
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO collection_saves SELECT profile,source_id,0 FROM collection_copies')
            if not db.execute("SELECT 1 FROM collection_schema_migrations WHERE name='discussion-v1'").fetchone():
                for row in db.execute('SELECT * FROM collection_comments ORDER BY created').fetchall():
                    ident = uuid.uuid5(uuid.NAMESPACE_URL, row['list_id'] + ':' + row['profile']).hex
                    db.execute('INSERT OR IGNORE INTO collection_messages(id,list_id,profile,body,created,updated) VALUES(?,?,?,?,?,?)',
                               (ident,row['list_id'],row['profile'],row['body'],row['created'],row['updated']))
                db.execute("INSERT INTO collection_schema_migrations VALUES('discussion-v1')")
        path = Path(__file__).parent / 'content/collection_challenge.json'
        self.challenge_config = json.loads(path.read_text()) if path.exists() else {}
        self.seed_editorial()
        # Approved example content ships with every release, including production.
        # An explicit 0 hides it without deleting discussions or users' copies.
        self.demo_enabled = os.environ.get('WINE_DEMO_COMMUNITY', '1') == '1'
        if self.demo_enabled:
            self.seed_demo()

    def seed_demo(self):
        """Disclosed examples, isolated from accounts, scans and earned rewards."""
        if not self.demo_enabled:
            return
        path = Path(__file__).parent / 'content/demo_community.json'
        content = json.loads(path.read_text())
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for author in content['profiles']:
                profile = 'system:demo:' + author['id']
                db.execute('INSERT INTO collection_demo_authors VALUES(?,?,?,?) ON CONFLICT(profile) DO UPDATE SET name=excluded.name,bio=excluded.bio,achievements=excluded.achievements',
                           (profile, author['name'], author['bio'], json.dumps(author['achievements'])))
                for item in author['collections']:
                    if not item['slugs'] or not all(s in self.product.cards for s in item['slugs']):
                        continue
                    existing = db.execute('SELECT profile FROM collection_lists WHERE id=?', (item['id'],)).fetchone()
                    if existing and existing['profile'] != profile:
                        continue
                    now = time.time()
                    db.execute('INSERT INTO collection_lists VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',
                               (item['id'], profile, item['name'], '', 0, now))
                    db.execute('INSERT INTO collection_list_meta(list_id,visibility,share_token,description,updated) VALUES(?,?,?,?,?) ON CONFLICT(list_id) DO UPDATE SET description=excluded.description',
                               (item['id'], 'public', secrets.token_urlsafe(24), item['description'], now))
                    current = [r[0] for r in db.execute('SELECT slug FROM collection_members WHERE list_id=? ORDER BY slot', (item['id'],))]
                    if current != item['slugs']:
                        db.execute('DELETE FROM collection_members WHERE list_id=?', (item['id'],))
                        for slot, slug in enumerate(item['slugs']):
                            db.execute('INSERT INTO collection_members VALUES(?,?,?,?,?)', (item['id'], profile, slug, slot, now))
            for thread in content.get('discussions', []):
                ident=thread['collection']
                if not db.execute("SELECT 1 FROM collection_lists WHERE id=? AND profile LIKE 'system:demo:%'",(ident,)).fetchone():
                    continue
                ids=[]
                for n,message in enumerate(thread['messages']):
                    author='system:demo:'+message['author']
                    if not db.execute('SELECT 1 FROM collection_demo_authors WHERE profile=?',(author,)).fetchone():continue
                    mid=uuid.uuid5(uuid.NAMESPACE_URL,'wine-example-discussion-v1:'+ident+':'+str(n)).hex
                    reply=ids[message['reply_to']] if 'reply_to' in message and message['reply_to']<len(ids) else None
                    now=time.time()
                    db.execute('INSERT OR IGNORE INTO collection_messages(id,list_id,profile,body,reply_to,created,updated) VALUES(?,?,?,?,?,?,?)',
                               (mid,ident,author,message['body'],reply,now,now))
                    ids.append(mid)

    def seed_status(self):
        """Only public seed-content counts; no account or private collection data."""
        with self.store.db() as db:
            editorial = db.execute("""SELECT count(*) FROM collection_lists l
                JOIN collection_list_meta m ON m.list_id=l.id
                WHERE l.profile=? AND m.visibility='public'""", (EDITORIAL_PROFILE,)).fetchone()[0]
            examples = messages = 0
            if self.demo_enabled:
                examples = db.execute("""SELECT count(*) FROM collection_lists l
                    JOIN collection_list_meta m ON m.list_id=l.id
                    WHERE l.profile LIKE 'system:demo:%' AND m.visibility='public'""").fetchone()[0]
                messages = db.execute("""SELECT count(*) FROM collection_messages c
                    JOIN collection_lists l ON l.id=c.list_id
                    JOIN collection_list_meta m ON m.list_id=l.id
                    WHERE l.profile LIKE 'system:demo:%' AND m.visibility='public'
                    AND c.profile LIKE 'system:demo:%' AND c.deleted=0""").fetchone()[0]
        return dict(examples_enabled=self.demo_enabled, editorial_collections=editorial,
                    example_collections=examples, example_messages=messages)

    def author_info(self, db, profile):
        from achievements import ACHIEVEMENTS, award_collection_badges
        demo = db.execute('SELECT * FROM collection_demo_authors WHERE profile=?', (profile,)).fetchone()
        account = db.execute('SELECT display_name FROM wine_accounts WHERE profile=?', (profile,)).fetchone()
        if demo:
            earned = set(json.loads(demo['achievements']))
        else:
            award_collection_badges(db, profile)
            earned = {r[0] for r in db.execute('SELECT challenge_id FROM collection_badges WHERE profile=?', (profile,))}
        badges = [dict(id=a[0], title=a[1], status=a[2], image='/assets/achievements/'+a[5]+'.webp') for a in ACHIEVEMENTS if a[0] in earned]
        status = next((a[2] for a in reversed(ACHIEVEMENTS) if a[3]=='collection_saves' and a[0] in earned), '') or next((a[2] for a in reversed(ACHIEVEMENTS[:4]) if a[0] in earned), '')
        return {'name': 'Редакция приложения' if profile == EDITORIAL_PROFILE else (demo['name'] if demo else account['display_name'] if account else 'Автор коллекции'),
                'bio': demo['bio'] if demo else '', 'demo': bool(demo), 'status': status, 'badges': badges}

    def public_author(self, profile, ident):
        """A public shelf is the entry point; never expose internal profile/session IDs."""
        with self.store.db() as db:
            row, meta = self.access(db, ident, profile)
            if meta['visibility'] != 'public':
                raise HTTPException(404, 'Профиль недоступен')
            author = self.author_info(db, row['profile'])
            ids = [r[0] for r in db.execute("SELECT l.id FROM collection_lists l JOIN collection_list_meta m ON m.list_id=l.id WHERE l.profile=? AND m.visibility='public' ORDER BY m.updated DESC,l.id", (row['profile'],))]
        author['collections'] = [self.detail(profile, key) for key in ids]
        return author

    def seed_editorial(self):
        """Versioned first-party collections, never fake users, votes or scans."""
        path = Path(__file__).parent / 'content/editorial_collections.json'
        self.editorial = json.loads(path.read_text()) if path.exists() else {'collections': []}
        self.editorial_by_id = {item['id']: item for item in self.editorial['collections']}
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in self.editorial['collections']:
                slugs = item['slugs']
                if not slugs or not all(s in self.product.cards for s in slugs):
                    continue
                existing = db.execute('SELECT profile FROM collection_lists WHERE id=?', (item['id'],)).fetchone()
                if existing and existing['profile'] != EDITORIAL_PROFILE:
                    continue
                now = time.time()
                db.execute('''INSERT INTO collection_lists VALUES(?,?,?,?,?,?) ON CONFLICT(id)
                    DO UPDATE SET name=excluded.name,goal=excluded.goal''',
                    (item['id'], EDITORIAL_PROFILE, item['name'], item['theme'], 0, now))
                db.execute('''INSERT INTO collection_list_meta(list_id,visibility,share_token,description,updated)
                    VALUES(?,?,?,?,?) ON CONFLICT(list_id) DO UPDATE SET description=excluded.description''',
                    (item['id'], 'public', secrets.token_urlsafe(24), item['description'], now))
                current = [r[0] for r in db.execute('SELECT slug FROM collection_members WHERE list_id=? ORDER BY slot', (item['id'],))]
                if current != slugs:
                    db.execute('DELETE FROM collection_members WHERE list_id=?', (item['id'],))
                    for slot, slug in enumerate(slugs):
                        db.execute('INSERT INTO collection_members VALUES(?,?,?,?,?)', (item['id'], EDITORIAL_PROFILE, slug, slot, now))

    def metadata(self, db, ident):
        row = db.execute('SELECT * FROM collection_list_meta WHERE list_id=?', (ident,)).fetchone()
        return dict(row) if row else {'visibility': 'private', 'share_token': None, 'description': '', 'source_id': None}

    def augment(self, profile, snapshot):
        with self.store.db() as db:
            for item in snapshot['lists']:
                item.update(self.metadata(db, item['id']))
        snapshot['counts']['liked_rated'] = sum(bool(w['favorite'] or w['rating']) for w in snapshot['items'])
        snapshot['challenge'] = self.challenge(profile)
        from achievements import achievement_snapshot
        snapshot['achievements'] = achievement_snapshot(self.store, profile, self.product.cards)
        return snapshot

    def access(self, db, ident, profile):
        row = db.execute('SELECT * FROM collection_lists WHERE id=?', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Коллекция недоступна')
        meta = self.metadata(db, ident)
        if row['profile'].startswith('system:demo:') and not self.demo_enabled:
            raise HTTPException(404, 'Коллекция недоступна')
        if row['profile'] != profile and meta['visibility'] != 'public':
            raise HTTPException(404, 'Коллекция недоступна')
        return dict(row), meta

    def detail(self, profile, ident=None, token=None):
        with self.store.db() as db:
            if token:
                row = db.execute("SELECT list_id FROM collection_list_meta WHERE share_token=? AND visibility='public'", (token,)).fetchone()
                if not row: raise HTTPException(404, 'Ссылка закрыта или коллекция больше не опубликована')
                ident = row['list_id']
            row, meta = self.access(db, ident, profile)
            slugs = [r['slug'] for r in db.execute('SELECT slug FROM collection_members WHERE list_id=? ORDER BY slot,created', (ident,))]
            author = self.author_info(db, row['profile'])
            discussion = self.discussion_rows(db, profile, ident)
            copies = self.save_count(db, ident)
            copied = db.execute('SELECT c.list_id FROM collection_copies c JOIN collection_lists l ON l.id=c.list_id WHERE c.profile=? AND source_id=?', (profile, ident)).fetchone()
        return {'id': ident, 'name': row['name'], 'size': row['size'], 'goal': row['goal'],
                'description': meta['description'], 'visibility': meta['visibility'],
                'share_token': meta['share_token'], 'owner': row['profile'] == profile,
                'author': author['name'], 'author_status': author['status'], 'demo': author['demo'],
                'author_link': ident if meta['visibility'] == 'public' and row['profile'] != EDITORIAL_PROFILE else None,
                'editorial': row['profile'] == EDITORIAL_PROFILE,
                'theme': self.editorial_by_id.get(ident, {}).get('theme', ''),
                'wines': self.product.cards_for(slugs, profile), 'comments': discussion['items'],
                'comments_total': discussion['total'], 'comments_before': discussion['next_before'],
                'copies': copies, 'copied_id': copied['list_id'] if copied else None}

    def feed(self, profile, q='', offset=0, author='all'):
        if len(q) > 100 or not 0 <= offset <= 10000 or author not in ('all', 'editorial', 'people'):
            raise HTTPException(422, 'Некорректный поиск')
        with self.store.db() as db:
            rows = db.execute('''SELECT l.id,l.name,l.profile FROM collection_lists l JOIN collection_list_meta m ON m.list_id=l.id
                  WHERE m.visibility='public' ORDER BY m.updated DESC,l.id''').fetchall()
        rows = [r for r in rows if q.casefold() in r['name'].casefold() and (self.demo_enabled or not r['profile'].startswith('system:demo:'))]
        all_total = len(rows)
        if author != 'all':
            rows = [r for r in rows if (r['profile'] == EDITORIAL_PROFILE) == (author == 'editorial')]
        selected = rows[offset:offset+24]
        return {'items': [self.detail(profile, r['id']) for r in selected], 'total': len(rows), 'all_total': all_total,
                'next': offset+24 if len(rows)>offset+24 else None}

    def recommendations(self, profile, query, limit=3):
        """Recommend actual accessible shelves; never infer access from chat text."""
        own_only=bool(re.search(r'\b(?:моих|моей|мои|мою|моя|своих|своей)\s+(?:коллекц|подборк|полк)\w*',normalized(query)))
        result=[{'id':detail['id'],'name':detail['name'],'owner':detail['owner'],
                 'visibility':detail['visibility'],'wine_count':len(detail['wines']),'matched_count':len(matched),
                 'wines':detail['wines'][:4],'reason':reason}
                for detail,matched,reason in self.ranked(profile,query,limit,'own' if own_only else 'any')]
        return {'recommendation_type':'collection','collections':result,'items':[],
            'text':('Вот коллекции под ваш запрос. В каждой отметил подходящие вина.' if result else
                    'Подходящих коллекций пока нет. Попробуйте другое блюдо или сохраните свою подборку в разделе «Коллекция».'),
            'mode':'catalog','warning':None}

    def for_you(self, profile, limit=4):
        """«Коллекции для вас» outside the chat: public shelves of other people the user has not saved
        yet, ranked by taste exactly as the chat ranks them. Nothing personal is known about a
        newcomer, so they get an invitation instead of a random pick."""
        if type(limit) is not int or not 1<=limit<=12:raise HTTPException(422,'limit от 1 до 12')
        if not self.product.recs.has_taste(profile):
            return {'items':[],'needs_taste':True,
                    'empty':'Оцените пару вин или пройдите анкету вкуса — и здесь появятся коллекции под ваш вкус'}
        items=[{**detail,'reason':reason} for detail,_,reason in self.ranked(profile,'',limit,'others')]
        return {'items':items,'needs_taste':False,'empty':None if items else 'Пока нет публичных коллекций под ваш вкус'}

    def ranked(self, profile, query, limit, scope='any'):
        """Shelves ranked by how well their wines fit the query and the user's taste (the scores of
        the wine feed): [(detail, matched slugs best first, reason)]. scope: 'any' — own and public,
        'own' — own only, 'others' — public shelves of other people not saved by the user yet."""
        q=normalized(query)
        ranked_wines={slug:(score,reason) for score,slug,reason in self.product.recommendation_candidates(profile,query)}
        # Access is constrained in SQL before either names or members are inspected.
        if scope=='others':
            where,args="l.profile!=? AND m.visibility='public' AND NOT EXISTS(SELECT 1 FROM collection_saves s WHERE s.profile=? AND s.source_id=l.id)",(profile,profile)
        else:
            where,args="l.profile=? OR (?=0 AND m.visibility='public')",(profile,int(scope=='own'))
        with self.store.db() as db:
            rows=db.execute(f'''SELECT l.id,l.name,l.profile,m.visibility,c.slug,c.slot
                FROM collection_lists l LEFT JOIN collection_list_meta m ON m.list_id=l.id
                JOIN collection_members c ON c.list_id=l.id
                WHERE {where}
                ORDER BY l.id,c.slot,c.created''',args).fetchall()
        grouped={}
        for row in rows:
            if row['slug'] not in self.product.cards:continue
            item=grouped.setdefault(row['id'],{'id':row['id'],'name':row['name'],
                'owner':row['profile']==profile,'visibility':row['visibility'] or 'private','slugs':[]})
            item['slugs'].append(row['slug'])
        candidates=[]
        for shelf in grouped.values():
            matched=[slug for slug in shelf['slugs'] if slug in ranked_wines]
            if not matched:continue
            matched.sort(key=lambda slug:-ranked_wines[slug][0])
            coverage=len(matched)/len(shelf['slugs'])
            score=sum(ranked_wines[s][0] for s in matched)/len(matched)+coverage*5
            if normalized(shelf['name']) in q:score+=8
            candidates.append((score,shelf,matched))
        candidates.sort(key=lambda x:(-x[0],not x[1]['owner'],x[1]['name'],x[1]['id']))
        result=[];seen_contents=set()
        for _,shelf,matched in candidates:
            # Recheck publication at response time; a revoked collection must vanish.
            try:detail=self.detail(profile,shelf['id'])
            except HTTPException as error:
                if error.status_code==404:continue
                raise
            if not detail['wines']:continue
            current={w['slug'] for w in detail['wines']}
            fingerprint=frozenset(current)
            if fingerprint in seen_contents:continue
            matched=[s for s in matched if s in current]
            if not matched:continue
            best=matched[0];wine=self.product.cards[best]
            seen_contents.add(fingerprint)
            why=ranked_wines[best][1]
            result.append((detail,matched,'«'+wine['name']+'» — '+why[:1].lower()+why[1:]))
            if len(result)>=limit:break
        return result

    def settings(self, profile, ident, body, authenticated):
        visibility = body.get('visibility', 'private')
        if visibility not in ('private', 'public'): raise HTTPException(422, 'Выберите видимость')
        if visibility == 'public' and not authenticated: raise HTTPException(401, 'Войдите, чтобы опубликовать коллекцию')
        name = text(body.get('name', ''), 80, 'Название')
        description = text(body.get('description', ''), 800, 'Описание')
        if not name: raise HTTPException(422, 'Введите название коллекции')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT profile FROM collection_lists WHERE id=? AND profile=?', (ident, profile)).fetchone()
            if not row: raise HTTPException(404, 'Коллекция недоступна')
            old = self.metadata(db, ident)
            token = (old['share_token'] or secrets.token_urlsafe(24)) if visibility == 'public' else None
            db.execute('UPDATE collection_lists SET name=? WHERE id=?', (name, ident))
            db.execute('''INSERT INTO collection_list_meta(list_id,visibility,share_token,description,updated) VALUES(?,?,?,?,?)
                ON CONFLICT(list_id) DO UPDATE SET visibility=excluded.visibility,share_token=excluded.share_token,
                description=excluded.description,updated=excluded.updated''', (ident, visibility, token, description, time.time()))
        return self.detail(profile, ident)

    def copy(self, profile, ident):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, meta = self.access(db, ident, profile)
            if row['profile'] == profile: return {'id': ident, 'existing': True}
            db.execute('INSERT OR IGNORE INTO collection_saves VALUES(?,?,?)',(profile,ident,time.time()))
            from achievements import award_collection_badges
            award_collection_badges(db,row['profile'])
            existing = db.execute('SELECT c.list_id FROM collection_copies c JOIN collection_lists l ON l.id=c.list_id WHERE c.profile=? AND source_id=?', (profile, ident)).fetchone()
            if existing: return {'id': existing['list_id'], 'existing': True}
            if db.execute('SELECT count(*) FROM collection_lists WHERE profile=?', (profile,)).fetchone()[0] >= 40:
                raise HTTPException(422, 'Можно сохранить до 40 коллекций')
            new = uuid.uuid4().hex
            db.execute('INSERT INTO collection_lists VALUES(?,?,?,?,?,?)', (new, profile, row['name'], row['goal'], row['size'], time.time()))
            db.execute('INSERT INTO collection_members SELECT ?,?,slug,slot,? FROM collection_members WHERE list_id=?', (new, profile, time.time(), ident))
            db.execute('INSERT INTO collection_list_meta(list_id,description,source_id,updated) VALUES(?,?,?,?)', (new, meta['description'], ident, time.time()))
            db.execute('INSERT INTO collection_copies VALUES(?,?,?) ON CONFLICT(profile,source_id) DO UPDATE SET list_id=excluded.list_id', (profile, ident, new))
        return {'id': new, 'existing': False}

    def copy_challenge(self, profile):
        challenge = self.challenge(profile)
        if not challenge['active']:
            raise HTTPException(404, 'Подборка пока недоступна')
        source = 'challenge:' + challenge['id']
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT c.list_id FROM collection_copies c JOIN collection_lists l ON l.id=c.list_id WHERE c.profile=? AND c.source_id=?', (profile, source)).fetchone()
            if row: return {'id': row['list_id'], 'existing': True}
            if db.execute('SELECT count(*) FROM collection_lists WHERE profile=?', (profile,)).fetchone()[0] >= 40:
                raise HTTPException(422, 'Можно сохранить до 40 коллекций')
            ident = uuid.uuid4().hex
            now = time.time()
            db.execute('INSERT INTO collection_lists VALUES(?,?,?,?,?,?)', (ident,profile,challenge['title'],'Открытия',0,now))
            for slot, wine in enumerate(challenge['wines']):
                db.execute('INSERT INTO collection_members VALUES(?,?,?,?,?)', (ident,profile,wine['slug'],slot,now))
            db.execute('INSERT INTO collection_list_meta(list_id,description,source_id,updated) VALUES(?,?,?,?)', (ident,challenge['description'],source,now))
            db.execute('INSERT INTO collection_copies VALUES(?,?,?) ON CONFLICT(profile,source_id) DO UPDATE SET list_id=excluded.list_id', (profile,source,ident))
        return {'id': ident, 'existing': False}

    def comment(self, profile, ident, body, authenticated):
        if not authenticated: raise HTTPException(401, 'Войдите, чтобы участвовать в обсуждении')
        note = text(body.get('body', ''), 800, 'Сообщение')
        message_id = body.get('id')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.access(db, ident, profile)
            if message_id:
                if not isinstance(message_id,str): raise HTTPException(422,'Некорректное сообщение')
                old=db.execute('SELECT * FROM collection_messages WHERE id=? AND list_id=? AND profile=?',(message_id,ident,profile)).fetchone()
                if not old: raise HTTPException(404,'Сообщение недоступно')
                if note: raise HTTPException(422,'Чтобы дополнить мысль, отправьте новое сообщение')
                db.execute('UPDATE collection_messages SET body=\'\',deleted=1,updated=? WHERE id=?',(time.time(),message_id))
                return {'ok':True,'id':message_id}
            if not note: raise HTTPException(422,'Введите сообщение')
            message_id=body.get('client_id') or uuid.uuid4().hex
            if not isinstance(message_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}',message_id):
                raise HTTPException(422,'Некорректный идентификатор сообщения')
            old=db.execute('SELECT list_id,profile,body,reply_to FROM collection_messages WHERE id=?',(message_id,)).fetchone()
            if old:
                if old['list_id']!=ident or old['profile']!=profile or old['body']!=note or old['reply_to']!=body.get('reply_to'):
                    raise HTTPException(409,'Это сообщение уже отправлено')
                return {'ok':True,'id':message_id}
            reply=body.get('reply_to')
            if reply is not None and (not isinstance(reply,str) or not db.execute('SELECT 1 FROM collection_messages WHERE id=? AND list_id=? AND deleted=0',(reply,ident)).fetchone()):
                raise HTTPException(422,'Сообщение для ответа недоступно')
            now=time.time()
            db.execute('INSERT INTO collection_messages(id,list_id,profile,body,reply_to,created,updated) VALUES(?,?,?,?,?,?,?)',
                       (message_id,ident,profile,note,reply,now,now))
        return {'ok': True, 'id':message_id}

    def discussion_rows(self, db, profile, ident, before=None):
        rows=db.execute('''SELECT m.*,COALESCE(a.display_name,d.name) AS display_name,p.body AS reply_body,p.deleted AS reply_deleted,
            COALESCE(pa.display_name,pd.name) AS reply_author FROM collection_messages m
            LEFT JOIN wine_accounts a ON a.profile=m.profile
            LEFT JOIN collection_demo_authors d ON d.profile=m.profile
            LEFT JOIN collection_messages p ON p.id=m.reply_to AND p.list_id=m.list_id
            LEFT JOIN wine_accounts pa ON pa.profile=p.profile
            LEFT JOIN collection_demo_authors pd ON pd.profile=p.profile
            WHERE m.list_id=? AND (? IS NULL OR m.seq<?) ORDER BY m.seq DESC LIMIT 51''',(ident,before,before)).fetchall()
        more=len(rows)>50; rows=rows[:50]
        items=[dict(id=r['id'],seq=r['seq'],author=r['display_name'] or 'Участник',body=r['body'],
                    created=r['created'],updated=r['updated'],mine=r['profile']==profile,deleted=bool(r['deleted']),
                    example=r['profile'].startswith('system:demo:'),
                    reply_to=r['reply_to'],reply_author=r['reply_author'] or 'Участник',
                    reply_body=('Сообщение удалено' if r['reply_deleted'] else r['reply_body']) if r['reply_to'] else None)
               for r in reversed(rows)]
        return {'items':items,'next_before':items[0]['seq'] if more else None,
                'total':db.execute('SELECT count(*) FROM collection_messages WHERE list_id=?',(ident,)).fetchone()[0]}

    def discussion(self, profile, ident, before=None):
        if before is not None and before<1: raise HTTPException(422,'Некорректная страница')
        with self.store.db() as db:
            self.access(db,ident,profile)
            return self.discussion_rows(db,profile,ident,before)

    def save_count(self, db, ident):
        return db.execute('''SELECT count(DISTINCT s.profile) FROM collection_saves s
            JOIN wine_accounts a ON a.profile=s.profile JOIN collection_lists l ON l.id=s.source_id
            WHERE s.source_id=? AND s.profile!=l.profile AND s.profile NOT LIKE 'system:%' ''',(ident,)).fetchone()[0]

    def ranking(self, profile, slug=None):
        if slug is not None:
            self.product.require_slug(slug)
        with self.store.db() as db:
            rows=db.execute('''SELECT l.id,count(DISTINCT a.profile) AS saves FROM collection_lists l
                JOIN collection_list_meta m ON m.list_id=l.id
                LEFT JOIN collection_saves s ON s.source_id=l.id AND s.profile!=l.profile AND s.profile NOT LIKE 'system:%'
                LEFT JOIN wine_accounts a ON a.profile=s.profile
                WHERE m.visibility='public' AND l.profile NOT LIKE 'system:demo:%'
                AND EXISTS(SELECT 1 FROM collection_members c WHERE c.list_id=l.id)
                AND (? IS NULL OR EXISTS(SELECT 1 FROM collection_members c WHERE c.list_id=l.id AND c.slug=?))
                GROUP BY l.id ORDER BY saves DESC,l.created,l.id LIMIT ?''',(slug,slug,5 if slug is not None else 50)).fetchall()
        items=[]
        for row in rows:
            try:
                item=self.detail(profile,row['id'])
            except HTTPException as error:
                if error.status_code==404: continue
                raise
            # Recheck current visibility and membership after the ranking query.
            if item['visibility']!='public' or (slug is not None and not any(w['slug']==slug for w in item['wines'])):
                continue
            if slug is not None:
                item={key:item[key] for key in ('id','name','author','wines','copies')}
            items.append(item)
        return {'items':items}

    def reorder(self, profile, ident, body):
        slugs = body.get('slugs')
        if not isinstance(slugs, list) or any(not isinstance(s,str) for s in slugs) or len(slugs) > 200 or len(set(slugs)) != len(slugs):
            raise HTTPException(422, 'Некорректный порядок бутылок')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, _ = self.access(db, ident, profile)
            if row['profile'] != profile: raise HTTPException(404, 'Коллекция недоступна')
            actual = {r[0] for r in db.execute('SELECT slug FROM collection_members WHERE list_id=?', (ident,))}
            if actual != set(slugs): raise HTTPException(409, 'Коллекция изменилась. Обновите страницу')
            db.execute('UPDATE collection_members SET slot=-slot-1 WHERE list_id=?', (ident,))
            for slot, slug in enumerate(slugs): db.execute('UPDATE collection_members SET slot=? WHERE list_id=? AND slug=?', (slot, ident, slug))
        return {'ok': True}

    def challenge(self, profile):
        config = self.challenge_config
        entries = config.get('wines', [])
        valid = len(entries) == 5 and len({x.get('slug') for x in entries}) == 5 and all(
            x.get('slug') in self.product.cards and isinstance(x.get('price'), (int,float)) and x['price'] > 0
            and str(x.get('source_url','')).startswith('https://') and x.get('checked_at') for x in entries)
        if not valid:
            return {'active': False, 'title': 'Пять дорогих открытий', 'badge': 'Коллекционер редкостей', 'progress': 0,
                    'message': 'Подборка появится после подтверждения цен на пять вин.'}
        from achievements import scanned_wines
        ident = config['id']; needed = {x['slug'] for x in entries}
        found = scanned_wines(self.store, profile, self.product.cards) & needed
        with self.store.db() as db:
            if len(found) == 5:
                db.execute('INSERT OR IGNORE INTO collection_badges VALUES(?,?,?,?)', (profile,ident,config.get('badge','Коллекционер редкостей'),time.time()))
            earned = db.execute('SELECT earned,title FROM collection_badges WHERE profile=? AND challenge_id=?', (profile,ident)).fetchone()
        wines = self.product.cards_for([x['slug'] for x in entries], profile)
        for w, item in zip(wines, entries):
            w.update(price=item['price'],source_url=item['source_url'],checked_at=item['checked_at'],
                     vintage=item.get('vintage',''),volume=item.get('volume',''),
                     price_note=item.get('price_note',''),source_name=item.get('source_name','Магазин'),scanned=w['slug'] in found)
        return {'active': True, 'id': ident, 'title': config.get('title','Пять дорогих открытий'),
                'description': config.get('description',''), 'price_note': config.get('price_note',''), 'badge': config.get('badge','Коллекционер редкостей'),
                'progress': len(found), 'earned': bool(earned), 'earned_at': earned['earned'] if earned else None, 'wines': wines}

    def routes(self, app):
        @app.get('/api/product/shelves')
        def feed(request: Request, q: str='', offset: int=0, author: str='all'):
            return self.feed(request.state.profile,q,offset,author)
        @app.get('/api/product/shelves/ranking')
        def ranking(request:Request,slug:str|None=None): return self.ranking(request.state.profile,slug)
        @app.get('/api/product/shelves/for-you')
        def for_you(request:Request,limit:int=4): return self.for_you(request.state.profile,limit)
        @app.get('/api/product/shelves/{ident}/discussion')
        def discussion(ident:str,request:Request,before:int|None=None):
            return self.discussion(request.state.profile,ident,before)
        @app.get('/api/product/shelves/shared/{token}')
        def shared(token:str, request:Request): return self.detail(request.state.profile,token=token)
        @app.get('/api/product/shelves/authors/{ident}')
        def author(ident:str, request:Request): return self.public_author(request.state.profile,ident)
        @app.get('/api/product/shelves/{ident}')
        def detail(ident:str, request:Request): return self.detail(request.state.profile,ident)
        @app.post('/api/product/shelves/{ident}/{action}')
        def action(ident:str, action:str, body:dict, request:Request):
            profile=request.state.profile
            authenticated=bool(self.collection.session(request))
            if ident=='challenge' and action=='copy': return self.copy_challenge(profile)
            if action=='settings': return self.settings(profile,ident,body,authenticated)
            if action=='copy': return self.copy(profile,ident)
            if action=='comment': return self.comment(profile,ident,body,authenticated)
            if action=='reorder': return self.reorder(profile,ident,body)
            raise HTTPException(404, 'Действие не найдено')
