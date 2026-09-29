"""Apply reviewed image replacements, retaining paths, labels and annotations.

The staged bundle is content-addressed. Every replacement is checked against
the frozen scope AND the current database/file, under the curation filesystem
lock. Originals and per-image recovery journals live outside images/.
"""
import argparse,contextlib,fcntl,hashlib,json,os,shutil,sqlite3,time,uuid
from pathlib import Path
from PIL import Image,ImageOps

def digest(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def stamp():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

def write_json(p,value):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
 with tmp.open('w') as f:json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
 tmp.replace(p)

@contextlib.contextmanager
def locked(root):
 with (root/'curation/.lock').open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX)
  try:yield
  finally:fcntl.flock(f,fcntl.LOCK_UN)

def image_size(p):
 with Image.open(p) as im:
  im.load();return ImageOps.exif_transpose(im).size

def safe_path(root,relative):
 p=(root/relative).resolve()
 if not p.is_relative_to(root) or Path(relative).parts[0]!='images':raise ValueError('Not a current images/ path')
 return p

def apply_one(root,work,db,scope_row,meta,bundle):
 ident=scope_row['id'];oldhash=scope_row['sha256'];newhash=meta['sha256']
 current=db.execute('SELECT * FROM images WHERE id=?',(ident,)).fetchone()
 if not current:return {'id':ident,'state':'skipped_missing_record'}
 current=dict(current)
 if not current['path'].startswith('images/'):return {'id':ident,'state':'skipped_moved_out_of_images'}
 if current['sha256']==newhash:
  p=safe_path(root,current['path'])
  if digest(p)!=newhash:raise ValueError('Already applied metadata conflicts with file')
  return {'id':ident,'state':'already_applied'}
 if current['sha256']!=oldhash:return {'id':ident,'state':'skipped_changed_content'}
 p=safe_path(root,current['path'])
 if not p.is_file() or digest(p)!=oldhash:return {'id':ident,'state':'skipped_changed_file'}
 replacement=bundle/Path(meta['output']).name
 if not replacement.is_file() or digest(replacement)!=newhash:raise ValueError('Invalid staged replacement checksum')
 size=image_size(replacement)
 if size!=image_size(p) or list(size)!=[meta['width'],meta['height']]:raise ValueError('Replacement changes image dimensions')
 backup=work/'originals'/(oldhash+p.suffix)
 backup.parent.mkdir(exist_ok=True)
 if not backup.exists():
  tmp=backup.with_suffix(backup.suffix+'.tmp');shutil.copy2(p,tmp)
  if digest(tmp)!=oldhash:raise ValueError('Backup checksum mismatch')
  tmp.replace(backup)
 if digest(backup)!=oldhash:raise ValueError('Existing backup checksum mismatch')
 ann=db.execute('SELECT * FROM annotations WHERE id=?',(ident,)).fetchone()
 ann=dict(ann) if ann else None
 if ann and (ann['sha256']!=oldhash or (ann['width'],ann['height'])!=size):
  return {'id':ident,'state':'skipped_annotation_conflict'}
 after={**current,**{k:meta[k] for k in ['sha256','pixel_sha256','phash','width','height','bytes']}}
 ann_after={**ann,'sha256':newhash,'updated':stamp(),'revision':ann['revision']+1} if ann else None
 payload={'id':ident,'created':stamp(),'path':current['path'],'backup':str(backup),'replacement':str(replacement),'before':current,'after':after,'annotation_before':ann,'annotation_after':ann_after,'processing':meta,'state':'prepared'}
 journal=work/'journal'/(ident+'.json');write_json(journal,payload)
 finish(root,db,journal,payload)
 return {'id':ident,'state':'applied','path':current['path'],'original_sha256':oldhash,'sha256':newhash}

