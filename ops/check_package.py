import sys
import tempfile
from pathlib import Path
from deploy_release import extract_release

with tempfile.TemporaryDirectory() as directory:
    extract_release(sys.argv[1], Path(directory))
print('Deploy archive: paths, size limits and required files verified.')
