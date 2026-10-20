"""Индекс по ЦЕНТРОИДУ нескольких эталонных фото на вино.

Отличие от отклонённого мультиреференса (§11.12). Там в индекс добавлялись
дополнительные ВЕКТОРЫ и бралcя максимум по slug — от этого часть каталога
получала лишний шанс случайно выстрелить высоким score, и top-1 упал на
13.2пп. Здесь число векторов на slug остаётся равным одному: несколько фото
усредняются в один центроид. Это не добавляет шансов, а снижает шум
конкретного эталона — принципиально другая операция.

Бьёт по той части ошибок, которую реранкинг не чинит в принципе: на 261
честном кадре около десяти запросов не находят верного ответа даже в top-10
(§11.28), и там проблема не в порядке выдачи, а в том, что единственный
эталон снят не в том ракурсе.

Уже посчитанные векторы проиндексированных эталонов переиспользуются, так
что считать приходится только дополнительные снимки.

    python -m retrieval.build_centroid_index
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

from data_prep import config
from .backbones import load_backbone
from .predict import BACKBONE, PREPROCESS
from .preprocess_field import preprocess

CATALOG_ROOT = Path("~/Downloads/catalog_photos").expanduser()
MAX_PER_SLUG = 4
BATCH_SIZE = 4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default=BACKBONE)
    parser.add_argument("--max-per-slug", type=int, default=MAX_PER_SLUG)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = [r for r in json.load(f) if r["photo_file"]]
    indexed_name = {r["slug"]: os.path.basename(r["photo_file"]) for r in records}

    existing = np.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    known = {str(slug): existing["embeddings"][i]
             for i, slug in enumerate(existing["slugs"])}
    print(f"Готовых векторов эталонов: {len(known)}")

    extra_paths: dict[str, list[Path]] = {}
    for slug_dir in sorted(CATALOG_ROOT.iterdir()):
        slug = slug_dir.name
        if not slug_dir.is_dir() or slug not in known:
            continue
        photos = [p for p in sorted(slug_dir.iterdir())
                  if p.is_file() and p.name != indexed_name.get(slug)]
        if photos:
            extra_paths[slug] = photos[:args.max_per_slug - 1]

    total_extra = sum(len(v) for v in extra_paths.values())
    print(f"Вин с дополнительными снимками: {len(extra_paths)}, снимков досчитать: {total_extra}")

    backbone = load_backbone(args.backbone)
    vectors: dict[str, list[np.ndarray]] = {slug: [vec] for slug, vec in known.items()}

    done = 0
    for slug, paths in extra_paths.items():
        images = []
        for path in paths:
            try:
                with Image.open(path) as raw:
                    images.append(preprocess(raw, PREPROCESS))
            except Exception as exc:
                print(f"  пропуск {path.name}: {exc}")
        for offset in range(0, len(images), BATCH_SIZE):
            vectors[slug].extend(backbone.encode_batch(images[offset:offset + BATCH_SIZE]))
        done += len(images)
        if done % 200 < len(images):
            print(f"  {done}/{total_extra}", flush=True)

    slugs, centroids = [], []
    for slug, group in vectors.items():
        stacked = np.stack([v / max(np.linalg.norm(v), 1e-8) for v in group])
        centroid = stacked.mean(axis=0)
        centroids.append(centroid / max(np.linalg.norm(centroid), 1e-8))
        slugs.append(slug)

    counts = [len(v) for v in vectors.values()]
    out_path = config.OUTPUTS_DIR / (args.out or f"embeddings_{args.backbone}_centroid.npz")
    np.savez(out_path, slugs=np.array(slugs), embeddings=np.stack(centroids).astype(np.float32))
    print(f"\nВин в индексе: {len(slugs)}, фото на вино в среднем {np.mean(counts):.2f} "
          f"(максимум {max(counts)})")
    print(f"Индекс: {out_path}")


if __name__ == "__main__":
    main()
