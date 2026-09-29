"""Считает top-1 accuracy под разными весами (embedding + alpha*ocr + beta*orb)
на уже собранных сырых сигналах (benchmark_rerank.py) — без повторного
прогона моделей.

Запуск:
    python -m retrieval.analyze_rerank --backbone siglip2
"""

import argparse
import json

from data_prep import config

WEIGHT_GRID = [
    ("baseline (только embedding)", 0.0, 0.0),
    ("+ OCR (alpha=0.3)", 0.3, 0.0),
    ("+ OCR (alpha=0.6)", 0.6, 0.0),
    ("+ ORB (beta=0.3)", 0.0, 0.3),
    ("+ ORB (beta=0.6)", 0.0, 0.6),
    ("+ OCR + ORB (0.3/0.3)", 0.3, 0.3),
    ("+ OCR + ORB (0.5/0.5)", 0.5, 0.5),
]


def evaluate(raw: list[dict], alpha: float, beta: float) -> dict:
    groups = {"near_dup": [], "control": []}
    for q in raw:
        scored = [
            (c["slug"], c["embedding_score"] + alpha * c["ocr_score"] + beta * c["orb_score"])
            for c in q["candidates"]
        ]
        top1_slug = max(scored, key=lambda x: x[1])[0]
        correct = top1_slug == q["true_slug"]
        confused = q["group"] == "near_dup" and top1_slug == q["sibling_slug"]
        groups[q["group"]].append((correct, confused))

    out = {}
    for g, rows in groups.items():
        if not rows:
            continue
        n = len(rows)
        acc = sum(r[0] for r in rows) / n
        confused = sum(r[1] for r in rows) / n
        out[g] = {"n": n, "acc": acc, "confused": confused}
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2")
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / f"rerank_raw_{args.backbone}.json", encoding="utf-8") as f:
        raw = json.load(f)

    n_near_dup = sum(1 for q in raw if q["group"] == "near_dup")
    n_control = sum(1 for q in raw if q["group"] == "control")
    print(f"Backbone: {args.backbone} | near_dup={n_near_dup} control={n_control}\n")

    header = f"{'вариант':32s} | {'near-dup acc':>13s} | {'confused w/ sibling':>19s} | {'control acc':>12s}"
    print(header)
    print("-" * len(header))
    for label, alpha, beta in WEIGHT_GRID:
        res = evaluate(raw, alpha, beta)
        nd = res.get("near_dup", {"acc": float("nan"), "confused": float("nan")})
        ctl = res.get("control", {"acc": float("nan")})
        print(f"{label:32s} | {nd['acc']:12.1%} | {nd['confused']:18.1%} | {ctl['acc']:11.1%}")


if __name__ == "__main__":
    main()
