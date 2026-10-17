import hashlib,importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from PIL import Image

SPEC=importlib.util.spec_from_file_location('cleanup',Path(__file__).parents[1]/'scripts/apply_watermark_cleanup.py')
cleanup=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cleanup)

class ApplyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.bundle=self.root/'bundle';self.bundle.mkdir()
  (self.root/'collector').mkdir();(self.root/'curation').mkdir();(self.root/'images/wine').mkdir(parents=True)
  self.path=self.root/'images/wine/one.webp';Image.new('RGB',(50,75),'white').save(self.path,lossless=True)
  self.sha=cleanup.digest(self.path);out=self.bundle/'edited.webp';Image.new('RGB',(50,75),'gray').save(out,lossless=True)
  self.meta={'original_sha256':self.sha,'sha256':cleanup.digest(out),'output':str(out),'pixel_sha256':'pixel','phash':'phash','width':50,'height':75,'bytes':out.stat().st_size}
  (self.bundle/(self.sha+'.json')).write_text(json.dumps(self.meta))
  self.db=sqlite3.connect(self.root/'collector/state.sqlite3');self.db.row_factory=sqlite3.Row
  self.db.executescript('CREATE TABLE images(id TEXT PRIMARY KEY,slug TEXT,path TEXT,sha256 TEXT,pixel_sha256 TEXT,phash TEXT,width INT,height INT,bytes INT,status TEXT); CREATE TABLE annotations(id TEXT PRIMARY KEY,sha256 TEXT,points TEXT,no_label INT,width INT,height INT,updated TEXT,revision INT); CREATE TABLE annotation_history(id INTEGER PRIMARY KEY,asset_id TEXT,before_json TEXT,after_json TEXT,created TEXT);')
  self.db.execute('INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?,?)',('one','wine','images/wine/one.webp',self.sha,'oldpixel','oldphash',50,75,self.path.stat().st_size,'accepted'))
  self.db.execute('INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?)',('one',self.sha,'[[0.1,0.2],[0.9,0.2],[0.9,0.8],[0.1,0.8]]',0,50,75,'before',3));self.db.commit()
  self.row=dict(self.db.execute('SELECT * FROM images').fetchone());self.scope=self.root/'scope.json';self.scope.write_text(json.dumps({'images':[self.row]}))
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def run_job(self):return cleanup.run(self.root,self.scope,self.bundle,'test')
 def test_preserves_annotation_slug_path_and_is_idempotent(self):
  self.assertEqual(self.run_job(),{'applied':1})
  r=dict(self.db.execute('SELECT * FROM images').fetchone());a=dict(self.db.execute('SELECT * FROM annotations').fetchone())
  self.assertEqual((r['slug'],r['path'],r['status']),('wine','images/wine/one.webp','accepted'))
  self.assertEqual(a['points'],'[[0.1,0.2],[0.9,0.2],[0.9,0.8],[0.1,0.8]]');self.assertEqual(a['revision'],4)
  self.assertEqual(a['sha256'],cleanup.digest(self.path));self.assertEqual(self.run_job(),{'already_applied':1})
  backups=list((self.root/'curation/watermark_cleanup/test/originals').glob('*'));self.assertEqual(len(backups),1);self.assertEqual(cleanup.digest(backups[0]),self.sha)
 def test_does_not_replace_manually_changed_file(self):
  Image.new('RGB',(50,75),'blue').save(self.path,lossless=True);before=cleanup.digest(self.path)
  self.assertEqual(self.run_job(),{'skipped_changed_file':1});self.assertEqual(cleanup.digest(self.path),before)
 def test_does_not_touch_moved_to_review(self):
  dest=self.root/'needs_review/wine/one.webp';dest.parent.mkdir(parents=True);self.path.replace(dest)
  self.db.execute('UPDATE images SET path=?',('needs_review/wine/one.webp',));self.db.commit()
  self.assertEqual(self.run_job(),{'skipped_moved_out_of_images':1});self.assertEqual(cleanup.digest(dest),self.sha)
 def test_rejects_wrong_dimensions_before_change(self):
  out=Path(self.meta['output']);Image.new('RGB',(51,75),'gray').save(out,lossless=True)
  self.meta.update(sha256=cleanup.digest(out),width=51);(self.bundle/(self.sha+'.json')).write_text(json.dumps(self.meta))
  self.assertEqual(self.run_job(),{'error':1});self.assertEqual(cleanup.digest(self.path),self.sha)
 def test_repairs_interrupted_file_replacement(self):
  self.run_job();j=self.root/'curation/watermark_cleanup/test/journal/one.json';p=json.loads(j.read_text());p['state']='prepared';j.write_text(json.dumps(p))
  self.db.execute('UPDATE images SET sha256=?,pixel_sha256=?',(self.sha,'oldpixel'));self.db.execute('UPDATE annotations SET sha256=?,revision=?,updated=?',(self.sha,3,'before'));self.db.commit()
  self.assertEqual(self.run_job(),{'already_applied':1});self.assertEqual(self.db.execute('SELECT sha256 FROM images').fetchone()[0],self.meta['sha256'])

if __name__=='__main__':unittest.main()
