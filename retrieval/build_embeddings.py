"""Предпосчитывает эмбеддинги эталонных фото каталога под заданный бэкбон.

Запуск:
    python -m retrieval.build_embeddings --backbone dinov2
    python -m retrieval.build_embeddings --backbone siglip2
"""

import argparse
import gc
import json
import time

import numpy as np
import torch
from PIL import Image

from data_prep import config
from .backbones import load_backbone

BATCH_SIZE = 16
CHECKPOINT_EVERY_BATCHES = 10  # ~160 фото — на случай падения процесса (MPS OOM и т.п.)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", required=True)
    parser.add_argument("--resume", action="store_true", help="продолжить с последнего чекпоинта, если файл уже существует")
    args = parser.parse_args()

    catalog_path = config.OUTPUTS_DIR / "catalog_resolved.json"
    with open(catalog_path, encoding="utf-8") as f:
        records = json.load(f)
    resolved = [r for r in records if r["photo_file"]]

    out_path = config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz"

    slugs: list[str] = []
    embeddings: list[np.ndarray] = []
    done_slugs: set[str] = set()
    if args.resume and out_path.exists():
        data = np.load(out_path)
        slugs = list(data["slugs"])
        embeddings = [data["embeddings"]]
        done_slugs = set(slugs)
        print(f"Резюмирую: уже готово {len(done_slugs)}/{len(resolved)}")

    todo = [r for r in resolved if r["slug"] not in done_slugs]
    print(f"Эмбеддим {len(todo)} каталожных фото бэкбоном '{args.backbone}' (всего {len(resolved)})...")

    backbone = load_backbone(args.backbone)
    print(f"  device: {backbone.device}")
    is_mps = backbone.device.type == "mps"

    t0 = time.time()
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        images, valid_batch = [], []
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

        for im in images:
            im.close()
        if is_mps:
            torch.mps.empty_cache()
        gc.collect()

        batch_no = i // BATCH_SIZE
        if batch_no % 5 == 0:
            elapsed = time.time() - t0
            done = len(done_slugs) + i + len(batch)
            print(f"  {done}/{len(resolved)}  ({elapsed:.1f}s, {elapsed / (i + len(batch)):.2f}s/фото)", flush=True)
        if batch_no % CHECKPOINT_EVERY_BATCHES == 0 and batch_no > 0:
            np.savez(out_path, slugs=np.array(slugs), embeddings=np.concatenate(embeddings, axis=0))
            print(f"  [checkpoint] сохранено {len(slugs)}/{len(resolved)}", flush=True)

    embeddings = np.concatenate(embeddings, axis=0)
    elapsed = time.time() - t0
    n_new = len(todo)
    if n_new:
        print(f"Готово за {elapsed:.1f}s ({elapsed / n_new * 1000:.1f} ms/фото). Shape: {embeddings.shape}")

    np.savez(out_path, slugs=np.array(slugs), embeddings=embeddings)
    print(f"Сохранено: {out_path} ({len(slugs)}/{len(resolved)})")


if __name__ == "__main__":
    main()
