"""Read-only verification of the deployed manifest and image bytes."""
import csv,hashlib,json,sqlite3,sys,time
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_store import DataStore

root=Path(sys.argv[1] if len(sys.argv)>1 else '/opt/wine/wine_dataset')
store=DataStore(root);started=time.monotonic();errors=[];checked=0
with store.guard(),store.db() as db:
    rows=[dict(r) for r in db.execute('SELECT * FROM images')]
    registered={r['path'] for r in rows}
    for r in rows:
        path=store.path(r['path'])
        try:
            h=hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            if h.hexdigest()!=r['sha256']:raise ValueError('SHA256 mismatch')
            with Image.open(path) as im:im.load()
            checked+=1
            if checked%1000==0:print(f"Verified {checked}/{len(rows)}",file=sys.stderr,flush=True)
        except Exception as e:errors.append({'id':r['id'],'path':r['path'],'error':str(e)})
    extra=[str(p.relative_to(root)) for bucket in ['images','needs_review'] for p in (root/bucket).rglob('*')
           if p.is_file() and not p.name.startswith('.') and str(p.relative_to(root)) not in registered]
    missing_folders=[slug for slug in store.catalog if not (root/'images'/slug).is_dir()]
    states=dict(db.execute('SELECT state,count(*) FROM curation_meta GROUP BY state'))
    counts={s:0 for s in store.catalog}
    for r in rows:
        if r['path'].startswith('images/'):counts[r['slug']]+=1
    result={'catalog_slugs':len(store.catalog),'registered_files':len(rows),'verified_files':checked,
        'missing_slug_folders':missing_folders,'unregistered_files':extra,'errors':errors,'review_states':states,
        'coverage':{'0':sum(n==0 for n in counts.values()),'1':sum(n==1 for n in counts.values()),
                    '2-4':sum(2<=n<=4 for n in counts.values()),'>=5':sum(n>=5 for n in counts.values())},
        'seconds':round(time.monotonic()-started,2)}
print(json.dumps(result,ensure_ascii=False,indent=2))
sys.exit(bool(errors or missing_folders or extra))
