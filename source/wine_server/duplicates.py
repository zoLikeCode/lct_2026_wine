"""Human resolution of identical photos assigned to several catalog wines."""
import hashlib,json
from pathlib import Path
from curation import norm,stamp,sha

class Duplicates:
    def __init__(self,store):self.store=store

    def groups(self):
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute("""SELECT i.id,i.slug,i.path,i.sha256,i.pixel_sha256,
                i.width,i.height,i.kind,i.source,m.state FROM images i JOIN curation_meta m ON m.id=i.id
                WHERE i.path LIKE 'images/%'
                AND lower(coalesce(i.source,'')) NOT IN ('vino-svoe.ru','www.vino-svoe.ru')
                AND coalesce(i.kind,'')!='catalog_reference' ORDER BY i.id""")]
        parent={r['id']:r['id'] for r in rows};seen={}
        def find(ident):
            while parent[ident]!=ident:
                parent[ident]=parent[parent[ident]];ident=parent[ident]
            return ident
        for r in rows:
            for key in ('sha256','pixel_sha256'):
                value=r[key]
                if not value:continue
                token=(key,value)
                if token in seen:parent[find(r['id'])]=find(seen[token])
                else:seen[token]=r['id']
        grouped={}
        for r in rows:grouped.setdefault(find(r['id']),[]).append(r)
        return {min(r['id'] for r in rr):rr for rr in grouped.values() if len({r['slug'] for r in rr})>1}

    @staticmethod
    def version(rows):
        return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def photo(rows):
        return max(rows,key=lambda r:(int(r['width'])*int(r['height']),r['kind']=='catalog_reference',r['id']))['id']

    def summary(self,key,rows):
        slugs=sorted({r['slug'] for r in rows})
        return {'id':key,'photo_id':self.photo(rows),'copies':len(rows),'slugs':slugs,
            'wines':[{'slug':s,'name':self.store.catalog[s]['Название вина'].strip(),'winery':self.store.catalog[s]['Винодельня']} for s in slugs],
            'match':'identical_files' if len({r['sha256'] for r in rows})==1 else 'identical_pixels'}

    def list(self,q=''):
        groups=self.groups();tokens=norm(q).split();items=[]
        for key,rows in groups.items():
            entry=self.summary(key,rows)
            text=norm(' '.join(w['name']+' '+w['winery']+' '+w['slug'] for w in entry['wines']))
            if any(t not in text for t in tokens):continue
            items.append(entry)
        items.sort(key=lambda x:(norm(x['wines'][0]['winery']),norm(x['wines'][0]['name']),x['id']))
        with self.store.db() as db:
            last=db.execute("SELECT id,kind FROM curation_events WHERE state='done' AND kind IN ('assign','review','migration','duplicate') ORDER BY rowid DESC LIMIT 1").fetchone()
        return {'items':items,'total':len(groups),'filtered':len(items),'copies':sum(x['copies'] for x in items),
                'undo_available':bool(last and last['kind']=='duplicate')}

    def detail(self,ident):
        with self.store.guard():
            rows=self.groups().get(ident)
            if not rows:raise ValueError('Группа уже изменена или разобрана. Обновите список.')
            ids={r['id'] for r in rows};result=self.summary(ident,rows)
            wines={w['slug']:w for w in self.store.wines()}
            folders=[]
            with self.store.db() as db:
                for slug in result['slugs']:
                    others=[dict(r) for r in db.execute("""SELECT i.id,i.kind,i.source FROM images i
                        JOIN curation_meta m ON m.id=i.id WHERE i.slug=? AND i.path LIKE 'images/%'
                        AND m.state!='rejected' ORDER BY i.kind='catalog_reference' DESC,i.source='vino-svoe.ru' DESC,i.id""",(slug,)) if r['id'] not in ids]
                    folders.append({**wines[slug],'reference':others[0]['id'] if others else None,
                        'reference_label':'Эталон «Своё Вино» · не проверяется' if others and (others[0]['kind']=='catalog_reference' or others[0]['source']=='vino-svoe.ru') else 'Другое фото из этой папки',
                        'other_photos':[r['id'] for r in others[:8]],'copies':sum(r['slug']==slug for r in rows)})
            return {**result,'version':self.version(rows),'folders':folders,
                'files':[{'id':r['id'],'slug':r['slug'],'name':Path(r['path']).name,'source':r['source'],
                          'state':r['state'],'width':r['width'],'height':r['height']} for r in rows]}

    def resolve(self,ident,version,target=None,quarantine=False):
        if not isinstance(quarantine,bool):raise ValueError('Неизвестное действие')
        if quarantine:
            if target:raise ValueError('Выберите одно действие: папка или needs_review')
        elif not isinstance(target,str) or target not in self.store.catalog:raise ValueError('Выберите правильное вино')
        with self.store.guard():
            rows=self.groups().get(ident)
            if not rows or self.version(rows)!=version:raise ValueError('Группа изменилась. Обновите её перед сохранением.')
            # Check every original before journalling any move; no partial user decision.
            for row in rows:
                if sha(self.store.path(row['path']))!=row['sha256']:raise ValueError('Файл изменён: '+row['path'])
            keep={r['id'] for r in rows if not quarantine and r['slug']==target}
            if not quarantine and not keep:keep={self.photo(rows)}
            files=[];changes=[];reserved=set();moved=0
            with self.store.db() as db:
                for item in rows:
                    row=self.store.asset(db,item['id']);meta=self.store.meta(db,item['id']);accepted=item['id'] in keep
                    after=dict(row);after_meta={**meta,'state':'confirmed' if accepted else 'rejected','reviewed_at':stamp()}
                    dest_slug=target if accepted else row['slug'];bucket='images' if accepted else 'needs_review'
                    if not accepted or dest_slug!=row['slug']:
                        src=self.store.path(row['path']);dst=self.store.available(dest_slug,src.name,bucket,reserved);reserved.add(str(dst))
                        files.append({'src':str(src),'dst':str(dst),'sha':row['sha256'],'mode':'move'})
                        after['path']=str(dst.relative_to(self.store.root))
                    after.update(slug=dest_slug,title=self.store.catalog[dest_slug]['Название вина'],
                        status='accepted' if accepted else 'needs_review',validation='human_confirmed' if accepted else 'human_rejected')
                    changes.append({'row_before':row,'row_after':after,'meta_before':meta,'meta_after':after_meta})
                    if accepted and dest_slug!=row['slug']:
                        moved+=1
                        for link in db.execute('SELECT * FROM curation_sources WHERE asset_id=?',(row['id'],)):
                            old=dict(link);changes.append({'source_before':old,'source_after':{**old,'assigned_slug':dest_slug}})
                        for link in db.execute('SELECT * FROM scans WHERE asset_id=?',(row['id'],)):
                            old=dict(link);changes.append({'scan_before':old,'scan_after':{**old,'assigned_slug':dest_slug}})
            event=self.store.commit('duplicate',files,changes)
            return {'event':event,'kept':len(keep),'rejected':len(rows)-len(keep),'moved':moved,'slug':target if not quarantine else None}

    def undo(self):
        with self.store.guard():
            with self.store.db() as db:
                event=db.execute("SELECT payload FROM curation_events WHERE state='done' AND kind='duplicate' ORDER BY rowid DESC LIMIT 1").fetchone()
            group=min(c['row_before']['id'] for c in json.loads(event['payload'])['changes'] if c.get('row_before')) if event else None
            return {**self.store.undo('duplicate'),'group':group}
