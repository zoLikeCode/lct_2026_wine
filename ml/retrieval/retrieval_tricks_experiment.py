"""Перебор классических приёмов ретривала на закешированных эмбеддингах.

Все варианты считаются поверх готовых векторов, поэтому полный перебор
занимает секунды. Отбеливание обучается ТОЛЬКО на векторах индекса — полевые
кадры в обучении преобразования не участвуют, иначе оценка была бы нечестной.

    python -m retrieval.cache_query_embeddings && python -m retrieval.retrieval_tricks_experiment
"""

import json

import numpy as np

from data_prep import config
from .cache_query_embeddings import SETS, load_cached
from .predict import BACKBONE
from .retrieval_tricks import (apply_whitening, database_augmentation, fit_whitening,
                               l2_normalize, query_expansion)


def accuracy(queries: np.ndarray, index: np.ndarray, slugs: list[str],
             true_slugs: list[str], top_k: int = 5) -> tuple[float, float]:
    similarities = queries @ index.T
    order = np.argsort(-similarities, axis=1)[:, :top_k]
    hits1 = sum(slugs[row[0]] == true for row, true in zip(order, true_slugs))
    hits5 = sum(true in [slugs[i] for i in row] for row, true in zip(order, true_slugs))
    n = len(true_slugs)
    return hits1 / n, hits5 / n


def bootstrap_interval(queries: np.ndarray, index: np.ndarray, slugs: list[str],
                       true_slugs: list[str], rounds: int = 2000, seed: int = 0) -> tuple[float, float]:
    """Насколько вообще различимы эффекты на нашей выборке."""
    similarities = queries @ index.T
    correct = np.array([slugs[int(np.argmax(row))] == true
                        for row, true in zip(similarities, true_slugs)], dtype=np.float64)
    rng = np.random.default_rng(seed)
    means = correct[rng.integers(0, len(correct), size=(rounds, len(correct)))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> None:
    data = np.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}.npz")
    slugs = [str(s) for s in data["slugs"]]
    index = l2_normalize(data["embeddings"].astype(np.float64))

    queries, true_slugs = [], []
    for name in SETS:
        cached = load_cached(name)
        if cached is None:
            raise SystemExit(f"нет кеша для '{name}' — сначала python -m retrieval.cache_query_embeddings")
        embeddings, slugs_part, _ = cached
        queries.append(l2_normalize(embeddings.astype(np.float64)))
        true_slugs.extend(slugs_part)
    queries = np.concatenate(queries)
    print(f"Запросов: {len(queries)}, позиций индекса: {len(slugs)}, размерность: {index.shape[1]}")

    base1, base5 = accuracy(queries, index, slugs, true_slugs)
    low, high = bootstrap_interval(queries, index, slugs, true_slugs)
    print(f"\nБАЗА: top-1 {base1:.1%}, top-5 {base5:.1%}")
    print(f"  95% доверительный интервал top-1: [{low:.1%}, {high:.1%}] "
          f"(ширина {100 * (high - low):.1f}пп — меньшие эффекты выборка не различает)\n")

    results = {"baseline": {"top1": base1, "top5": base5}}

    print(f"{'вариант':44s} {'top-1':>16s} {'top-5':>8s}")
    print(f"{'база':44s} {base1:>7.1%} {'':>8s} {base5:>7.1%}")

    def report(title: str, q: np.ndarray, idx: np.ndarray) -> None:
        a1, a5 = accuracy(q, idx, slugs, true_slugs)
        delta = (a1 - base1) * 100
        results[title] = {"top1": a1, "top5": a5}
        print(f"{title:44s} {a1:>7.1%} ({delta:>+5.1f}пп) {a5:>7.1%}")

    for alpha in (0.25, 0.5):
        for n_components in (256, 512, 1024, None):
            mean, transform = fit_whitening(index, n_components=n_components, alpha=alpha)
            report(f"отбеливание alpha={alpha} dim={n_components or 'полная'}",
                   apply_whitening(queries, mean, transform),
                   apply_whitening(index, mean, transform))

    for top_k in (2, 3, 5):
        for alpha in (1.0, 3.0):
            report(f"query expansion k={top_k} alpha={alpha}",
                   query_expansion(queries, index, top_k=top_k, alpha=alpha), index)

    for top_k in (1, 2):
        for alpha in (1.0, 3.0):
            report(f"database augmentation k={top_k} alpha={alpha}",
                   queries, database_augmentation(index, top_k=top_k, alpha=alpha))

    best_whitening = max((k for k in results if k.startswith("отбеливание")),
                         key=lambda k: results[k]["top1"])
    alpha = float(best_whitening.split("alpha=")[1].split()[0])
    dim_token = best_whitening.split("dim=")[1]
    n_components = None if dim_token == "полная" else int(dim_token)
    mean, transform = fit_whitening(index, n_components=n_components, alpha=alpha)
    white_index = apply_whitening(index, mean, transform)
    white_queries = apply_whitening(queries, mean, transform)
    for top_k in (2, 3):
        report(f"лучшее отбеливание + QE k={top_k}",
               query_expansion(white_queries, white_index, top_k=top_k, alpha=3.0), white_index)

    out = config.OUTPUTS_DIR / "retrieval_tricks.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"n": len(queries), "baseline_ci": [low, high], "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out}")


if __name__ == "__main__":
    main()
