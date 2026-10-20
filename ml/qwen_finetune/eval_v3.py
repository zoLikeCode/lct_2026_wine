"""Проверка адаптера v3 на тесте (379 кадров) попарно против v2 — ровно как в проде.

Прод-конфигурация: эталоны 1024 токена + доп. эталоны (максимум схожести по вину),
запрос — целый кадр на 2560 токенах. Для v2 векторы берутся из кешей прошлых
прогонов (cache_ref_1024.npz, run1_extra_1024.npz, run1_q2560.npz), для v3
считаются тем же кодом тем же способом (эмбеддер с max_pixels 1024 для эталонов,
2560 для кадров).

Считается: top-1/top-5 по кадрам и по винам (одно вино = один голос, доля
верных кадров вина), попарно исправлено/сломано, правило §11.37.

    python eval_v3.py --adapter runs/v3/best 2>&1 | tee results/eval_v3.log
"""

import argparse
import csv
import gc
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps

from common import INSTRUCTION, load_embedder, load_image

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_frame(path):
    with Image.open(path) as raw:
        return ImageOps.exif_transpose(raw).convert("RGB")


def build(tokens, adapter):
    emb = load_embedder(max_pixels=tokens * 32 * 32)
    from peft import PeftModel
    emb.model = PeftModel.from_pretrained(emb.model, adapter).eval()
    return emb


@torch.no_grad()
def encode(emb, images):
    convs = [emb.format_model_input(image=im, instruction=INSTRUCTION) for im in images]
    inputs = {k: v.to(emb.model.device) for k, v in emb._preprocess_inputs(convs).items()}
    with torch.autocast("cuda", dtype=torch.bfloat16):
        out = emb.model(**inputs, use_cache=False)
    e = emb._pooling_last(out.last_hidden_state, inputs["attention_mask"])
    return F.normalize(e.float(), dim=-1).cpu().numpy()


def vectors(adapter, tag, refs, extras, frames, bs):
    cache = RESULTS / f"v3eval_{tag}.npz"
    if cache.exists():
        b = np.load(cache)
        print(f"[{tag}] векторы из кеша")
        return b["R"], b["E"], b["Q"]
    emb = build(1024, adapter)
    R = np.concatenate([encode(emb, [load_image(DATA / r["path"]) for r in refs[i:i + bs]]) for i in range(0, len(refs), bs)])
    print(f"[{tag}] эталоны {R.shape}", flush=True)
    E = np.concatenate([encode(emb, [load_image(DATA / e["path"]) for e in extras[i:i + bs]]) for i in range(0, len(extras), bs)])
    print(f"[{tag}] доп. эталоны {E.shape}", flush=True)
    del emb; gc.collect(); torch.cuda.empty_cache()
    emb = build(2560, adapter)
    Q = np.stack([encode(emb, [load_frame(DATA / f["path"])])[0] for f in frames])
    print(f"[{tag}] кадры {Q.shape}", flush=True)
    del emb; gc.collect(); torch.cuda.empty_cache()
    np.savez(cache, R=R, E=E, Q=Q)
    return R, E, Q


def v2_vectors(refs, extras, frames):
    R = np.load(RESULTS / "cache_ref_1024.npz", allow_pickle=True)
    E = np.load(RESULTS / "run1_extra_1024.npz", allow_pickle=True)
    Q = np.load(RESULTS / "run1_q2560.npz", allow_pickle=True)
    assert list(R["ref_slugs"]) == [r["slug"] for r in refs]
    assert list(E["paths"]) == [e["path"] for e in extras]
    assert list(Q["keys"]) == [f["key"] for f in frames]
    return R["ref_emb"].astype(np.float32), E["emb"].astype(np.float32), Q["full"].astype(np.float32)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", default=str(HERE / "runs/v3/best"))
    p.add_argument("--tag", default="v3")
    p.add_argument("--bs", type=int, default=16)
    args = p.parse_args()
    RESULTS.mkdir(exist_ok=True)

    refs = read_csv(DATA / "refs.csv")
    frames = read_csv(DATA / "frames_v2.csv")
    extras_all = read_csv(DATA / "extra_refs.csv")
    slugs = np.array([r["slug"] for r in refs])
    sidx = {s: i for i, s in enumerate(slugs)}
    extras = [e for e in extras_all if e["slug"] in sidx]
    es = np.array([sidx[e["slug"]] for e in extras])
    true = np.array([sidx[f["true_slug"]] for f in frames])
    sets = sorted({f["set"] for f in frames})

    def scores(R, E, Q, multi):
        s = Q @ R.T
        if multi:
            se = Q @ E.T
            for j, k in enumerate(es):
                s[:, k] = np.maximum(s[:, k], se[:, j])
        return s

    V = {"v2": v2_vectors(refs, extras, frames), args.tag: vectors(args.adapter, args.tag, refs, extras, frames, args.bs)}
    S = {}
    for name, (R, E, Q) in V.items():
        S[f"{name}, один эталон"] = scores(R, E, Q, False)
        S[f"{name}, доп. эталоны"] = scores(R, E, Q, True)
    base_key = "v2, доп. эталоны"
    pred = {k: s.argmax(1) for k, s in S.items()}
    top5 = {k: np.argsort(-s, axis=1)[:, :5] for k, s in S.items()}
    base_ok = pred[base_key] == true

    def per_wine(ok):
        by = {}
        for w, o in zip(true, ok):
            by.setdefault(w, []).append(o)
        return np.mean([np.mean(v) for v in by.values()])

    print(f"\n=== Тест: {len(frames)} кадров, {len(set(true))} вин ===")
    print(f"{'вариант':26s}" + "".join(f"{s[:13]:>15s}" for s in sets) + f"{'top1':>8s}{'top5':>8s}{'по винам':>10s}   исправлено/сломано vs v2 прод")
    out = {}
    for k in S:
        ok = pred[k] == true
        t5 = np.array([true[i] in top5[k][i] for i in range(len(true))])
        fx, br = int((ok & ~base_ok).sum()), int((~ok & base_ok).sum())
        thr = 2 * (fx + br) ** 0.5
        verdict = "—" if k == base_key else ("без изменений" if fx + br == 0 else
                                            (("ЗНАЧИМО ЛУЧШЕ" if fx > br else "ЗНАЧИМО ХУЖЕ") if abs(fx - br) >= thr else "шум"))
        cells = "".join(f"{ok[[f['set'] == s for f in frames]].mean():>15.1%}" for s in sets)
        tail = "" if k == base_key else f"{fx}/{br}  порог {thr:.1f} -> {verdict}"
        print(f"{k:26s}{cells}{ok.mean():>8.1%}{t5.mean():>8.1%}{per_wine(ok):>10.1%}   {tail}")
        out[k] = {"top1": float(ok.mean()), "top5": float(t5.mean()), "per_wine": float(per_wine(ok)),
                  "fixed": [frames[i]["key"] for i in np.where(ok & ~base_ok)[0]],
                  "broken": [[frames[i]["key"], str(slugs[pred[k][i]])] for i in np.where(~ok & base_ok)[0]]}

    k = f"{args.tag}, доп. эталоны"
    print(f"\n{k} против v2 прода:")
    for key in out[k]["fixed"]:
        print(f"   + {key[-64:]}")
    for key, pr in out[k]["broken"]:
        print(f"   - {key[-50:]:52s} -> {pr[:46]}")
    json.dump(out, open(RESULTS / f"eval_{args.tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Сохранено: results/eval_{args.tag}.json")


if __name__ == "__main__":
    main()
