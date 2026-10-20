"""Оракул по найденным боксам: разделяет две гипотезы о том, почему кроп вредит.

Противоречие, которое надо разрешить (см. §11.23 против §11.16/§11.25/§11.26):
ручная область этикетки у коллеги даёт +15.4пп, а все наши автоматические
кропы дают минус. Возможны две причины, и лечатся они противоположно:

- **сломана селекция** — боксы нормальные, но выбирается не та бутылка;
  тогда надо обучать селектор целевой бутылки;
- **вреден сам кроп** — любая обрезка выбрасывает контекст, которым
  пользуется эмбеддер; тогда направление закрывается совсем.

Разделяются они без разметки: кропаем по КАЖДОМУ найденному боксу и берём
лучший исход. Если «лучший из найденных» заметно обгоняет целый кадр —
боксы хорошие, виновата селекция. Если нет — виноват кроп.

Оракул недостижим в production (он подглядывает в ответ), это верхняя
граница, а не метрика.

    python -m retrieval.box_oracle_experiment
"""

import csv
import json
from pathlib import Path

from PIL import Image, ImageOps

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .predict import BACKBONE, PREPROCESS
from .preprocess_field import BOTTLE_PAD_FRAC, BOTTLE_WEIGHTS, enhance, preprocess

SETS = (("205 фото команды", "~/Downloads/real_photos", "field_labels_own.csv"),
        ("56 фото организатора", "~/Downloads/Реальные фото", "field_labels.csv"))
DETECTOR_MIN_CONF = 0.15
MAX_BOXES = 4
BATCH_SIZE = 3


def crop(image: Image.Image, xyxy: list[float]) -> Image.Image:
    x0, y0, x1, y1 = xyxy
    width, height = image.size
    pad_x, pad_y = (x1 - x0) * BOTTLE_PAD_FRAC, (y1 - y0) * BOTTLE_PAD_FRAC
    return image.crop((max(0, int(x0 - pad_x)), max(0, int(y0 - pad_y)),
                       min(width, int(x1 + pad_x)), min(height, int(y1 + pad_y))))


def main() -> None:
    from ultralytics import YOLO

    detector = YOLO(str(BOTTLE_WEIGHTS))
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}.npz")
    backbone = load_backbone(BACKBONE)

    totals = {"whole": 0, "best_box": 0, "conf_box": 0, "n": 0, "with_boxes": 0}
    per_set = {}

    for title, photos_dir, labels_name in SETS:
        root = Path(photos_dir).expanduser()
        with open(config.OUTPUTS_DIR / labels_name, encoding="utf-8") as f:
            labels = [row for row in csv.DictReader(f) if row.get("true_slug")]

        stats = {"whole": 0, "best_box": 0, "conf_box": 0, "n": 0, "with_boxes": 0}
        for i, label in enumerate(labels, start=1):
            path = root / label["image_path"]
            if not path.exists():
                continue
            true_slug = label["true_slug"]
            stats["n"] += 1

            with Image.open(path) as raw:
                oriented = ImageOps.exif_transpose(raw).convert("RGB")
                whole_view = preprocess(raw, PREPROCESS)

            result = detector.predict(source=oriented, verbose=False, device="cpu")[0]
            boxes = [(float(b.conf[0]), b.xyxy[0].tolist()) for b in result.boxes
                     if detector.names[int(b.cls[0])] == "bottle"
                     and float(b.conf[0]) >= DETECTOR_MIN_CONF]
            boxes.sort(key=lambda b: -b[0])
            boxes = boxes[:MAX_BOXES]

            views = [whole_view] + [enhance(crop(oriented, box)) for _, box in boxes]
            embeddings = []
            for offset in range(0, len(views), BATCH_SIZE):
                embeddings.extend(backbone.encode_batch(views[offset:offset + BATCH_SIZE]))

            tops = [index.search(emb, top_k=1)[0].slug for emb in embeddings]
            stats["whole"] += tops[0] == true_slug
            if boxes:
                stats["with_boxes"] += 1
                stats["best_box"] += any(slug == true_slug for slug in tops[1:])
                stats["conf_box"] += tops[1] == true_slug
            else:
                stats["best_box"] += tops[0] == true_slug
                stats["conf_box"] += tops[0] == true_slug

            if i % 40 == 0:
                print(f"  {title}: {i}/{len(labels)}", flush=True)

        per_set[title] = stats
        for key in totals:
            totals[key] += stats[key]

    print(f"\n{'набор':24s} {'n':>4s} {'целый кадр':>12s} {'лучший бокс':>13s} {'бокс по conf':>14s}")
    for title, s in list(per_set.items()) + [("ВСЕГО", totals)]:
        n = s["n"]
        print(f"{title:24s} {n:>4d} {s['whole'] / n:>11.1%} {s['best_box'] / n:>12.1%} "
              f"{s['conf_box'] / n:>13.1%}")

    n = totals["n"]
    gain_selection = (totals["best_box"] - totals["conf_box"]) / n
    gain_vs_whole = (totals["best_box"] - totals["whole"]) / n
    print(f"\nКадров хотя бы с одним боксом: {totals['with_boxes']}/{n}")
    print(f"Потолок ИДЕАЛЬНОЙ селекции среди найденных боксов: {gain_vs_whole * 100:+.1f}пп к целому кадру")
    print(f"Сколько теряет текущая селекция по уверенности:      {gain_selection * 100:+.1f}пп")
    print("\nЕсли первое число заметно положительное — боксы хорошие, надо учить")
    print("селектор целевой бутылки. Если около нуля или отрицательное — вреден")
    print("сам кроп, и направление локализации закрывается.")

    out = config.OUTPUTS_DIR / "box_oracle_experiment.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"per_set": per_set, "totals": totals}, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out}")


if __name__ == "__main__":
    main()
