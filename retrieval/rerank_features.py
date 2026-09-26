"""Признаки для обучаемого реранкера top-K.

Замер §11.28 показал, где лежит выигрыш: две трети запаса — это позиции 2-3,
где верный ответ проигрывает вплотную (отрыв на ошибках доходил до 0.0002).
Значит задача не «найти иголку», а «развести двух близнецов», и признаки
должны описывать именно геометрию близкой конкуренции внутри выдачи, а не
абсолютное сходство.

Осознанно НЕ используется OCR: как источник сигнала о запросе он отклонён
дважды (§11.12, §11.18 — на фото полки читается текст соседней бутылки) и
стоит ~4 с на кадр. Признаки здесь считаются из уже готовой выдачи и
метаданных каталога, поэтому реранкинг почти бесплатен по времени.

Сторона запроса в v1 представлена только формой распределения скоров —
нового сигнала о самом кадре здесь нет. Это сознательное ограничение:
сперва проверяем, есть ли выигрыш в калибровке и априорных свойствах
кандидатов, и только потом добавляем дорогие мультиракурсные признаки.
"""

import json
from pathlib import Path

FEATURE_NAMES = (
    "score",
    "rank",
    "margin_to_top1",
    "margin_to_next",
    "score_z",
    "top1_gap",
    "same_winery_as_top1",
    "winery_count_in_list",
    "near_dup_with_top1",
    "near_dup_siblings_in_list",
    "has_year",
    "has_sweetness",
    "has_abv",
    "reference_is_catalog",
)


def load_near_dup_pairs(path: Path) -> set[frozenset[str]]:
    """Пары slug, у которых эталонные фото визуально почти неразличимы."""
    with open(path, encoding="utf-8") as f:
        report = json.load(f)
    pairs = set()
    for group in ("near_identical_pairs", "similar_pairs"):
        for item in report.get(group, []):
            pairs.add(frozenset((item["slug_a"], item["slug_b"])))
    return pairs


def _mean_std(values: list[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, var ** 0.5


def candidate_features(candidates: list[dict], lookup: dict[str, dict],
                       near_dup_pairs: set[frozenset[str]]) -> list[list[float]]:
    """По списку кандидатов `[{slug, score}, ...]` (уже отсортированному по
    убыванию score) возвращает вектор признаков на каждого кандидата."""
    if not candidates:
        return []

    scores = [c["score"] for c in candidates]
    mean, std = _mean_std(scores)
    top1_slug, top1_score = candidates[0]["slug"], scores[0]
    top1_gap = scores[0] - scores[1] if len(scores) > 1 else 0.0

    wineries = [lookup.get(c["slug"], {}).get("winery", "") for c in candidates]
    slugs_in_list = [c["slug"] for c in candidates]

    rows = []
    for i, candidate in enumerate(candidates):
        slug = candidate["slug"]
        record = lookup.get(slug, {})
        target = record.get("rerank_target", {})
        winery = wineries[i]
        next_score = scores[i + 1] if i + 1 < len(scores) else scores[i]

        rows.append([
            scores[i],
            float(i + 1),
            scores[i] - top1_score,
            scores[i] - next_score,
            (scores[i] - mean) / std if std > 1e-9 else 0.0,
            top1_gap,
            float(bool(winery) and winery == wineries[0] and slug != top1_slug),
            float(sum(1 for w in wineries if w and w == winery)),
            float(frozenset((slug, top1_slug)) in near_dup_pairs),
            float(sum(1 for other in slugs_in_list
                      if other != slug and frozenset((slug, other)) in near_dup_pairs)),
            float(bool(target.get("year"))),
            float(bool(target.get("sweetness_ru"))),
            float(bool(target.get("abv_tokens"))),
            float(str(record.get("photo_match_method", "")).endswith("reference")),
        ])
    return rows
