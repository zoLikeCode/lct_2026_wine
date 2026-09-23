"""Каталожные фото из датасета `Downloads/images` — вручную подтверждённая
разметка (человеком проверено соответствие фото слагу), организованная по
`<slug>/*.webp` напрямую, в отличие от `prod-svoe-vino/strapi/uploads/`, где
связь slug -> файл приходится восстанавливать (транслитерация, fuzzy).

Это надмножество более раннего `Downloads/Archive/images` (те же хеши
файлов, плюс больше вин и больше фото на вино после дополнительной
проверки) — используется как основной источник эталонов.

Внутри каждой папки файлы трёх видов:
  `<hash>_catalog_reference.webp` — тот же эталон, что и в родном каталоге
                                     портала (предпочтительный источник);
  `<hash>_bottle.webp/.png`        — доп. подтверждённое реальное фото;
  `<hash>_front_label.webp`        — доп. подтверждённое фото этикетки.
"""

import os
from pathlib import Path

PARSER_IMAGES_DIR = Path(os.environ.get("WINE_PARSER_IMAGES_DIR", "~/Downloads/images")).expanduser()


def classify(filename: str) -> str | None:
    if filename.endswith("catalog_reference.webp"):
        return "reference"
    if filename.endswith("_bottle.webp") or filename.endswith("_bottle.png"):
        return "bottle"
    if filename.endswith("_front_label.webp") or filename.endswith("_label.webp"):
        return "label"
    return None


def best_reference_for(slug: str) -> Path | None:
    """Лучший файл для использования как эталон индекса: предпочитаем
    `catalog_reference`, иначе берём первое доступное `bottle`/`label`."""
    slug_dir = PARSER_IMAGES_DIR / slug
    if not slug_dir.is_dir():
        return None

    by_kind: dict[str, list[Path]] = {"reference": [], "bottle": [], "label": []}
    for f in slug_dir.iterdir():
        kind = classify(f.name)
        if kind:
            by_kind[kind].append(f)

    for kind in ("reference", "bottle", "label"):
        if by_kind[kind]:
            return sorted(by_kind[kind])[0]
    return None
