"""Собирает обучающую выборку для реранкера: запрос -> top-K кандидатов с
признаками и меткой «этот кандидат верный».

Источник запросов — фотографии с известным slug, которых НЕТ в индексе:
промо-съёмка из `real_photos` и лишние снимки из `catalog_photos` сверх
проиндексированного эталона. Честные 261 полевой кадр не используются —
они остаются единственным измерителем.

Проверено на подвыборке 120 промо-кадров: верный ответ на 1-м месте в 79%,
на 2-м в 12%, на 3-5-м в 6%, вне top-10 — 0%. То есть около пятой части
примеров трудные, и при этом все обучаемые.

    python -m retrieval.build_rerank_dataset --limit-extra 1200
"""

import argparse
import csv
import json
import os
import random
from pathlib import Path

from PIL import Image

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .predict import BACKBONE, PREPROCESS
from .preprocess_field import preprocess
from .rerank_features import FEATURE_NAMES, candidate_features, load_near_dup_pairs

REAL_ROOT = Path("~/Downloads/real_photos").expanduser()
CATALOG_ROOT = Path("~/Downloads/catalog_photos").expanduser()
TOP_K = 10


def promo_queries() -> list[tuple[Path, str]]:
    path = config.OUTPUTS_DIR / "field_labels_promo.csv"
    with open(path, encoding="utf-8") as f:
        return [(REAL_ROOT / row["image_path"], row["true_slug"])
                for row in csv.DictReader(f) if row.get("true_slug")]


def catalog_extra_queries(lookup: dict[str, dict], limit: int, seed: int) -> list[tuple[Path, str]]:
    """Снимки из catalog_photos, кроме того файла, что уже лежит в индексе."""
    indexed = {slug: os.path.basename(rec["photo_file"]) for slug, rec in lookup.items()}
    extras = []
    for slug_dir in sorted(CATALOG_ROOT.iterdir()):
        if not slug_dir.is_dir() or slug_dir.name not in indexed:
            continue
        for photo in sorted(slug_dir.iterdir()):
            if photo.is_file() and photo.name != indexed[slug_dir.name]:
                extras.append((photo, slug_dir.name))
    random.Random(seed).shuffle(extras)
    return extras[:limit]


FIELD_SETS = {
    "own": ("~/Downloads/real_photos", "field_labels_own.csv"),
    "org": ("~/Downloads/Реальные фото", "field_labels.csv"),
}


def field_queries(name: str) -> list[tuple[Path, str]]:
    photos_dir, labels_name = FIELD_SETS[name]
    root = Path(photos_dir).expanduser()
    with open(config.OUTPUTS_DIR / labels_name, encoding="utf-8") as f:
        return [(root / row["image_path"], row["true_slug"])
                for row in csv.DictReader(f) if row.get("true_slug")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default="train", choices=["train", "own", "org"],
                        help="train — промо + лишние каталожные; own/org — честные полевые наборы")
    parser.add_argument("--limit-extra", type=int, default=1200,
                        help="сколько лишних каталожных снимков добавить к промо")
    parser.add_argument("--keep-unreachable", action="store_true",
                        help="не отбрасывать запросы, где верного ответа нет в top-K (нужно для честной оценки)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="rerank_dataset.json")
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        lookup = {r["slug"]: r for r in json.load(f) if r["photo_file"]}
    near_dup = load_near_dup_pairs(config.OUTPUTS_DIR / "near_duplicates_report.json")
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}.npz")
    backbone = load_backbone(BACKBONE)

    if args.queries == "train":
        queries = promo_queries() + catalog_extra_queries(lookup, args.limit_extra, args.seed)
        print(f"Запросов: {len(queries)} (промо + лишние каталожные)")
    else:
        queries = field_queries(args.queries)
        print(f"Запросов: {len(queries)} (честный полевой набор '{args.queries}')")

    groups, skipped = [], 0
    for i, (path, true_slug) in enumerate(queries, start=1):
        if not path.exists():
            skipped += 1
            continue
        try:
            with Image.open(path) as raw:
                embedding = backbone.encode(preprocess(raw, PREPROCESS))
        except Exception as exc:
            print(f"  пропуск {path.name}: {exc}")
            skipped += 1
            continue

        candidates = [{"slug": r.slug, "score": r.score}
                      for r in index.search(embedding, top_k=TOP_K)]
        if not any(c["slug"] == true_slug for c in candidates) and not args.keep_unreachable:
            # верного ответа нет в списке — учить на таком нечему; но при оценке
            # такие запросы обязаны оставаться в знаменателе
            skipped += 1
            continue

        groups.append({
            "query": str(path), "true_slug": true_slug,
            "slugs": [c["slug"] for c in candidates],
            "labels": [int(c["slug"] == true_slug) for c in candidates],
            "features": candidate_features(candidates, lookup, near_dup),
        })
        if i % 100 == 0:
            print(f"  {i}/{len(queries)} (собрано групп: {len(groups)})", flush=True)

    hard = sum(1 for g in groups if g["labels"][0] != 1)
    out_path = config.OUTPUTS_DIR / args.out
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"feature_names": list(FEATURE_NAMES), "top_k": TOP_K, "groups": groups},
                  f, ensure_ascii=False)

    print(f"\nГрупп собрано: {len(groups)}, пропущено запросов: {skipped}")
    print(f"Из них трудных (верный ответ не на 1-м месте): {hard} ({hard / max(len(groups), 1):.1%})")
    print(f"Датасет: {out_path}")


if __name__ == "__main__":
    main()
