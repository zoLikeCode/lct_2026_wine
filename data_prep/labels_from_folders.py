"""Строит CSV разметки из папок вида `<корень>/<slug>/<фото>`.

Папки `~/Downloads/real_photos` и `~/Downloads/catalog_photos` — ручное
разделение датасета `images/` (4886 файлов) на витринные и «реальные» снимки.
Разметка там задана именем папки, поэтому отдельный проход разметки не нужен.

Два важных нюанса, ради которых существует этот модуль:

1. **Разделение по типу съёмки.** `real_photos` — смесь: файлы `own_*` сняты
   командой в магазине (телефон, бутылка в руке, полка на фоне — те самые
   условия, в которых нас будут мерить), а `*_bottle.*` — промо/студийная
   съёмка из тех же интернет-источников, что и каталожные эталоны. Мерить на
   вторых — воспроизвести смещение `Archive` (§11.11), поэтому `--kind`
   разделяет их и метрики считаются отдельно.
2. **Исключение утечки.** Небольшая часть файлов сама используется эталоном
   своего slug в индексе; как запрос они дали бы тривиально верный ответ.
   Такие строки отбрасываются (`--catalog` для проверки).

    python -m data_prep.labels_from_folders --root ~/Downloads/real_photos \\
        --kind own --out outputs/field_labels_own.csv
"""

import argparse
import csv
import json
import os
from pathlib import Path

from . import config

KIND_PREFIXES = {"own": ("own_",)}
KIND_SUFFIXES = {"promo": ("_bottle.webp", "_bottle.png", "_bottle.jpg", "_bottle.jpeg")}


def matches_kind(filename: str, kind: str) -> bool:
    if kind == "all":
        return True
    if kind in KIND_PREFIXES:
        return filename.startswith(KIND_PREFIXES[kind])
    if kind in KIND_SUFFIXES:
        return filename.endswith(KIND_SUFFIXES[kind])
    raise ValueError(f"unknown kind: {kind}")


CORRECTIONS_PATH = Path(__file__).resolve().parent / "field_label_corrections.csv"


def load_corrections() -> dict[str, tuple[str, str]]:
    """Правки разметки, сделанные по ЧИТАЕМОМУ на этикетке названию.

    Разбор ошибок на 205 кадрах (§11.33) нашёл фотографии, лежащие не в своей
    папке: например, в папке Ая Органик стоит «Мускатель Массандра белый».
    Правила, которыми ограничены правки:

    - `relabel` — только если название на этикетке читается и в каталоге ему
      соответствует РОВНО ОДНА позиция;
    - `exclude` — если вино в кадре опознано, но в каталоге у него несколько
      дублирующих slug: выбрать между ними по фотографии нельзя, и оставлять
      такой кадр в метрике нечестно по отношению к модели.

    Правки не меняют файлы пользователя и лежат отдельно, чтобы их можно было
    просмотреть и оспорить. Основание каждой — в колонке `reason`.
    """
    if not CORRECTIONS_PATH.exists():
        return {}
    with open(CORRECTIONS_PATH, encoding="utf-8") as f:
        return {row["image_path"]: (row["action"], row["new_slug"])
                for row in csv.DictReader(f)}


def indexed_basenames(catalog_path: Path) -> dict[str, set[str]]:
    """slug -> множество basename файлов, которые уже лежат в индексе."""
    with open(catalog_path, encoding="utf-8") as f:
        records = json.load(f)
    result: dict[str, set[str]] = {}
    for record in records:
        if record["photo_file"]:
            result.setdefault(record["slug"], set()).add(os.path.basename(record["photo_file"]))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="папка вида <корень>/<slug>/<фото>")
    parser.add_argument("--kind", default="all", choices=["all", "own", "promo"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--catalog", default=None, help="catalog_resolved.json для проверки утечки")
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    catalog_path = Path(args.catalog).expanduser() if args.catalog \
        else config.OUTPUTS_DIR / "catalog_resolved.json"
    indexed = indexed_basenames(catalog_path)
    known_slugs = set(indexed)
    corrections = load_corrections()

    rows, leaked, unknown = [], [], set()
    relabeled, excluded = [], []
    for slug_dir in sorted(root.iterdir()):
        if not slug_dir.is_dir():
            continue
        slug = slug_dir.name
        if slug not in known_slugs:
            unknown.add(slug)
        for photo in sorted(slug_dir.iterdir()):
            if not photo.is_file() or not matches_kind(photo.name, args.kind):
                continue
            if photo.name in indexed.get(slug, ()):
                leaked.append(f"{slug}/{photo.name}")
                continue

            image_path = f"{slug}/{photo.name}"
            true_slug = slug
            action, new_slug = corrections.get(image_path, ("", ""))
            if action == "exclude":
                excluded.append(image_path)
                continue
            if action == "relabel":
                relabeled.append((image_path, slug, new_slug))
                true_slug = new_slug

            rows.append({"query_id": f"q-{len(rows) + 1:06d}",
                         "image_path": image_path,
                         "true_slug": true_slug})

    out_path = Path(args.out).expanduser()
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["query_id", "image_path", "true_slug"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Записано строк: {len(rows)} по {len({r['true_slug'] for r in rows})} slug")
    if relabeled:
        print(f"Переразмечено по читаемой этикетке: {len(relabeled)}")
        for image_path, was, now in relabeled:
            print(f"  {image_path[:52]}\n      {was[:56]} -> {now[:56]}")
    if excluded:
        print(f"Исключено (вино опознано, но в каталоге дубликаты slug): {len(excluded)}")
        for item in excluded:
            print(f"  {item[:66]}")
    print(f"Исключено как утечка (файл сам в индексе): {len(leaked)}")
    for item in leaked:
        print(f"  {item}")
    if unknown:
        print(f"Slug вне каталога (в метрику войдут как недостижимые): {len(unknown)}")
    print(f"Разметка: {out_path}")


if __name__ == "__main__":
    main()
