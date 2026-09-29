"""Собирает сырые сигналы (embedding + ORB + OCR) по top-K кандидатам для
того же набора запросов, что и benchmark_near_dup.py, и считает точность под
разными комбинациями весов — без повторного прогона моделей на каждую
комбинацию.

Запуск:
    python -m retrieval.benchmark_rerank --backbone siglip2 --n-aug 2 --control-size 30
"""

import argparse
import json
import random

from PIL import Image

from data_prep import config
from .augment import simulate_field_photo
from .backbones import load_backbone
from .index import EmbeddingIndex
from .rerank_signals import ocr_match_score, ocr_text, orb_inlier_score

TOP_K = 5


def load_catalog_lookup() -> dict:
    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    return {r["slug"]: r for r in records if r["photo_file"]}


def collect_signals(backbone, index, lookup, query_img: Image.Image, true_slug: str, sibling_slug: str, group: str) -> dict:
    emb = backbone.encode(query_img)
    top = index.search(emb, top_k=TOP_K)
    query_ocr = ocr_text(query_img)

    candidates = []
    for r in top:
        cand_record = lookup[r.slug]
        cand_img = Image.open(config.UPLOADS_DIR / cand_record["photo_file"])
        candidates.append({
            "slug": r.slug,
            "embedding_score": r.score,
            "orb_score": orb_inlier_score(query_img, cand_img),
            "ocr_score": ocr_match_score(query_ocr, cand_record["rerank_target"]),
        })

    return {
        "group": group,
        "true_slug": true_slug,
        "sibling_slug": sibling_slug,
        "query_ocr_text": query_ocr,
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2")
    parser.add_argument("--n-aug", type=int, default=2)
    parser.add_argument("--control-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    lookup = load_catalog_lookup()
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    backbone = load_backbone(args.backbone)

    with open(config.OUTPUTS_DIR / "near_duplicates_report.json", encoding="utf-8") as f:
        report = json.load(f)
    pairs = [p for p in report["near_identical_pairs"] if p["slug_a"] in lookup and p["slug_b"] in lookup]
    print(f"Near-duplicate пар: {len(pairs)}, n_aug={args.n_aug} -> {len(pairs) * 2 * args.n_aug} near-dup запросов")

    raw = []
    for i, pair in enumerate(pairs):
        for anchor_slug, sibling_slug in [(pair["slug_a"], pair["slug_b"]), (pair["slug_b"], pair["slug_a"])]:
            base_img = Image.open(config.UPLOADS_DIR / lookup[anchor_slug]["photo_file"])
            for aug_i in range(args.n_aug):
                q = simulate_field_photo(base_img, seed=hash((anchor_slug, aug_i)) % (2**31))
                raw.append(collect_signals(backbone, index, lookup, q, anchor_slug, sibling_slug, "near_dup"))
        if (i + 1) % 20 == 0:
            print(f"  near-dup пар обработано: {i + 1}/{len(pairs)}")

    near_dup_slugs = {p["slug_a"] for p in pairs} | {p["slug_b"] for p in pairs}
    control_candidates = [s for s in lookup if s not in near_dup_slugs]
    control_slugs = random.sample(control_candidates, min(args.control_size, len(control_candidates)))

    for j, slug in enumerate(control_slugs):
        base_img = Image.open(config.UPLOADS_DIR / lookup[slug]["photo_file"])
        for aug_i in range(args.n_aug):
            q = simulate_field_photo(base_img, seed=hash((slug, aug_i)) % (2**31))
            raw.append(collect_signals(backbone, index, lookup, q, slug, None, "control"))
        if (j + 1) % 10 == 0:
            print(f"  control обработано: {j + 1}/{len(control_slugs)}")

    out_path = config.OUTPUTS_DIR / f"rerank_raw_{args.backbone}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"\nСырые сигналы сохранены: {out_path} ({len(raw)} запросов)")
    print("Дальше: python -m retrieval.analyze_rerank --backbone", args.backbone)


if __name__ == "__main__":
    main()
