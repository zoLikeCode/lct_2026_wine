"""Считает и кеширует эмбеддинги полевых запросов.

Каждый эксперимент до сих пор заново прогонял 258 кадров через бэкбон
(~2 с на кадр = 10 минут), хотя вектор запроса от эксперимента не зависит.
После кеширования приёмы поверх эмбеддингов перебираются мгновенно.

Кеш инвалидируется вручную: он зависит от бэкбона и режима препроцессинга,
которые записаны в файл рядом с векторами.

    python -m retrieval.cache_query_embeddings
"""

import csv
from pathlib import Path

import numpy as np
from PIL import Image

from data_prep import config
from .backbones import load_backbone
from .predict import BACKBONE, PREPROCESS
from .preprocess_field import preprocess

SETS = {
    "own": ("~/Downloads/real_photos", "field_labels_own.csv"),
    "org": ("~/Downloads/Реальные фото", "field_labels.csv"),
}


def cache_path(name: str) -> Path:
    return config.OUTPUTS_DIR / f"query_embeddings_{name}_{BACKBONE}_{PREPROCESS}.npz"


def load_cached(name: str):
    path = cache_path(name)
    if not path.exists():
        return None
    data = np.load(path, allow_pickle=True)
    return data["embeddings"], list(data["true_slugs"]), list(data["image_paths"])


def build(name: str):
    photos_dir, labels_name = SETS[name]
    root = Path(photos_dir).expanduser()
    with open(config.OUTPUTS_DIR / labels_name, encoding="utf-8") as f:
        labels = [row for row in csv.DictReader(f) if row.get("true_slug")]

    backbone = load_backbone(BACKBONE)
    embeddings, true_slugs, image_paths = [], [], []
    for i, label in enumerate(labels, start=1):
        path = root / label["image_path"]
        if not path.exists():
            continue
        with Image.open(path) as raw:
            embeddings.append(backbone.encode(preprocess(raw, PREPROCESS)))
        true_slugs.append(label["true_slug"])
        image_paths.append(label["image_path"])
        if i % 25 == 0:
            print(f"  {name}: {i}/{len(labels)}", flush=True)

    out = cache_path(name)
    np.savez(out, embeddings=np.stack(embeddings).astype(np.float32),
             true_slugs=np.array(true_slugs), image_paths=np.array(image_paths))
    print(f"  {name}: {len(embeddings)} векторов -> {out}")


def main() -> None:
    for name in SETS:
        if load_cached(name) is not None:
            print(f"  {name}: кеш уже есть, пропуск")
            continue
        build(name)


if __name__ == "__main__":
    main()
