"""Fixed server-side deployment worker. Installed root-owned outside the checkout.
Only data/code from the allowlisted public repository is extracted. No repository
scripts, shell hooks, package installers or migrations are executed as root.
"""
import compileall
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

BASE = Path('/opt/wine')
STATE = Path('/var/lib/wine-deploy')
REPOSITORY = 'zoLikeCode/lct_2026_wine'
MAX_ARCHIVE = 200 * 1024 * 1024
MAX_EXPANDED = 400 * 1024 * 1024


def json_write(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.chmod(0o644)
    os.replace(tmp, path)


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'SvoeWine-Deploy/1'})
    with urllib.request.urlopen(request, timeout=90) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Download exceeds the configured limit')
    return data


def main_head():
    return json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/git/ref/heads/main', 65536))['object']['sha']


def extract_release(archive, destination):
    """Reject traversal, links and oversized archives; map backend/dist to server layout."""
    total = 0
    count = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith('/') or '..' in parts or not parts:
                raise ValueError('Unsafe archive path')
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError('Links and special files are not allowed')
            if member.isdir():
                continue
            total += member.size
            count += 1
            if total > MAX_EXPANDED or count > 20000:
                raise ValueError('Expanded archive is too large')
            path = parts[1:]
            if not path:
                continue
            if path == ('requirements.txt',):
                target = destination / 'requirements.txt'
            elif path[0] == 'backend' and len(path) > 1:
                if path[1] in {'models', 'tests', '__pycache__'}:
                    continue
                target = destination / 'wine_server' / Path(*path[1:])
            elif path[0] == 'dist' and len(path) > 1:
                target = destination / 'wine_server/static/mobile' / Path(*path[1:])
            elif path[0] == 'wine_curation' and len(path) == 2 and path[1].endswith('.py'):
                target = destination / Path(*path)
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, target.open('wb') as out:
                shutil.copyfileobj(src, out)
            target.chmod(0o644)
    for required in ['wine_server/app.py', 'wine_server/collection.py', 'wine_server/inference.py',
                     'wine_server/static/mobile/index.html', 'wine_curation/curation.py', 'requirements.txt']:
        if not (destination / required).is_file():
            raise ValueError(f'Release is missing {required}')


def health(timeout=180):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            data = json.loads(fetch('http://127.0.0.1:8100/api/status', 65536))
            if data.get('ready') is True and data.get('backend') == 'local' and data.get('catalog_count', 0) > 0:
                fetch('http://127.0.0.1:8100/', 2 * 1024 * 1024)
                return data
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError('Application did not become ready within the timeout')


def switch(target):
    tmp = BASE / '.current.next'
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target, target_is_directory=True)
    os.replace(tmp, BASE / 'current')


def restart():
    subprocess.run(['systemctl', 'restart', 'wine.service'], check=True, timeout=120)


