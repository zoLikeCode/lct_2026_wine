"""Интернет-фото в реальной обстановке как ДОП. ЭТАЛОНЫ (без дообучения) — помогает ли?

Дообучение на этих 383 фото (v3) не дало прироста, но несколько эталонов на вино дали +3.4пп
(run1). Здесь те же фото просто добавляются в индекс к своим винам. Кадры теста — из кеша
прогонов (results/run1_q2560.npz), кодируются только интернет-фото (1024 токена, ~1 мин).

    python inet_refs_eval.py                 # сравнение: прод-индекс vs + интернет-фото
    python inet_refs_eval.py --adopt         # если лучше: дописать их в data/extra_refs.csv

После --adopt: python build_index.py (закодирует только новое) и перезапустить сервис.
"""

import argparse
import csv
import hashlib
from pathlib import Path

import numpy as np

from common import DATA

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def per_wine_scores(Q, vecs, owner, n):
    s = Q @ vecs.T
    out = np.full((len(Q), n), -1.0, dtype=np.float32)
    for j in range(vecs.shape[0]):
        np.maximum(out[:, owner[j]], s[:, j], out=out[:, owner[j]])
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", default=str(HERE / "adapter"))
    p.add_argument("--adopt", action="store_true")
    args = p.parse_args()

    idx = np.load(RESULTS / "index_prod.npz")
    vecs, owner, slugs = idx["vecs"], idx["owner"], [str(s) for s in idx["slugs"]]
    sidx = {s: i for i, s in enumerate(slugs)}
    frames = read_csv(DATA / "frames_v2.csv")
    qc = np.load(RESULTS / "run1_q2560.npz", allow_pickle=True)
    assert list(qc["keys"]) == [f["key"] for f in frames]
    Q = qc["full"].astype(np.float32)
    true = np.array([sidx[f["true_slug"]] for f in frames])

    frame_hash = {hashlib.sha256((DATA / f["path"]).read_bytes()).hexdigest() for f in frames}
    inet = [r for r in read_csv(DATA / "inet_queries.csv") if r["slug"] in sidx]
    inet = [r for r in inet if hashlib.sha256((DATA / r["path"]).read_bytes()).hexdigest() not in frame_hash]
    print(f"интернет-фото: {len(inet)} ({len({r['slug'] for r in inet})} вин), совпадений с кадрами теста нет")

    cache = RESULTS / "inet_1024.npz"
    if cache.exists():
        V = np.load(cache)["emb"]
    else:
        from peft import PeftModel
        from common import encode_paths, load_embedder
        emb = load_embedder(max_pixels=1024 * 32 * 32)
        emb.model = PeftModel.from_pretrained(emb.model, args.adapter).eval()
        V = encode_paths(emb, [r["path"] for r in inet], batch_size=16, desc="интернет-фото").numpy()
        np.savez(cache, emb=V, paths=np.array([r["path"] for r in inet]))
    V = V.astype(np.float32)

    base = per_wine_scores(Q, vecs, owner, len(slugs))
    new = per_wine_scores(Q, np.concatenate([vecs, V]),
                          np.concatenate([owner, [sidx[r["slug"]] for r in inet]]), len(slugs))
    ok_b, ok_n = base.argmax(1) == true, new.argmax(1) == true
    t5 = lambda s: np.mean([true[i] in np.argsort(-s[i])[:5] for i in range(len(true))])
    fx, br = int((ok_n & ~ok_b).sum()), int((ok_b & ~ok_n).sum())
    thr = 2 * (fx + br) ** 0.5
    verdict = "без изменений" if fx + br == 0 else (("ЗНАЧИМО ЛУЧШЕ" if fx > br else "ЗНАЧИМО ХУЖЕ") if abs(fx - br) >= thr else "шум")
    covered = np.isin(true, [sidx[r["slug"]] for r in inet])
    print(f"\n{'':28s}{'top1':>8s}{'top5':>8s}{'вина с инет-фото':>18s}")
    print(f"{'прод-индекс':28s}{ok_b.mean():>8.1%}{t5(base):>8.1%}{ok_b[covered].mean():>18.1%}")
    print(f"{'+ интернет-фото':28s}{ok_n.mean():>8.1%}{t5(new):>8.1%}{ok_n[covered].mean():>18.1%}")
    print(f"исправлено {fx} / сломано {br}, порог {thr:.1f} -> {verdict}")
    for i in np.where(ok_n != ok_b)[0]:
        mark = "+" if ok_n[i] else "-"
        print(f"  {mark} {frames[i]['true_slug'][:45]:45s} было {slugs[base[i].argmax()][:32]:32s} стало {slugs[new[i].argmax()][:32]}")

    if args.adopt:
        ex = read_csv(DATA / "extra_refs.csv")
        have = {e["path"] for e in ex}
        add = [r for r in inet if r["path"] not in have]
        with open(DATA / "extra_refs.csv", "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            for r in add:
                w.writerow([r["slug"], r["path"], "inet"])
        print(f"\nдописано в extra_refs.csv: {len(add)}. Дальше: python build_index.py")


if __name__ == "__main__":
    main()
