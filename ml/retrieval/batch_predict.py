"""Пакетный прогон пайплайна по папке с фотографиями.

Нужен, чтобы получить предсказания на наборе без готовой разметки: сохраняет
top-K кандидатов, уверенность и вклад ORB по каждому кадру. Дальше результат
либо размечается вручную (`data_prep.label_field_photos`), либо просматривается
глазами для оценки качества.

Запуск:
    python -m retrieval.batch_predict --photos ~/Downloads/Реальные\\ фото
"""

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

from PIL import Image, ImageOps

from data_prep import config
from .predict import WineFinder

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_SIDE = 1280


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--photos", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    photos_dir = Path(args.photos).expanduser()
    images = sorted(p for p in photos_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    print(f"Фотографий: {len(images)}")

    finder = WineFinder()
    rows = []
    latencies = []

    for i, path in enumerate(images, start=1):
        with Image.open(path) as raw:
            img = ImageOps.exif_transpose(raw).convert("RGB")
            if max(img.size) > MAX_SIDE:
                img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            t0 = time.perf_counter()
            results = finder.predict(img, top_k=args.top_k)
            latencies.append((time.perf_counter() - t0) * 1000)

        best = results[0]
        gap = best.score - results[1].score if len(results) > 1 else 0.0
        rows.append({
            "image": path.name,
            "top1_slug": best.slug,
            "top1_name": best.record["name"],
            "top1_winery": best.record["winery"],
            "score": round(best.score, 4),
            "gap": round(gap, 4),
            "orb": round(best.orb_score, 4),
            "candidates": [
                {"slug": p.slug, "name": p.record["name"], "winery": p.record["winery"],
                 "score": round(p.score, 4), "orb": round(p.orb_score, 4)}
                for p in results
            ],
        })
        if i % 20 == 0:
            print(f"  {i}/{len(images)}", flush=True)

    out_json = Path(args.out) if args.out else config.OUTPUTS_DIR / "batch_predictions.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    out_csv = out_json.with_suffix(".csv")
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image", "top1_name", "top1_winery", "top1_slug", "score", "gap", "orb"])
        for r in rows:
            w.writerow([r["image"], r["top1_name"], r["top1_winery"], r["top1_slug"],
                        r["score"], r["gap"], r["orb"]])

    orb_used = sum(1 for r in rows if r["orb"] > 0.01)
    print(f"\nORB дал ненулевой вклад: {orb_used} из {len(rows)} ({orb_used / len(rows):.0%})")
    print(f"Скор top-1:  медиана {statistics.median(r['score'] for r in rows):.3f}, "
          f"мин {min(r['score'] for r in rows):.3f}, макс {max(r['score'] for r in rows):.3f}")
    print(f"Отрыв top1-top2: медиана {statistics.median(r['gap'] for r in rows):.4f}")
    print(f"Латентность: медиана {statistics.median(latencies):.0f} мс")
    print(f"\nСохранено: {out_json}\n           {out_csv}")


if __name__ == "__main__":
    main()
