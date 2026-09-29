"""Проверка сценария «на фото нет вина»: насколько кадры без вина далеки от эталонов.

Для каждого фото из папки считается схожесть с ближайшим эталоном каталога —
ровно тем же способом, что в проде (эталоны 1024 + доп. эталоны, запрос 2560).
Рядом печатается, какая доля настоящих фото вина из теста (379 кадров) имеет
схожесть НИЖЕ этого значения: если почти ноль — порог можно ставить безопасно.

    python nowine_check.py --dir negatives                     # адаптер v2 (прод)
    python nowine_check.py --dir negatives --adapter runs/v3/best --index results/v3eval_v3.npz
"""

import argparse
import csv
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps

from common import INSTRUCTION, load_embedder

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic"}


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True, help="папка с фото без вина")
    p.add_argument("--adapter", default=str(HERE / "adapter"))
    p.add_argument("--index", default=None, help="npz с R, E, Q (eval_v3); по умолчанию кеши v2")
    args = p.parse_args()

    refs = read_csv(DATA / "refs.csv")
    slugs = np.array([r["slug"] for r in refs])
    sidx = {s: i for i, s in enumerate(slugs)}
    extras = [e for e in read_csv(DATA / "extra_refs.csv") if e["slug"] in sidx]
    es = np.array([sidx[e["slug"]] for e in extras])
    frames = read_csv(DATA / "frames_v2.csv")
    true = np.array([sidx[f["true_slug"]] for f in frames])
    if args.index:
        b = np.load(args.index)
        R, E, Qpos = b["R"], b["E"], b["Q"]
    else:
        R = np.load(RESULTS / "cache_ref_1024.npz", allow_pickle=True)["ref_emb"]
        E = np.load(RESULTS / "run1_extra_1024.npz", allow_pickle=True)["emb"]
        Qpos = np.load(RESULTS / "run1_q2560.npz", allow_pickle=True)["full"]
    R, E, Qpos = R.astype(np.float32), E.astype(np.float32), Qpos.astype(np.float32)

    def scores(Q):
        s, se = Q @ R.T, Q @ E.T
        for j, k in enumerate(es):
            s[:, k] = np.maximum(s[:, k], se[:, j])
        return s

    pos = scores(Qpos)
    pos_max = pos.max(1)
    pos_ok = pos.argmax(1) == true
    print(f"Фото вина (тест, {len(pos_max)}): схожесть с лучшим эталоном min {pos_max.min():.3f}, "
          f"p1 {np.percentile(pos_max, 1):.3f}, p5 {np.percentile(pos_max, 5):.3f}, медиана {np.median(pos_max):.3f}")

    files = sorted(f for f in Path(args.dir).rglob("*") if f.suffix.lower() in EXT)
    if not files:
        raise SystemExit(f"в {args.dir} нет картинок")
    emb = load_embedder(max_pixels=2560 * 32 * 32)
    from peft import PeftModel
    emb.model = PeftModel.from_pretrained(emb.model, args.adapter).eval()
    Q = []
    for f in files:
        with Image.open(f) as raw:
            img = ImageOps.exif_transpose(raw).convert("RGB")
        conv = emb.format_model_input(image=img, instruction=INSTRUCTION)
        inputs = {k: v.to(emb.model.device) for k, v in emb._preprocess_inputs([conv]).items()}
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            out = emb.model(**inputs, use_cache=False)
        e = emb._pooling_last(out.last_hidden_state, inputs["attention_mask"])
        Q.append(F.normalize(e.float(), dim=-1).cpu().numpy()[0])
    neg = scores(np.stack(Q))

    print(f"\n{'фото без вина':40s}{'схожесть':>10s}{'доля фото вина ниже':>22s}   ближайшие вина")
    for f, s in zip(files, neg):
        top = np.argsort(-s)[:3]
        below = (pos_max < s[top[0]]).mean()
        print(f"{f.name[:40]:40s}{s[top[0]]:>10.3f}{below:>21.1%}    " + ", ".join(f"{slugs[i][:28]} {s[i]:.2f}" for i in top))

    neg_max = neg.max(1)
    print("\nПорог «вина нет»: сколько фото вина потеряем / сколько фото без вина отсечём")
    for thr in (0.55, 0.60, 0.65, 0.69, 0.72, 0.75):
        lost = (pos_max < thr).sum()
        lost_ok = ((pos_max < thr) & pos_ok).sum()
        print(f"  порог {thr:.2f}: фото вина отклонено {lost}/{len(pos_max)} (из них верных {lost_ok}), "
              f"фото без вина отсечено {(neg_max < thr).sum()}/{len(neg_max)}")


if __name__ == "__main__":
    main()