def deploy(sha, ident):
    if not re.fullmatch(r'[a-f0-9]{40}', sha) or not re.fullmatch(r'[0-9]{1,20}\.[0-9]{1,20}', ident):
        raise ValueError('Invalid deployment identity')
    if sha != main_head():
        return {'status': 'superseded', 'sha': sha}
    release = BASE / 'releases' / ('cicd-' + ident + '-' + sha[:12])
    if release.exists():
        raise ValueError('Release directory already exists; rerun with a new attempt')
    if shutil.disk_usage(BASE).free < 1_000_000_000:
        raise RuntimeError('Less than 1 GB free disk space; deployment stopped')
    previous = (BASE / 'current').resolve(strict=True)
    try:
        with tempfile.TemporaryDirectory(prefix='wine-release-') as tmp:
            archive = Path(tmp) / 'release.tar.gz'
            archive.write_bytes(fetch(f'https://codeload.github.com/{REPOSITORY}/tar.gz/{sha}', MAX_ARCHIVE))
            release.mkdir(mode=0o755)
            extract_release(archive, release)
        # Runtime packages are managed separately, so code rollback also restores a compatible runtime.
        expected = (STATE / 'runtime-requirements.sha256').read_text().strip()
        if hashlib.sha256((release / 'requirements.txt').read_bytes()).hexdigest() != expected:
            raise RuntimeError('Runtime dependencies changed; install and verify them before updating the server fingerprint')
        if not compileall.compile_dir(release, quiet=1):
            raise RuntimeError('Python syntax check failed')
        (release / 'wine_server/models').symlink_to(BASE / 'wine_server/models', target_is_directory=True)
        (release / 'wine_dataset').symlink_to(BASE / 'wine_dataset', target_is_directory=True)
        (release / 'wine_media').symlink_to(BASE / 'wine_media', target_is_directory=True)
        backup = BASE / 'backups' / ('cicd-' + ident)
        backup.mkdir(mode=0o700)
        # SQLite online backup is consistent even while users continue making changes.
        database = BASE / 'wine_dataset/collector/state.sqlite3'
        with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as src:
            with sqlite3.connect(backup / 'state.sqlite3') as dst:
                src.backup(dst)
        with tarfile.open(backup / 'code.tar.gz', 'w:gz') as tar:
            for folder in ['wine_server', 'wine_curation']:
                source = previous / folder
                for p in source.rglob('*'):
                    rel = p.relative_to(source)
                    if any(part in {'models', 'reports', '__pycache__'} for part in rel.parts):
                        continue
                    if p.is_file() and not p.is_symlink() and p.name != 'ДОСТУП.md':
                        tar.add(p, arcname=folder + '/' + str(rel), recursive=False)
        json_write(backup / 'release.json', {'previous': str(previous), 'sha': sha, 'job': ident})
        if sha != main_head():
            return {'status': 'superseded', 'sha': sha}
        switch(release)
        try:
            restart()
            status = health()
        except BaseException:
            switch(previous)
            restart()
            health()
            raise
        version = {'sha': sha, 'run': ident, 'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'model_version': status['model_version']}
        json_write(STATE / 'current.json', version)
        # Only our own artifacts, oldest first. Never touch manually created backups or active releases.
        prune(BASE / 'backups', 5, set())
        prune(BASE / 'releases', 5, {release, previous})
        return {'status': 'success', **version}
    except BaseException:
        if (BASE / 'current').resolve() != release:
            shutil.rmtree(release, ignore_errors=True)
        raise


def prune(parent, keep, protected):
    entries = sorted((p for p in parent.glob('cicd-*') if re.fullmatch(r'cicd-[0-9]+\.[0-9]+(?:-[a-f0-9]{12})?', p.name) and p.is_dir() and not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in entries[keep:]:
        if p not in protected:
            shutil.rmtree(p)


def run():
    with (STATE / 'worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A worker restart during deployment must restore the last known healthy release.
        pending = STATE / 'pending.json'
        if pending.exists():
            request = json.loads(pending.read_text())
            backup = BASE / 'backups' / ('cicd-' + request['job']) / 'release.json'
            if backup.exists():
                switch(Path(json.loads(backup.read_text())['previous']))
                restart()
                health()
            json_write(STATE / 'status' / (request['job'] + '.json'), {'status': 'failed', 'error': 'Deployment interrupted; previous code restored', 'sha': request['sha']})
            (STATE / 'queue' / (request['job'] + '.json')).unlink(missing_ok=True)
            pending.unlink()
        while True:
            for path in sorted((STATE / 'queue').glob('*.json')):
                try:
                    if path.is_symlink() or path.stat().st_size > 4096:
                        raise ValueError('Invalid queue file')
                    request = json.loads(path.read_text())
                    ident, sha = request['job'], request['sha']
                    if path.name != ident + '.json' or not re.fullmatch(r'[0-9]{1,20}\.[0-9]{1,20}', ident) or not re.fullmatch(r'[a-f0-9]{40}', sha):
                        raise ValueError('Invalid job')
                    result = STATE / 'status' / (ident + '.json')
                    if result.exists() and json.loads(result.read_text()).get('status') in {'success', 'failed', 'superseded'}:
                        path.unlink()
                        continue
                    json_write(pending, request)
                    json_write(result, {'status': 'deploying', 'sha': sha})
                    try:
                        if not 0 <= time.time() - request['created'] < 1800:
                            raise ValueError('Expired queued deployment')
                        output = deploy(sha, ident)
                    except Exception as exc:
                        output = {'status': 'failed', 'sha': sha, 'error': str(exc)[:500]}
                    json_write(result, output)
                    pending.unlink(missing_ok=True)
                    print(json.dumps({'job': ident, **output}), flush=True)
                except Exception as exc:
                    print('Rejected invalid queue entry: ' + str(exc)[:200], flush=True)
                path.unlink(missing_ok=True)
            time.sleep(2)


if __name__ == '__main__':
    run()
