import sys,importlib.util
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from test_watermark_cleanup import ApplyTests
import revert_watermark_cleanup as restore
class RevertTests(ApplyTests):
 def test_restores_only_image_data_preserves_new_slug_annotation(self):
  self.run_job();p=self.root/'images/new-slug/one.webp';p.parent.mkdir();self.path.rename(p)
  self.db.execute('UPDATE images SET slug=?,path=?',('new-slug','images/new-slug/one.webp'));self.db.execute('UPDATE annotations SET points=?,revision=?',('[[0,0],[1,0],[1,1],[0,1]]',8));self.db.commit()
  result=restore.run(self.root,{self.sha},'test');self.assertEqual(result['results'][0]['state'],'restored_original')
  row=dict(self.db.execute('SELECT * FROM images').fetchone());ann=dict(self.db.execute('SELECT * FROM annotations').fetchone())
  self.assertEqual(row['slug'],'new-slug');self.assertEqual(row['sha256'],self.sha);self.assertEqual(ann['points'],'[[0,0],[1,0],[1,1],[0,1]]');self.assertEqual(ann['revision'],9);self.assertEqual(ann['sha256'],self.sha)
  self.assertEqual(restore.digest(p),self.sha);self.assertEqual(restore.run(self.root,{self.sha},'test')['results'],[])
