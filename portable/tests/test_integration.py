"""Opt-in real-model regression; source photos are never committed to Git."""
import hashlib
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from portable.app import create_app


def test_real_predictions_match_frozen_server():
    directory = os.environ.get('WINE_TEST_IMAGES_DIR')
    if not directory:
        pytest.skip('Set WINE_TEST_IMAGES_DIR to run the real-model regression')
    cases = json.loads(Path(__file__).with_name('regression_cases.json').read_text(encoding='utf-8'))
    with TestClient(create_app()) as client:
        health = client.get('/health').json()
        assert health['catalog_count'] == 2034
        for case in cases:
            payload = (Path(directory) / case['filename']).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == case['sha256']
            result = client.post('/v1/predict', files={'image': (case['filename'], payload)})
            assert result.status_code == 200, result.text
            pred = result.json()
            assert pred['model_version'] == case['model_version']
            assert pred['slug'] == case['slug']
            assert [p['slug'] for p in pred['top5']] == case['top5']
            assert len(set(case['top5'])) == 5
            assert pred['recognized_text'] == case['recognized_text']
        response = client.post('/v1/eval/predict', files={'image': (case['filename'], payload)})
        assert response.json() == {'slug': case['slug']}
