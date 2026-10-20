"""Прицельный OCR-реранкинг near-duplicate пар.

Отличие от generic OCR-реранкинга (§11.12 findings — отклонён на честных
данных, n=29, эффекта нет): там query-текст fuzzy-сравнивался с целым
`combined_text` кандидата (10+ общих слов, различитель — 1-2 слова из них,
поэтому сигнал тонул в шуме). Здесь вместо этого для КАЖДОЙ пары кандидатов
в top-K находится их конкретное структурное различие (год/сладость/крепость/
сорт — уже посчитаны в `rerank_target`, см. `data_prep/rerank_target.py`) и
проверяется буквальное присутствие ИМЕННО этого слова/числа в OCR-тексте
запроса — не похожесть текста целиком.

Мотивация числами: на честном 56-фото наборе (§11.17) 7 из 9 ошибок имеют
разрыв top-1/top-2 по эмбеддингу < 0.02 (иногда < 0.001) — то есть верного
кандидата от неверного отделяет крошечный зазор, который такой точечный
сигнал в принципе способен перевесить, если слово физически читается на
фото.
"""

import re

_BOUNDARY_CHARS = r"a-zа-яё0-9."
_WORD_RE = re.compile(rf"[{_BOUNDARY_CHARS}]+", re.IGNORECASE)


def _normalize(text: str) -> str:
    text = text.lower().replace(",", ".")
    text = re.sub(rf"[^{_BOUNDARY_CHARS}]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _abv_variants(token: str) -> set[str]:
    """'135' на этикетке обычно напечатано как '13,5' -> после нормализации
    '13.5'; двузначные крепости ('14') печатаются как есть."""
    variants = {token}
    if len(token) == 3 and token.isdigit():
        variants.add(f"{token[:2]}.{token[2]}")
    return variants


def _grape_terms(grape: str) -> set[str]:
    return {g.strip().lower() for g in grape.split(",") if g.strip()}


def _words(phrases: set[str]) -> set[str]:
    words = set()
    for p in phrases:
        words.update(_WORD_RE.findall(p))
    return words


def _exclusive(only_a: set[str], only_b: set[str], all_a: set[str], all_b: set[str]) -> tuple[set[str], set[str]]:
    """Отбрасывает кандидатов-различителей, чьи слова целиком содержатся в
    полном наборе терминов ДРУГОЙ стороны — иначе, например, «мускат»
    засчитался бы отличием от «Мускат Белый, Мускат Оттонель»: слово
    буквально встречается и там, OCR не может по нему различить стороны."""
    words_a, words_b = _words(all_a), _words(all_b)
    safe_a = {p for p in only_a if not set(_WORD_RE.findall(p)) <= words_b}
    safe_b = {p for p in only_b if not set(_WORD_RE.findall(p)) <= words_a}
    return safe_a, safe_b


def _diff_signals(record_a: dict, record_b: dict) -> list[tuple[set[str], set[str]]]:
    """Список (варианты_a, варианты_b) — по одной паре множеств на каждое
    найденное структурное различие между двумя каталожными записями.
    Сигнал берётся, только если у ОБЕИХ сторон есть свой уникальный,
    словарно-независимый от другой стороны вариант."""
    ta, tb = record_a["rerank_target"], record_b["rerank_target"]
    signals = []

    ya, yb = ta.get("year"), tb.get("year")
    if ya and yb and ya != yb:
        signals.append(({ya}, {yb}))

    sa, sb = set(ta.get("sweetness_ru", [])), set(tb.get("sweetness_ru", []))
    only_a, only_b = _exclusive(sa - sb, sb - sa, sa, sb)
    if only_a and only_b:
        signals.append((only_a, only_b))

    aa, ab = set(ta.get("abv_tokens", [])), set(tb.get("abv_tokens", []))
    only_a, only_b = aa - ab, ab - aa
    if only_a and only_b:
        va = set().union(*(_abv_variants(t) for t in only_a))
        vb = set().union(*(_abv_variants(t) for t in only_b))
        signals.append((va, vb))

    ga, gb = _grape_terms(record_a.get("grape", "")), _grape_terms(record_b.get("grape", ""))
    only_a, only_b = _exclusive(ga - gb, gb - ga, ga, gb)
    if only_a and only_b:
        signals.append((only_a, only_b))

    return signals


def _contains_phrase(normalized_query: str, phrase: str) -> bool:
    phrase_norm = _normalize(phrase)
    if not phrase_norm:
        return False
    pattern = r"(?<![" + _BOUNDARY_CHARS + r"])" + re.escape(phrase_norm).replace(r"\ ", r"\s+") \
        + r"(?![" + _BOUNDARY_CHARS + r"])"
    return re.search(pattern, normalized_query) is not None


def pair_vote(normalized_query: str, record_a: dict, record_b: dict) -> int:
    """>0 -> голос за a, <0 -> голос за b, 0 -> сигнала нет или противоречив."""
    votes = 0
    for variants_a, variants_b in _diff_signals(record_a, record_b):
        has_a = any(_contains_phrase(normalized_query, v) for v in variants_a)
        has_b = any(_contains_phrase(normalized_query, v) for v in variants_b)
        if has_a and not has_b:
            votes += 1
        elif has_b and not has_a:
            votes -= 1
    return votes


def net_votes(query_text: str, records: list[dict]) -> dict[str, int]:
    """Для каждого slug среди `records` (top-K кандидатов) — сумма голосов
    в его пользу минус против, по результатам всех попарных сравнений."""
    normalized_query = _normalize(query_text)
    votes = {r["slug"]: 0 for r in records}
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            v = pair_vote(normalized_query, a, b)
            votes[a["slug"]] += v
            votes[b["slug"]] -= v
    return votes
