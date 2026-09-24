"""Строит синтетический датасет в формате YOLO для дообучения детектора
этикетки на условиях, близких к реальным полевым фото — не на студийных
Roboflow-снимках (§11.16 findings: обученный на Roboflow YOLO11m проверен на
наших 56 честных полевых фото и дал -12.5пп к retrieval).

Bbox этикетки вычисляется автоматически: `augment_realistic.
simulate_realistic_photo_with_mask` проводит бинарную маску этикетки через
ту же цепочку геометрических преобразований, что и само изображение (тот же
random seed), поэтому bbox честный, без единой секунды ручной разметки —
см. `mask_track_check.png` в scratchpad для визуальной проверки.

Запуск:
    python -m retrieval.build_synthetic_yolo_dataset --n-per-wine 3 --limit 400
"""

import argparse
import json
import random
from pathlib import Path

from PIL import Image

from data_prep import config
from .augment_realistic import mask_to_bbox, simulate_realistic_photo_with_mask

OUT_DIR = config.OUTPUTS_DIR / "yolo_synthetic"


def write_yolo_label(path: Path, bbox: tuple[int, int, int, int], img_size: tuple[int, int]) -> None:
    x0, y0, x1, y1 = bbox
    w, h = img_size
    xc, yc = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
    bw, bh = (x1 - x0) / w, (y1 - y0) / h
    path.write_text(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-per-wine", type=int, default=3, help="сколько аугментаций на каждое каталожное фото")
    parser.add_argument("--limit", type=int, default=None, help="ограничить число каталожных вин (для быстрой проверки)")
    parser.add_argument("--val-frac", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    indexed = [r for r in records if r["photo_file"]]
    if args.limit:
        indexed = random.sample(indexed, min(args.limit, len(indexed)))
    print(f"Каталожных вин: {len(indexed)}, аугментаций на вино: {args.n_per_wine}")

    for split in ("train", "val"):
        (OUT_DIR / split / "images").mkdir(parents=True, exist_ok=True)
        (OUT_DIR / split / "labels").mkdir(parents=True, exist_ok=True)

    n_val_wines = max(1, int(len(indexed) * args.val_frac))
    val_slugs = set(r["slug"] for r in random.sample(indexed, n_val_wines))

    written = skipped = 0
    for i, r in enumerate(indexed):
        split = "val" if r["slug"] in val_slugs else "train"
        try:
            with Image.open(r["photo_file"]) as img:
                img = img.convert("RGB")
                for aug_i in range(args.n_per_wine):
                    seed = hash((r["slug"], aug_i)) % (2**31)
                    out, mask = simulate_realistic_photo_with_mask(img, seed=seed)
                    bbox = mask_to_bbox(mask)
                    if bbox is None:
                        skipped += 1
                        continue
                    stem = f"{r['slug'][:60]}_{aug_i}"
                    img_path = OUT_DIR / split / "images" / f"{stem}.jpg"
                    label_path = OUT_DIR / split / "labels" / f"{stem}.txt"
                    out.save(img_path, quality=90)
                    write_yolo_label(label_path, bbox, out.size)
                    written += 1
        except Exception as e:
            print(f"  пропуск {r['slug']}: {e}")
            continue

        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(indexed)}  (написано {written})", flush=True)

    data_yaml = OUT_DIR / "data.yaml"
    data_yaml.write_text(
        f"path: {OUT_DIR}\n"
        f"train: train/images\n"
        f"val: val/images\n"
        f"names:\n  0: label\n"
    )

    print(f"\nЗаписано изображений: {written}, пропущено (этикетка ушла за кадр): {skipped}")
    print(f"Датасет: {OUT_DIR}")
    print(f"data.yaml: {data_yaml}")


if __name__ == "__main__":
    main()
