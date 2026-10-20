"""Строит "reranking target" на каждый slug — набор текстовых атрибутов, с
которыми на инференсе будет сверяться OCR-текст с фото этикетки, чтобы отличить
near-duplicates (см. ARCHITECTURE.md, этап реранкинга).

Комбинирует:
  - чистые текстовые поля CSV (Название вина, Сорт винограда, Винодельня)
  - атрибуты, восстановленные из slug (год, сладость/тип, крепость) —
    их нет отдельными колонками в CSV организатора

Атрибуты сладости переводятся обратно на кириллицу, потому что именно так они
напечатаны на этикетке и именно так их прочитает OCR.
"""

from dataclasses import dataclass, field

from .slug_parser import ParsedSlug

SWEETNESS_RU = {
    "suhoe": "сухое",
    "polusuhoe": "полусухое",
    "polusladkoe": "полусладкое",
    "sladkoe": "сладкое",
    "bryut": "брют",
    "brut": "брют",
    "ekstra": "экстра брют",
    "igristoe": "игристое",
    "desertnoe": "десертное",
    "zekt": "зект",
    "petnat": "петнат",
    "tihoe": "тихое",
}


@dataclass
class RerankTarget:
    slug: str
    name: str
    grape: str
    winery: str
    category_color: str
    year: str | None
    sweetness_ru: list = field(default_factory=list)
    abv_tokens: list = field(default_factory=list)
    combined_text: str = ""

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "grape": self.grape,
            "winery": self.winery,
            "category_color": self.category_color,
            "year": self.year,
            "sweetness_ru": self.sweetness_ru,
            "abv_tokens": self.abv_tokens,
            "combined_text": self.combined_text,
        }


def build_rerank_target(row: dict, parsed: ParsedSlug) -> RerankTarget:
    sweetness_ru = [SWEETNESS_RU[t] for t in parsed.sweetness if t in SWEETNESS_RU]

    parts = [
        row.get("Название вина", "").strip(),
        row.get("Сорт винограда", "").strip(),
        row.get("Винодельня", "").strip(),
        " ".join(sweetness_ru),
        parsed.year or "",
    ]
    combined_text = " ".join(p for p in parts if p)

    return RerankTarget(
        slug=row["Slug"],
        name=row.get("Название вина", "").strip(),
        grape=row.get("Сорт винограда", "").strip(),
        winery=row.get("Винодельня", "").strip(),
        category_color=row.get("Категория", "").strip(),
        year=parsed.year,
        sweetness_ru=sweetness_ru,
        abv_tokens=parsed.abv_tokens,
        combined_text=combined_text,
    )
