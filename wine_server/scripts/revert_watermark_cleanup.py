"""Restore only this cleanup's edits rejected by later quality checks."""
import argparse,json,sqlite3,shutil
from pathlib import Path
from apply_watermark_cleanup import locked,digest,write_json,finish,stamp,image_size

def revert_one(root,db,edit,work):
 old_journal=json.loads(Path(edit['journal']).read_text());original=old_journal['before'];current=db.execute('SELECT * FROM images WHERE id=?',(edit['id'],)).fetchone()
 if not current:return {'id':edit['id'],'state':'missing_record'}
 current=dict(current)
 if current['sha256']!=edit['sha256']:return {'id':edit['id'],'state':'skipped_changed_content'}
 if not current['path'].startswith('images/'):return {'id':edit['id'],'state':'skipped_moved_out_of_images'}
 path=(root/current['path']).resolve()
 if not path.is_relative_to(root) or digest(path)!=current['sha256']:return {'id':edit['id'],'state':'skipped_changed_file'}
 replacement=Path(old_journal['backup'])
 if digest(replacement)!=original['sha256'] or image_size(replacement)!=image_size(path):raise ValueError('Original backup failed verification')
 modified=work/'modified'/(current['sha256']+path.suffix);modified.parent.mkdir(parents=True,exist_ok=True)
 if not modified.exists():shutil.copy2(path,modified)
 if digest(modified)!=current['sha256']:raise ValueError('Edited backup failed verification')
 fields=['sha256','pixel_sha256','phash','width','height','bytes'];after={**current,**{k:original[k] for k in fields}}
 ann=db.execute('SELECT * FROM annotations WHERE id=?',(edit['id'],)).fetchone();ann=dict(ann) if ann else None
 if ann and ann['sha256']!=current['sha256']:return {'id':edit['id'],'state':'skipped_annotation_conflict'}
 ann_after={**ann,'sha256':original['sha256'],'updated':stamp(),'revision':ann['revision']+1} if ann else None
 payload={'id':edit['id'],'created':stamp(),'path':current['path'],'backup':str(modified),'replacement':str(replacement),'before':current,'after':after,'annotation_before':ann,'annotation_after':ann_after,'processing':{'reason':'watermark quality check rejected replacement','original_cleanup_journal':edit['journal']},'state':'prepared'}
 journal=work/'journal'/(edit['id']+'.json');write_json(journal,payload);finish(root,db,journal,payload)
 db.execute('DELETE FROM watermark_edits WHERE id=? AND sha256=?',(edit['id'],original['sha256']));db.commit()
 return {'id':edit['id'],'state':'restored_original','path':current['path']}

def run(root,rejected,run_id='20260922'):
 root=Path(root).resolve();work=root/'curation/watermark_cleanup'/run_id/'reverts';work.mkdir(parents=True,exist_ok=True);results=[]
 with locked(root):
  db=sqlite3.connect(root/'collector/state.sqlite3',timeout=60);db.row_factory=sqlite3.Row
  for p in (work/'journal').glob('*.json'):
   payload=json.loads(p.read_text())
   if payload['state']=='prepared':finish(root,db,p,payload)
   db.execute('DELETE FROM watermark_edits WHERE id=? AND sha256=?',(payload['id'],payload['after']['sha256']));db.commit()
  edits=[dict(r) for r in db.execute('SELECT * FROM watermark_edits') if r['original_sha256'] in rejected];db.close()
 for edit in edits:
  with locked(root):
   db=sqlite3.connect(root/'collector/state.sqlite3',timeout=60);db.row_factory=sqlite3.Row
   try:r=revert_one(root,db,edit,work)
   except Exception as e:r={'id':edit['id'],'state':'error','error':str(e)}
   finally:db.close()
  results.append(r)
 report={'created':stamp(),'results':results};write_json(work/'last_revert.json',report);return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--rejected',required=True);p.add_argument('--run-id',default='20260922');a=p.parse_args();r=run(a.root,set(json.loads(Path(a.rejected).read_text())),a.run_id)
 from collections import Counter
 print(json.dumps(dict(Counter(i['state'] for i in r['results']))))
