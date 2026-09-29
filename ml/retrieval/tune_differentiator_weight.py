"""Подбор веса точечного OCR-дифференциатора (`differentiator_rerank.py`) на
честном 56-фото наборе (§11.17 findings). OCR по кадру считается один раз на
фото, дальше веса перебираются бесплатно (тот же паттерн, что
`benchmark_catalog_rerank.py`).

Запуск:
    python -m retrieval.tune_differentiator_weight
"""

import csv
import json
from pathlib import Path

from PIL import Image

from data_prep import config
from .differentiator_rerank import net_votes
from .predict import WineFinder
from .rerank_signals import ocr_text

WEIGHT_GRID = [0.0, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5]


def main() -> None:
    photos_dir = Path("/Users/nikitamesh/Downloads/Реальные фото")
    labels_path = config.OUTPUTS_DIR / "field_labels.csv"
    with open(labels_path, encoding="utf-8") as f:
        labels = [row for row in csv.DictReader(f) if row.get("true_slug")]

    finder = WineFinder()  # orb_weight=0, differentiator_weight=0 — только embedding top-K

    raw = []
    for i, row in enumerate(labels):
        path = photos_dir / row["image_path"]
        if not path.exists():
            continue
        with Image.open(path) as img:
            img = img.convert("RGB")
            preds = finder.predict(img, top_k=5)
            text = ocr_text(img)
        records = [p.record for p in preds]
        votes = net_votes(text, records)
        raw.append({
            "image_path": row["image_path"],
            "true_slug": row["true_slug"],
            "ocr_text": text,
            "candidates": [{"slug": p.slug, "embedding_score": p.embedding_score,
                            "vote": votes[p.slug]} for p in preds],
        })
        print(f"  {i + 1}/{len(labels)}", flush=True)

    out_path = config.OUTPUTS_DIR / "differentiator_tune_raw.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"Сырые данные сохранены: {out_path}")

    print(f"\n{'вес':>6} | {'top-1':>7} | сдвинутые ответы")
    for w in WEIGHT_GRID:
        hits = 0
        moved = []
        for q in raw:
            best = max(q["candidates"], key=lambda c: c["embedding_score"] + w * c["vote"])
            correct = best["slug"] == q["true_slug"]
            hits += correct
            baseline_best = max(q["candidates"], key=lambda c: c["embedding_score"])
            if w > 0 and best["slug"] != baseline_best["slug"]:
                moved.append((q["image_path"], baseline_best["slug"], best["slug"], q["true_slug"]))
        acc = hits / len(raw) if raw else 0.0
        print(f"{w:>6.3f} | {acc:>6.1%} | {len(moved)}")
        if w > 0:
            for path, before, after, true in moved:
                mark = "OK" if after == true else ("ИСПОРТИЛ" if before == true else "-")
                print(f"    [{mark}] {path[:25]:27s} {before[:35]} -> {after[:35]}")


if __name__ == "__main__":
    main()
