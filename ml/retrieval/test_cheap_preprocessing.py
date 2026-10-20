"""Две дешёвые, необучаемые гипотезы препроцессинга на честном 56-фото
наборе (§11.17/§11.18 findings) — до дорогого fine-tuning:

1. Кроп по БУТЫЛКЕ целиком (не по этикетке — то отклонено в §11.16, выбрасывает
   контекст формы/стекла). Детекция `bottle` у обученного YOLO11 надёжна почти
   всегда (только 3.6% фото без бокса, см. §11.16), в отличие от `label`
   (43% ненадёжных). Кроп по бутылке с большим отступом убирает с кадра
   соседние бутылки на полке — ровно то, что путает OCR/ORB (§11.18) — но
   сохраняет форму, цвет стекла и почти всю этикетку.
2. `preprocess_field.enhance()` — баланс белого + CLAHE-контраст, без обрезки.

Запуск:
    python -m retrieval.test_cheap_preprocessing --backbone siglip2_so400m512
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
from .preprocess_field import enhance

WEIGHTS = Path(__file__).resolve().parent.parent / "roboflow" / "wine_label_yolo11m_best.pt"
PHOTOS_DIR = Path("~/Downloads/Реальные фото").expanduser()
BOTTLE_CONF_THRESHOLD = 0.5
PAD_FRAC = 0.15  # заметно больше, чем у label-кропа (0.05) — цель не тесная рамка


def crop_to_bottle(model: YOLO, img: Image.Image, image_path: Path) -> Image.Image | None:
    result = model.predict(source=str(image_path), verbose=False, device="cpu")[0]
    best = None
    for box in result.boxes:
        if model.names[int(box.cls[0])] != "bottle":
            continue
        conf = float(box.conf[0])
        if conf < BOTTLE_CONF_THRESHOLD:
            continue
        if best is None or conf > best[0]:
            best = (conf, box.xyxy[0].tolist())
    if best is None:
        return None

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

    variants = ["whole", "bottle_crop", "enhanced", "bottle_crop_enhanced"]
    hits1 = {v: 0 for v in variants}
    hits5 = {v: 0 for v in variants}
    n_cropped = 0
    n = 0
    rows_out = []

    for i, row in enumerate(rows, start=1):
        path = PHOTOS_DIR / row["image_path"]
        if not path.exists():
            continue
        true_slug = row["true_slug"]
        n += 1

        with Image.open(path) as raw:
            whole = ImageOps.exif_transpose(raw).convert("RGB")

        crop = crop_to_bottle(model, whole, path)
        if crop is not None:
            n_cropped += 1

        images = {
            "whole": whole,
            "bottle_crop": crop,
            "enhanced": enhance(whole),
            "bottle_crop_enhanced": enhance(crop) if crop is not None else None,
        }

        row_result = {"image": row["image_path"], "true_slug": true_slug, "cropped": crop is not None}
        for v, img in images.items():
            if img is None:
                continue
            emb = backbone.encode(img)
            top = index.search(emb, top_k=5)
            slugs = [r.slug for r in top]
            h1 = bool(slugs) and slugs[0] == true_slug
            h5 = true_slug in slugs
            hits1[v] += h1
            hits5[v] += h5
            row_result[f"{v}_top1"] = slugs[0] if slugs else None
            row_result[f"{v}_hit1"] = h1

        rows_out.append(row_result)

        if is_mps and i % 10 == 0:
            torch.mps.empty_cache()
            gc.collect()
        if i % 10 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    print(f"\n########## Дешёвый препроцессинг: {n} фото, {args.backbone} ##########")
    print(f"  фото с надёжным bottle-кропом (conf>={BOTTLE_CONF_THRESHOLD}): {n_cropped} ({n_cropped/n:.1%})\n")
    for v in variants:
        denom = n_cropped if "crop" in v else n
        if denom == 0:
            continue
        print(f"  {v:22s} top1={hits1[v]/denom:.1%}  top5={hits5[v]/denom:.1%}  (n={denom})")

    out_path = config.OUTPUTS_DIR / f"cheap_preprocessing_{args.backbone}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": n, "n_cropped": n_cropped,
                   "hits1": hits1, "hits5": hits5, "rows": rows_out}, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
