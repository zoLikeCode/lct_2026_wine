#!/usr/bin/env python3
"""Publish reference-pass provenance and before/after coverage from the durable state."""
import collections,csv,hashlib,json,sqlite3
import collect as c
from reference_search import WORK,load_ocr,visible_years

def sync_ocr():
    bysha=load_ocr();path=c.ROOT/'collector'/'ocr_annotations.jsonl'
    records={}
    if path.exists():
        for line in path.read_text().splitlines():
            r=json.loads(line);records[r['id']]=r
    for r in c.DB.execute('SELECT * FROM images'):
        if r['sha256'] in bysha:
            records[r['id']]={'id':r['id'],'path':str(c.ROOT/r['path']),'lines':bysha[r['sha256']]}
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records.values()))
    c.log('reference_ocr_synced',records=len(records))

def audit_labels():
    from audit import move
    from reference_search import references
    rows=[dict(r) for r in c.DB.execute('SELECT * FROM images')];refs={r['slug']:r for r in references()};ocr=load_ocr()
    file=c.ROOT/'collector'/'reference_visual_overrides.json';overrides=json.loads(file.read_text()) if file.exists() else {}
    verified_path=c.ROOT/'collector'/'reference_decisions.json';verified=json.loads(verified_path.read_text()) if verified_path.exists() else {}
    groups=collections.defaultdict(set)
    for r in rows:
        if r['status']=='accepted':groups[r['pixel_sha256']].add(r['slug'])
    changed=[]
    for r in rows:
        if r['status']!='accepted':continue
        reasons=[];ref=refs.get(r['slug']);target=c.BY_SLUG[r['slug']]
        expected=c.vintage(target['Название вина']+' '+r['slug'])
        if not expected and ref and r['source']!='vino-svoe.ru':expected=visible_years(ocr.get(ref['sha256'],[]))
        actual=visible_years(ocr.get(r['sha256'],[]))
        if expected and actual and not expected&actual:reasons.append('visible_year_conflict')
        override=overrides.get(hashlib.sha256(r['image_url'].encode()).hexdigest(),{})
        if override.get('decision')=='review':reasons+=override.get('reasons',['manual_reference_review'])
        if len(groups[r['pixel_sha256']])>1:reasons.append('same_pixels_accepted_for_multiple_slugs')
        if reasons:
            move(r,'needs_review','; '.join(reasons),'reference_final_audit_requires_review');verified.pop(r['id'],None)
            changed.append({'id':r['id'],'slug':r['slug'],'reasons':reasons})
    c.DB.commit();verified_path.write_text(json.dumps(verified,ensure_ascii=False,indent=2))
    log=c.ROOT/'collector'/'reference_final_audit.json'
    old=json.loads(log.read_text()) if log.exists() else [];byid={r['id']:r for r in old+changed}
    log.write_text(json.dumps(list(byid.values()),ensure_ascii=False,indent=2));c.export();c.log('reference_final_audit',moved_to_review=len(changed))

