"""Isolated, disposable manual browser checks, never touches the live dataset."""
import sys,tempfile,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_duplicates import duplicate_fixture,add_photo
from test_server import FakeEngine,create_app
import uvicorn
parser=argparse.ArgumentParser();parser.add_argument('--portal-reference',action='store_true');args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='wine_duplicates_qa_') as folder:
    root,camera=duplicate_fixture(Path(folder))
    app=create_app(root,FakeEngine(),test=True);store=app.state.store
    add_photo(store,'3','cabernet',root/'images/merlo/photo.jpg')
    add_photo(store,'4','pinot',root/'images/cabernet/photo.jpg')
    if args.portal_reference:
        with store.db() as db:db.execute("UPDATE images SET source='vino-svoe.ru',kind='catalog_reference' WHERE id='2'")
    uvicorn.run(app,host='127.0.0.1',port=8200)
