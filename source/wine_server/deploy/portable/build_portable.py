"""Make a portable release with ready assets and editable application source.

The allowlist excludes private data, local ML inference code and model weights.
"""
import argparse
import hashlib
import os
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SERVER = HERE.parents[1]
PROJECT = SERVER.parent
PREFIX = Path('wine-portable')

BACKEND = (
    'app.py', 'annotation_formats.py', 'data_store.py', 'duplicates.py',
    'exports.py', 'gigachat_client.py', 'ml_client.py', 'product.py',
)
PORTABLE = ('start.sh', 'bootstrap_dataset.py', 'requirements.txt',
            'README.md', '.env.example', '.gitignore')
SOURCE = PREFIX / 'source'
LOCAL_ML = {'inference.py', 'ranking_core.py'}
LOCAL_ML_SCRIPTS = {'export_models.py', 'validate_port.py'}
# Existing VPS templates are useful when moving the service. The old publish
# script and sysctl tuning are tied to the local ML pipeline and stay out.
REFERENCE_DEPLOY = ('nginx-http.conf', 'nginx.conf', 'wine.service',
                    'renew-certificate.sh')


def source_entries():
    """Keep the original project layout so the bundled source can rebuild a ZIP."""
    server_dest = SOURCE / 'wine_server'
    for source in sorted(SERVER.glob('*.py')):
        if source.name not in LOCAL_ML:
            yield source, server_dest / source.name
    for name in ('README.md', 'DESIGN.md', 'requirements.txt',
                 'requirements-dev.txt', 'requirements.server.lock.txt', '.gitignore'):
        yield SERVER / name, server_dest / name
    yield SERVER / 'content' / 'portal_wines.json', server_dest / 'content' / 'portal_wines.json'
    for folder in ('mobile', 'admin'):
        root = SERVER / 'static' / folder
        for source in sorted(root.rglob('*')):
            if source.is_file() and not source.name.startswith('.'):
                yield source, server_dest / 'static' / folder / source.relative_to(root)
    for source in sorted((SERVER / 'scripts').glob('*.py')):
        if source.name not in LOCAL_ML_SCRIPTS:
            yield source, server_dest / 'scripts' / source.name
    for source in sorted((SERVER / 'tests').iterdir()):
        if source.is_file() and source.suffix in {'.py', '.cjs'}:
            yield source, server_dest / 'tests' / source.name
    for name in REFERENCE_DEPLOY:
        yield SERVER / 'deploy' / name, server_dest / 'deploy' / name
    for name in (*PORTABLE, 'build_portable.py'):
        yield HERE / name, server_dest / 'deploy' / 'portable' / name

    curation = PROJECT / 'wine_curation'
    for source in sorted(curation.rglob('*')):
        relative = source.relative_to(curation)
        if (source.is_file() and not any(part.startswith('.') or part in {'__pycache__', 'node_modules'}
                                             for part in relative.parts) and source.suffix in {
            '.py', '.js', '.html', '.css', '.md', '.txt', '.command'
        }):
            yield source, SOURCE / 'wine_curation' / relative

    dataset = PROJECT / 'wine_dataset'
    for name in ('catalog.csv', 'README.md'):
        yield dataset / name, SOURCE / 'wine_dataset' / name
    for source in sorted((dataset / 'collector').iterdir()):
        if source.is_file() and (source.suffix == '.py' or source.name == 'requirements.txt'):
            yield source, SOURCE / 'wine_dataset' / 'collector' / source.name


def entries():
    for name in PORTABLE:
        yield HERE / name, PREFIX / name
    for name in BACKEND:
        yield SERVER / name, PREFIX / 'backend' / name
    yield SERVER / 'content' / 'portal_wines.json', PREFIX / 'backend' / 'content' / 'portal_wines.json'
    yield PROJECT / 'wine_curation' / 'curation.py', PREFIX / 'wine_curation' / 'curation.py'
    yield PROJECT / 'wine_dataset' / 'catalog.csv', PREFIX / 'seed' / 'catalog.csv'
    for src_root, dest_root in ((SERVER / 'static' / 'admin', PREFIX / 'backend' / 'static' / 'admin'),
                                (SERVER / 'static' / 'mobile', PREFIX / 'dist')):
        for source in sorted(src_root.rglob('*')):
            if source.is_file() and not source.name.startswith('.'):
                if 'dark-design' in source.parts or 'old-design' in source.parts:
                    raise ValueError(f'Unexpected theme in release: {source}')
                yield source, dest_root / source.relative_to(src_root)
    yield from source_entries()


def build(output: Path):
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = list(entries())
    paths = [str(dest) for _, dest in files]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate archive path')
    for source, _ in files:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f'Missing or linked source: {source}')
    staged = output.with_name(output.name + '.tmp')
    try:
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
            for source, dest in files:
                data = source.read_bytes()
                info = zipfile.ZipInfo(str(dest), date_time=(2026, 9, 29, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = ((0o755 if dest.name == 'start.sh' else 0o644) << 16)
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        with zipfile.ZipFile(staged) as archive:
            if archive.testzip() is not None:
                raise ValueError('ZIP integrity failed')
        os.replace(staged, output)
    finally:
        staged.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(f'{digest}  {output.name}\n')
    print(f'{output}\n{digest}\n{len(files)} files, {output.stat().st_size} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output)
