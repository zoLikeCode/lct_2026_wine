#!/usr/bin/env python3
"""Rebuild a legacy collection using the front-view policy, preserving exclusions outside it."""
import argparse,collections,csv,json,shutil,sqlite3
from pathlib import Path
import collect as c

def rebuild(archive):
    archive=Path(archive).resolve()
    if archive==c.ROOT or c.ROOT in archive.parents:
        raise ValueError('The exclusion archive must be outside the delivered dataset')
    archive.mkdir(parents=True,exist_ok=True)
    backup=archive/'before.sqlite3'
    if not backup.exists():
        with sqlite3.connect(backup) as dest:c.DB.backup(dest)
        for name in ('manifest.csv','coverage.csv','summary.json','README.md'):
            if (c.ROOT/name).exists():shutil.copy2(c.ROOT/name,archive/name)
    specs={}
    for table in ('products','profiles'):
        for row in c.DB.execute('SELECT data FROM '+table):
            p=json.loads(row[0])
            for s in p.get('images',[]):specs[(p['url'],s['url'])]=s
    excluded=[];counts=collections.Counter()
    for row in [dict(r) for r in c.DB.execute('SELECT * FROM images')]:
        spec=specs.get((row['page_url'],row['image_url']),{'url':row['image_url'],'kind':row['kind']})
        decision=c.front_spec(spec)
        if decision is None:
            why=c.FRONT_DECISIONS.get(row['image_url'],{}).get('reason','excluded_role_'+spec['kind'])
            src=c.ROOT/row['path'];dest=archive/'excluded'/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
            if src.exists():src.replace(dest)
            elif not dest.exists():raise FileNotFoundError(src)
            excluded.append({**row,'exclusion_reason':why})
            c.DB.execute('DELETE FROM images WHERE id=?',(row['id'],))
            c.DB.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',(row['id'],row['slug'],row['image_url'],'excluded_non_front',why,c.now()))
            counts['excluded']+=1
        else:
            kind=decision['kind'];old=c.ROOT/row['path']
            folder='needs_review' if row['status']=='needs_review' else 'images'
            name=row['sha256'][:16]+'_'+kind+old.suffix
            dest=c.ROOT/folder/row['slug']/name;dest.parent.mkdir(parents=True,exist_ok=True)
            if old!=dest:old.replace(dest)
            reason=row['reason']
            for flag in ('; view_type_requires_review','; view_type_not_verified'):
                reason=reason.replace(flag,'')
            if row['image_url'] in c.FRONT_DECISIONS:
                if '; front_visible_visually_checked' not in reason:reason+='; front_visible_visually_checked'
                counts['visually_confirmed_front']+=1
            if kind!=row['kind']:counts['reclassified_front']+=1
            c.DB.execute('UPDATE images SET kind=?,path=?,reason=? WHERE id=?',(kind,str(dest.relative_to(c.ROOT)),reason,row['id']))
        c.DB.commit()
    log=c.ROOT/'excluded_non_front.jsonl'
    if excluded:
        with log.open('a') as f:
            for r in excluded:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    # Only remove generated empty directories. Preserve any user-added files.
    for base in ('auxiliary','needs_review'):
        directory=c.ROOT/base
        if directory.exists():
            for p in sorted(directory.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                if p.is_dir():
                    try:p.rmdir()
                    except OSError:pass
            try:directory.rmdir()
            except OSError:pass
    current={r['id']:r['path'] for r in c.DB.execute('SELECT id,path FROM images')}
    for name in ('label_text.csv','year_conflicts.csv'):
        p=c.ROOT/name
        if not p.exists():continue
        with p.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);fields=reader.fieldnames
            rows=[r for r in reader if r['id'] in current]
        for r in rows:
            if 'path' in r:r['path']=current[r['id']]
        c.write_csv(p,rows,fields)
    c.export();c.log('front_only_rebuild',**counts)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);args=p.parse_args()
    c.init(c.DEFAULT_CSV);rebuild(args.archive)
