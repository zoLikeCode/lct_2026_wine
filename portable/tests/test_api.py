import io
import json
import hashlib
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from portable.app import create_app
from portable.images import ImageTooLarge, load_image
from portable import assets, images


class FakeEngine:
    version = 'test'
    slugs = ['test-wine']
    catalog = {'test-wine': {'Название вина': 'Тест'}}

    def predict(self, image):
        assert image.mode == 'RGB'
        self.size = image.size
        return {'slug': 'test-wine', 'top5': [{'slug': 'test-wine', 'score': 0.7}],
                'model_version': self.version, 'score_type': 'ranking_score_not_probability'}


def photo(mode='RGB', orientation=None):
    buffer = io.BytesIO()
    image = Image.new(mode, (20, 40))
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    image.save(buffer, format='PNG', exif=exif)
    return buffer.getvalue()


def test_api_contracts_and_invalid_input():
    engine = FakeEngine()
    with TestClient(create_app(engine=engine)) as client:
        assert client.get('/health').json()['catalog_count'] == 1
        result = client.post('/v1/eval/predict', files={'image': ('photo.png', photo('L', 6))})
        assert result.status_code == 200
        assert result.json() == {'slug': 'test-wine'}
        assert engine.size == (40, 20)
        result = client.post('/v1/predict', files={'image': ('photo.png', photo('RGBA'))})
        assert result.json()['portal_url'] == 'https://vino-svoe.ru/wines/test-wine'
        assert result.json()['wine']['Название вина'] == 'Тест'
        assert client.post('/v1/eval/predict', files={'image': ('bad.jpg', b'not an image')}).status_code == 422
        assert client.post('/v1/eval/predict').status_code == 422


def test_oversized_input(monkeypatch):
    monkeypatch.setattr(images, 'MAX_BYTES', 2)
    with pytest.raises(ImageTooLarge):
        load_image(b'123')
    with TestClient(create_app(engine=FakeEngine())) as client:
        assert client.post('/v1/eval/predict', files={'image': ('big.png', photo())}).status_code == 413


def test_bundle_rejects_missing_or_changed_file(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'files': {'weights': {
        'bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}}}))
    monkeypatch.setattr(assets, 'MANIFEST_PATH', manifest)
    with pytest.raises(ValueError, match='missing: weights'):
        assets.verify_bundle(tmp_path)
    (tmp_path / 'weights').write_bytes(b'abc')
    assets.verify_bundle(tmp_path)
    (tmp_path / 'weights').write_bytes(b'abd')
    with pytest.raises(ValueError, match='checksum mismatch'):
        assets.verify_bundle(tmp_path)
