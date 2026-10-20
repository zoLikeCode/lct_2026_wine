"""Разметка снятых в магазине фото: фото -> slug каталога.

Ищет по каталогу подстрокой (название/винодельня/сорт), показывает
пронумерованные варианты, записывает выбор в CSV. Формат совпадает с
манифестом организатора (`query_id<TAB>image_path`) плюс колонка истинного
slug, чтобы на этом наборе можно было считать accuracy напрямую.

Запуск:
    python -m data_prep.label_field_photos --photos ~/Downloads/field_photos
"""

import argparse
import csv
import json
from pathlib import Path

from rapidfuzz import fuzz

from . import config

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


def search(records: list[dict], query: str, limit: int = 12) -> list[dict]:
    q = query.lower().strip()
    scored = []
    for r in records:
        haystack = f"{r['winery']} {r['name']} {r['grape']}".lower()
        score = 100 if q in haystack else fuzz.token_set_ratio(q, haystack)
        if score >= 60:
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["slug"]))
    return [r for _, r in scored[:limit]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--photos", required=True, help="папка со снятыми фото")
    parser.add_argument("--out", default=None, help="куда писать разметку (CSV)")
    args = parser.parse_args()

    photos_dir = Path(args.photos).expanduser()
    out_path = Path(args.out).expanduser() if args.out else config.OUTPUTS_DIR / "field_labels.csv"

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)

    done = {}
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                done[row["image_path"]] = row["true_slug"]
        print(f"Уже размечено: {len(done)}")

    images = sorted(p for p in photos_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    todo = [p for p in images if p.name not in done]
    print(f"Всего фото: {len(images)}, осталось разметить: {len(todo)}\n")

    rows = [{"query_id": f"q-{i:06d}", "image_path": name, "true_slug": slug}
            for i, (name, slug) in enumerate(done.items(), start=1)]

    for path in todo:
        print(f"\n=== {path.name} ===")
        print("  (Enter — пропустить, 'q' — закончить)")
        while True:
            query = input("  поиск> ").strip()
            if query.lower() == "q":
                todo = []
                break
            if not query:
                break
            hits = search(records, query)
            if not hits:
                print("  ничего не найдено")
                continue
            for i, r in enumerate(hits, start=1):
                mark = "" if r["photo_file"] else "  [НЕТ ФОТО в каталоге]"
                print(f"   {i:2d}. {r['winery']} — {r['name']} ({r['category_color']}){mark}")
                print(f"       {r['slug']}")
            choice = input("  номер (Enter — искать заново)> ").strip()
            if not choice:
                continue
            if choice.isdigit() and 1 <= int(choice) <= len(hits):
                chosen = hits[int(choice) - 1]
                rows.append({"query_id": f"q-{len(rows) + 1:06d}",
                             "image_path": path.name,
                             "true_slug": chosen["slug"]})
                print(f"  -> {chosen['slug']}")
                break
        if not todo:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["query_id", "image_path", "true_slug"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nСохранено {len(rows)} меток -> {out_path}")


if __name__ == "__main__":
    main()
