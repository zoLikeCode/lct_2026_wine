"""Работа с дополнительным датасетом реальных фото: `Downloads/images`
(до 22.09 — `Downloads/Archive/images`, тот же источник, позже дополненный
до полного покрытия каталога и вручную перепроверенный, см.
data_prep/parser_images.py).

Команда собрала и вручную подтвердила соответствие интернет-фото каталожным
позициям (`images/<slug>/*_bottle.webp`, `*_front_label.webp`,
`*_catalog_reference.webp`). Это первая честная замена синтетической
аугментации: реальные, разные фотографии одного и того же вина, а не
искажённые копии единственного эталонного файла.

`needs_review/` содержит неподтверждённые кандидаты — не используется здесь.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

ARCHIVE_DIR = Path(os.environ.get("WINE_ARCHIVE_DIR", "~/Downloads")).expanduser()
IMAGES_DIR = ARCHIVE_DIR / "images"


@dataclass
class SlugPhotos:
    reference: list = field(default_factory=list)
    bottle: list = field(default_factory=list)
    label: list = field(default_factory=list)

    @property
    def extra(self) -> list:
        """Подтверждённые фото, отличные от нашего собственного эталона."""
        return self.bottle + self.label


def classify(filename: str) -> str | None:
    if filename.endswith("catalog_reference.webp"):
        return "reference"
    if filename.endswith("_bottle.webp") or filename.endswith("_bottle.png"):
        return "bottle"
    if filename.endswith("_front_label.webp") or filename.endswith("_label.webp"):
        return "label"
    return None


def load_archive() -> dict[str, SlugPhotos]:
    """slug -> SlugPhotos с полными путями до файлов."""
    result: dict[str, SlugPhotos] = {}
    if not IMAGES_DIR.exists():
        return result
    for slug_dir in IMAGES_DIR.iterdir():
        if not slug_dir.is_dir():
            continue
        photos = SlugPhotos()
        for f in slug_dir.iterdir():
            kind = classify(f.name)
            if kind == "reference":
                photos.reference.append(f)
            elif kind == "bottle":
                photos.bottle.append(f)
            elif kind == "label":
                photos.label.append(f)
        result[slug_dir.name] = photos
    return result
