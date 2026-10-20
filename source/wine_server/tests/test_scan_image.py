import io,sys,unittest
from pathlib import Path
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_server


class ScanImageTest(unittest.TestCase):
    setUp=test_server.ServerTest.setUp
    tearDown=test_server.ServerTest.tearDown

    def test_scan_keeps_detail_and_strips_exif(self):
        image=Image.new('RGB',(3100,900),'#4a1943')
        exif=image.getexif();exif[274]=6;exif[271]='Private camera make'
        source=io.BytesIO();image.save(source,'JPEG',quality=95,exif=exif)
        ident,path=self.store.scan_start(source.getvalue(),'qa-detail')
        with Image.open(path) as saved:
            self.assertEqual(saved.size,(900,3100))
            self.assertEqual(len(saved.getexif()),0)
        entry=self.store.history(device='qa-detail')['items'][0]
        self.assertEqual(entry['id'],ident)
        self.assertEqual(entry['status'],'processing')


if __name__=='__main__':unittest.main()
