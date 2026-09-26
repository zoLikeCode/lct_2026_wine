"""Paired field-set comparison: OCR-локализация области этикетки внутри
bottle-кропа против production-пути.

Проверяет гипотезу §11.23: узкое место — локализация целевой этикетки, а не
эмбеддер (ручная область даёт +15пп у коллеги). Здесь область ищется
автоматически по ДЕТЕКЦИИ текста (координаты блоков, текст не читается) —
см. `retrieval/label_region.py`, почему это не повторяет §11.12/§11.16/§11.18.

Production defaults не меняет. Построчные результаты — в игнорируемом
`outputs/label_region_experiment.json`.

    python -m retrieval.label_region_experiment
"""

import csv
import json
import statistics
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .label_region import label_region, normalize_boxes, scale_boxes
from .preprocess_field import crop_to_bottle, enhance
from .rerank_signals import get_ocr_reader

PHOTOS_DIR = Path("~/Downloads/Реальные фото").expanduser()
BACKBONE_NAME = "siglip2_so400m512"
OCR_MAX_SIDE = 1280
# (имя, selection, mode, margin); selection=None — фиксированная полоса по
# геометрии бутылки, без OCR: контроль, показывающий, добавляет ли детекция
# текста хоть что-то поверх обычного априорного расположения этикетки.
VARIANTS = (
    ("cluster_band", "cluster_area", "band", 0.10),
    ("central_band", "union_central", "band", 0.10),
    ("central_tight", "union_central", "tight", 0.10),
    ("fixed_band", None, "band", 0.0),
)
# Доля высоты кропа бутылки, где этикетка лежит обычно (та же эвристика, что
# в augment_realistic.LABEL_REGION_FRACTION, расширенная с запасом).
FIXED_BAND = (0.30, 0.85)
BATCH_SIZE = 2


def detect_text_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Только координаты текстовых блоков; распознавание не запускается."""
    width, height = image.size
    scale = OCR_MAX_SIDE / max(width, height)
    probe = image.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                         Image.BILINEAR) if scale < 1.0 else image

    horizontal, free = get_ocr_reader().detect(np.array(probe))
    boxes = normalize_boxes(horizontal[0], free[0])
    return scale_boxes(boxes, 1.0 / scale) if scale < 1.0 else boxes


def _metrics(rows: list[dict], variant: str) -> dict:
    n = len(rows)
    hits1 = sum(row["predictions"][variant][0]["slug"] == row["true_slug"] for row in rows)
    hits5 = sum(any(c["slug"] == row["true_slug"] for c in row["predictions"][variant])
                for row in rows)
    return {"top1": hits1 / n, "top1_count": hits1, "top5": hits5 / n, "top5_count": hits5}


def _paired_changes(rows: list[dict], variant: str) -> dict:
    fixed, broken = [], []
    for row in rows:
        base_ok = row["predictions"]["production"][0]["slug"] == row["true_slug"]
        new_ok = row["predictions"][variant][0]["slug"] == row["true_slug"]
        if new_ok and not base_ok:
            fixed.append(row["image"])
        elif base_ok and not new_ok:
            broken.append(row["image"])
    return {"fixed": fixed, "broken": broken}


def main() -> None:
    with open(config.OUTPUTS_DIR / "field_labels.csv", encoding="utf-8") as f:
        labels = [row for row in csv.DictReader(f) if row.get("true_slug")]

    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE_NAME}.npz")
    backbone = load_backbone(BACKBONE_NAME)

    variant_names = ["production"] + [name for name, _, _, _ in VARIANTS]
    rows, latencies = [], []

    for i, label in enumerate(labels, start=1):
        path = PHOTOS_DIR / label["image_path"]
        if not path.exists():
            continue

        started = time.perf_counter()
        with Image.open(path) as raw:
            whole = ImageOps.exif_transpose(raw).convert("RGB")
        bottle = crop_to_bottle(whole)
        base = bottle if bottle is not None else whole
        boxes = detect_text_boxes(base)
        latencies.append((time.perf_counter() - started) * 1000)

        views = {"production": enhance(base)}
        regions = {}
        for name, selection, mode, margin in VARIANTS:
            if selection is None:
                width, height = base.size
                region = (0, int(height * FIXED_BAND[0]), width, int(height * FIXED_BAND[1]))
            else:
                region = label_region(boxes, base.size, mode, margin, selection=selection)
            regions[name] = region
            views[name] = enhance(base.crop(region)) if region is not None else views["production"]

        ordered = [views[name] for name in variant_names]
        embeddings = []
        for offset in range(0, len(ordered), BATCH_SIZE):
            embeddings.extend(backbone.encode_batch(ordered[offset:offset + BATCH_SIZE]))

        rows.append({
            "image": label["image_path"],
            "true_slug": label["true_slug"],
            "bottle_cropped": bottle is not None,
            "text_boxes": len(boxes),
            "regions": regions,
            "predictions": {
                name: [{"slug": r.slug, "score": r.score} for r in index.search(emb, top_k=5)]
                for name, emb in zip(variant_names, embeddings)
            },
        })

        if i % 10 == 0:
            print(f"  {i}/{len(labels)}", flush=True)

    n = len(rows)
    no_text = sum(row["text_boxes"] == 0 for row in rows)
    print(f"\n########## OCR-локализация этикетки: {n} фото, {BACKBONE_NAME} ##########")
    print(f"  кадров без найденного текста (fallback на bottle-кроп): {no_text} ({no_text / n:.1%})")
    print(f"  детекция текста: медиана {statistics.median(latencies):.0f} мс на кадр\n")
    print(f"  {'вариант':16s} {'top-1':>14s} {'top-5':>14s}   исправлено/сломано")
    for name in variant_names:
        m = _metrics(rows, name)
        if name == "production":
            print(f"  {name:16s} {m['top1']:>7.1%} ({m['top1_count']:>2d}/{n}) "
                  f"{m['top5']:>7.1%} ({m['top5_count']:>2d}/{n})   —")
            continue
        changes = _paired_changes(rows, name)
        print(f"  {name:16s} {m['top1']:>7.1%} ({m['top1_count']:>2d}/{n}) "
              f"{m['top5']:>7.1%} ({m['top5_count']:>2d}/{n})   "
              f"{len(changes['fixed'])}/{len(changes['broken'])}")

    for name in variant_names[1:]:
        changes = _paired_changes(rows, name)
        for image in changes["fixed"]:
            print(f"    [ИСПРАВЛЕНО] {name}: {image}")
        for image in changes["broken"]:
            print(f"    [СЛОМАНО]    {name}: {image}")

    out_path = config.OUTPUTS_DIR / "label_region_experiment.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": n, "backbone": BACKBONE_NAME, "variants": variant_names,
                   "metrics": {name: _metrics(rows, name) for name in variant_names},
                   "changes": {name: _paired_changes(rows, name) for name in variant_names[1:]},
                   "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
