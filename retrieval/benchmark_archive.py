"""Честная валидация на реальных фото: датасет `Archive`.

В отличие от всех прежних замеров, здесь запрос — это НЕ искажённая копия
эталонного файла из индекса, а другая реальная фотография того же вина
(с интернет-магазинов/маркетплейсов, вручную подтверждённая командой).
Индекс не меняется — используется штатный `outputs/embeddings_{backbone}.npz`,
построенный по нашим собственным каталожным эталонам.

Запуск:
    python -m retrieval.benchmark_archive --backbone siglip2_512 --orb-weight 0.8
"""

import argparse
import gc
import json
import pickle
import time

import torch
from PIL import Image

from data_prep import config
from .archive_dataset import load_archive
from .backbones import load_backbone
from .index import EmbeddingIndex
from .rerank_signals import describe, orb_inlier_score_cached

TOP_K = 5
PROGRESS_EVERY = 100


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2_512")
    parser.add_argument("--orb-weight", type=float, default=0.8)
    parser.add_argument("--limit", type=int, default=None, help="ограничить число запросов (для быстрой проверки)")
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    lookup = {r["slug"]: r for r in records if r["photo_file"]}

    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    backbone = load_backbone(args.backbone)
    is_mps = backbone.device.type == "mps"

    orb_cache = None
    cache_path = config.OUTPUTS_DIR / "orb_cache.pkl"
    if cache_path.exists():
        with open(cache_path, "rb") as f:
            orb_cache = pickle.load(f)["cache"]

    archive = load_archive()
    queries = []  # (slug, path)
    for slug, photos in archive.items():
        if slug not in lookup:
            continue
        for path in photos.extra:
            queries.append((slug, path))
    if args.limit:
        queries = queries[: args.limit]
    print(f"Реальных фото для проверки: {len(queries)} (вин: {len({s for s, _ in queries})})")
    print(f"Индекс: {args.backbone}, ORB-кеш: {'да' if orb_cache else 'нет'}, вес ORB: {args.orb_weight}\n")

    rows = []
    t0 = time.time()
    for i, (true_slug, path) in enumerate(queries, start=1):
        try:
            with Image.open(path) as raw:
                img = raw.convert("RGB")
                emb = backbone.encode(img)
                top = index.search(emb, top_k=TOP_K)

                points_q, des_q = (describe(img) if orb_cache is not None else (None, None))
        except Exception as e:
            print(f"  пропуск {path.name}: {e}")
            continue

        candidates = []
        for r in top:
            orb = 0.0
            if orb_cache is not None:
                points_c, des_c = orb_cache.get(r.slug, (None, None))
                orb = orb_inlier_score_cached(points_q, des_q, points_c, des_c)
            candidates.append({"slug": r.slug, "embedding_score": r.score, "orb_score": orb})

        rows.append({"true_slug": true_slug, "image": str(path), "candidates": candidates})

        if is_mps and i % 20 == 0:
            torch.mps.empty_cache()
            gc.collect()
        if i % PROGRESS_EVERY == 0:
            el = time.time() - t0
            print(f"  {i}/{len(queries)}  ({el:.0f}s, {el / i:.2f}s/фото)", flush=True)

    def accuracy(alpha_orb: float) -> dict:
        top1 = top5 = 0
        for row in rows:
            scored = [(c["slug"], c["embedding_score"] + alpha_orb * c["orb_score"]) for c in row["candidates"]]
            scored.sort(key=lambda x: -x[1])
            slugs = [s for s, _ in scored]
            if slugs and slugs[0] == row["true_slug"]:
                top1 += 1
            if row["true_slug"] in slugs:
                top5 += 1
        n = len(rows)
        return {"n": n, "top1": top1 / n, "top5": top5 / n}

    baseline = accuracy(0.0)
    with_orb = accuracy(args.orb_weight)
    orb_nonzero = sum(1 for row in rows if any(c["orb_score"] > 0.01 for c in row["candidates"])) / len(rows)

    print(f"\n########## Archive: реальные фото, {len(rows)} запросов ##########")
    print(f"  baseline (только embedding):  top1={baseline['top1']:.1%}  top5={baseline['top5']:.1%}")
    print(f"  + ORB (вес {args.orb_weight}):        top1={with_orb['top1']:.1%}  top5={with_orb['top5']:.1%}")
    print(f"  ORB дал ненулевой вклад в:     {orb_nonzero:.1%} запросов")

    out_path = config.OUTPUTS_DIR / f"benchmark_archive_{args.backbone}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"backbone": args.backbone, "orb_weight": args.orb_weight,
                   "baseline": baseline, "with_orb": with_orb, "orb_nonzero_rate": orb_nonzero,
                   "rows": rows}, f, ensure_ascii=False)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
