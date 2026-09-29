"""Сопоставление slug из каталога с реальным файлом фото в дампе uploads/.

Strapi хранит файлы как <slugified-original-name>_<10 hex>.webp, плюс размерные
варианты (thumbnail_/small_/medium_/large_). Колонка "Название фото" в CSV
организатора этому не соответствует (проверено — 0 точных совпадений), но сам
Slug почти всегда совпадает с именем файла после замены "-" на "_" и удаления
размерного префикса и hex-суффикса.

Там, где точного совпадения нет, используется fuzzy-матчинг (rapidfuzz), с
защитой от попадания на файлы, размеченные людьми как "не вино" (has_wine/no_wine).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from . import config
from .translit import normalize_filename


def strip_size_prefix(filename: str) -> str:
    for prefix in config.SIZE_PREFIXES:
        if filename.startswith(prefix):
            return filename[len(prefix):]
    return filename


def to_base(filename: str) -> str:
    """thumbnail_foo_bar_a1b2c3d4e5.webp -> foo_bar"""
    name = strip_size_prefix(filename)
    return re.sub(r"_[0-9a-f]{10}\.(webp|jpg|jpeg|png)$", "", name, flags=re.I)


def strip_upload_prefix(filename: str) -> str:
    """Разметочные копии в has_wine/no_wine названы 'uploads__<оригинал>'."""
    return filename[len("uploads__"):] if filename.startswith("uploads__") else filename


def tokenize(s: str):
    return set(re.split(r"[_\-]+", s.lower())) - {""}


@dataclass
class UploadsIndex:
    base_to_files: dict = field(default_factory=dict)   # base_name -> [filenames]
    has_wine_bases: set = field(default_factory=set)
    no_wine_bases: set = field(default_factory=set)
    normalized_to_base: dict = field(default_factory=dict)  # normalize_filename(base) -> base

    @classmethod
    def build(cls) -> "UploadsIndex":
        base_to_files: dict[str, list[str]] = {}
        normalized_to_base: dict[str, str] = {}
        for fn in Path(config.UPLOADS_DIR).iterdir():
            if not fn.is_file():
                continue
            base = to_base(fn.name)
            base_to_files.setdefault(base, []).append(fn.name)
            normalized_to_base.setdefault(normalize_filename(base), base)

        has_wine_bases = cls._labeled_bases(config.HAS_WINE_DIR)
        no_wine_bases = cls._labeled_bases(config.NO_WINE_DIR)
        return cls(base_to_files, has_wine_bases, no_wine_bases, normalized_to_base)

    @staticmethod
    def _labeled_bases(directory: Path) -> set:
        if not directory.exists():
            return set()
        return {
            to_base(strip_upload_prefix(fn.name))
            for fn in directory.iterdir()
            if fn.is_file()
        }

    def is_confirmed_not_wine(self, base: str) -> bool:
        """True только если base размечен как 'не вино' и НИКОГДА как 'вино'."""
        return base in self.no_wine_bases and base not in self.has_wine_bases

    def pick_best_file(self, base: str) -> str:
        files = self.base_to_files[base]
        for pref in ("large_", None, "medium_", "small_", "thumbnail_"):
            for fn in files:
                if pref is None:
                    if not fn.startswith(config.SIZE_PREFIXES):
                        return fn
                elif fn.startswith(pref):
                    return fn
        return files[0]


@dataclass
class MatchResult:
    slug: str
    base: str | None
    file: str | None
    method: str        # "photo_column" | "exact" | "fuzzy" | "unresolved"
    score: float | None
    flagged_no_wine: bool = False


def match_slug(slug: str, index: UploadsIndex, photo_name: str | None = None) -> MatchResult:
    key = slug.replace("-", "_")

    # 1. колонка «Название фото» — авторитетный источник самой CMS.
    # Сравнивается в нормализованном виде: CSV хранит исходное имя
    # (`Агора_Блэк Стоун.webp`), а Strapi транслитерирует его и дописывает
    # hex-суффикс (`Agora_Blek_Stoun_e98339ae1b.webp`).
    if photo_name and photo_name.strip():
        normalized = normalize_filename(photo_name.strip())
        base = index.normalized_to_base.get(normalized)
        if base and not index.is_confirmed_not_wine(base):
            return MatchResult(slug, base, index.pick_best_file(base), "photo_column", 100.0)

    # 2. exact match по самому slug
    if key in index.base_to_files:
        if not index.is_confirmed_not_wine(key):
            return MatchResult(slug, key, index.pick_best_file(key), "exact", 100.0)
        # exact match landed on a photo confirmed as non-wine junk — fall through to fuzzy

    # 3. fuzzy match, skipping confirmed non-wine candidates
    slug_tokens = tokenize(slug)
    bases = list(index.base_to_files.keys())
    candidates = [b for b in bases if len(slug_tokens & tokenize(b)) >= 2] or bases

    ranked = process.extract(key, candidates, scorer=fuzz.token_sort_ratio, limit=10)
    for base, score, _ in ranked:
        if score < config.FUZZY_THRESHOLD:
            break
        if not index.is_confirmed_not_wine(base):
            return MatchResult(slug, base, index.pick_best_file(base), "fuzzy", score)

    # 4. nothing usable found — report best guess (even if below threshold / non-wine) for manual review
    if ranked:
        base, score, _ = ranked[0]
        return MatchResult(
            slug, base, None, "unresolved", score,
            flagged_no_wine=index.is_confirmed_not_wine(base),
        )
    return MatchResult(slug, None, None, "unresolved", None)