def finish(root,db,journal,payload):
 p=safe_path(root,payload['path']);old=payload['before'];new=payload['after']
 # Durable prepared journal precedes both file and database changes. Re-running
 # repairs an interrupted file replacement before processing new work.
 current=db.execute('SELECT * FROM images WHERE id=?',(old['id'],)).fetchone()
 if not current or current['path']!=payload['path'] or current['sha256'] not in (old['sha256'],new['sha256']):
  raise ValueError('Recovery conflict: asset moved or changed; original retained')
 actual=digest(p)
 if actual not in (old['sha256'],new['sha256']):raise ValueError('Recovery file checksum conflict')
 db.execute('BEGIN IMMEDIATE')
 replaced=False
 try:
  if actual==old['sha256']:
   staged=Path(payload['replacement'])
   if digest(staged)!=new['sha256']:raise ValueError('Replacement changed')
   tmp=p.with_name('.watermark-'+uuid.uuid4().hex+p.suffix)
   shutil.copyfile(staged,tmp);os.chmod(tmp,p.stat().st_mode&0o777)
   with tmp.open('rb') as f:os.fsync(f.fileno())
   os.replace(tmp,p);replaced=True
  fields=['sha256','pixel_sha256','phash','width','height','bytes']
  db.execute('UPDATE images SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[new[k] for k in fields]+[old['id']])
  ann=payload['annotation_after']
  if ann:
   live=db.execute('SELECT * FROM annotations WHERE id=?',(ann['id'],)).fetchone()
   if dict(live or {}) not in (payload['annotation_before'],ann):raise ValueError('Annotation changed during recovery')
   if dict(live)!=ann:
    db.execute('UPDATE annotations SET sha256=?,updated=?,revision=? WHERE id=?',(ann['sha256'],ann['updated'],ann['revision'],ann['id']))
    db.execute('INSERT INTO annotation_history(asset_id,before_json,after_json,created) VALUES(?,?,?,?)',(ann['id'],json.dumps(payload['annotation_before']),json.dumps(ann),stamp()))
  db.execute('INSERT OR REPLACE INTO watermark_edits VALUES(?,?,?,?,?,?)',(old['id'],old['sha256'],new['sha256'],payload['path'],str(journal),stamp()))
  db.commit()
 except BaseException:
  db.rollback()
  if replaced:
   restore=p.with_name('.restore-'+uuid.uuid4().hex+p.suffix);shutil.copy2(payload['backup'],restore);os.replace(restore,p)
  raise
 payload['state']='done';write_json(journal,payload)

def run(root,scope,bundle,run_id,limit=0):
 root=Path(root).resolve();bundle=Path(bundle).resolve();work=root/'curation/watermark_cleanup'/run_id;work.mkdir(parents=True,exist_ok=True)
 rows=json.loads(Path(scope).read_text())['images'];allowed={r['id']:r for r in rows}
 metas={}
 for f in bundle.glob('*.json'):
  m=json.loads(f.read_text())
  if 'original_sha256' in m and 'output' in m:metas[m['original_sha256']]=m
 results=[]
 with locked(root):
  db=sqlite3.connect(root/'collector/state.sqlite3',timeout=60);db.row_factory=sqlite3.Row
  db.execute('CREATE TABLE IF NOT EXISTS watermark_edits(id TEXT PRIMARY KEY, original_sha256 TEXT, sha256 TEXT, path TEXT, journal TEXT, updated TEXT)');db.commit()
  for f in (work/'journal').glob('*.json'):
   payload=json.loads(f.read_text())
   if payload['state']=='prepared':finish(root,db,f,payload)
  db.close()
 for ident,r in allowed.items():
  if r['sha256'] not in metas:continue
  with locked(root):
   db=sqlite3.connect(root/'collector/state.sqlite3',timeout=60);db.row_factory=sqlite3.Row
   try:result=apply_one(root,work,db,r,metas[r['sha256']],bundle)
   except Exception as e:result={'id':ident,'state':'error','error':str(e)}
   finally:db.close()
  results.append(result)
  with (work/'apply.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
  if limit and sum(r['state']=='applied' for r in results)>=limit:break
 summary={k:sum(r['state']==k for r in results) for k in sorted({r['state'] for r in results})}
 write_json(work/'last_apply.json',{'created':stamp(),'summary':summary,'results':results})
 return summary

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--scope',required=True);p.add_argument('--bundle',required=True);p.add_argument('--run-id',default='20260922');p.add_argument('--limit',type=int,default=0);a=p.parse_args()
 print(json.dumps(run(a.root,a.scope,a.bundle,a.run_id,a.limit),ensure_ascii=False))
