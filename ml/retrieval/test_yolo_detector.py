"""Честная проверка YOLO11-детектора коллеги (roboflow/wine_label_yolo11m_best.pt)
на наших реально размеченных полевых фото — а не на test-сплите Roboflow.

STATUS.md прямо предупреждал: метрики Roboflow (mAP50~0.995) подозрительно
идеальны для 353 фото, и нужна проверка на "боевых" фото пользователей
перед тем, как полагаться на них. Это она.

Классы модели: bottle, label. Для retrieval нужен именно `label`.

Запуск:
    python -m retrieval.test_yolo_detector
"""

import csv
from pathlib import Path

from PIL import Image
from ultralytics import YOLO

from data_prep import config

WEIGHTS = Path(__file__).resolve().parent.parent / "roboflow" / "wine_label_yolo11m_best.pt"
PHOTOS_DIR = Path("~/Downloads/Реальные фото").expanduser()
LABELS_CSV = config.OUTPUTS_DIR / "field_labels.csv"
PREVIEW_DIR = config.OUTPUTS_DIR / "yolo_preview"


def main() -> None:
    if not WEIGHTS.exists():
        raise SystemExit(f"нет весов: {WEIGHTS}")

    model = YOLO(str(WEIGHTS))
    print("Классы модели:", model.names)
    # CPU намеренно: параллельно может идти MPS-инференс эмбеддера, два
    # процесса на MPS одновременно уже один раз замедляли всё в разы.

    with open(LABELS_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Фото для проверки: {len(rows)}\n")

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    no_label_box = 0
    no_any_box = 0
    label_conf_low = 0
    results_summary = []

    for row in rows:
        path = PHOTOS_DIR / row["image_path"]
        if not path.exists():
            continue
        result = model.predict(source=str(path), verbose=False, device="cpu")[0]

        label_boxes = []
        bottle_boxes = []
        for box in result.boxes:
            cls_name = model.names[int(box.cls[0])]
            conf = float(box.conf[0])
            if cls_name == "label":
                label_boxes.append(conf)
            elif cls_name == "bottle":
                bottle_boxes.append(conf)

        if not label_boxes and not bottle_boxes:
            no_any_box += 1
        elif not label_boxes:
            no_label_box += 1
        else:
            best_label_conf = max(label_boxes)
            if best_label_conf < 0.5:
                label_conf_low += 1

        results_summary.append({
            "image": row["image_path"],
            "n_label_boxes": len(label_boxes),
            "best_label_conf": max(label_boxes) if label_boxes else 0.0,
            "n_bottle_boxes": len(bottle_boxes),
            "best_bottle_conf": max(bottle_boxes) if bottle_boxes else 0.0,
        })

    n = len(results_summary)
    print(f"########## YOLO11m на {n} реальных полевых фото ##########")
    print(f"  фото БЕЗ единого бокса (ни label, ни bottle): {no_any_box} ({no_any_box/n:.1%})")
    print(f"  фото с bottle, но БЕЗ label:                  {no_label_box} ({no_label_box/n:.1%})")
    print(f"  фото с label, но conf < 0.5:                  {label_conf_low} ({label_conf_low/n:.1%})")

    found_label = [r for r in results_summary if r["n_label_boxes"] > 0]
    if found_label:
        avg_conf = sum(r["best_label_conf"] for r in found_label) / len(found_label)
        print(f"\n  среди фото с найденным label: средняя уверенность = {avg_conf:.3f}")

    out_path = config.OUTPUTS_DIR / "yolo_detector_test.csv"
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results_summary[0].keys()))
        w.writeheader()
        w.writerows(results_summary)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
