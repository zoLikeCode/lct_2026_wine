import importlib.util,contextlib,io,tempfile,unittest
from pathlib import Path
from PIL import Image
from test_duplicates import duplicate_fixture,add_photo
from test_server import DataStore
from curation import sha
spec=importlib.util.spec_from_file_location('audit_duplicates',Path(__file__).resolve().parents[1]/'scripts/audit_duplicates.py')
audit_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit_module)

class DuplicateAuditTest(unittest.TestCase):
    def test_reads_real_bytes_and_pixels_without_changing_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,_=duplicate_fixture(Path(tmp));s=DataStore(root)
            add_photo(s,'3','cabernet',root/'images/merlo/photo.jpg')
            with Image.open(root/'images/merlo/photo.jpg') as im:im.save(Path(tmp)/'same.png')
            add_photo(s,'4','merlo',Path(tmp)/'same.png','same.png')
            with s.db() as db:
                db.execute("UPDATE images SET sha256='wrong stored hash' WHERE id='3'")
                db.execute("UPDATE images SET source='vino-svoe.ru',kind='catalog_reference' WHERE id='0'")
            before={str(p):sha(p) for p in root.rglob('*') if p.is_file()}
            with contextlib.redirect_stdout(io.StringIO()):report=audit_module.audit(root,Path(tmp)/'reports')
            after={str(p):sha(p) for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertEqual(report['scopes']['images']['bytes']['duplicate_groups'],1)
            self.assertEqual(report['scopes']['images']['bytes']['cross_slug_groups'],1)
            self.assertEqual(report['scopes']['images']['bytes']['same_slug_extra_copies'],0)
            self.assertEqual(report['scopes']['images']['pixels']['same_slug_extra_copies'],1)
            self.assertEqual(report['scopes']['images_without_portal']['bytes']['duplicate_groups'],0)
            self.assertEqual(len(report['additional_pixel_groups']),1)
            self.assertEqual(report['verified_identical_file_pairs'],1)
            self.assertEqual(report['issues'][0]['issue'],'stored_sha256_mismatch')
            self.assertFalse(report['inventory_changed_during_run'])

if __name__=='__main__':unittest.main()
