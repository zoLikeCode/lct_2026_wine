"""Честный A/B: один эталон на вино vs несколько подтверждённых реальных
фото на вино в индексе.

Гипотеза: если запрос снят под другим углом/освещением, чем единственный
студийный эталон, у эмбеддинга меньше шансов совпасть. Добавление ещё одного
реального ракурса того же вина как второго вектора в индексе должно поднять
шанс совпадения с ЛЮБЫМ будущим запросом — это прямое улучшение retrieval,
а не реранкинг (в отличие от ORB, не полагается на пиксельное сходство).

Методология без утечки: берём вина с >=2 подтверждённых доп. фото
(`retrieval.archive_dataset`), ОДНО добавляем в индекс (multi-ref), ОСТАЛЬНЫЕ
идут в query-выборку — одинаковую для обоих индексов (baseline и multi-ref),
поэтому сравнение честное и вес выборки одинаков.

Запуск:
    python -m retrieval.multi_ref_experiment --backbone siglip2_512
"""

import argparse
import gc
import json
import time

import numpy as np
import torch
from PIL import Image

from data_prep import config
from .archive_dataset import load_archive
from .backbones import load_backbone
from .index import EmbeddingIndex

PROGRESS_EVERY = 200


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2_512")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    lookup = {r["slug"]: r for r in records if r["photo_file"]}

    archive = load_archive()
    index_extra: dict[str, str] = {}   # slug -> путь фото, добавляемого в индекс
    query_paths: list[tuple[str, object]] = []  # (slug, path)

    for slug, photos in archive.items():
        if slug not in lookup:
            continue
        indexed_path = str(lookup[slug]["photo_file"])
        extras = [p for p in photos.extra if str(p) != indexed_path]
        if len(extras) < 2:
            continue
        extras_sorted = sorted(extras, key=lambda p: p.name)
        index_extra[slug] = extras_sorted[0]
        for p in extras_sorted[1:]:
            query_paths.append((slug, p))

    if args.limit:
        query_paths = query_paths[: args.limit]
    print(f"Вин с доп. вектором в индексе: {len(index_extra)}")
    print(f"Query-фото (одинаковые для baseline и multi-ref): {len(query_paths)}\n")

    backbone = load_backbone(args.backbone)
    is_mps = backbone.device.type == "mps"

    # --- строим multi-ref индекс: базовые эмбеддинги + доп. векторы ---
    base = np.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    base_slugs, base_embs = list(base["slugs"]), base["embeddings"]

    print("Эмбеддим дополнительные референсы для multi-ref индекса...")
    extra_slugs, extra_embs = [], []
    items = list(index_extra.items())
    t0 = time.time()
    for i, (slug, path) in enumerate(items, start=1):
        with Image.open(path) as img:
            emb = backbone.encode(img.convert("RGB"))
        extra_slugs.append(slug)
        extra_embs.append(emb)
        if is_mps and i % 20 == 0:
            torch.mps.empty_cache()
            gc.collect()
        if i % PROGRESS_EVERY == 0:
            el = time.time() - t0
            print(f"  {i}/{len(items)}  ({el:.0f}s)", flush=True)

    multi_slugs = np.concatenate([base_slugs, np.array(extra_slugs)])
    multi_embs = np.concatenate([base_embs, np.array(extra_embs)], axis=0)

    baseline_index = EmbeddingIndex(np.array(base_slugs), base_embs)
    multi_index = EmbeddingIndex(multi_slugs, multi_embs)

    out_path = config.OUTPUTS_DIR / f"embeddings_{args.backbone}_multiref.npz"
    np.savez(out_path, slugs=multi_slugs, embeddings=multi_embs)
    print(f"Multi-ref индекс сохранён: {out_path} ({multi_embs.shape[0]} векторов, "
          f"{len(set(multi_slugs.tolist()))} уникальных вин)\n")

    # --- гоним одну и ту же query-выборку по обоим индексам ---
    print(f"Оцениваем на {len(query_paths)} query-фото...")
    baseline_hits1 = baseline_hits5 = multi_hits1 = multi_hits5 = 0
    t0 = time.time()
    for i, (true_slug, path) in enumerate(query_paths, start=1):
        with Image.open(path) as img:
            emb = backbone.encode(img.convert("RGB"))

        b_top = baseline_index.search(emb, top_k=5)
        m_top = multi_index.search(emb, top_k=5)
        b_slugs = [r.slug for r in b_top]
        m_slugs = [r.slug for r in m_top]

        if b_slugs and b_slugs[0] == true_slug:
            baseline_hits1 += 1
        if true_slug in b_slugs:
            baseline_hits5 += 1
        if m_slugs and m_slugs[0] == true_slug:
            multi_hits1 += 1
        if true_slug in m_slugs:
            multi_hits5 += 1

        if is_mps and i % 20 == 0:
            torch.mps.empty_cache()
            gc.collect()
        if i % PROGRESS_EVERY == 0:
            el = time.time() - t0
            print(f"  {i}/{len(query_paths)}  ({el:.0f}s, {el / i:.2f}с/фото)", flush=True)

    n = len(query_paths)
    print(f"\n########## Multi-ref эксперимент: {n} запросов, {len(index_extra)} вин с доп. вектором ##########")
    print(f"  baseline (1 эталон/вино):    top1={baseline_hits1/n:.1%}  top5={baseline_hits5/n:.1%}")
    print(f"  multi-ref (2 эталона/вино):  top1={multi_hits1/n:.1%}  top5={multi_hits5/n:.1%}")
    print(f"  дельта top1: {(multi_hits1-baseline_hits1)/n:+.1%}")

    out = {"backbone": args.backbone, "n_queries": n, "n_multiref_wines": len(index_extra),
           "baseline": {"top1": baseline_hits1/n, "top5": baseline_hits5/n},
           "multi_ref": {"top1": multi_hits1/n, "top5": multi_hits5/n}}
    with open(config.OUTPUTS_DIR / f"multiref_experiment_{args.backbone}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: outputs/multiref_experiment_{args.backbone}.json")


if __name__ == "__main__":
    main()
