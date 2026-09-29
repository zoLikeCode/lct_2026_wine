"""Расширение покрытия каталога за счёт ~4000 фото из uploads/, которые ни к
одной позиции не привязаны.

Зачем это нужно (см. docs/findings.md): вино без эталонного фото не может быть
возвращено индексом вообще, поэтому непокрытые позиции — жёсткий потолок
accuracy. Плюс дополнительные ракурсы уже известных вин делают retrieval
устойчивее к тому, что реальный запрос снят под другим углом.

Два независимых канала:
  image->image  каждое свободное фото сравнивается с эталонами; близкое
                совпадение = скорее всего другой ракурс того же вина;
  text->image   для позиций БЕЗ фото берётся их текстовое описание и ищется
                среди свободных фото (SigLIP2 мультимодален, картинки и текст
                живут в одном пространстве).

Запуск:
    python -m retrieval.expand_coverage --backbone siglip2 --limit 4000
"""

import argparse
import gc
import json
import re
import time

import numpy as np
import torch
from PIL import Image

from data_prep import config
from data_prep.slug_matching import UploadsIndex, to_base
from .backbones import load_backbone
from .index import EmbeddingIndex

BATCH_SIZE = 16
PROGRESS_EVERY = 400


def collect_free_photos(index: UploadsIndex, used_bases: set) -> list[str]:
    """Базы, не привязанные ни к одной позиции и не размеченные как «не вино»."""
    free = []
    for base in index.base_to_files:
        if base in used_bases or index.is_confirmed_not_wine(base):
            continue
        free.append(base)
    return sorted(free)


def embed_bases(backbone, index: UploadsIndex, bases: list[str]) -> tuple[list[str], np.ndarray]:
    kept, vectors = [], []
    is_mps = backbone.device.type == "mps"
    t0 = time.time()
    for i in range(0, len(bases), BATCH_SIZE):
        chunk = bases[i:i + BATCH_SIZE]
        images, ok = [], []
        for base in chunk:
            try:
                images.append(Image.open(config.UPLOADS_DIR / index.pick_best_file(base)))
                ok.append(base)
            except Exception:
                continue
        if not images:
            continue
        vectors.append(backbone.encode_batch(images))
        kept.extend(ok)
        for im in images:
            im.close()
        if is_mps:
            torch.mps.empty_cache()
        gc.collect()
        if i % PROGRESS_EVERY == 0 and i:
            el = time.time() - t0
            print(f"  {i}/{len(bases)}  ({el:.0f}s, {el/i:.2f}s/фото)", flush=True)
    return kept, np.concatenate(vectors, axis=0) if vectors else np.zeros((0, 768))


def build_catalog_text(record: dict) -> str:
    parts = [record["name"], record["winery"], record["grape"], record["category_color"]]
    text = " ".join(p for p in parts if p)
    return re.sub(r"\s+", " ", text).strip()[:180]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="siglip2")
    parser.add_argument("--limit", type=int, default=4000, help="максимум свободных фото для эмбеддинга")
    parser.add_argument("--alt-threshold", type=float, default=0.92, help="порог image->image для «другого ракурса»")
    parser.add_argument("--text-threshold", type=float, default=0.10, help="порог text->image")
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    resolved = [r for r in records if r["photo_file"]]
    unresolved = [r for r in records if not r["photo_file"]]
    used_bases = {to_base(r["photo_file"]) for r in resolved}

    uploads = UploadsIndex.build()
    free_bases = collect_free_photos(uploads, used_bases)[: args.limit]
    print(f"Свободных фото (не привязаны, не размечены как «не вино»): {len(free_bases)}")

    backbone = load_backbone(args.backbone)
    print(f"Эмбеддим свободные фото на {backbone.device}...")
    free_bases, free_vecs = embed_bases(backbone, uploads, free_bases)
    print(f"Получено векторов: {free_vecs.shape}\n")

    # --- канал 1: image -> image, поиск других ракурсов известных вин ---
    ref_index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{args.backbone}.npz")
    alternates = []
    for base, vec in zip(free_bases, free_vecs):
        top = ref_index.search(vec, top_k=1)
        if top and top[0].score >= args.alt_threshold:
            alternates.append({"base": base, "slug": top[0].slug, "score": round(top[0].score, 4)})
    print(f"[image->image] кандидатов в «другой ракурс известного вина» "
          f"(sim >= {args.alt_threshold}): {len(alternates)}")

    # --- канал 2: text -> image, поиск фото для позиций без фото ---
    text_hits = []
    if unresolved and free_vecs.shape[0]:
        texts = [build_catalog_text(r) for r in unresolved]
        print(f"\n[text->image] ищем фото для {len(texts)} позиций без фото...")
        text_vecs = []
        for i in range(0, len(texts), BATCH_SIZE):
            text_vecs.append(backbone.encode_text(texts[i:i + BATCH_SIZE]))
        text_vecs = np.concatenate(text_vecs, axis=0)

        sims = text_vecs @ free_vecs.T
        for row, record in zip(sims, unresolved):
            best = int(np.argmax(row))
            score = float(row[best])
            if score >= args.text_threshold:
                text_hits.append({
                    "slug": record["slug"], "name": record["name"],
                    "base": free_bases[best], "score": round(score, 4),
                })
        text_hits.sort(key=lambda x: -x["score"])
        print(f"[text->image] кандидатов выше порога {args.text_threshold}: {len(text_hits)}")

    out = {
        "n_free_photos": len(free_bases),
        "alternates": sorted(alternates, key=lambda x: -x["score"]),
        "text_matches": text_hits,
    }
    out_path = config.OUTPUTS_DIR / f"coverage_expansion_{args.backbone}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\nТоп кандидатов [image->image] (другой ракурс):")
    for a in out["alternates"][:10]:
        print(f"  {a['score']:.3f}  {a['slug'][:50]:52s} <- {a['base'][:44]}")
    print("\nТоп кандидатов [text->image] (фото для позиции без фото):")
    for t in text_hits[:10]:
        print(f"  {t['score']:.3f}  {t['name'][:34]:36s} <- {t['base'][:44]}")
    print(f"\nСохранено: {out_path}")


if __name__ == "__main__":
    main()
