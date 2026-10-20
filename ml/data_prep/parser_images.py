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


# Пустая папка в Downloads/images означает "команда искала и не подтвердила
# фото через СВОЙ процесс (внешние источники)" — это НЕ то же самое, что
# "наш старый uploads-маппинг (через колонку CSV) неверен": при ручной
# проверке всех 13 таких случаев 10 оказались нормальными фото бутылок
# (§11.15 findings.md), включая `rozovoe-zoloto`, где откат на uploads дал
# верный результат. Поэтому вместо блокировки по пустой папке целиком —
# точечный список подтверждённо неверных сопоставлений: проверено глазами,
# что файл, на который указывал старый uploads-маппинг, физически не то
# вино (афиша фестиваля, чужая винодельня, не тот цвет).
CONFIRMED_BAD_FALLBACK: set[str] = {
    "oleg",                       # эталон = афиша винного фестиваля, не бутылка
    "rozovoe-polusladkoe-2",      # эталон = бутылка "Усадьба Перовских" (slug — Абрау-Дюрсо)
    "igristoe-zhemchuzhnoe-vino-polusuhoe-krasnoe-di-kaspiko-fiori-di-mare-di-caspico-fiori-di-mare",
    # эталон = явно светлая/белая бутылка Di Caspico Fiori di Mare при slug "красное"
}


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
