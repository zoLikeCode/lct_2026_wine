"""Create a fresh, private dataset without copying any user's scans or labels.

Existing database and catalog are never replaced. This permits a code upgrade
to reuse a dataset mounted at DATA_ROOT on the same server.
"""
import argparse
import csv
import os
import shutil
import sqlite3
from pathlib import Path


IMAGES_SCHEMA = '''CREATE TABLE images(
    id TEXT PRIMARY KEY, slug TEXT, source TEXT, page_url TEXT, image_url TEXT,
    title TEXT, kind TEXT, status TEXT, reason TEXT, path TEXT, sha256 TEXT,
    pixel_sha256 TEXT, phash TEXT, width INTEGER, height INTEGER, bytes INTEGER,
    vintage TEXT, validation TEXT, downloaded_at TEXT
)'''


def bootstrap(dataset: Path, seed_catalog: Path) -> None:
    dataset = dataset.expanduser().resolve()
    (dataset / 'collector').mkdir(parents=True, exist_ok=True)
    (dataset / 'curation').mkdir(exist_ok=True)
    (dataset / 'images').mkdir(exist_ok=True)
    (dataset / 'needs_review').mkdir(exist_ok=True)
    (dataset.parent / 'wine_media' / 'scans').mkdir(parents=True, exist_ok=True)
    (dataset.parent / 'wine_media' / 'uploads').mkdir(parents=True, exist_ok=True)

    catalog = dataset / 'catalog.csv'
    if not catalog.exists():
        shutil.copyfile(seed_catalog, catalog)
    with catalog.open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or 'Slug' not in reader.fieldnames or not any(row.get('Slug') for row in reader):
            raise ValueError(f'Invalid catalog: {catalog}')

    dbpath = dataset / 'collector' / 'state.sqlite3'
    if not dbpath.exists():
        temporary = dbpath.with_suffix('.sqlite3.tmp')
        try:
            with sqlite3.connect(temporary) as db:
                db.execute(IMAGES_SCHEMA)
                db.execute('CREATE INDEX image_slug ON images(slug)')
                db.commit()
            os.replace(temporary, dbpath)
        finally:
            temporary.unlink(missing_ok=True)
    with sqlite3.connect(dbpath) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError(f'Database integrity check failed: {dbpath}')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='images'").fetchone():
            raise ValueError(f'Database is missing images table: {dbpath}')

    # Store.export() reads coverage.csv when a photo is annotated or assigned.
    for name, header in (
        ('manifest.csv', ['id', 'slug', 'path', 'status']),
        ('coverage.csv', ['slug', 'name', 'winery']),
    ):
        path = dataset / name
        if not path.exists():
            with path.open('x', encoding='utf-8-sig', newline='') as dest:
                csv.writer(dest).writerow(header)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    args = parser.parse_args()
    bootstrap(args.dataset, args.catalog)
