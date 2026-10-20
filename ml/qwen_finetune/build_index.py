"""Индекс для сервиса: векторы всех эталонов (основной + доп. фото каталога) одним файлом.

Прод-конфигурация (STATUS_gpu_runs_20260929.md): адаптер v2, эталоны на 1024 визуальных
токенах, у вина может быть несколько эталонов — сервис берёт максимум схожести по вину.

Векторы берутся из кешей прошлых прогонов (results/cache_ref_1024.npz и
results/run1_extra_1024.npz) — покадрово те же, что дали 93.4%; моделью кодируется
только то, чего в кешах нет (новые эталоны, новые доп. фото). Без кешей — всё (~10 мин на A100).

    python build_index.py                  # -> results/index_prod.npz
    python build_index.py --reencode       # пересчитать, не глядя на кеши

Эталоны из ref_conflicts.csv с action=drop_main не попадают в индекс: это байт-идентичное
фото другого вина (проверено глазами по этикетке).

Добавить новые фото к вину без переобучения: дописать строки в data/extra_refs.csv
(slug,path,kind) или новый эталон в data/refs.csv и просто запустить снова — закодируется
только новое.
"""

import argparse
import csv
from pathlib import Path

import numpy as np

from common import DATA

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CONFLICTS = HERE / "ref_conflicts.csv"


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def encoder(adapter):
    from peft import PeftModel
    from common import load_embedder

    emb = load_embedder(max_pixels=1024 * 32 * 32)
    emb.model = PeftModel.from_pretrained(emb.model, adapter).eval()
    return emb


def encode_list(emb, paths, bs, desc):
    from common import encode_paths
    return encode_paths(emb, paths, batch_size=bs, desc=desc).numpy().astype(np.float32)


def build(refs, extras, adapter, bs, reencode):
    """Векторы берутся из кешей прогонов; то, чего в кешах нет (новые эталоны и доп. фото),
    кодируется моделью. С --reencode кодируется всё."""
    cached_r, cached_e = {}, {}
    if not reencode and (RESULTS / "cache_ref_1024.npz").exists():
        c = np.load(RESULTS / "cache_ref_1024.npz", allow_pickle=True)
        cached_r = dict(zip(c["ref_slugs"], c["ref_emb"].astype(np.float32)))
    if not reencode and (RESULTS / "run1_extra_1024.npz").exists():
        c = np.load(RESULTS / "run1_extra_1024.npz", allow_pickle=True)
        cached_e = dict(zip(c["paths"], c["emb"].astype(np.float32)))
    new_r = [r for r in refs if r["slug"] not in cached_r]
    new_e = [e for e in extras if e["path"] not in cached_e]
    print(f"из кешей: эталонов {len(refs) - len(new_r)}, доп. {len(extras) - len(new_e)}; "
          f"кодируем: эталонов {len(new_r)}, доп. {len(new_e)}")
    if new_r or new_e:
        emb = encoder(adapter)
        if new_r:
            cached_r.update(zip([r["slug"] for r in new_r], encode_list(emb, [r["path"] for r in new_r], bs, "эталоны")))
        if new_e:
            cached_e.update(zip([e["path"] for e in new_e], encode_list(emb, [e["path"] for e in new_e], bs, "доп. эталоны")))
    R = np.stack([cached_r[r["slug"]] for r in refs])
    E = np.stack([cached_e[e["path"]] for e in extras]) if extras else np.zeros((0, R.shape[1]), np.float32)
    return R, E, (len(new_r) + len(new_e)) == 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", default=str(HERE / "adapter"))
    p.add_argument("--out", default=str(RESULTS / "index_prod.npz"))
    p.add_argument("--reencode", action="store_true")
    p.add_argument("--bs", type=int, default=16)
    args = p.parse_args()

    refs = read_csv(DATA / "refs.csv")
    slugs = [r["slug"] for r in refs]
    sidx = {s: i for i, s in enumerate(slugs)}
    extras = [e for e in read_csv(DATA / "extra_refs.csv") if e["slug"] in sidx]

    R, E, use_cache = build(refs, extras, args.adapter, args.bs, args.reencode)

    # Основной эталон, который на самом деле фото другого вина (байт-идентичен эталону соседа),
    # выкидываем: иначе при равенстве скоров побеждает сосед по алфавиту, и настоящее вино
    # не находится даже по собственному фото каталога. Вино остаётся с доп. эталонами, если есть.
    drop = {r["slug"] for r in read_csv(CONFLICTS) if r["action"] == "drop_main"} if CONFLICTS.exists() else set()
    keep_main = [i for i, s in enumerate(slugs) if s not in drop]
    vecs = np.concatenate([R[keep_main], E]).astype(np.float32)
    owner = np.array(keep_main + [sidx[e["slug"]] for e in extras], dtype=np.int32)
    orphans = sorted(set(range(len(slugs))) - set(owner.tolist()))
    print(f"выкинуто чужих основных эталонов: {len(drop)}; вин без единого эталона (нужны фото): {len(orphans)}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.out, vecs=vecs, owner=owner, slugs=np.array(slugs),
             source=np.array(["cache" if use_cache else "reencode"]))
    print(f"индекс: {len(slugs)} вин, {len(vecs)} эталонов ({len(extras)} доп.), "
          f"источник {'кеши' if use_cache else 'кеши + закодировано новое'} -> {args.out}")


if __name__ == "__main__":
    main()
