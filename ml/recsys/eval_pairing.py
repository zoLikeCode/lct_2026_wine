"""Offline check of the recommender's wine similarity on real portal labels.

There are no user logs yet, so we test the part everything else rests on: does "similar
wine" mean similar in the way that matters? The portal tags each wine with the food it goes
with (34 categories, set by the producers/editors, not by us). For every wine we hide its
own tags, find its nearest neighbours with the recommender's similarity *built without the
dish block*, and predict its dishes from theirs. Good taste similarity → right dishes.

Leak guard: a wine's duplicates and re-vintages carry copy-pasted tags, so neighbours from
the same duplicate group are always excluded; `--strict` also excludes the same producer.

    python ml/recsys/eval_pairing.py            # all variants
    python ml/recsys/eval_pairing.py --strict
"""
import argparse
import collections
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'backend'))
sys.path.insert(0, str(HERE))
from catalog_cards import load_cards  # noqa: E402
from recsys import Recommender  # noqa: E402

ZERO = {'style': 0, 'grape': 0, 'text': 0, 'dish': 0, 'region': 0, 'winery': 0, 'body': 0}
VARIANTS = {
    'style only': dict(ZERO, style=1),
    'style+grape': dict(ZERO, style=1, grape=0.6),
    'style+grape+text': dict(ZERO, style=1, grape=0.6, text=1.0),
    'full (no dish)': {},
}


def sim_matrix(rec):
    n = len(rec.slugs)
    m = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        m[i] = rec.sims_to(i)
    return m


def evaluate(cards, blocks, k, strict):
    rec = Recommender(cards, blocks=blocks, use_dishes=False)
    slugs = rec.slugs
    dishes = sorted({d for s in slugs for d in cards[s]['pairings']})
    col = {d: j for j, d in enumerate(dishes)}
    y = np.zeros((len(slugs), len(dishes)), dtype=np.float32)
    for i, s in enumerate(slugs):
        for d in cards[s]['pairings']:
            y[i, col[d]] = 1
    labelled = y.sum(1) > 0
    sim = sim_matrix(rec)
    group = np.array([hash(g) for g in rec.dupe])
    winery = np.array([hash(g[0]) for g in rec.dupe])
    blocked = group[:, None] == group[None, :]
    if strict:
        blocked |= winery[:, None] == winery[None, :]
    sim[blocked] = -1
    sim[:, ~labelled] = -1  # only wines with tags can vote
    pred = np.zeros_like(y)
    for i in range(len(slugs)):
        nn = np.argpartition(-sim[i], k)[:k]
        w = np.clip(sim[i, nn], 0, None)
        pred[i] = (w[:, None] * y[nn]).sum(0) / (w.sum() + 1e-9)
    return y[labelled], pred[labelled], dishes


def metrics(y, p):
    ap, p3, r3 = [], [], []
    for t, s in zip(y, p):
        order = np.argsort(-s, kind='stable')
        hits = t[order]
        cum = np.cumsum(hits)
        ap.append(float((hits * cum / np.arange(1, len(hits) + 1)).sum() / hits.sum()))
        p3.append(hits[:3].mean())
        r3.append(hits[:3].sum() / hits.sum())
    aucs = []
    for j in range(y.shape[1]):
        t = y[:, j]
        if 20 <= t.sum() <= len(t) - 20:
            ranks = np.argsort(np.argsort(p[:, j], kind='stable')) + 1
            pos = t == 1
            aucs.append((ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum()))
    return {'mAP': np.mean(ap), 'P@3': np.mean(p3), 'R@3': np.mean(r3), 'AUC': np.mean(aucs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--k', type=int, default=40)
    ap.add_argument('--strict', action='store_true', help='also exclude neighbours of the same producer')
    args = ap.parse_args()
    cards = load_cards()
    rows = []
    y, _, dishes = evaluate(cards, VARIANTS['style only'], args.k, args.strict)
    prior = np.tile(y.mean(0), (len(y), 1))
    rows.append(('popular dishes (no model)', metrics(y, prior)))
    rng = np.random.default_rng(0)
    rows.append(('random', metrics(y, rng.random(y.shape))))
    for name, blocks in VARIANTS.items():
        y, p, _ = evaluate(cards, blocks, args.k, args.strict)
        rows.append((name, metrics(y, p)))
    print(f'{len(y)} wines with portal tags, {len(dishes)} dish categories, k={args.k}, '
          f'neighbours from same producer {"excluded" if args.strict else "allowed"}\n')
    print(f'{"variant":28s} {"mAP":>6s} {"P@3":>6s} {"R@3":>6s} {"AUC":>6s}')
    for name, m in rows:
        print(f'{name:28s} {m["mAP"]:6.3f} {m["P@3"]:6.3f} {m["R@3"]:6.3f} {m["AUC"]:6.3f}')


if __name__ == '__main__':
    main()
