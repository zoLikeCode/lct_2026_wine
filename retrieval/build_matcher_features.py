"""Считает признаки обученного матчера (DISK + LightGlue) для уже собранных
групп кандидатов и дописывает их в готовый датасет реранкера.

Отдельный проход, а не часть `build_rerank_dataset`, потому что матчер на
порядок дороже эмбеддера: ~1.1 с на описание кадра и ~0.2 с на каждую пару.
Дескрипторы эталонов считаются один раз и кешируются на диск.

    python -m retrieval.build_matcher_features --in rerank_mv_own.json \\
        --out rerank_match_own.json
"""

import argparse
import json
import pickle
import time
from pathlib import Path

from PIL import Image

from data_prep import config
from .learned_matcher import describe, inlier_ratio
from .predict import PREPROCESS
from .preprocess_field import preprocess

MATCHER_FEATURE_NAMES = (
    "match_inliers",
    "match_rank",
    "match_share_of_best",
    "match_is_best",
)
CACHE_PATH = config.OUTPUTS_DIR / "matcher_reference_cache.pkl"


def load_cache() -> dict:
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "rb") as f:
            return pickle.load(f)
    return {}


def save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(cache, f)


def matcher_features(inliers: list[float]) -> list[list[float]]:
    """Сырое число inlier-ов само по себе мало что значит — важно, как
    кандидат выглядит на фоне остальных в этой же выдаче."""
    best = max(inliers) if inliers else 0.0
    order = sorted(range(len(inliers)), key=lambda i: -inliers[i])
    rank_of = {idx: position + 1 for position, idx in enumerate(order)}
    return [[
        inliers[i],
        float(rank_of[i]),
        inliers[i] / best if best > 1e-9 else 0.0,
        float(best > 1e-9 and inliers[i] >= best),
    ] for i in range(len(inliers))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="source", required=True)
    parser.add_argument("--out", dest="target", required=True)
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / args.source, encoding="utf-8") as f:
        data = json.load(f)
    groups, names = data["groups"], data["feature_names"]

    # У 10 позиций (photo_match_method=photo_column) путь относительный —
    # он указывает внутрь uploads-дампа организатора, а не в parser-датасет.
    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        photo_of = {r["slug"]: (r["photo_file"] if r["photo_file"].startswith("/")
                                else str(config.UPLOADS_DIR / r["photo_file"]))
                    for r in json.load(f) if r["photo_file"]}

    cache = load_cache()
    print(f"Групп: {len(groups)}, дескрипторов в кеше: {len(cache)}")
    started = time.perf_counter()

    for i, group in enumerate(groups, start=1):
        with Image.open(group["query"]) as raw:
            query_features = describe(preprocess(raw, PREPROCESS))

        inliers = []
        for slug in group["slugs"]:
            if slug not in cache:
                path = photo_of.get(slug)
                if path is None:
                    cache[slug] = None
                else:
                    with Image.open(path) as reference:
                        cache[slug] = describe(reference.convert("RGB"))
            reference_features = cache[slug]
            inliers.append(0.0 if reference_features is None
                           else inlier_ratio(query_features, reference_features))

        for row, extra in zip(group["features"], matcher_features(inliers)):
            row.extend(extra)

        if i % 20 == 0:
            elapsed = time.perf_counter() - started
            print(f"  {i}/{len(groups)}  ({elapsed / i:.1f} с/запрос, кеш {len(cache)})", flush=True)
            save_cache(cache)

    save_cache(cache)
    out_path = config.OUTPUTS_DIR / args.target
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"feature_names": names + list(MATCHER_FEATURE_NAMES),
                   "top_k": data["top_k"], "groups": groups}, f, ensure_ascii=False)
    print(f"\nГотово за {(time.perf_counter() - started) / 60:.1f} мин: {out_path}")


if __name__ == "__main__":
    main()
