"""Попарное сравнение результатов hires_eval.py с SigLIP2 (production) на тех же кадрах.

hires_eval.py гоняется на поде и SigLIP не трогает. Ответы SigLIP берутся здесь,
локально, из кеша эмбеддингов запросов — так же, как в verify_on_our_protocol.py.
Кеш строится один раз (~10 минут):  python -m retrieval.cache_query_embeddings

Поле кандидатов — slug-и индекса SigLIP (ответ Qwen берётся первым из его top-5,
попавшим в это поле), кадры с верным вином вне поля исключаются.

    python qwen_finetune/pair_with_siglip.py qwen_finetune/results/hires_rows_1024x1024.json \\
        qwen_finetune/results/hires_rows_2560x2560.json
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

SET_CACHE = {"команды": "own", "организатора": "org"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("rows", nargs="+", help="results/hires_rows_<эталон>x<запрос>.json")
    args = parser.parse_args()

    from data_prep import config
    from retrieval.predict import BACKBONE, PREPROCESS

    data = np.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}.npz")
    slugs = np.array([str(s) for s in data["slugs"]])
    index = data["embeddings"].astype(np.float32)
    index /= np.clip(np.linalg.norm(index, axis=1, keepdims=True), 1e-8, None)
    allowed = set(slugs)

    cached = {}
    for name in ("own", "org"):
        path = config.OUTPUTS_DIR / f"query_embeddings_{name}_{BACKBONE}_{PREPROCESS}.npz"
        if not path.exists():
            raise SystemExit(f"нет {path.name} — сначала: python -m retrieval.cache_query_embeddings")
        blob = np.load(path, allow_pickle=True)
        for image_path, emb in zip(blob["image_paths"], blob["embeddings"]):
            cached[(name, str(image_path))] = emb / max(np.linalg.norm(emb), 1e-8)

    print(f"SigLIP: {BACKBONE}/{PREPROCESS}, индекс {len(slugs)}")
    for rows_path in args.rows:
        rows = json.load(open(rows_path, encoding="utf-8"))["rows"]
        paired = []
        for r in rows:
            emb = cached.get((SET_CACHE[r["set"]], r["key"]))
            if emb is None or r["true"] not in allowed:
                continue
            qwen = next((s for s, _ in r["qwen_top5"] if s in allowed), None)
            paired.append({**r, "qwen": qwen, "siglip": str(slugs[int(np.argmax(index @ emb))])})

        print(f"\n=== {Path(rows_path).name}: кадров в паре {len(paired)} из {len(rows)} ===")
        print(f"{'набор':16s}{'n':>5s}{'SigLIP2':>10s}{'Qwen':>9s}{'испр':>7s}{'слом':>6s}{'порог':>7s}")
        for title in list(SET_CACHE) + ["ВСЕГО"]:
            part = paired if title == "ВСЕГО" else [r for r in paired if r["set"] == title]
            if not part:
                continue
            s = sum(r["siglip"] == r["true"] for r in part)
            q = sum(r["qwen"] == r["true"] for r in part)
            fixed = sum(r["qwen"] == r["true"] and r["siglip"] != r["true"] for r in part)
            broken = sum(r["qwen"] != r["true"] and r["siglip"] == r["true"] for r in part)
            thr = 2 * (fixed + broken) ** 0.5
            verdict = "ЗНАЧИМО" if fixed + broken and abs(fixed - broken) >= thr else "шум"
            print(f"{title:16s}{len(part):>5d}{s / len(part):>9.1%}{q / len(part):>9.1%}"
                  f"{fixed:>7d}{broken:>6d}{thr:>7.1f}  {verdict}")


if __name__ == "__main__":
    main()
