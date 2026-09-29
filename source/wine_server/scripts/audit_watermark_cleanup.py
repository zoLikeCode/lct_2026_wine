"""Read-only verification of watermark replacements and original backups."""
import argparse,json,sqlite3,hashlib,time
from collections import Counter
from pathlib import Path
from PIL import Image,ImageOps

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(root,run_id):
 root=Path(root);work=root/'curation/watermark_cleanup'/run_id
 db=sqlite3.connect(f'file:{root}/collector/state.sqlite3?mode=ro',uri=True);db.row_factory=sqlite3.Row
 edits=[dict(r) for r in db.execute('SELECT * FROM watermark_edits')];results=[];backups=set();folders=set()
 for edit in edits:
  journal=json.loads(Path(edit['journal']).read_text());current=db.execute('SELECT * FROM images WHERE id=?',(edit['id'],)).fetchone();errors=[]
  backup=Path(journal['backup'])
  if str(backup) not in backups:
   if not backup.is_file() or digest(backup)!=edit['original_sha256']:errors.append('backup_checksum')
   backups.add(str(backup))
  if not current:
   results.append({'id':edit['id'],'state':'record_removed_after_cleanup','errors':errors});continue
  current=dict(current);path=root/current['path']
  if current['sha256']!=edit['sha256']:
   results.append({'id':edit['id'],'state':'changed_after_cleanup','errors':errors});continue
  if not path.is_file() or digest(path)!=current['sha256']:errors.append('current_checksum')
  else:
   with Image.open(path) as image:
    image.load();im=ImageOps.exif_transpose(image)
    if im.size!=(current['width'],current['height']):errors.append('dimensions')
    pixel=hashlib.sha256(str(im.size).encode()+im.convert('RGBA').tobytes()).hexdigest()
    if pixel!=current['pixel_sha256']:errors.append('pixel_checksum')
  annotation=db.execute('SELECT * FROM annotations WHERE id=?',(edit['id'],)).fetchone()
  if annotation and annotation['sha256']!=current['sha256']:errors.append('annotation_checksum')
  folders.add(current['slug']);results.append({'id':edit['id'],'path':current['path'],'state':'verified' if not errors else 'error','errors':errors})
 report={'created':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'edited_records':len(edits),'original_backups':len(backups),'current_slug_folders':len(folders),'states':dict(Counter(r['state'] for r in results)),'results':results}
 print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--run-id',default='20260922');a=ap.parse_args();run(a.root,a.run_id)
