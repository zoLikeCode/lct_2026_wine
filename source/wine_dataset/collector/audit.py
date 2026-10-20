#!/usr/bin/env python3
"""Post-collection catalog, label and duplicate audit. Run after download workers finish."""
import argparse,collections,hashlib,json,math,random,re
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw,ImageFont
import numpy as np
import collect as c
from expand import nuxt

def profiles():
    count=0
    for row in c.DB.execute('SELECT * FROM profiles').fetchall():
        p=json.loads(row['data']);page=c.DB.execute('SELECT cache_path FROM pages WHERE url=?',(p['url'],)).fetchone()
        if not page or not Path(page[0]).exists():continue
        try:
            _,a,de=nuxt(Path(page[0]).read_bytes())
            index=next(i for i,x in enumerate(a) if isinstance(x,dict) and {'alcohol','manufacturer','grapes','slug'}<=set(x))
            p['metadata']=de(index)
            c.DB.execute('UPDATE profiles SET data=? WHERE slug=?',(json.dumps(p,ensure_ascii=False),row['slug']));count+=1
        except Exception as e:c.log('profile_parse_error',slug=row['slug'],error=str(e))
    c.DB.commit();c.log('profiles_enriched',count=count)

def move(row,status,reason,validation=None):
    if row['kind'] not in c.FRONT_KINDS:raise ValueError('Non-front image: run front_only.py first')
    folder='needs_review' if status=='needs_review' else 'images'
    old=c.ROOT/row['path'];new=c.ROOT/folder/row['slug']/old.name
    if old!=new:
        new.parent.mkdir(parents=True,exist_ok=True)
        if old.exists():old.replace(new)
    c.DB.execute('UPDATE images SET status=?,reason=?,path=?,validation=? WHERE id=?',
                 (status,reason,str(new.relative_to(c.ROOT)),validation or row['validation'],row['id']))

def reconcile(ocr_files):
    from reference_search import visible_years
    # Normalize the source site's verified route for sparkling wines.
    for old in c.DB.execute("SELECT * FROM products WHERE source='wine.rbc.ru' AND url LIKE '%/igristoe/p/%'").fetchall():
        p=json.loads(old['data']);p['url']=p['url'].replace('/igristoe/p/','/igristoe-vino/p/')
        c.DB.execute('DELETE FROM products WHERE url=?',(old['url'],))
        c.DB.execute('INSERT OR REPLACE INTO products VALUES(?,?,?)',(p['url'],p['source'],json.dumps(p,ensure_ascii=False)))
    c.DB.execute("UPDATE images SET page_url=REPLACE(page_url,'/igristoe/p/','/igristoe-vino/p/') WHERE source='wine.rbc.ru'");c.DB.commit()
    targets=c.build_matcher();products={r['url']:json.loads(r['data']) for r in c.DB.execute('SELECT * FROM products')}
    matches={url:{slug:(status,why) for slug,status,why in c.match_product(p,targets)} for url,p in products.items()}
    ocr={}
    for file in ocr_files:
        for line in Path(file).read_text().splitlines():
            x=json.loads(line)
            if 'lines' in x:ocr[x['id']]=x
    ref_year={}
    for ref in c.DB.execute("SELECT * FROM images WHERE source='vino-svoe.ru'"):
        if ref['id'] not in ocr:continue
        years=visible_years(ocr[ref['id']]['lines'])
        if len(years)==1:ref_year[ref['slug']]=years
    changes=collections.Counter();conflicts=[];ocr_rows=[]
    verified_file=c.ROOT/'collector'/'reference_decisions.json'
    verified=json.loads(verified_file.read_text()) if verified_file.exists() else {}
    ref_hashes={r['slug']:r['sha256'] for r in c.DB.execute("SELECT slug,sha256 FROM images WHERE source='vino-svoe.ru'")}
    for row in [dict(r) for r in c.DB.execute('SELECT * FROM images')]:
        status=row['status'];reason=row['reason'];validation=row['validation'];target=c.BY_SLUG[row['slug']]
        proof=verified.get(row['id'],{})
        visual_valid=(proof.get('sha256')==row['sha256'] and proof.get('reference_sha256')==ref_hashes.get(row['slug']) and proof.get('slug')==row['slug'])
        ty=c.vintage(target['Название вина']+' '+target['Slug'])
        if visual_valid:
            status='accepted';validation='reference_label_geometry_plus_metadata_and_ocr'
        elif row['source']!='vino-svoe.ru':
            status,reason=matches.get(row['page_url'],{}).get(row['slug'],('needs_review','metadata_no_longer_matches_catalog'))
        else:
            py=c.vintage(row['title'])
            if ty and py and ty!=py:status='needs_review';reason+='; publisher_title_year_conflict'
        if visual_valid:
            shared=bool(c.DB.execute('SELECT 1 FROM images WHERE pixel_sha256=? AND slug<>? AND status="accepted" LIMIT 1',(row['pixel_sha256'],row['slug'])).fetchone())
        else:
            shared=c.DB.execute('SELECT count(distinct slug) FROM images WHERE pixel_sha256=?',(row['pixel_sha256'],)).fetchone()[0]>1
        if shared:status='needs_review';reason+='; shared_image_across_slugs'
        if not ty and row['source']!='vino-svoe.ru' and ref_year.get(row['slug']):
            source_year=c.vintage(row['vintage'])
            if source_year and source_year!=ref_year[row['slug']]:
                status='needs_review';reason+='; source_year_differs_from_reference_label'
        if row['kind'] in ('group_photo','unclassified_view'):status='needs_review';reason+='; view_type_requires_review'
        if row['id'] in ocr:
            lines=ocr[row['id']]['lines'];txt=' '.join(x['text'] for x in lines)
            # Only isolated four-digit front-label readings: avoid dates in addresses / back-label legal text.
            years=visible_years(lines)
            ocr_rows.append({'id':row['id'],'slug':row['slug'],'text':txt,'standalone_years':','.join(sorted(years))})
            expected=ty or (ref_year.get(row['slug'],set()) if row['source']!='vino-svoe.ru' else set())
            if expected and years and not (expected&years) and row['kind'] in ('bottle','front_label','catalog_reference'):
                status='needs_review';reason+='; visible_year_conflict';conflicts.append({'id':row['id'],'slug':row['slug'],'expected_year':','.join(sorted(expected)),'visible_year':','.join(sorted(years)),'path':row['path']})
            elif status=='accepted' and txt and not visual_valid:
                validation='metadata_plus_local_ocr_audit'
        if status!=row['status']:changes[row['status']+'->'+status]+=1
        move(row,status,reason,validation)
    c.DB.commit()
    for r in conflicts:
        current=c.DB.execute('SELECT path FROM images WHERE id=?',(r['id'],)).fetchone()
        if current:r['path']=current[0]
    c.write_csv(c.ROOT/'label_text.csv',ocr_rows,['id','slug','text','standalone_years'])
    c.write_csv(c.ROOT/'year_conflicts.csv',conflicts,['id','slug','expected_year','visible_year','path'])
    c.log('reconciled',changes=dict(changes),ocr_images=len(ocr_rows),year_conflicts=len(conflicts));c.export()

