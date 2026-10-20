"""A consistent database backup. Images and scan files are not duplicated."""
import datetime,os,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_store import DataStore
root=Path(os.environ.get('WINE_DATASET','/opt/wine/wine_dataset'));s=DataStore(root)
folder=root/'curation'/'server_backups';folder.mkdir(exist_ok=True)
dest=folder/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.sqlite3')
with s.guard(),s.db() as source,sqlite3.connect(dest) as target:source.backup(target)
dest.chmod(0o600)
print(dest)
