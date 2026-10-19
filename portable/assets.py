"""Locate and verify the separately distributed weights and retrieval index."""
import argparse
import hashlib
import json
import os
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name('model_manifest.json')


def model_directory(root=None):
    return Path(root or os.environ.get('WINE_MODEL_DIR') or
                Path(__file__).resolve().parents[1] / 'models' / 'refined-7db54690044e').expanduser().resolve()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_bundle(root=None):
    root = model_directory(root)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    errors = []
    for name, expected in manifest['files'].items():
        path = root / name
        if not path.is_file():
            errors.append(f'missing: {name}')
        elif path.stat().st_size != expected['bytes'] or sha256(path) != expected['sha256']:
            errors.append(f'checksum mismatch: {name}')
    if errors:
        raise ValueError(f'Invalid model bundle in {root}: ' + '; '.join(errors) +
                         '. Extract the complete external bundle; see portable/README.md.')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path)
    args = parser.parse_args()
    manifest = verify_bundle(args.model_dir)
    print(json.dumps({'ok': True, 'model_version': manifest['model_version'],
                      'files_verified': len(manifest['files'])}, indent=2))


if __name__ == '__main__':
    main()
