"""Сравнение обычного индекса (один эталон на вино) с центроидным
(усреднение нескольких эталонов) на честных полевых кадрах.

См. `build_centroid_index.py`, почему это не повтор отклонённого
мультиреференса из §11.12.

    python -m retrieval.eval_centroid_index
"""

import csv
import json
import time
from pathlib import Path

from PIL import Image

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .predict import BACKBONE, PREPROCESS
from .preprocess_field import preprocess

SETS = (("205 фото команды", "~/Downloads/real_photos", "field_labels_own.csv"),
        ("56 фото организатора", "~/Downloads/Реальные фото", "field_labels.csv"))


def main() -> None:
    base_index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}.npz")
    centroid_index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}_centroid.npz")
    backbone = load_backbone(BACKBONE)

    totals = {"n": 0, "base1": 0, "base5": 0, "cent1": 0, "cent5": 0}
    per_set, changes = {}, []
    started = time.perf_counter()

    for title, photos_dir, labels_name in SETS:
        root = Path(photos_dir).expanduser()
        with open(config.OUTPUTS_DIR / labels_name, encoding="utf-8") as f:
            labels = [row for row in csv.DictReader(f) if row.get("true_slug")]

        stats = {"n": 0, "base1": 0, "base5": 0, "cent1": 0, "cent5": 0}
        for i, label in enumerate(labels, start=1):
            path = root / label["image_path"]
            if not path.exists():
                continue
            true_slug = label["true_slug"]
            with Image.open(path) as raw:
                embedding = backbone.encode(preprocess(raw, PREPROCESS))

            stats["n"] += 1
            base_top = [r.slug for r in base_index.search(embedding, top_k=5)]
            cent_top = [r.slug for r in centroid_index.search(embedding, top_k=5)]
            was, now = base_top[0] == true_slug, cent_top[0] == true_slug
            stats["base1"] += was
            stats["cent1"] += now
            stats["base5"] += true_slug in base_top
            stats["cent5"] += true_slug in cent_top
            if was != now:
                changes.append(("ИСПРАВЛЕНО" if now else "СЛОМАНО", label["image_path"],
                                true_slug, base_top[0], cent_top[0]))

            if i % 25 == 0:
                rate = (time.perf_counter() - started) / max(totals["n"] + stats["n"], 1)
                print(f"  {title}: {i}/{len(labels)}  ({rate:.1f} с/кадр)", flush=True)

        per_set[title] = stats
        for key in totals:
            totals[key] += stats[key]

    print(f"\n{'набор':24s} {'n':>4s} {'обычный top-1':>15s} {'центроид top-1':>16s} "
          f"{'обычный top-5':>15s} {'центроид top-5':>16s}")
    for title, s in list(per_set.items()) + [("ВСЕГО", totals)]:
        n = s["n"]
        print(f"{title:24s} {n:>4d} {s['base1'] / n:>14.1%} {s['cent1'] / n:>15.1%} "
              f"{s['base5'] / n:>14.1%} {s['cent5'] / n:>15.1%}")

    fixed = sum(1 for c in changes if c[0] == "ИСПРАВЛЕНО")
    broken = len(changes) - fixed
    print(f"\nисправлено {fixed}, сломано {broken}, чистый эффект {fixed - broken:+d} кадров")
    print("Напоминание §11.25: меньше 3-4 кадров — не результат.")
    for kind, image, true_slug, was, now in changes[:12]:
        print(f"  [{kind}] {image[:40]}")
        print(f"      верный: {true_slug[:52]}")
        print(f"      было:   {was[:52]}")
        print(f"      стало:  {now[:52]}")

    out = config.OUTPUTS_DIR / "centroid_index_eval.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"per_set": per_set, "totals": totals, "changes": changes},
                  f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out}")


if __name__ == "__main__":
    main()
