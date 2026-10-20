"""Сравнение двух детекторов бутылки на 205 полевых кадрах команды.

Проверяет, меняет ли выводы §11.25 (кроп по бутылке вредит) замена нашего
детектора на стоковую COCO-модель из поставки `refined-7db54690044e`
(§11.26: это YOLO11n на COCO, не дообученная на винах).

Гипотеза за замену: наш `wine_label_yolo11m_best.pt` дообучен на 353
студийных снимках Roboflow и даёт уверенный бокс лишь на ~80% полевых
кадров; COCO видел несоизмеримо больше бутылок в произвольных сценах.
Гипотеза против: COCO-модель не знает, какая из бутылок на полке целевая —
на пробном кадре она дала бутылке в руке conf 0.383, а фоновым 0.594/0.462.

    python -m retrieval.compare_detectors_experiment
"""

import csv
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .preprocess_field import BOTTLE_PAD_FRAC, BOTTLE_WEIGHTS, enhance

PHOTOS_DIR = Path("~/Downloads/real_photos").expanduser()
LABELS = "field_labels_own.csv"
STOCK_WEIGHTS = Path("~/Downloads/refined-7db54690044e/yolo.onnx").expanduser()
BACKBONE_NAME = "siglip2_so400m512"
CONF_THRESHOLD = 0.5
LARGEST_MIN_CONF = 0.25
BATCH_SIZE = 2


def bottle_boxes(model, image: Image.Image) -> list[tuple[float, list[float]]]:
    result = model.predict(source=image, verbose=False, device="cpu")[0]
    boxes = []
    for box in result.boxes:
        if model.names[int(box.cls[0])] != "bottle":
            continue
        boxes.append((float(box.conf[0]), box.xyxy[0].tolist()))
    return boxes


def crop(image: Image.Image, xyxy: list[float]) -> Image.Image:
    x0, y0, x1, y1 = xyxy
    width, height = image.size
    pad_x, pad_y = (x1 - x0) * BOTTLE_PAD_FRAC, (y1 - y0) * BOTTLE_PAD_FRAC
    return image.crop((max(0, int(x0 - pad_x)), max(0, int(y0 - pad_y)),
                       min(width, int(x1 + pad_x)), min(height, int(y1 + pad_y))))


def area(xyxy: list[float]) -> float:
    return (xyxy[2] - xyxy[0]) * (xyxy[3] - xyxy[1])


def main() -> None:
    from ultralytics import YOLO

    with open(config.OUTPUTS_DIR / LABELS, encoding="utf-8") as f:
        labels = [row for row in csv.DictReader(f) if row.get("true_slug")]

    ours = YOLO(str(BOTTLE_WEIGHTS))
    stock = YOLO(str(STOCK_WEIGHTS), task="detect")
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE_NAME}.npz")
    backbone = load_backbone(BACKBONE_NAME)

    names = ["whole", "ours_conf", "stock_conf", "stock_largest"]
    rows = []
    detect_ms = {"ours": [], "stock": []}

    for i, label in enumerate(labels, start=1):
        path = PHOTOS_DIR / label["image_path"]
        if not path.exists():
            continue
        with Image.open(path) as raw:
            whole = ImageOps.exif_transpose(raw).convert("RGB")

        started = time.perf_counter()
        ours_boxes = bottle_boxes(ours, whole)
        detect_ms["ours"].append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        stock_boxes = bottle_boxes(stock, whole)
        detect_ms["stock"].append((time.perf_counter() - started) * 1000)

        def best_conf(boxes):
            eligible = [b for b in boxes if b[0] >= CONF_THRESHOLD]
            return max(eligible, key=lambda b: b[0])[1] if eligible else None

        def largest(boxes):
            eligible = [b for b in boxes if b[0] >= LARGEST_MIN_CONF]
            return max(eligible, key=lambda b: area(b[1]))[1] if eligible else None

        chosen = {"ours_conf": best_conf(ours_boxes), "stock_conf": best_conf(stock_boxes),
                  "stock_largest": largest(stock_boxes)}
        views = [enhance(whole)] + [
            enhance(crop(whole, box)) if box is not None else enhance(whole)
            for box in (chosen["ours_conf"], chosen["stock_conf"], chosen["stock_largest"])
        ]

        embeddings = []
        for offset in range(0, len(views), BATCH_SIZE):
            embeddings.extend(backbone.encode_batch(views[offset:offset + BATCH_SIZE]))

        rows.append({
            "image": label["image_path"], "true_slug": label["true_slug"],
            "ours_n_boxes": len(ours_boxes), "stock_n_boxes": len(stock_boxes),
            "ours_has_box": chosen["ours_conf"] is not None,
            "stock_has_box": chosen["stock_conf"] is not None,
            "predictions": {name: [{"slug": r.slug, "score": r.score}
                                   for r in index.search(emb, top_k=5)]
                            for name, emb in zip(names, embeddings)},
        })
        if i % 20 == 0:
            print(f"  {i}/{len(labels)}", flush=True)

    n = len(rows)
    print(f"\n########## Детекторы на {n} полевых кадрах ##########")
    print(f"  наш YOLO11m:  уверенный бокс на {sum(r['ours_has_box'] for r in rows)}/{n}"
          f" ({sum(r['ours_has_box'] for r in rows)/n:.1%}), "
          f"боксов в среднем {np.mean([r['ours_n_boxes'] for r in rows]):.1f}, "
          f"{np.median(detect_ms['ours']):.0f} мс/кадр")
    print(f"  сток COCO 11n: уверенный бокс на {sum(r['stock_has_box'] for r in rows)}/{n}"
          f" ({sum(r['stock_has_box'] for r in rows)/n:.1%}), "
          f"боксов в среднем {np.mean([r['stock_n_boxes'] for r in rows]):.1f}, "
          f"{np.median(detect_ms['stock']):.0f} мс/кадр")

    print(f"\n  {'вариант':16s} {'top-1':>15s} {'top-5':>8s}   испр/слом к whole")
    for name in names:
        hits1 = sum(r["predictions"][name][0]["slug"] == r["true_slug"] for r in rows)
        hits5 = sum(any(c["slug"] == r["true_slug"] for c in r["predictions"][name]) for r in rows)
        fixed = sum(r["predictions"][name][0]["slug"] == r["true_slug"]
                    and r["predictions"]["whole"][0]["slug"] != r["true_slug"] for r in rows)
        broken = sum(r["predictions"][name][0]["slug"] != r["true_slug"]
                     and r["predictions"]["whole"][0]["slug"] == r["true_slug"] for r in rows)
        tail = "—" if name == "whole" else f"{fixed}/{broken}"
        print(f"  {name:16s} {hits1/n:>7.1%} ({hits1:>3d}/{n}) {hits5/n:>7.1%}   {tail}")

    out_path = config.OUTPUTS_DIR / "compare_detectors_experiment.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": n, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
