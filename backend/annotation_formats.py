"""Portable COCO and Ultralytics YOLO exports of existing label annotations."""
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageOps

FORMATS = {'native', 'coco', 'yolo_detect', 'yolo_segment'}
SPLITS = {'none', 'grouped'}


def sha256_file(path):
    # Production runs Python 3.10, before hashlib.file_digest was introduced.
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def assign_splits(rows, mode):
    """Keep one slug and exact file/pixel duplicates together, seed 42."""
    if mode == 'none':
        return {r['id']: '' for r in rows}
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, r in enumerate(rows):
        for field in ('slug', 'sha256', 'pixel_sha256'):
            if not r.get(field):
                continue
            key = (field, r[field])
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    if len(groups) < 3:
        raise ValueError('Для train/valid/test нужны минимум три независимые группы вин. Выберите «Без разбиения».')
    ordered = sorted(groups.values(), key=lambda group: hashlib.sha256(
        ('42:' + '|'.join(sorted({r['slug'] for r in group}))).encode()).hexdigest())
    n = len(ordered)
    val = test = max(1, round(n * .15))
    result = {}
    for i, group in enumerate(ordered):
        split = 'train' if i < n-val-test else 'valid' if i < n-test else 'test'
        result.update({r['id']: split for r in group})
    return result


def write_standard(archive, rows, snapshot, options, store, progress):
    fmt = options['format']
    splits = assign_splits(rows, options['split'])
    includes_images = options['mode'] != 'json'
    coco = {split: {'info': {'description': 'Wine front label annotations', 'version': '1.0'},
                    'licenses': [], 'images': [], 'annotations': [],
                    'categories': [{'id': 0, 'name': 'front_label', 'supercategory': 'label'}]}
            for split in sorted(set(splits.values()))}
    manifest = {'format': fmt, 'class_names': ['front_label'], 'selection': options,
                'split_seed': 42 if options['split'] == 'grouped' else None, 'items': []}
    for n, row in enumerate(rows):
        a = row['annotation']
        bounds = store.validate_bounds({'points': json.loads(a['points']), 'no_label': bool(a['no_label'])})
        points = bounds['points']
        source = snapshot / str(n)
        # Hash source even for annotations-only exports, so dimensions/coordinates
        # never describe a missing or silently replaced image.
        original_sha = sha256_file(source)
        if original_sha != row['sha256']:
            raise ValueError('Изменился файл изображения: ' + row['path'])
        with Image.open(source) as im:
            orientation = im.getexif().get(274, 1)
            suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'BMP': '.bmp'}.get(im.format)
            convert = orientation not in (None, 1) or suffix is None
            normalized = ImageOps.exif_transpose(im) if convert else im
            width, height = normalized.size
            if (width, height) != (a['width'], a['height']):
                raise ValueError('Размер фото не совпадает с разметкой: ' + row['path'])
            if convert:
                suffix = '.png'
            # IDs prevent filename collisions across wine folders and formats.
            name = row['id'] + suffix
            split = splits[row['id']]
            prefix = split + '/' if split else ''
            image_path = prefix + ('' if fmt == 'coco' else 'images/') + name
            export_sha = None
            if includes_images:
                required = width*height*8+131072 if convert else source.stat().st_size
                if shutil.disk_usage(snapshot).free < required+256*1024**2:
                    raise ValueError('Недостаточно места для архива. Выберите меньше папок.')
                if convert:
                    converted = snapshot / (str(n) + '.png')
                    normalized.convert('RGBA' if 'A' in normalized.getbands() else 'RGB').save(converted, format='PNG')
                    export_sha = sha256_file(converted)
                    archive.write(converted, image_path)
                    converted.unlink()
                else:
                    archive.write(source, image_path)
                    export_sha = original_sha
        if fmt == 'coco':
            doc = coco[split]
            doc['images'].append({'id': n+1, 'file_name': name, 'width': width, 'height': height})
            annotation_path = prefix + '_annotations.coco.json'
            if points:
                pixels = [[x*width, y*height] for x, y in points]
                xx, yy = zip(*pixels)
                area = abs(sum(pixels[i][0]*pixels[(i+1)%len(pixels)][1] -
                               pixels[(i+1)%len(pixels)][0]*pixels[i][1] for i in range(len(pixels)))) / 2
                doc['annotations'].append({'id': n+1, 'image_id': n+1, 'category_id': 0,
                    'bbox': [min(xx), min(yy), max(xx)-min(xx), max(yy)-min(yy)],
                    'segmentation': [[v for p in pixels for v in p]], 'area': area, 'iscrowd': 0})
        else:
            annotation_path = prefix + 'labels/' + row['id'] + '.txt'
            values = []
            if points:
                if fmt == 'yolo_detect':
                    xx, yy = zip(*points)
                    values = [(min(xx)+max(xx))/2, (min(yy)+max(yy))/2, max(xx)-min(xx), max(yy)-min(yy)]
                else:
                    values = [v for p in points for v in p]
            archive.writestr(annotation_path, '0 ' + ' '.join(f'{v:.10g}' for v in values) + '\n' if values else '')
        manifest['items'].append({'id': row['id'], 'slug': row['slug'], 'source_path': row['path'],
            'source_sha256': original_sha, 'export_sha256': export_sha, 'image_path': image_path,
            'image_included': includes_images, 'annotation_path': annotation_path, 'split': split or None,
            'no_label': bool(a['no_label']), 'annotation_revision': a['revision'],
            'orientation_applied': convert, 'width': width, 'height': height})
        if (n+1) % 20 == 0:
            progress(n+1)
    if fmt == 'coco':
        for split, doc in coco.items():
            archive.writestr((split+'/' if split else '') + '_annotations.coco.json', json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        paths = 'train: train/images\nval: valid/images\ntest: test/images\n' if options['split'] == 'grouped' else 'train: images\n# For training, add an independent val split. This export is unsplit for import.\n'
        archive.writestr('data.yaml', paths + 'nc: 1\nnames:\n  0: front_label\n')
    archive.writestr('_export.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    archive.writestr('README.txt', '''Wine label annotations — standard COCO / Ultralytics YOLO format.
Class 0: front_label. Wine slug is metadata in _export.json, not a detection class.
Only saved annotations are included. Explicit no_label examples are negatives;
unannotated images are excluded. COCO retains both bounding box and polygon.
Image orientation matches annotations. Rotated images are exported as lossless
PNG with EXIF orientation applied; other supported images retain original bytes.
Source files, annotations and slug folders on the server are never modified.
Annotations-only archives reference the filenames used by this same standard
photo export; use _export.json to map them to original files and apply orientation.
Without splitting, this is an import dataset, not an independent validation set.
Grouped split uses seed 42 and approximately 70/15/15 of groups, keeping each
slug and exact file/pixel duplicates together. Near-duplicates and related wine
series are not automatically detected: review split leakage before evaluation.
''')
