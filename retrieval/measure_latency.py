"""Замер латентности и памяти полного пайплайна.

SLA кейса — 3 секунды на запрос, и скорость отдельно оценивается (5/100).
Отдельная причина мерить: при нехватке памяти на машине разработки запрос
деградировал с 1.1 до 19 секунд из-за свопа, то есть риск провалить демо
вполне реален.

Меряется то, что происходит на один пользовательский запрос:
  1. декодирование фото,
  2. эмбеддинг (самая тяжёлая часть на 512px),
  3. ANN-поиск по индексу,
  4. ORB против top-5 кандидатов.

Запуск:
    python -m retrieval.measure_latency --backbone siglip2_512 --runs 30
"""

import argparse
import json
import random
import statistics
import time

import torch
from PIL import Image

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .rerank_signals import orb_inlier_score

TOP_K = 5


def rss_mb() -> float:
    import resource
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS отдаёт байты, Linux — килобайты
    return usage / (1024 * 1024) if usage > 10**7 else usage / 1024


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2_512")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--no-rerank", action="store_true", help="мерить только retrieval")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    print(f"RSS до загрузки моделей: {rss_mb():.0f} МБ")

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    lookup = {r["slug"]: r for r in records if r["photo_file"]}
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    backbone = load_backbone(args.backbone)
    print(f"RSS после загрузки:      {rss_mb():.0f} МБ   (device={backbone.device})")

    sample = random.sample(sorted(lookup), args.runs)
    stages = {"decode": [], "embed": [], "search": [], "rerank": [], "total": []}

    for i, slug in enumerate(sample):
        path = config.UPLOADS_DIR / lookup[slug]["photo_file"]

        t_start = time.perf_counter()
        img = Image.open(path).convert("RGB")
        t_decode = time.perf_counter()

        emb = backbone.encode(img)
        if backbone.device.type == "mps":
            torch.mps.synchronize()
        t_embed = time.perf_counter()

        top = index.search(emb, top_k=TOP_K)
        t_search = time.perf_counter()

        if not args.no_rerank:
            for r in top:
                with Image.open(config.UPLOADS_DIR / lookup[r.slug]["photo_file"]) as cand:
                    orb_inlier_score(img, cand)
        t_rerank = time.perf_counter()

        img.close()

        if i == 0:
            continue  # первый прогон прогревает кеши, в статистику не идёт

        stages["decode"].append((t_decode - t_start) * 1000)
        stages["embed"].append((t_embed - t_decode) * 1000)
        stages["search"].append((t_search - t_embed) * 1000)
        stages["rerank"].append((t_rerank - t_search) * 1000)
        stages["total"].append((t_rerank - t_start) * 1000)

    print(f"RSS пиковый:             {rss_mb():.0f} МБ\n")
    print(f"Замеров: {len(stages['total'])} (первый прогон отброшен как прогрев)\n")
    print(f"{'этап':10s} | {'среднее':>9s} | {'медиана':>9s} | {'p95':>9s}")
    print("-" * 46)
    for name in ("decode", "embed", "search", "rerank", "total"):
        vals = sorted(stages[name])
        if not vals:
            continue
        p95 = vals[min(int(len(vals) * 0.95), len(vals) - 1)]
        print(f"{name:10s} | {statistics.mean(vals):7.1f}мс | "
              f"{statistics.median(vals):7.1f}мс | {p95:7.1f}мс")

    total = stages["total"]
    p95_total = sorted(total)[min(int(len(total) * 0.95), len(total) - 1)]
    budget = 3000
    print(f"\nSLA {budget} мс: p95 = {p95_total:.0f} мс "
          f"({'укладываемся, запас %.1fx' % (budget / p95_total) if p95_total < budget else 'НЕ УКЛАДЫВАЕМСЯ'})")


if __name__ == "__main__":
    main()
