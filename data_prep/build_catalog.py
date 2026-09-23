"""Главный entry point data-prep пайплайна.

Собирает: CSV каталога -> (маппинг на фото + разбор slug + reranking target)
-> outputs/catalog_resolved.json (готово для построения эмбеддингов/индекса)
       outputs/unresolved.csv    (что не нашлось — приоритет для доразметки/доп. данных)

Запуск:
    python -m data_prep.build_catalog
"""

import csv
import json

from . import config, parser_images
from .rerank_target import build_rerank_target
from .slug_matching import MatchResult, UploadsIndex, match_slug
from .slug_parser import parse_slug


def load_unique_rows() -> list[dict]:
    with open(config.CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    seen = set()
    uniq = []
    for row in rows:
        if row["Slug"] not in seen:
            seen.add(row["Slug"])
            uniq.append(row)
    return uniq


def build_record(row: dict, match: MatchResult) -> dict:
    parsed = parse_slug(row["Slug"])
    target = build_rerank_target(row, parsed)
    return {
        "slug": row["Slug"],
        "name": row.get("Название вина", "").strip(),
        "category_color": row.get("Категория", "").strip(),
        "color_desc": row.get("Цвет", "").strip(),
        "region": row.get("Регион", "").strip(),
        "grape": row.get("Сорт винограда", "").strip(),
        "description": row.get("Описание", "").strip(),
        "winery": row.get("Винодельня", "").strip(),
        "photo_file": match.file,
        "photo_match_method": match.method,
        "photo_match_score": match.score,
        "photo_flagged_no_wine": match.flagged_no_wine,
        "parsed_slug": {
            "year": parsed.year,
            "sweetness": parsed.sweetness,
            "abv_tokens": parsed.abv_tokens,
        },
        "rerank_target": target.to_dict(),
    }


def resolve_photo(slug: str, row: dict, uploads_index: UploadsIndex) -> MatchResult:
    """Сначала — вручную подтверждённый датасет `Downloads/images`
    (организован по slug напрямую, разметка человеком, см.
    data_prep/parser_images.py), затем — старый путь через
    prod-svoe-vino/strapi/uploads (транслитерация/fuzzy)."""
    parser_path = parser_images.best_reference_for(slug)
    if parser_path is not None:
        kind = parser_images.classify(parser_path.name) or "reference"
        return MatchResult(slug, None, str(parser_path), f"parser_{kind}", 100.0)
    return match_slug(slug, uploads_index, photo_name=row.get("Название фото"))


def main() -> None:
    rows = load_unique_rows()
    index = UploadsIndex.build()

    records = []
    for row in rows:
        match = resolve_photo(row["Slug"], row, index)
        records.append(build_record(row, match))

    config.OUTPUTS_DIR.mkdir(exist_ok=True)

    catalog_path = config.OUTPUTS_DIR / "catalog_resolved.json"
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    unresolved = [r for r in records if r["photo_match_method"] == "unresolved"]
    unresolved_path = config.OUTPUTS_DIR / "unresolved.csv"
    with open(unresolved_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slug", "name", "winery", "best_guess_base", "best_guess_score", "flagged_no_wine"])
        for r in unresolved:
            w.writerow([
                r["slug"], r["name"], r["winery"],
                "", r["photo_match_score"], r["photo_flagged_no_wine"],
            ])

    n = len(records)
    print(f"Всего уникальных вин: {n}")
    methods = ("parser_reference", "parser_bottle", "parser_label",
               "photo_column", "exact", "fuzzy", "unresolved")
    for method in methods:
        count = sum(1 for r in records if r["photo_match_method"] == method)
        if count:
            print(f"  {method:17s} {count:5d} ({100 * count / n:.1f}%)")
    resolved = n - len(unresolved)
    print(f"\nПокрытие индекса: {resolved}/{n} ({100 * resolved / n:.1f}%) "
          f"-> потолок accuracy на равномерном eval")
    print(f"\nКаталог записан:    {catalog_path}")
    print(f"Список на доразбор: {unresolved_path}")


if __name__ == "__main__":
    main()
