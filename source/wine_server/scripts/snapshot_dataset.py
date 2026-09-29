"""Consistent metadata snapshot; local labels remain untouched."""
import sys,sqlite3,shutil,csv,json,hashlib,uuid
from pathlib import Path
from PIL import Image,ImageOps
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.parent/'wine_dataset'
DEST=ROOT.parent.parent/'work/wine_deploy_dataset'
DEST.mkdir(exist_ok=True);(DEST/'collector').mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT.parent/'wine_curation'))
from curation import Store,sha,stamp
store=Store(SOURCE)
with store.guard():
    with store.db() as db,sqlite3.connect(DEST/'collector/state.sqlite3') as dest:db.backup(dest)
    for name in ['catalog.csv','manifest.csv','coverage.csv']:
        shutil.copy2(SOURCE/name,DEST/name)
db=sqlite3.connect(DEST/'collector/state.sqlite3');db.row_factory=sqlite3.Row
records={r['path']:dict(r) for r in db.execute('SELECT * FROM images')}
actual={str(p.relative_to(SOURCE)):p for p in (SOURCE/'images').rglob('*') if p.is_file() and not p.name.startswith('.')}
added=[];moved=[];missing=[]
for rel,row in records.items():
    if not rel.startswith('images/'):
        db.execute('DELETE FROM images WHERE id=?',(row['id'],))
        continue
    if rel in actual:continue
    alternatives=[p for p in actual.values() if p.name==Path(rel).name and p.parent.name==row['slug']]
    match=next((p for p in alternatives if sha(p)==row['sha256']),None)
    if match:
        new=str(match.relative_to(SOURCE));db.execute('UPDATE images SET path=? WHERE id=?',(new,row['id']));moved.append([rel,new])
    else:missing.append(rel)
known={r[0] for r in db.execute('SELECT path FROM images')}
for rel,p in actual.items():
    if rel in known:continue
    slug=p.parent.name
    if slug not in store.catalog:raise ValueError('Unknown folder '+slug)
    digest=sha(p)
    with Image.open(p) as im:
        im=ImageOps.exif_transpose(im).convert('RGB');w,h=im.size;pixel=hashlib.sha256(im.tobytes()+str(im.size).encode()).hexdigest()
    ident=uuid.uuid4().hex
    row=dict(id=ident,slug=slug,source='manual_file',page_url='',image_url=p.as_uri(),title=store.catalog[slug]['Название вина'],kind='bottle',status='needs_review',reason='manual_file_requires_label_review',path=rel,sha256=digest,pixel_sha256=pixel,phash='',width=w,height=h,bytes=p.stat().st_size,vintage='',validation='unverified_manual_file',downloaded_at=stamp())
    db.execute('INSERT INTO images ('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
    db.execute('INSERT INTO curation_meta VALUES(?,?,?,?,?,?)',(ident,'pending',rel,'manual_import','','needs_review'));added.append(rel)
if missing:raise ValueError('Missing registered files: '+str(missing[:10]))
# Imported desktop operations remain audit records; their absolute paths cannot be undone on Linux.
db.execute('DELETE FROM curation_events')
db.execute('DELETE FROM curation_sources')
db.execute('DELETE FROM curation_meta WHERE id NOT IN (SELECT id FROM images)')
db.execute('UPDATE curation_meta SET original_path=(SELECT path FROM images WHERE images.id=curation_meta.id)')
db.execute("UPDATE images SET page_url='',image_url='' WHERE source IN ('own_photo','manual_file')")
# Source collector caches are unnecessary for server recognition and curation.
for table in ['pages','products','profiles','attempts']:
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('DELETE FROM '+table)
db.commit();db.execute('VACUUM');db.close()
Store(DEST).export()
summary={'created':stamp(),'files':len(actual),'images':sum(x.startswith('images/') for x in actual),'needs_review':sum(x.startswith('needs_review/') for x in actual),'unregistered_added_as_pending':len(added),'moved_paths_reconciled':moved,'missing':missing}
(DEST/'snapshot.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(summary)
