import hashlib
import io
import json
import unittest
import zipfile
from PIL import Image, ImageOps
import test_exports
from annotation_formats import assign_splits, write_standard


class StandardFormatsTest(unittest.TestCase):
    setUp = test_exports.ExportTest.setUp
    tearDown = test_exports.ExportTest.tearDown
    options = test_exports.ExportTest.options
    wait = test_exports.ExportTest.wait
    build = test_exports.ExportTest.build

    def test_coco_geometry_negatives_and_source_immutability(self):
        self.store.annotate('0', {'points': [[.1,.2],[.8,.1],[.9,.8],[.2,.9]]})
        self.store.annotate('2', {'no_label': True})
        before = self.store.annotation('0')
        z,j = self.build(mode='images_json', filter='labeled', format='coco')
        doc = json.loads(z.read('_annotations.coco.json'))
        self.assertEqual(len(doc['images']), 2)
        self.assertEqual(len(doc['annotations']), 1)
        a = doc['annotations'][0]
        im = next(r for r in doc['images'] if r['id']==a['image_id'])
        w,h = im['width'], im['height']
        for actual, expected in zip(a['bbox'], [.1*w,.1*h,.8*w,.8*h]): self.assertAlmostEqual(actual,expected)
        self.assertAlmostEqual(a['area'], .5*w*h)
        self.assertEqual(a['segmentation'], [[.1*w,.2*h,.8*w,.1*h,.9*w,.8*h,.2*w,.9*h]])
        for item in doc['images']:
            image = Image.open(io.BytesIO(z.read(item['file_name'])))
            self.assertEqual(image.size, (item['width'],item['height']))
        self.assertEqual(self.store.annotation('0'),before)
        self.assertEqual(j['summary']['negative_images'],1)

    def test_yolo_variants_and_unannotated_exclusion(self):
        self.store.annotate('0', {'points': [[.1,.2],[.9,.8]]})
        for fmt,expected in [('yolo_detect',[0,.5,.5,.8,.6]),('yolo_segment',[0,.1,.2,.9,.2,.9,.8,.1,.8])]:
            z,_ = self.build(mode='images_json',filter='labeled',format=fmt)
            self.assertEqual([float(v) for v in z.read('labels/0.txt').split()],expected)
            self.assertIn('images/0.jpg',z.namelist())
            self.assertNotIn('labels/2.txt',z.namelist())
            self.assertIn('0: front_label',z.read('data.yaml').decode())
        self.store.annotate('2', {'no_label':True})
        z,_=self.build(mode='json',filter='labeled',format='yolo_detect')
        self.assertEqual(z.read('labels/2.txt'),b'')
        self.assertFalse(any(n.startswith('images/') for n in z.namelist()))

    def test_orientation_is_applied_to_export_pixels_not_sources(self):
        path=self.root/'images/merlo/photo.jpg'
        im=Image.new('RGB',(60,100),'red');im.paste('blue',(0,0,30,50));exif=im.getexif();exif[274]=6;im.save(path,exif=exif)
        raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        with self.store.db() as db:db.execute('UPDATE images SET sha256=?,bytes=? WHERE id=?',(sha,len(raw),'0'))
        self.store.annotate('0',{'points':[[.1,.2],[.9,.8]]})
        z,_=self.build(mode='images_json',filter='labeled',format='coco')
        image=Image.open(io.BytesIO(z.read('0.png')))
        self.assertEqual(image.size,(100,60));self.assertNotIn(274,image.getexif())
        self.assertEqual(image.tobytes(),ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).tobytes())
        self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(json.loads(z.read('_export.json'))['items'][0]['source_sha256'],sha)

    def test_invalid_options_and_missing_file_do_not_yield_training_negatives(self):
        for opts in [{'format':'coco','filter':'all'},{'format':'bad','filter':'labeled'}]:
            r=self.c.post('/admin/api/exports/preview',json=self.options(mode='images_json',**opts))
            self.assertEqual(r.status_code,409)
        self.store.annotate('0',{'no_label':True})
        r=self.c.post('/admin/api/exports/preview',json=self.options(mode='images_json',format='coco',filter='labeled',split='grouped'))
        self.assertEqual(r.status_code,409);self.assertIn('три',r.text)
        (self.root/'images/merlo/photo.jpg').unlink()
        j=self.wait(self.exports.create(self.options(mode='json',format='coco',filter='labeled'))['id'])
        self.assertEqual(j['status'],'error')

    def test_grouping_is_repeatable_and_joins_duplicate_slugs_transitively(self):
        rows=[{'id':str(i),'slug':f'wine-{i}','sha256':f'sha-{i}','pixel_sha256':f'pixel-{i}'} for i in range(20)]
        rows[1]['slug']=rows[0]['slug']
        rows[2]['sha256']=rows[1]['sha256']
        rows[3]['pixel_sha256']=rows[2]['pixel_sha256']
        result=assign_splits(rows,'grouped')
        self.assertEqual(result,assign_splits(list(reversed(rows)),'grouped'))
        self.assertEqual(len({result[str(i)] for i in range(4)}),1)
        self.assertEqual(set(result.values()),{'train','valid','test'})

    def test_split_archives_have_resolvable_image_paths_and_category_ids(self):
        snapshot=self.root/'snap';snapshot.mkdir();rows=[]
        for i,color in enumerate(['red','green','blue']):
            p=snapshot/str(i);Image.new('RGB',(20,30),color).save(p,format='PNG')
            rows.append({'id':str(i),'slug':str(i),'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                'annotation':{'points':'[[0.1,0.2],[0.9,0.2],[0.9,0.8],[0.1,0.8]]','width':20,'height':30,'revision':1,'no_label':False}})
        for fmt in ('coco','yolo_segment'):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:write_standard(z,rows,snapshot,{'format':fmt,'split':'grouped','mode':'images_json'},self.store,lambda n:None)
            with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
                manifest=json.loads(z.read('_export.json'))
                self.assertEqual({i['split'] for i in manifest['items']},{'train','valid','test'})
                for item in manifest['items']:
                    self.assertIn(item['image_path'],z.namelist());self.assertIn(item['annotation_path'],z.namelist())
                if fmt=='coco':
                    for split in ('train','valid','test'):
                        doc=json.loads(z.read(split+'/_annotations.coco.json'))
                        self.assertEqual(doc['annotations'][0]['category_id'],doc['categories'][0]['id'])
                        self.assertIn(split+'/'+doc['images'][0]['file_name'],z.namelist())
                else:
                    import yaml
                    cfg=yaml.safe_load(z.read('data.yaml'))
                    for k in ('train','val','test'):self.assertTrue(any(n.startswith(cfg[k]+'/') for n in z.namelist()))


if __name__=='__main__':unittest.main()
