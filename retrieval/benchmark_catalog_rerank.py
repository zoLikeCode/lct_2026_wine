"""Реранкинг на целевой метрике: случайная выборка каталога, реалистичная
аугментация, сигналы ORB (+ опционально OCR) поверх top-K от эмбеддинга.

Зачем отдельно от benchmark_rerank.py: тот считает near-duplicate пары, а балл
кейса — accuracy на равномерной выборке. Запас измерен: на siglip2_384
top-1 = 82.0% при top-5 = 95.4%, то есть 13.4пп запросов имеют верное вино в
пятёрке, но не первым.

OCR по умолчанию выключен: он дорог (секунды на кадр) и слаб (§8.2, плюс
кейсодержатель сообщил, что у них OCR прироста не дал). Флаг --with-ocr
позволяет проверить его вклад отдельно.

Запуск:
    python -m retrieval.benchmark_catalog_rerank --backbone siglip2_384 --sample 250
"""

import argparse
import gc
import json
import pickle
import random
import time

import torch
from PIL import Image

from data_prep import config
from .augment_realistic import simulate_realistic_photo
from .backbones import load_backbone
from .index import EmbeddingIndex
from .rerank_signals import (describe, ocr_match_score, ocr_text,
                             orb_inlier_score, orb_inlier_score_cached)

PROGRESS_EVERY = 50

WEIGHT_GRID = [
    ("baseline (только embedding)", 0.0, 0.0),
    ("+ ORB 0.05", 0.0, 0.05),
    ("+ ORB 0.10", 0.0, 0.10),
    ("+ ORB 0.20", 0.0, 0.20),
    ("+ ORB 0.40", 0.0, 0.40),
    ("+ OCR 0.05", 0.05, 0.0),
    ("+ OCR 0.10", 0.10, 0.0),
    ("+ OCR 0.05 + ORB 0.10", 0.05, 0.10),
    ("+ OCR 0.10 + ORB 0.20", 0.10, 0.20),
]


def evaluate(raw: list[dict], alpha: float, beta: float) -> float:
    hits = 0
    for q in raw:
        best = max(q["candidates"],
                   key=lambda c: c["embedding_score"] + alpha * c["ocr_score"] + beta * c["orb_score"])
        hits += best["slug"] == q["true_slug"]
    return hits / len(raw) if raw else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2_384")
    parser.add_argument("--sample", type=int, default=250)
    parser.add_argument("--top-k", type=int, default=5,
                        help="сколько кандидатов отдавать реранкеру")
    parser.add_argument("--n-aug", type=int, default=1)
    parser.add_argument("--with-ocr", action="store_true", help="включить OCR-сигнал (дорого)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    total_catalog = len(records)
    lookup = {r["slug"]: r for r in records if r["photo_file"]}
    coverage = len(lookup) / total_catalog

    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    backbone = load_backbone(args.backbone)
    is_mps = backbone.device.type == "mps"

    orb_cache = None
    cache_path = config.OUTPUTS_DIR / "orb_cache.pkl"
    if cache_path.exists():
        with open(cache_path, "rb") as f:
            orb_cache = pickle.load(f)["cache"]

    sample_slugs = random.sample(sorted(lookup), min(args.sample, len(lookup)))
    n_queries = len(sample_slugs) * args.n_aug
    print(f"Индекс: {args.backbone} ({len(lookup)} позиций, покрытие {coverage:.1%})")
    print(f"Запросов: {n_queries}  top-k: {args.top_k}  "
          f"OCR: {'вкл' if args.with_ocr else 'выкл'}  "
          f"ORB-кеш: {'да' if orb_cache else 'нет'}\n")

    raw = []
    done = 0
    t0 = time.time()

    for slug in sample_slugs:
        base_img = Image.open(config.UPLOADS_DIR / lookup[slug]["photo_file"])
        for aug_i in range(args.n_aug):
            query = simulate_realistic_photo(base_img, seed=hash((slug, aug_i)) % (2**31))
            emb = backbone.encode(query)
            top = index.search(emb, top_k=args.top_k)

            query_ocr = ocr_text(query) if args.with_ocr else ""
            points_q, des_q = describe(query) if orb_cache is not None else (None, None)
            candidates = []
            for r in top:
                rec = lookup[r.slug]
                if orb_cache is not None:
                    points_c, des_c = orb_cache[r.slug]
                    orb = orb_inlier_score_cached(points_q, des_q, points_c, des_c)
                else:
                    with Image.open(config.UPLOADS_DIR / rec["photo_file"]) as cand_img:
                        orb = orb_inlier_score(query, cand_img)
                candidates.append({
                    "slug": r.slug,
                    "embedding_score": r.score,
                    "orb_score": orb,
                    "ocr_score": ocr_match_score(query_ocr, rec["rerank_target"]) if args.with_ocr else 0.0,
                })
            raw.append({"true_slug": slug, "candidates": candidates})

            done += 1
            if is_mps and done % 20 == 0:
                torch.mps.empty_cache()
                gc.collect()
            if done % PROGRESS_EVERY == 0:
                el = time.time() - t0
                print(f"  {done}/{n_queries}  ({el:.0f}s, {el/done:.2f}s/запрос)", flush=True)
        base_img.close()

    in_top5 = sum(any(c["slug"] == q["true_slug"] for c in q["candidates"]) for q in raw) / len(raw)
    print(f"\nПотолок реранкинга (верное вино есть в top-{args.top_k}): {in_top5:.1%}\n")

    header = f"{'вариант':30s} | {'top-1':>7s} | {'ожидаемый балл':>15s}"
    print(header)
    print("-" * len(header))
    best = None
    for label, alpha, beta in WEIGHT_GRID:
        if not args.with_ocr and alpha > 0:
            continue
        acc = evaluate(raw, alpha, beta)
        exp = acc * coverage
        print(f"{label:30s} | {acc:6.1%} | {exp:6.1%} -> {exp*50:.1f}/50")
        if best is None or acc > best[1]:
            best = (label, acc, alpha, beta)

    print(f"\nЛучший вариант: {best[0]}  top-1={best[1]:.1%}")

    out_path = config.OUTPUTS_DIR / f"rerank_catalog_{args.backbone}_k{args.top_k}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"backbone": args.backbone, "n_queries": n_queries,
                   "with_ocr": args.with_ocr, "top5_ceiling": in_top5,
                   "best": {"label": best[0], "top1": best[1], "alpha": best[2], "beta": best[3]},
                   "raw": raw}, f, ensure_ascii=False)
    print(f"Сохранено: {out_path}")


if __name__ == "__main__":
    main()
