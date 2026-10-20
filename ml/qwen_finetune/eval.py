"""Оценка Qwen3-VL-Embedding (без обучения или с LoRA-адаптером).

Строит индекс по эталонам (по одному на slug) и считает top-1/top-5 на:
  val   — отложенные доп. фото каталога (по ним выбирается чекпоинт);
  field — 56 размеченных фото организаторов (главная цифра);
  own   — собственные полевые фото.

Точка отсчёта для сравнения (SigLIP2 so400m 512px, тот же индекс):
  field 82.1% top-1 / 92.9% top-5, own 91.1% / 96.8%.

Запуск:
  python eval.py                               # без обучения
  python eval.py --adapter runs/exp1/best      # после обучения
  python eval.py --adapter runs/exp1/best --save-index   # + индекс для сервиса
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from common import (DATA, DEFAULT_MAX_PIXELS, encode_paths, load_embedder, load_meta,
                    load_query_set, load_references, rank_metrics)


def attach_adapter(embedder, adapter: str) -> None:
    from peft import PeftModel
    embedder.model = PeftModel.from_pretrained(embedder.model, adapter)
    embedder.model.eval()


def evaluate(embedder, sets: list[str], batch_size: int, ref_emb=None, ref_slugs=None,
             show_errors: int = 0) -> dict:
    meta = load_meta()
    if ref_emb is None:
        ref_slugs, ref_paths = load_references()
        ref_emb = encode_paths(embedder, ref_paths, batch_size, desc="эталоны")
    results = {}
    for name in sets:
        rows = load_query_set(name)
        t0 = time.time()
        q = encode_paths(embedder, [r["path"] for r in rows], batch_size, desc=name)
        m, tops = rank_metrics(q, ref_emb, ref_slugs, [r["slug"] for r in rows])
        m["sec_per_img"] = round((time.time() - t0) / max(len(rows), 1), 3)
        results[name] = m
        print(f"  {name:5s}: n={m['n']:4d}  top1 {m['top1']:.1%}  top5 {m['top5']:.1%}"
              f"  (нет в индексе: {m['not_in_index']})", flush=True)
        if show_errors:
            errs = [(r, t) for r, t in zip(rows, tops) if t[0][0] != r["slug"]]
            for r, t in errs[:show_errors]:
                rank = next((i + 1 for i, (s, _) in enumerate(t) if s == r["slug"]), None)
                want = meta.get(r["slug"], {}).get("name", "")
                got = meta.get(t[0][0], {}).get("name", "")
                print(f"      {Path(r['path']).name[:40]:42s} {'место ' + str(rank) if rank else 'нет в top5'}"
                      f" | ожидали: {want[:35]} | выдали: {got[:35]} ({t[0][1]:.3f})")
    return {"results": results, "ref_emb": ref_emb, "ref_slugs": ref_slugs}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", default=None, help="папка LoRA-адаптера (runs/<exp>/best)")
    p.add_argument("--sets", default="val,field,own")
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    p.add_argument("--errors", type=int, default=15, help="сколько ошибок печатать на выборку")
    p.add_argument("--save-index", action="store_true",
                   help="сохранить индекс в формате retrieval/index.py (slugs, embeddings)")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    embedder = load_embedder(max_pixels=args.max_pixels)
    tag = "zeroshot"
    if args.adapter:
        attach_adapter(embedder, args.adapter)
        tag = Path(args.adapter).parent.name + "_" + Path(args.adapter).name
    print(f"модель: {tag}, max_pixels={args.max_pixels}")

    res = evaluate(embedder, [s.strip() for s in args.sets.split(",") if s.strip()],
                   args.batch_size, show_errors=args.errors)

    out = Path(args.out) if args.out else Path("results")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"eval_{tag}.json").write_text(json.dumps(res["results"], ensure_ascii=False, indent=2))
    if args.save_index:
        path = out / f"embeddings_qwen3vl8b_{tag}.npz"
        np.savez(path, slugs=np.array(res["ref_slugs"]), embeddings=res["ref_emb"].numpy().astype(np.float32))
        print("индекс:", path)
    print("сохранено:", out / f"eval_{tag}.json")


if __name__ == "__main__":
    main()
