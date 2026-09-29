"""Проверяет не просто "находит ли YOLO этикетку", а даёт ли кроп по ней
реальный прирост retrieval-точности на наших 56 честно размеченных полевых
фото — тест самого детектора (test_yolo_detector.py) отвечает только на
первый вопрос, этот — на главный.

Для каждого фото: если YOLO нашёл `label`-бокс с уверенностью >= порога,
эмбеддим и полное фото, и кроп; сравниваем top-1 по всему индексу.

Запуск:
    python -m retrieval.test_yolo_crop_retrieval --backbone siglip2_so400m512
"""

import argparse
import csv
import gc
import json
from pathlib import Path

import torch
from PIL import Image, ImageOps
from ultralytics import YOLO

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex

WEIGHTS = Path(__file__).resolve().parent.parent / "roboflow" / "wine_label_yolo11m_best.pt"
PHOTOS_DIR = Path("~/Downloads/Реальные фото").expanduser()
LABEL_CONF_THRESHOLD = 0.5
PAD_FRAC = 0.05


def crop_to_label(model: YOLO, image_path: Path) -> Image.Image | None:
    result = model.predict(source=str(image_path), verbose=False, device="cpu")[0]
    best = None
    for box in result.boxes:
        if model.names[int(box.cls[0])] != "label":
            continue
        conf = float(box.conf[0])
        if conf < LABEL_CONF_THRESHOLD:
            continue
        if best is None or conf > best[0]:
            best = (conf, box.xyxy[0].tolist())
    if best is None:
        return None

    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    x0, y0, x1, y1 = best[1]
    w, h = img.size
    pad_x, pad_y = (x1 - x0) * PAD_FRAC, (y1 - y0) * PAD_FRAC
    x0, y0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
    x1, y1 = min(w, x1 + pad_x), min(h, y1 + pad_y)
    return img.crop((int(x0), int(y0), int(x1), int(y1)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2_so400m512")
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / "field_labels.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    model = YOLO(str(WEIGHTS))
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    backbone = load_backbone(args.backbone)
    is_mps = backbone.device.type == "mps"

    n_cropped = 0
    whole_hits1 = whole_hits5 = crop_hits1 = crop_hits5 = 0
    subset_whole_hits1 = 0  # baseline ТОЛЬКО на той же подвыборке, где кроп нашёлся
    results = []

    for i, row in enumerate(rows, start=1):
        path = PHOTOS_DIR / row["image_path"]
        if not path.exists():
            continue
        true_slug = row["true_slug"]

        with Image.open(path) as raw:
            whole_img = ImageOps.exif_transpose(raw).convert("RGB")
            emb_whole = backbone.encode(whole_img)
        top_whole = index.search(emb_whole, top_k=5)
        slugs_whole = [r.slug for r in top_whole]
        whole_hits1 += slugs_whole[0] == true_slug if slugs_whole else 0
        whole_hits5 += true_slug in slugs_whole

        crop_img = crop_to_label(model, path)
        crop_result = {"image": row["image_path"], "true_slug": true_slug,
                        "whole_top1": slugs_whole[0] if slugs_whole else None,
                        "cropped": crop_img is not None}

        if crop_img is not None:
            n_cropped += 1
            emb_crop = backbone.encode(crop_img)
            top_crop = index.search(emb_crop, top_k=5)
            slugs_crop = [r.slug for r in top_crop]
            crop_hits1 += slugs_crop[0] == true_slug if slugs_crop else 0
            crop_hits5 += true_slug in slugs_crop
            subset_whole_hits1 += slugs_whole[0] == true_slug if slugs_whole else 0
            crop_result["crop_top1"] = slugs_crop[0] if slugs_crop else None

        results.append(crop_result)

        if is_mps and i % 10 == 0:
            torch.mps.empty_cache()
            gc.collect()
        if i % 10 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    n = len(rows)
    print(f"\n########## YOLO-кроп vs целое фото: {n} запросов, {args.backbone} ##########")
    print(f"  фото с надёжным кропом этикетки (conf>={LABEL_CONF_THRESHOLD}): {n_cropped} ({n_cropped/n:.1%})")
    print(f"\n  ЦЕЛОЕ ФОТО, все {n}:            top1={whole_hits1/n:.1%}  top5={whole_hits5/n:.1%}")
    if n_cropped:
        print(f"  ЦЕЛОЕ ФОТО, только подмножество с кропом (n={n_cropped}): top1={subset_whole_hits1/n_cropped:.1%}")
        print(f"  КРОП ПО ЭТИКЕТКЕ, то же подмножество:     top1={crop_hits1/n_cropped:.1%}  top5={crop_hits5/n_cropped:.1%}")
        print(f"  дельта top1 (кроп - целое, на этом подмножестве): {(crop_hits1-subset_whole_hits1)/n_cropped:+.1%}")

    out = {"backbone": args.backbone, "n": n, "n_cropped": n_cropped,
           "whole_top1": whole_hits1/n, "whole_top5": whole_hits5/n,
           "subset_whole_top1": subset_whole_hits1/n_cropped if n_cropped else None,
           "crop_top1": crop_hits1/n_cropped if n_cropped else None,
           "crop_top5": crop_hits5/n_cropped if n_cropped else None,
           "results": results}
    out_path = config.OUTPUTS_DIR / f"yolo_crop_retrieval_{args.backbone}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