def report():
    rows=[dict(r) for r in c.DB.execute('SELECT * FROM images')];current={r['id']:r for r in rows}
    refs={r['slug']:{k:r[k] for k in ['page_url','image_url','path','sha256','status']} for r in rows if r['source']=='vino-svoe.ru'}
    (c.ROOT/'collector'/'reference_index.json').write_text(json.dumps(refs,ensure_ascii=False,indent=2))
    olddb=sqlite3.connect(WORK/'before.sqlite3');olddb.row_factory=sqlite3.Row
    old={r['id']:dict(r) for r in olddb.execute('SELECT * FROM images')};olddb.close()
    before=json.loads((WORK/'before_summary.json').read_text());now=c.export()
    new_accepted=[r for r in rows if r['status']=='accepted' and (r['id'] not in old or old[r['id']]['status']!='accepted')]
    removed_accepted=[r for r in old.values() if r['status']=='accepted' and (r['id'] not in current or current[r['id']]['status']!='accepted')]
    additions=[]
    for r in new_accepted:
        additions.append({'slug':r['slug'],'path':r['path'],'source':r['source'],'page_url':r['page_url'],'image_url':r['image_url'],'change':'promoted_after_reference_comparison' if r['id'] in old else 'new_download','sha256':r['sha256']})
    c.write_csv(c.ROOT/'new_images.csv',additions,['slug','path','source','page_url','image_url','change','sha256'])
    ocr=load_ocr();label_rows=[];conflicts=[]
    for r in rows:
        lines=ocr.get(r['sha256'],[])
        if lines:label_rows.append({'id':r['id'],'slug':r['slug'],'text':' '.join(x['text'] for x in lines),'standalone_years':','.join(sorted(visible_years(lines)))})
        if 'year_conflict' in r['reason'] or 'source_year_differs' in r['reason']:
            expected=c.vintage(c.BY_SLUG[r['slug']]['Название вина']+' '+r['slug'])
            conflicts.append({'id':r['id'],'slug':r['slug'],'expected_year':','.join(sorted(expected)),'visible_year':','.join(sorted(visible_years(lines))),'path':r['path']})
    c.write_csv(c.ROOT/'label_text.csv',label_rows,['id','slug','text','standalone_years'])
    c.write_csv(c.ROOT/'year_conflicts.csv',conflicts,['id','slug','expected_year','visible_year','path'])
    matchfile=c.ROOT/'reference_matches.csv'
    if matchfile.exists():
        with matchfile.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);fields=reader.fieldnames;matches=list(reader)
        for r in matches:
            ident=hashlib.sha256((r['slug']+'\n'+r['image_url']).encode()).hexdigest()
            r['path']=current.get(ident,{}).get('path','')
            if ident in current:r['result']=current[ident]['status']
            else:
                attempt=c.DB.execute('SELECT error,status FROM attempts WHERE id=?',(ident,)).fetchone()
                if attempt and attempt['status']=='duplicate':
                    import re
                    linked=re.findall(r'\b[a-f0-9]{64}\b',attempt['error'])
                    if linked and linked[0] in current:r['path']=current[linked[0]]['path']
                    r['result']='duplicate'
                elif r['result']=='accepted':r['result']='duplicate_removed'
        c.write_csv(matchfile,matches,fields)
    pool=[json.loads(x) for x in (WORK/'pool.jsonl').read_text().splitlines()]
    decisions=[json.loads(x) for x in (WORK/'decisions.jsonl').read_text().splitlines()]
    candidates=collections.Counter(r['choice']['slug'] for r in decisions)
    rejected=collections.Counter(r['choice']['slug'] for r in decisions if r['decision']=='review')
    flags=collections.defaultdict(collections.Counter)
    for r in decisions:
        flags[r['choice']['slug']].update(r['flags'])
    reference_slugs={r['slug'] for r in rows if r['source']=='vino-svoe.ru'}
    coverage_file=c.ROOT/'coverage.csv'
    with coverage_file.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);coverage_fields=reader.fieldnames;coverage=list(reader)
    extra=['reference_image_present','reference_comparison_candidates','unconfirmed_search_candidates','main_review_reasons']
    for r in coverage:
        slug=r['slug'];r.update(reference_image_present=int(slug in reference_slugs),reference_comparison_candidates=candidates[slug],unconfirmed_search_candidates=rejected[slug],main_review_reasons='; '.join(k for k,_ in flags[slug].most_common(3)))
        if int(r['accepted'])<5:
            r['note']+='; '+('reference_missing_or_unusable' if slug not in reference_slugs else ('no_confirmable_additional_candidates' if not candidates[slug] else 'insufficient_confirmed_distinct_photos'))
    c.write_csv(coverage_file,coverage,coverage_fields+[k for k in extra if k not in coverage_fields])
    checks=c.ROOT/'collector'/'reference_visual_checks.json'
    checkrows=json.loads(checks.read_text()) if checks.exists() else []
    stats={'completed_at':c.now(),'before':before,'after':now,
           'net_accepted_gain':now['accepted']-before['accepted'],
           'new_accepted_downloads':sum(r['id'] not in old for r in new_accepted),
           'previous_candidates_promoted':sum(r['id'] in old for r in new_accepted),
           'previous_accepted_removed_or_reviewed':len(removed_accepted),
           'slugs_with_additions':len({r['slug'] for r in new_accepted}),
           'gains_by_source':dict(collections.Counter(r['source'] for r in new_accepted)),
           'candidate_urls_examined':len(pool),'usable_candidate_urls':sum('error' not in r for r in pool),
           'candidate_errors':dict(collections.Counter(r['error'] for r in pool if 'error'in r)),
           'reference_comparisons_logged':len(decisions),'manual_reference_pair_checks':len(checkrows),
           'note':'Unconfirmed new search candidates stay in the work cache, outside the delivered training folders. Existing needs_review files remain excluded from training.'}
    (c.ROOT/'enrichment_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
    c.log('enrichment_report',net_gain=stats['net_accepted_gain'],accepted=now['accepted'],coverage=now['coverage'])

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['ocr','audit','report']);a=p.parse_args()
    c.init(c.DEFAULT_CSV)
    if a.stage=='ocr':sync_ocr()
    elif a.stage=='audit':audit_labels()
    else:report()
