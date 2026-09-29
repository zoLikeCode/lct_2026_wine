"""Creates export artifacts only; does not edit photos, scans or annotations."""
import hashlib,io,json,time,zipfile
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[3]
credentials=json.loads((ROOT/'work/wine_server_private/credentials.json').read_text())
base='https://wine.77-105-169-21.sslip.io'
report={'base':base,'checks':[]}
with httpx.Client(base_url=base,timeout=90) as c:
    def get(path):
        r=c.get('/admin/api/'+path);r.raise_for_status();return r.json()
    def post(path,data):
        r=c.post('/admin/api/'+path,json=data);r.raise_for_status();return r.json()
    def build(options):
        job=post('exports',options);started=time.monotonic()
        while time.monotonic()-started<180:
            found=next(j for j in get('exports')['items'] if j['id']==job['id'])
            if found['status']=='error':raise RuntimeError(found['error'])
            if found['status']=='ready':return found
            time.sleep(1)
        raise TimeoutError('Archive build exceeded 180 s')
    post('login',{'password':credentials['admin_password']})
    catalog=get('exports/catalog')['items'];before=get('export/boundaries')
    candidates=sorted((w for w in catalog if w['total']),key=lambda w:(-w['labeled'],w['total'],w['slug']))
    slugs=[w['slug'] for w in candidates[:2]]
    default={'scope':'all','slugs':[],'mode':'images','filter':'all'}
    previews={f:post('exports/preview',{**default,'filter':f}) for f in ['all','labeled','unlabeled']}
    assert previews['all']['images']==sum(w['total'] for w in catalog)
    assert previews['labeled']['images']+previews['unlabeled']['images']==previews['all']['images']
    report['preview_counts']={f:{k:p[k] for k in ['folders','images','labeled','unlabeled']} for f,p in previews.items()}
    report['checks'].append('whole-catalog filter counts')
    for mode,filter in [('json','labeled'),('images_json','all'),('images','unlabeled')]:
        job=build({**default,'scope':'selected','slugs':slugs,'mode':mode,'filter':filter})
        r=c.get(job['download_url']);r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert z.testzip() is None
            photo_names=[n for n in z.namelist() if n.startswith('images/') and not n.endswith('/')]
            json_names=[n for n in z.namelist() if n.startswith('annotations/') and n.endswith('.json')]
            assert len(photo_names)==job['summary']['images'];assert len(json_names)==job['summary']['annotations']
            for name in json_names:
                a=json.loads(z.read(name));assert a['slug'] in slugs
                original=next(x for x in before['items'] if x['id']==a['id'])
                assert a['points']==original['points'];assert a['sha256']==original['sha256'];assert a['revision']==original['revision']
                if mode=='images_json':assert hashlib.sha256(z.read(a['image_path'])).hexdigest()==a['sha256']
        report['checks'].append({'mode':mode,'filter':filter,'images':len(photo_names),'annotations':len(json_names),'zip_bytes':len(r.content)})
        post('exports/'+job['id']+'/delete',{})
    assert get('export/boundaries')==before
    report['checks'].append('original label annotations unchanged')
    print('Selected-folder ZIP modes verified. Building full images archive...',flush=True)
    started=time.monotonic();job=build(default);report['full_archive']={**job,'build_seconds':round(time.monotonic()-started,2)}
    r=c.get(job['download_url'],headers={'Range':'bytes=0-99'})
    assert r.status_code==206,(r.status_code,len(r.content));assert r.content[:4]==b'PK\x03\x04';assert len(r.content)==100
    report['checks'].append('full ZIP authenticated HTTP range download')
    with httpx.Client(base_url=base,timeout=30) as anonymous:
        assert anonymous.get(job['download_url']).status_code==401
    report['checks'].append('anonymous download denied')
    assert get('export/boundaries')==before
    report['ok']=True
dest=ROOT/'outputs/wine_server/reports/remote_exports.json';dest.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'ok':True,'images':job['summary']['images'],'folders':job['summary']['folders'],'bytes':job['bytes'],'build_seconds':report['full_archive']['build_seconds'],'archive_id':job['id']},ensure_ascii=False))
