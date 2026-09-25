"""Оценка на размеченном наборе реальных фотографий.

Считает ровно то, что считает кейсодержатель: долю точных совпадений slug,
плюс балл `accuracy * 0.5`. Дополнительно разбирает, где именно теряется
точность — чтобы отличать «вина нет в индексе» от «вино есть, но ранжировано
не первым», потому что лечится это разными вещами.

Вход — CSV от `data_prep.label_field_photos`: query_id, image_path, true_slug.

Запуск:
    python -m retrieval.evaluate_field --photos ~/Downloads/field_photos \\
        --labels outputs/field_labels.csv
"""

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

from PIL import Image

from data_prep import config
from .predict import DIFFERENTIATOR_WEIGHT, ORB_WEIGHT, WineFinder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--photos", required=True, help="папка с фотографиями")
    parser.add_argument("--labels", default=None, help="CSV с разметкой")
    parser.add_argument("--orb-weight", type=float, default=ORB_WEIGHT)
    parser.add_argument("--differentiator-weight", type=float, default=DIFFERENTIATOR_WEIGHT)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    photos_dir = Path(args.photos).expanduser()
    labels_path = Path(args.labels).expanduser() if args.labels else config.OUTPUTS_DIR / "field_labels.csv"
    if not labels_path.exists():
        raise SystemExit(f"нет разметки: {labels_path} (сначала data_prep.label_field_photos)")

    with open(labels_path, encoding="utf-8") as f:
        labels = [row for row in csv.DictReader(f) if row.get("true_slug")]
    if not labels:
        raise SystemExit("в разметке нет строк с true_slug")

    finder = WineFinder(orb_weight=args.orb_weight, differentiator_weight=args.differentiator_weight)
    indexed = set(finder.lookup)

    rows = []
    latencies = []
    for row in labels:
        path = photos_dir / row["image_path"]
        if not path.exists():
            print(f"  пропуск, нет файла: {row['image_path']}")
            continue
        with Image.open(path) as img:
            t0 = time.perf_counter()
            results = finder.predict(img.convert("RGB"), top_k=args.top_k)
            latencies.append((time.perf_counter() - t0) * 1000)

        true_slug = row["true_slug"]
        top_slugs = [p.slug for p in results]
        rows.append({
            "image_path": row["image_path"],
            "true_slug": true_slug,
            "predicted": top_slugs[0] if top_slugs else None,
            "correct": bool(top_slugs) and top_slugs[0] == true_slug,
            "rank": top_slugs.index(true_slug) + 1 if true_slug in top_slugs else None,
            "in_index": true_slug in indexed,
            "gap": (results[0].score - results[1].score) if len(results) > 1 else 0.0,
            "orb_top1": results[0].orb_score if results else 0.0,
        })

    n = len(rows)
    correct = sum(r["correct"] for r in rows)
    in_topk = sum(r["rank"] is not None for r in rows)
    not_indexed = sum(not r["in_index"] for r in rows)
    accuracy = correct / n if n else 0.0

    print(f"\n########## Реальные фото: {n} шт ##########")
    print(f"  ТОЧНОСТЬ (top-1):        {accuracy:.1%}  ->  {accuracy * 50:.1f} из 50 баллов")
    print(f"  верное вино в top-{args.top_k}:      {in_topk / n:.1%}" if n else "")
    print(f"  вина нет в индексе:      {not_indexed} ({not_indexed / n:.1%})" if n else "")

    fixable = in_topk - correct
    print(f"\nРазбор потерь ({n - correct} ошибок):")
    print(f"  вино вне индекса, найти невозможно:  {not_indexed}")
    print(f"  вино в top-{args.top_k}, но не первое (реранкинг): {fixable}")
    print(f"  вино не попало даже в top-{args.top_k} (retrieval):  {n - in_topk - not_indexed}")

    orb_nonzero = sum(r["orb_top1"] > 0.01 for r in rows)
    print(f"\nORB дал ненулевой вклад в {orb_nonzero} из {n} запросов "
          f"({orb_nonzero / n:.0%})" if n else "")

    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]
        print(f"Латентность: медиана {statistics.median(latencies):.0f} мс, p95 {p95:.0f} мс")

    errors = [r for r in rows if not r["correct"] and r["in_index"]]
    if errors:
        print(f"\nОшибки на винах, которые ЕСТЬ в индексе ({len(errors)}):")
        for r in errors[:15]:
            rank = f"верное на {r['rank']} месте" if r["rank"] else f"нет в top-{args.top_k}"
            print(f"  {r['image_path'][:28]:30s} {rank}")
            print(f"      ожидали: {r['true_slug'][:58]}")
            print(f"      выдали:  {r['predicted'][:58] if r['predicted'] else '-'}")

    out_path = config.OUTPUTS_DIR / "field_evaluation.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": n, "accuracy": accuracy, "points": accuracy * 50,
                   "in_topk": in_topk, "not_indexed": not_indexed,
                   "orb_weight": args.orb_weight, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
