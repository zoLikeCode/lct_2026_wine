import os,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_server import fixture,FakeEngine,create_app
import uvicorn
parent=HERE.parents[2]/'work'/'wine_server_qa'
parent.mkdir(parents=True,exist_ok=True)
root=parent/'dataset'
if not root.exists():fixture(parent)
os.environ['WINE_SCAN_KEY']='qa-scanner-key-012345678901234567890'
uvicorn.run(create_app(root,FakeEngine(),test=True),host='127.0.0.1',port=8199)
