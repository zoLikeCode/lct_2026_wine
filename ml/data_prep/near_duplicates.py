"""Диагностика near-duplicates: какие позиции каталога визуально почти
неотличимы (эталонное фото совпадает или почти совпадает), и есть ли у них
текстовый различитель (rerank_target) для последующего OCR-реранкинга.

Требует уже собранный outputs/catalog_resolved.json (см. build_catalog.py).

Запуск:
    python -m data_prep.near_duplicates
"""

import json
from collections import defaultdict

import imagehash
from PIL import Image
from rapidfuzz import fuzz

from . import config

CLUSTER_NAME_THRESHOLD = 75      # похожесть slug (без хвостового числа) внутри винодельни
NEAR_IDENTICAL_PHASH_DIST = 5    # <= этого — считаем фото визуально идентичным
SIMILAR_PHASH_DIST = 15          # <= этого — визуально похожи, но не идентичны


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def strip_trailing_number(slug: str) -> str:
    import re
    return re.sub(r"-\d+$", "", slug)


def build_metadata_clusters(records: list[dict]) -> list[list[dict]]:
    by_winery = defaultdict(list)
    for r in records:
        by_winery[r["winery"]].append(r)

    clusters = []
    for winery, items in by_winery.items():
        n = len(items)
        if n < 2:
            continue
        uf = UnionFind(n)
        base_slugs = [strip_trailing_number(it["slug"]) for it in items]
        for i in range(n):
            for j in range(i + 1, n):
                if fuzz.token_sort_ratio(base_slugs[i], base_slugs[j]) >= CLUSTER_NAME_THRESHOLD:
                    uf.union(i, j)
        groups = defaultdict(list)
        for i in range(n):
            groups[uf.find(i)].append(items[i])
        clusters.extend(g for g in groups.values() if len(g) > 1)
    return clusters


_hash_cache: dict[str, imagehash.ImageHash | None] = {}


def get_phash(photo_file: str) -> imagehash.ImageHash | None:
    if photo_file in _hash_cache:
        return _hash_cache[photo_file]
    path = config.UPLOADS_DIR / photo_file
    try:
        h = imagehash.phash(Image.open(path).convert("RGB"), hash_size=16)
    except Exception:
        h = None
    _hash_cache[photo_file] = h
    return h


def has_text_differentiator(a: dict, b: dict) -> tuple[bool, list[str]]:
    """Проверяет rerank_target + сорт/название на предмет уже готового
    текстового различителя между двумя позициями каталога."""
    signals = []
    ta, tb = a["rerank_target"], b["rerank_target"]
    if ta["year"] and tb["year"] and ta["year"] != tb["year"]:
        signals.append(f"year: {ta['year']} vs {tb['year']}")
    if set(ta["sweetness_ru"]) != set(tb["sweetness_ru"]) and (ta["sweetness_ru"] or tb["sweetness_ru"]):
        signals.append(f"sweetness: {ta['sweetness_ru']} vs {tb['sweetness_ru']}")
    if set(ta["abv_tokens"]) != set(tb["abv_tokens"]) and (ta["abv_tokens"] or tb["abv_tokens"]):
        signals.append(f"abv: {ta['abv_tokens']} vs {tb['abv_tokens']}")
    if a["grape"] != b["grape"]:
        signals.append(f"grape: {a['grape']!r} vs {b['grape']!r}")
    if a["name"] != b["name"]:
        signals.append(f"name: {a['name']!r} vs {b['name']!r}")
    return bool(signals), signals


def main() -> None:
    catalog_path = config.OUTPUTS_DIR / "catalog_resolved.json"
    with open(catalog_path, encoding="utf-8") as f:
        records = json.load(f)

    resolved = {r["slug"]: r for r in records if r["photo_file"]}
    clusters = build_metadata_clusters(list(resolved.values()))
    print(f"Кластеров-кандидатов по метаданным: {len(clusters)}")

    near_identical_pairs = []
    similar_pairs = []
    total_pairs = 0

    for cluster in clusters:
        items = [r for r in cluster if r["slug"] in resolved]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                ha, hb = get_phash(a["photo_file"]), get_phash(b["photo_file"])
                if ha is None or hb is None:
                    continue
                dist = ha - hb
                total_pairs += 1
                if dist <= NEAR_IDENTICAL_PHASH_DIST:
                    has_diff, signals = has_text_differentiator(a, b)
                    near_identical_pairs.append({
                        "slug_a": a["slug"], "slug_b": b["slug"],
                        "phash_dist": dist,
                        "has_text_differentiator": has_diff,
                        "signals": signals,
                    })
                elif dist <= SIMILAR_PHASH_DIST:
                    similar_pairs.append({"slug_a": a["slug"], "slug_b": b["slug"], "phash_dist": dist})

    hopeless = [p for p in near_identical_pairs if not p["has_text_differentiator"]]

    print(f"Всего пар сравнено:            {total_pairs}")
    print(f"Визуально почти идентичны:     {len(near_identical_pairs)} (phash <= {NEAR_IDENTICAL_PHASH_DIST})")
    print(f"  из них БЕЗ текстового различителя (нужна эвристика fallback): {len(hopeless)}")
    print(f"Визуально похожи (не идентичны): {len(similar_pairs)} (phash <= {SIMILAR_PHASH_DIST})")

    report = {
        "near_identical_pairs": near_identical_pairs,
        "similar_pairs": similar_pairs,
        "hopeless_pairs": hopeless,
    }
    out_path = config.OUTPUTS_DIR / "near_duplicates_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nОтчёт записан: {out_path}")


if __name__ == "__main__":
    main()
