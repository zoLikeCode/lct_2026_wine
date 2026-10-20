"""Разбор slug на структурные атрибуты, которых нет отдельными колонками в CSV
организатора (год урожая, сладость/тип, крепость) — они закодированы только
внутри строки Slug, например:

    abrau-dyurso-pino-nuar-krasnoe-suhoe-13
                            ^^^^^^ ^^^^^ ^^
                            цвет   тип   крепость(?)

Нужно для построения "reranking target" — набора текстовых атрибутов, с которыми
сверяется то, что OCR прочитал на фото этикетки.
"""

import re
from dataclasses import dataclass, field

COLORS = {"beloe", "krasnoe", "rozovoe", "oranzhevoe"}

SWEETNESS_VOCAB = {
    "suhoe", "polusuhoe", "polusladkoe", "sladkoe",
    "bryut", "ekstra", "brut", "igristoe", "desertnoe",
    "zekt", "petnat", "tihoe",
}

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
NUM_RE = re.compile(r"^\d{1,3}$")


@dataclass
class ParsedSlug:
    year: str | None = None
    color: str | None = None
    sweetness: list = field(default_factory=list)
    abv_tokens: list = field(default_factory=list)

    def has_any_differentiator(self) -> bool:
        return bool(self.year or self.sweetness or self.abv_tokens)


def parse_slug(slug: str) -> ParsedSlug:
    tokens = slug.split("-")

    year = next((t for t in tokens if YEAR_RE.fullmatch(t)), None)

    color, color_idx = None, None
    for i, t in enumerate(tokens):
        if t in COLORS:
            color, color_idx = t, i

    sweetness: list[str] = []
    abv_tokens: list[str] = []
    if color_idx is not None:
        for t in tokens[color_idx + 1:]:
            if NUM_RE.match(t) and not YEAR_RE.fullmatch(t):
                abv_tokens.append(t)
            elif t in SWEETNESS_VOCAB:
                sweetness.append(t)
        # тип вина иногда стоит ПЕРЕД цветом, напр. "...-igristoe-bryut-beloe-12"
        for t in tokens[max(0, color_idx - 2):color_idx]:
            if t in SWEETNESS_VOCAB and t not in sweetness:
                sweetness.append(t)

    return ParsedSlug(year=year, color=color, sweetness=sweetness, abv_tokens=abv_tokens)
