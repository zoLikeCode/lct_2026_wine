"""Готовит список вин для съёмки в магазине.

Смысл: каждое снятое фото стоит времени, поэтому снимать надо не случайные
бутылки, а те, что дают максимум информации о качестве пайплайна:

  1. near-duplicate позиции — на них система ошибается чаще всего;
  2. вина крупных виноделен — их реально встретить на полке;
  3. позиции БЕЗ эталонного фото — их фото закрывают дыру в покрытии,
     которая сейчас является жёстким потолком accuracy.

Запуск:
    python -m data_prep.shooting_list
"""

import csv
import json
from collections import defaultdict

from . import config

TOP_WINERIES = 14
PER_WINERY = 6


def main() -> None:
    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    with open(config.OUTPUTS_DIR / "near_duplicates_report.json", encoding="utf-8") as f:
        report = json.load(f)

    near_dup = set()
    for pair in report["near_identical_pairs"]:
        near_dup.add(pair["slug_a"])
        near_dup.add(pair["slug_b"])

    indexed = [r for r in records if r["photo_file"]]
    missing = [r for r in records if not r["photo_file"]]

    by_winery = defaultdict(list)
    for r in indexed:
        by_winery[r["winery"]].append(r)
    big_wineries = [w for w, items in
                    sorted(by_winery.items(), key=lambda x: -len(x[1]))[:TOP_WINERIES]]

    rows = []

    # 1. near-duplicates у крупных виноделен — самые информативные кадры
    for winery in big_wineries:
        for r in by_winery[winery]:
            if r["slug"] in near_dup:
                rows.append(("1-near-dup", r))

    # 2. обычные позиции крупных виноделен — база для оценки общей accuracy
    for winery in big_wineries:
        regular = [r for r in by_winery[winery] if r["slug"] not in near_dup]
        for r in regular[:PER_WINERY]:
            rows.append(("2-обычное", r))

    # 3. позиции без фото — закрывают потолок покрытия
    for r in missing:
        if r["winery"] in big_wineries:
            rows.append(("3-НЕТ ФОТО", r))

    seen = set()
    unique_rows = []
    for priority, r in rows:
        if r["slug"] in seen:
            continue
        seen.add(r["slug"])
        unique_rows.append((priority, r))

    out_path = config.OUTPUTS_DIR / "shooting_list.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["приоритет", "винодельня", "название", "категория", "сорт", "slug"])
        for priority, r in unique_rows:
            w.writerow([priority, r["winery"], r["name"], r["category_color"],
                        r["grape"], r["slug"]])

    counts = defaultdict(int)
    for priority, _ in unique_rows:
        counts[priority] += 1
    print(f"Список на съёмку: {len(unique_rows)} позиций -> {out_path}")
    for k in sorted(counts):
        print(f"  {k:12s} {counts[k]}")


if __name__ == "__main__":
    main()
