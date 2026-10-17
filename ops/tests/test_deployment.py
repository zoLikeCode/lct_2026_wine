import io
import json
import sys
import tarfile
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oidc_gateway as gateway
import deploy_release as worker


@pytest.fixture(scope='module')
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims():
    now = int(time.time())
    return {'iss': gateway.ISSUER, 'aud': gateway.AUDIENCE, 'sub': next(iter(gateway.SUBJECTS)),
            'iat': now, 'nbf': now, 'exp': now + 300, 'jti': 'test-id',
            'repository': gateway.REPOSITORY, 'repository_id': '1371476726',
            'repository_owner_id': '70718862', 'ref': 'refs/heads/main',
            'workflow_ref': gateway.WORKFLOW, 'workflow_sha': 'a' * 40,
            'sha': 'a' * 40, 'event_name': 'push', 'runner_environment': 'github-hosted',
            'run_id': '123', 'run_attempt': '1'}


def test_main_identity_and_both_subject_formats(private_key):
    for subject in gateway.SUBJECTS:
        data = claims() | {'sub': subject}
        token = jwt.encode(data, private_key, algorithm='RS256')
        result = gateway.authenticate(token, private_key.public_key())
        assert result['sha'] == 'a' * 40
        assert gateway.job_id(result) == '123.1'


@pytest.mark.parametrize('changes', [
    {'repository_id': '99'}, {'repository_owner_id': '99'},
    {'repository': 'attacker/lct_2026_wine'}, {'ref': 'refs/heads/evil'},
    {'workflow_ref': gateway.WORKFLOW.replace('deploy.yml', 'other.yml')},
    {'workflow_sha': 'b' * 40}, {'event_name': 'pull_request'},
    {'sub': f'repo:{gateway.REPOSITORY}:pull_request'}, {'aud': 'other'},
    {'iss': 'https://attacker.invalid'}, {'runner_environment': 'self-hosted'},
    {'sha': '../../etc/passwd'}, {'run_id': '../42'},
    {'exp': int(time.time()) - 30}, {'exp': int(time.time()) + 1800},
])
def test_reject_wrong_identity(private_key, changes):
    token = jwt.encode(claims() | changes, private_key, algorithm='RS256')
    with pytest.raises((ValueError, jwt.PyJWTError)):
        gateway.authenticate(token, private_key.public_key())


def test_reject_forged_signature(private_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(claims(), other, algorithm='RS256')
    with pytest.raises(jwt.InvalidSignatureError):
        gateway.authenticate(token, private_key.public_key())


@pytest.mark.parametrize('name,kind', [('repo/../../evil', 'file'), ('/etc/passwd', 'file'), ('repo/backend/link', 'link')])
def test_archive_rejects_unsafe_files(tmp_path, name, kind):
    archive = tmp_path / 'input.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        info = tarfile.TarInfo(name)
        if kind == 'link':
            info.type = tarfile.SYMTYPE
            info.linkname = '/etc/passwd'
            tar.addfile(info)
        else:
            info.size = 1
            tar.addfile(info, io.BytesIO(b'x'))
    with pytest.raises(ValueError):
        worker.extract_release(archive, tmp_path / 'release')
    assert not (tmp_path / 'evil').exists()


def test_rollback_preserves_live_data(tmp_path, monkeypatch):
    base = tmp_path / 'wine'
    base.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(worker, 'BASE', base)
    monkeypatch.setattr(worker, 'STATE', state)
    old = base / 'old'
    old.mkdir()
    (base / 'current').symlink_to(old)
    (base / 'releases').mkdir()
    (base / 'backups').mkdir()
    data = base / 'wine_dataset/collector'
    data.mkdir(parents=True)
    database = data / 'state.sqlite3'
    with worker.sqlite3.connect(database) as db:
        db.execute('CREATE TABLE notes (body TEXT)')
        db.execute("INSERT INTO notes VALUES ('before')")
    requirements = b'fastapi==0.115.12\n'
    (state / 'runtime-requirements.sha256').write_text(worker.hashlib.sha256(requirements).hexdigest())
    monkeypatch.setattr(worker, 'main_head', lambda: 'a' * 40)
    monkeypatch.setattr(worker, 'fetch', lambda *args: b'archive')
    monkeypatch.setattr(worker, 'restart', lambda: None)
    def extract(_archive, release):
        (release / 'wine_server').mkdir()
        (release / 'wine_server/app.py').write_text('pass\n')
        (release / 'requirements.txt').write_bytes(requirements)
    monkeypatch.setattr(worker, 'extract_release', extract)
    attempts = []
    def health():
        attempts.append(True)
        if len(attempts) == 1:
            with worker.sqlite3.connect(database) as db:
                db.execute("INSERT INTO notes VALUES ('saved during deployment')")
            raise RuntimeError('unhealthy release')
        return {'ready': True}
    monkeypatch.setattr(worker, 'health', health)
    with pytest.raises(RuntimeError, match='unhealthy release'):
        worker.deploy('a' * 40, '123.1')
    assert (base / 'current').resolve() == old
    with worker.sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM notes').fetchone()[0] == 2
    with worker.sqlite3.connect(base / 'backups/cicd-123.1/state.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM notes').fetchone()[0] == 1


def test_stale_commit_does_not_touch_application(monkeypatch):
    monkeypatch.setattr(worker, 'main_head', lambda: 'b' * 40)
    assert worker.deploy('a' * 40, '123.1')['status'] == 'superseded'
