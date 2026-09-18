"""Предпосчитывает эмбеддинги эталонных фото каталога под заданный бэкбон.

Запуск:
    python -m retrieval.build_embeddings --backbone dinov2
    python -m retrieval.build_embeddings --backbone siglip2
"""

import argparse
import json
import time

import numpy as np
from PIL import Image

from data_prep import config
from .backbones import load_backbone

BATCH_SIZE = 16


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", choices=["dinov2", "dinov2_mean", "siglip2"], required=True)
    args = parser.parse_args()

    catalog_path = config.OUTPUTS_DIR / "catalog_resolved.json"
    with open(catalog_path, encoding="utf-8") as f:
        records = json.load(f)
    resolved = [r for r in records if r["photo_file"]]
    print(f"Эмбеддим {len(resolved)} каталожных фото бэкбоном '{args.backbone}'...")

    backbone = load_backbone(args.backbone)
    print(f"  device: {backbone.device}")

    slugs, embeddings = [], []
    t0 = time.time()
    for i in range(0, len(resolved), BATCH_SIZE):
        batch = resolved[i:i + BATCH_SIZE]
        images = []
        valid_batch = []
        for r in batch:
            path = config.UPLOADS_DIR / r["photo_file"]
            try:
                images.append(Image.open(path))
                valid_batch.append(r)
            except Exception as e:
                print(f"  skip {r['slug']}: {e}")
        if not images:
            continue
        embs = backbone.encode_batch(images)
        slugs.extend(r["slug"] for r in valid_batch)
        embeddings.append(embs)
        if i % (BATCH_SIZE * 10) == 0:
            elapsed = time.time() - t0
            print(f"  {i + len(batch)}/{len(resolved)}  ({elapsed:.1f}s)")

    embeddings = np.concatenate(embeddings, axis=0)
    elapsed = time.time() - t0
    print(f"Готово за {elapsed:.1f}s ({elapsed / len(slugs) * 1000:.1f} ms/фото). Shape: {embeddings.shape}")

    out_path = config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz"
    np.savez(out_path, slugs=np.array(slugs), embeddings=embeddings)
    print(f"Сохранено: {out_path}")


if __name__ == "__main__":
    main()