def thumb(path):
    with Image.open(path) as im:
        rgba=im.convert('RGBA');bg=Image.new('RGBA',rgba.size,'white');bg.alpha_composite(rgba);rgb=bg.convert('RGB')
        arr=np.asarray(rgb);yy,xx=np.where(np.any(arr<235,axis=2))
        if len(xx)>50:rgb=rgb.crop((int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1))
        return np.asarray(rgb.resize((48,96)),dtype=np.float32)/255

def dedupe():
    removed=[];groups=collections.defaultdict(list)
    for row in c.DB.execute('SELECT * FROM images ORDER BY slug, CASE WHEN source="vino-svoe.ru" THEN 0 ELSE 1 END, CASE WHEN status="accepted" THEN 0 ELSE 1 END, width*height DESC'):
        row=dict(row);kind='bottle' if row['kind']=='catalog_reference' else row['kind'];groups[(row['slug'],kind)].append(row)
    for group in groups.values():
        keep=[];small={}
        for row in group:
            same=None
            for prior in keep:
                distance=(int(row['phash'],16)^int(prior['phash'],16)).bit_count()
                if distance>20:continue
                for v in [row,prior]:
                    if v['id'] not in small:small[v['id']]=thumb(c.ROOT/v['path'])
                rmse=float(np.sqrt(np.mean((small[row['id']]-small[prior['id']])**2)))
                if rmse<0.065:same=prior;break
            if same:
                removed.append({**row,'canonical_id':same['id'],'canonical_path':same['path'],'duplicate_reason':'near_identical_normalized_pixels'})
                (c.ROOT/row['path']).unlink(missing_ok=True)
                c.DB.execute('DELETE FROM images WHERE id=?',(row['id'],))
                c.DB.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',(row['id'],row['slug'],row['image_url'],'duplicate','Same photograph as '+same['id'],c.now()))
            else:keep.append(row)
    c.DB.commit()
    if removed:
        file=c.ROOT/'discarded_duplicates.jsonl'
        with file.open('a') as f:
            for r in removed:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    c.log('deduplicated',removed=len(removed));c.export()

def catalog_audit():
    groups=collections.defaultdict(list)
    for r in c.CAT:groups[(c.norm(r['Винодельня']),tuple(sorted(c.tokens(r['Название вина']))))].append(r)
    fields=['group','slug','name','winery','category','grapes','year','image_filename'];rows=[]
    for key,g in groups.items():
        if len(g)<2:continue
        for r in g:rows.append({'group':'similar_name_'+hashlib.sha256(str(key).encode()).hexdigest()[:10],'slug':r['Slug'],'name':r['Название вина'],'winery':r['Винодельня'],'category':r['Категория'],'grapes':r['Сорт винограда'],'year':','.join(sorted(c.vintage(r['Название вина']+' '+r['Slug']))),'image_filename':r['Название фото']})
    names=collections.defaultdict(list)
    for r in c.CAT:names[r['Название фото']].append(r)
    for key,g in names.items():
        if len(g)<2:continue
        for r in g:rows.append({'group':'shared_filename_'+hashlib.sha256(key.encode()).hexdigest()[:10],'slug':r['Slug'],'name':r['Название вина'],'winery':r['Винодельня'],'category':r['Категория'],'grapes':r['Сорт винограда'],'year':','.join(sorted(c.vintage(r['Название вина']+' '+r['Slug']))),'image_filename':key})
    c.write_csv(c.ROOT/'catalog_ambiguities.csv',rows,fields)

if __name__=='__main__':
    saved=c.ROOT/'collector'/'ocr_annotations.jsonl'
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['profiles','reconcile','dedupe','catalog']);p.add_argument('--ocr',nargs='*',default=[str(saved)] if saved.exists() else []);a=p.parse_args();c.init(c.DEFAULT_CSV)
    if a.stage=='profiles':profiles()
    elif a.stage=='reconcile':reconcile(a.ocr)
    elif a.stage=='dedupe':dedupe()
    elif a.stage=='catalog':catalog_audit()
