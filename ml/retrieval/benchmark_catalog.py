"""Бенчмарк, соответствующий целевой метрике кейса.

Организатор считает балл как `(верные ответы / все ответы) * 0.5` на 100 фото
реальных вин. Значит мерить надо accuracy на равномерной выборке каталога,
а не на near-duplicate парах (они всего ~9% позиций, см. docs/findings.md).

Отдельно печатается оценка ожидаемого балла с учётом потолка покрытия: вино
без эталонного фото не может быть найдено в принципе, поэтому реальная
accuracy = accuracy_по_индексу * доля_каталога_в_индексе.

Запуск:
    python -m retrieval.benchmark_catalog --backbone siglip2 --sample 400 --n-aug 2
"""

import argparse
import gc
import json
import random
import time

import torch
from PIL import Image

from data_prep import config
from .augment import simulate_field_photo
from .augment_realistic import simulate_realistic_photo
from .backbones import load_backbone
from .index import EmbeddingIndex

PROGRESS_EVERY = 100

AUGMENTERS = {
    "simple": simulate_field_photo,        # плоская перспектива + блик + блюр
    "realistic": simulate_realistic_photo,  # цилиндр + фон + наклон + оптика
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2", help="чем построен индекс")
    parser.add_argument("--query-backbone", default=None,
                        help="чем кодировать запрос (по умолчанию = --backbone). "
                             "Разные значения позволяют, например, индексировать чистые "
                             "каталожные фото целиком, а на запросе резать этикетку детектором.")
    parser.add_argument("--sample", type=int, default=400, help="сколько позиций каталога взять")
    parser.add_argument("--n-aug", type=int, default=2, help="аугментаций на позицию")
    parser.add_argument("--aug", choices=list(AUGMENTERS), default="realistic")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)
    augment = AUGMENTERS[args.aug]

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    total_catalog = len(records)
    lookup = {r["slug"]: r for r in records if r["photo_file"]}
    coverage = len(lookup) / total_catalog

    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    query_backbone_name = args.query_backbone or args.backbone
    backbone = load_backbone(query_backbone_name)
    is_mps = backbone.device.type == "mps"

    sample_slugs = random.sample(sorted(lookup), min(args.sample, len(lookup)))
    n_queries = len(sample_slugs) * args.n_aug
    print(f"Каталог: {total_catalog}, в индексе: {len(lookup)} ({coverage:.1%})")
    print(f"Индекс: {args.backbone} | запрос: {query_backbone_name}")
    print(f"Выборка: {len(sample_slugs)} позиций x {args.n_aug} аугментаций ({args.aug}) = {n_queries} запросов\n")

    hits_top1 = hits_top5 = 0
    gaps = []
    done = 0
    t0 = time.time()

    for slug in sample_slugs:
        base_img = Image.open(config.UPLOADS_DIR / lookup[slug]["photo_file"])
        for aug_i in range(args.n_aug):
            query = augment(base_img, seed=hash((slug, aug_i)) % (2**31))
            emb = backbone.encode(query)
            results = index.search(emb, top_k=5)
            top_slugs = [r.slug for r in results]
            if top_slugs and top_slugs[0] == slug:
                hits_top1 += 1
            if slug in top_slugs:
                hits_top5 += 1
            if len(results) > 1:
                gaps.append(results[0].score - results[1].score)

            done += 1
            if is_mps and done % 20 == 0:
                torch.mps.empty_cache()
                gc.collect()
            if done % PROGRESS_EVERY == 0:
                el = time.time() - t0
                print(f"  {done}/{n_queries}  top1={hits_top1/done:.1%}  ({el:.0f}s, {el/done:.2f}s/запрос)", flush=True)
        base_img.close()

    acc1 = hits_top1 / n_queries
    acc5 = hits_top5 / n_queries
    avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
    expected = acc1 * coverage

    print(f"\n########## индекс={args.backbone} запрос={query_backbone_name} ##########")
    print(f"  top-1 accuracy (по индексу):  {acc1:.1%}")
    print(f"  top-5 accuracy (по индексу):  {acc5:.1%}")
    print(f"  средний gap(top1,top2):       {avg_gap:.4f}")
    print(f"  покрытие каталога:            {coverage:.1%}")
    print(f"  ОЖИДАЕМАЯ accuracy:           {expected:.1%}  -> {expected*50:.1f} из 50 баллов")

    out = {
        "backbone": args.backbone, "query_backbone": query_backbone_name, "aug": args.aug, "sample": len(sample_slugs), "n_aug": args.n_aug,
        "top1_acc_indexed": acc1, "top5_acc_indexed": acc5, "avg_gap": avg_gap,
        "catalog_coverage": coverage, "expected_accuracy": expected,
    }
    out_path = config.OUTPUTS_DIR / f"benchmark_catalog_{args.backbone}_q{query_backbone_name}_{args.aug}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
