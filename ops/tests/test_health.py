import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy_release as worker


@pytest.mark.parametrize('backend', ['local', 'runpod'])
def test_ready_local_and_runpod_deployments_are_accepted(monkeypatch, backend):
    state = dict(ready=True, backend=backend, catalog_count=2103, model_version='model-v1')
    urls = []
    def fetch(url, limit):
        urls.append(url)
        return json.dumps(state).encode() if url.endswith('/api/status') else b'<html>Wine</html>'
    monkeypatch.setattr(worker, 'fetch', fetch)
    assert worker.health(timeout=1) == state
    assert urls == ['http://127.0.0.1:8100/api/status', 'http://127.0.0.1:8100/']


@pytest.mark.parametrize('changes', [dict(ready=False), dict(catalog_count=0), dict(backend='unknown')])
def test_remote_deployment_still_requires_healthy_model(monkeypatch, changes):
    state = dict(ready=True, backend='runpod', catalog_count=2103) | changes
    monkeypatch.setattr(worker, 'fetch', lambda *args: json.dumps(state).encode())
    clock = iter([0, 0, 2])
    monkeypatch.setattr(worker.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='Application did not become ready'):
        worker.health(timeout=1)
