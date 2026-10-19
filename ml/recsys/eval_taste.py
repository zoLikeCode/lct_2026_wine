"""Does the sommelier's taste profile beat the rule-based one?

Only wines the model has processed are compared, and only among themselves, so both sources
describe exactly the same wines. Same task as eval_pairing.py: hide a wine's portal dish tags,
predict them from its nearest neighbours, neighbours from the same producer or duplicate group
excluded. Two representations: taste only (style + taste axes + aromas) — isolates the profile —
and the full one the app uses.

    python ml/recsys/eval_taste.py                       # backend/content/wine_taste.json
    python ml/recsys/eval_taste.py --taste path/to.json
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'backend'))
sys.path.insert(0, str(HERE))
from catalog_cards import load_cards  # noqa: E402
from eval_pairing import ZERO, metrics  # noqa: E402
from recsys import Recommender  # noqa: E402
from taste import AXES, load_profiles, rules_profile  # noqa: E402


def predict(rec, k):
    n = len(rec.slugs)
    dishes = sorted({d for s in rec.slugs for d in rec.cards[s]['pairings']})
    col = {d: j for j, d in enumerate(dishes)}
    y = np.zeros((n, len(dishes)), dtype=np.float32)
    for i, s in enumerate(rec.slugs):
        for d in rec.cards[s]['pairings']:
            y[i, col[d]] = 1
    sim = np.array([rec.sims_to(i) for i in range(n)], dtype=np.float32)
    winery = np.array([hash(g[0]) for g in rec.dupe])
    group = np.array([hash(g) for g in rec.dupe])
    sim[(winery[:, None] == winery[None, :]) | (group[:, None] == group[None, :])] = -1
    labelled = y.sum(1) > 0
    sim[:, ~labelled] = -1
    pred = np.zeros_like(y)
    for i in range(n):
        nn = np.argpartition(-sim[i], k)[:k]
        w = np.clip(sim[i, nn], 0, None)
        pred[i] = (w[:, None] * y[nn]).sum(0) / (w.sum() + 1e-9)
    return metrics(y[labelled], pred[labelled])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--taste', default=str(HERE.parents[1] / 'backend/content/wine_taste.json'))
    ap.add_argument('--k', type=int, default=20)
    args = ap.parse_args()
    model = json.loads(Path(args.taste).read_text())['wines']
    cards = {s: c for s, c in load_cards().items() if s in model and c['pairings']}
    rules = {s: rules_profile(c) for s, c in cards.items()}
    merged = load_profiles(cards, args.taste)
    print(f'{len(cards)} вин с профилем от модели и разметкой блюд; k={args.k}, своя винодельня исключена\n')

    agree = {a: np.corrcoef([rules[s][a] for s in cards], [merged[s][a] for s in cards])[0, 1] for a in AXES}
    jac = np.mean([len(set(rules[s]['aromas']) & set(merged[s]['aromas'])) /
                   max(1, len(set(rules[s]['aromas']) | set(merged[s]['aromas']))) for s in cards])
    print('согласие правил и модели: ' + ', '.join(f'{a} r={v:.2f}' for a, v in agree.items()) +
          f'; ароматы Жаккар {jac:.2f}\n')

    taste_only = dict(ZERO, style=1, taste=1, aroma=1)
    print(f'{"представление":34s} {"mAP":>6s} {"P@3":>6s} {"R@3":>6s} {"AUC":>6s}')
    for name, blocks in (('только стиль', dict(ZERO, style=1)), ('стиль + вкус', taste_only), ('полное', {})):
        for src, prof in (('правила', rules), ('модель', merged)):
            m = predict(Recommender(cards, blocks=blocks, use_dishes=False, profiles=prof), args.k)
            label = f'{name} ({src})' if name != 'только стиль' else name
            print(f'{label:34s} ' + ' '.join(f'{v:6.3f}' for v in m.values()))
            if name == 'только стиль':
                break


if __name__ == '__main__':
    main()
