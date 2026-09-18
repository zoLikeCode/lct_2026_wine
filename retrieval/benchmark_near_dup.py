"""Главный эксперимент: насколько retrieval сам по себе (без реранкинга)
справляется с near-duplicate парами, и как это соотносится с обычными
(визуально уникальными) позициями каталога.

Для каждой near-duplicate пары (slug_a, slug_b) с известным phash-расстоянием:
берём эталонное фото slug_a, накладываем synthetic "полевую" аугментацию
(retrieval.augment), считаем эмбеддинг, ищем по всему индексу каталога.
Смотрим: на первом месте действительно slug_a, или модель путает с slug_b?
То же самое симметрично для slug_b. Плюс контрольная группа — случайные
визуально уникальные позиции (не near-dup) для сравнения baseline.

Запуск:
    python -m retrieval.benchmark_near_dup --backbone dinov2 --n-aug 5
    python -m retrieval.benchmark_near_dup --backbone siglip2 --n-aug 5
"""

import argparse
import json
import random

import numpy as np
from PIL import Image

from data_prep import config
from .augment import simulate_field_photo
from .backbones import load_backbone
from .index import EmbeddingIndex


def load_catalog_lookup() -> dict:
    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    return {r["slug"]: r for r in records if r["photo_file"]}


def run_query(backbone, index: EmbeddingIndex, image: Image.Image, true_slug: str, sibling_slug: str) -> dict:
    emb = backbone.encode(image)
    results = index.search(emb, top_k=5)
    top1 = results[0].slug if results else None
    top1_score = results[0].score if results else None
    top2_score = results[1].score if len(results) > 1 else None
    sibling_score = next((r.score for r in results if r.slug == sibling_slug), None)
    true_rank = index.rank_of(emb, true_slug)
    return {
        "correct_top1": top1 == true_slug,
        "confused_with_sibling": top1 == sibling_slug,
        "true_rank": true_rank,
        "gap_top1_top2": (top1_score - top2_score) if (top1_score is not None and top2_score is not None) else None,
        "top1_score": top1_score,
        "sibling_score": sibling_score,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", choices=["dinov2", "dinov2_mean", "siglip2"], required=True)
    parser.add_argument("--n-aug", type=int, default=5, help="аугментированных вариантов на каждый anchor")
    parser.add_argument("--control-size", type=int, default=60, help="размер контрольной группы (не near-dup)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    lookup = load_catalog_lookup()
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    backbone = load_backbone(args.backbone)

    with open(config.OUTPUTS_DIR / "near_duplicates_report.json", encoding="utf-8") as f:
        report = json.load(f)
    pairs = [p for p in report["near_identical_pairs"] if p["slug_a"] in lookup and p["slug_b"] in lookup]
    print(f"Near-duplicate пар для теста: {len(pairs)}")

    near_dup_results = []
    for pair in pairs:
        for anchor_slug, sibling_slug in [(pair["slug_a"], pair["slug_b"]), (pair["slug_b"], pair["slug_a"])]:
            photo_path = config.UPLOADS_DIR / lookup[anchor_slug]["photo_file"]
            base_img = Image.open(photo_path)
            for aug_i in range(args.n_aug):
                query_img = simulate_field_photo(base_img, seed=hash((anchor_slug, aug_i)) % (2**31))
                near_dup_results.append(run_query(backbone, index, query_img, anchor_slug, sibling_slug))

    # контрольная группа: случайные визуально уникальные позиции
    near_dup_slugs = {p["slug_a"] for p in pairs} | {p["slug_b"] for p in pairs}
    control_candidates = [s for s in lookup if s not in near_dup_slugs]
    control_slugs = random.sample(control_candidates, min(args.control_size, len(control_candidates)))

    control_results = []
    for slug in control_slugs:
        photo_path = config.UPLOADS_DIR / lookup[slug]["photo_file"]
        base_img = Image.open(photo_path)
        for aug_i in range(args.n_aug):
            query_img = simulate_field_photo(base_img, seed=hash((slug, aug_i)) % (2**31))
            control_results.append(run_query(backbone, index, query_img, slug, "__none__"))

    def summarize(results, label):
        n = len(results)
        acc = sum(r["correct_top1"] for r in results) / n
        confused = sum(r["confused_with_sibling"] for r in results) / n
        gaps = [r["gap_top1_top2"] for r in results if r["gap_top1_top2"] is not None]
        avg_gap = sum(gaps) / len(gaps) if gaps else None
        avg_rank = sum(r["true_rank"] for r in results if r["true_rank"] > 0) / n
        print(f"\n=== {label} (n={n}) ===")
        print(f"  top1 accuracy:         {acc:.1%}")
        print(f"  confused with sibling: {confused:.1%}")
        print(f"  avg true rank:         {avg_rank:.2f}")
        print(f"  avg gap(top1,top2):    {avg_gap:.4f}" if avg_gap is not None else "  avg gap: n/a")
        return {"n": n, "top1_acc": acc, "confused_rate": confused, "avg_true_rank": avg_rank, "avg_gap": avg_gap}

    print(f"\n########## Backbone: {args.backbone} ##########")
    summary_near_dup = summarize(near_dup_results, "NEAR-DUPLICATE queries")
    summary_control = summarize(control_results, "CONTROL queries (визуально уникальные)")

    out = {
        "backbone": args.backbone,
        "n_aug": args.n_aug,
        "near_duplicate": summary_near_dup,
        "control": summary_control,
    }
    out_path = config.OUTPUTS_DIR / f"benchmark_{args.backbone}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
