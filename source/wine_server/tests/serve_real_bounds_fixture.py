"""Isolated browser fixture. Production data is only read for sample image bytes."""
import json,sys,tempfile,shutil
from pathlib import Path
from PIL import Image
import uvicorn
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_server import fixture,FakeEngine,create_app
from test_duplicates import add_photo
from curation import sha

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='wine-real-bounds-') as folder:
        root,camera=fixture(Path(folder));app=create_app(root,FakeEngine(),test=True);store=app.state.store
        dataset=HERE.parents[2]/'outputs/wine_split_20260926'
        photos=json.loads((dataset/'manifest.json').read_text())
        real=[r for r in photos if r['category']=='real_photos']
        for ident,row in [('0',real[0]),('2',real[1])]:
            with store.db() as db:
                p=store.path(db.execute('SELECT path FROM images WHERE id=?',(ident,)).fetchone()['path'])
                with Image.open(dataset/row['path']) as im:im.convert('RGB').save(p,'JPEG',quality=94)
                with Image.open(p) as im:w,h=im.size
                db.execute('UPDATE images SET sha256=?,width=?,height=?,bytes=? WHERE id=?',(sha(p),w,h,p.stat().st_size,ident))
        add_photo(store,'3','merlo',dataset/real[2]['path'],'real_extra'+Path(real[2]['path']).suffix)
        catalog=next(r for r in photos if r['category']=='catalog_photos')
        add_photo(store,'4','merlo',dataset/catalog['path'],'catalog_extra'+Path(catalog['path']).suffix)
        selected=[{'image_id':r['id'],'sha256':r['sha256']} for r in store.rows() if r['id'] in {'0','2','3'}]
        (root/'collector/real_photo_selection.json').write_text(json.dumps({'items':selected}))
        store.annotate('0',{'points':[[.1,.2],[.9,.8]]})
        uvicorn.run(app,host='127.0.0.1',port=8206)
