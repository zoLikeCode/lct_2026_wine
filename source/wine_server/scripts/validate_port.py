"""Diagnostic on the existing validation set; no training or model selection."""
import sys,json,time,sqlite3
from pathlib import Path
import numpy as np
from PIL import Image,ImageOps
from sklearn.metrics import f1_score
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from inference import Engine
records=json.loads((ROOT.parent/'wine_mvp/artifacts/data/records.json').read_text())
queries=[r for r in records if r['split']=='validation' and r['query']]
out=ROOT/'reports/portable_validation.jsonl';done={}
if out.exists():
    for line in out.read_text().splitlines():
        row=json.loads(line);done[row['id']]=row
engine=Engine();dataset=ROOT.parent/'wine_dataset'
with sqlite3.connect(dataset/'collector/state.sqlite3') as db:current=dict(db.execute('SELECT id,path FROM images'))
for n,r in enumerate(queries):
    if r['id'] in done:continue
    p=dataset/current.get(r['id'],r['path'])
    try:
        with Image.open(p) as im:result=engine.predict(ImageOps.exif_transpose(im).convert('RGB'))
        row={'id':r['id'],'true_slug':r['slug'],'prediction':result['slug'],'top5':[t['slug'] for t in result['top5']],
             'latency_ms':result['latency_ms'],'hard':r.get('hard',False),'error':None}
    except Exception as e:row={'id':r['id'],'true_slug':r['slug'],'error':str(e)}
    with out.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    done[r['id']]=row
    if (n+1)%10==0:print(f'{n+1}/{len(queries)}',flush=True)
valid=[r for r in done.values() if not r['error']];hard=[r for r in valid if r['hard']]
result={'protocol':'Existing reused validation set; full EXIF-oriented images; no test or training; runtime port diagnostic, not independent shop accuracy',
    'expected_queries':len(queries),'evaluated':len(valid),'errors':[r for r in done.values() if r['error']],
    'top1':float(np.mean([r['prediction']==r['true_slug'] for r in valid])),
    'recall5':float(np.mean([r['true_slug'] in r['top5'] for r in valid])),
    'macro_f1':float(f1_score([r['true_slug'] for r in valid],[r['prediction'] for r in valid],labels=sorted({r['true_slug'] for r in valid}),average='macro',zero_division=0)),
    'macro_f1_definition':'Macro over ground-truth query classes, matching the original MVP report',
    'hard_n':len(hard),'hard_top1':float(np.mean([r['prediction']==r['true_slug'] for r in hard])) if hard else None,
    'mac_cpu_latency_p50_ms':float(np.median([r['latency_ms'] for r in valid])),
    'model_version':engine.version}
(ROOT/'reports/portable_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False),flush=True)
