"""Реранкер Qwen3-VL-Reranker поверх Qwen3-VL-Embedding + LoRA — двусторонняя проверка.

Зачем. Почти все оставшиеся ошибки эмбеддера — соседи по линейке одной винодельни,
различающиеся мелким текстом сорта (KAFFA Каберне Фран / Каберне Совиньон,
Криница Рислинг / Вионье), и верный ответ почти всегда в top-5 (99.2%).
Кросс-энкодер видит фото запроса и кандидата В ОДНОМ контексте и может сверить
надпись с названием кандидата. Кандидат подаётся картинкой эталона и текстом
(винодельня, название, категория, сорт).

Чем отличается от probe_reranker.py (§11.43): там гонялись только 9 кадров, где
ошиблись обе модели, — проверка односторонняя. Здесь переставляется top-K у ВСЕХ
кадров, и считается попарно, сколько реранкер исправил и сколько СЛОМАЛ
(правило §11.37: значимо при |исправлено - сломано| >= 2*sqrt(сумма)).

Кандидаты берутся из векторов, которые оставил hires_eval.py
(results/emb_<токены>.npz) — эмбеддер повторно не гоняется. По умолчанию лучшая
конфигурация прошлого прогона: эталоны 1024 токена, запрос 2560.

Запуск на поде, после hires_eval.py:
    export HF_HOME=/workspace/hf OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
    python rerank_eval.py --model Qwen/Qwen3-VL-Reranker-8B
"""

import argparse
import csv
import inspect
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

from common import load_image

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
REPO = HERE / "Qwen3-VL-Embedding"

INSTRUCTION = ("Given a photo of a wine bottle, decide whether the candidate is the exact same wine: "
               "the same producer, wine name, grape variety, sweetness and vintage printed on the label.")


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_frame(path: Path) -> Image.Image:
    with Image.open(path) as raw:
        return ImageOps.exif_transpose(raw).convert("RGB")


def load_cached(tokens: int, refs: list[dict], frames: list[dict]) -> dict:
    path = RESULTS / f"emb_{tokens}.npz"
    if not path.exists():
        raise SystemExit(f"нет {path} — сначала: python hires_eval.py --tokens 1024 2560")
    blob = np.load(path, allow_pickle=True)
    if list(blob["ref_slugs"]) != [r["slug"] for r in refs] or \
            list(blob["frame_keys"]) != [f["key"] for f in frames]:
        raise SystemExit(f"{path.name} посчитан на других эталонах/кадрах — перезапустите hires_eval.py")
    return {"ref": blob["ref_emb"], "frame": blob["frame_emb"]}


def load_reranker(model_id: str, tokens: int, attn: str):
    sys.path.insert(0, str(REPO))
    from src.models.qwen3_vl_reranker import Qwen3VLReranker

    kwargs = {"model_name_or_path": model_id, "torch_dtype": torch.bfloat16, "attn_implementation": attn}
    accepts = inspect.signature(Qwen3VLReranker.__init__).parameters
    if "max_pixels" in accepts:
        kwargs["max_pixels"] = tokens * 32 * 32
    reranker = Qwen3VLReranker(**kwargs)
    if "max_pixels" not in accepts:
        if hasattr(reranker, "max_pixels"):
            reranker.max_pixels = tokens * 32 * 32
        else:
            print(f"ВНИМАНИЕ: реранкер не принимает max_pixels — работает на своём разрешении по умолчанию")

    device = next(reranker.model.parameters()).device
    if device.type != "cuda":
        reranker.model = reranker.model.to("cuda")
        reranker.device = torch.device("cuda")
        if hasattr(reranker, "score_linear"):
            reranker.score_linear = reranker.score_linear.to("cuda")

    # Код Qwen написан под transformers 4.57; в 5.x `mm_token_type_ids` приходит
    # списком, а get_rope_index индексирует его булевой маской (§11.43).
    original = reranker.tokenize

    def tokenize_compat(pairs, **kw):
        out = original(pairs, **kw)
        if isinstance(out.get("mm_token_type_ids"), list):
            out["mm_token_type_ids"] = torch.tensor(out["mm_token_type_ids"], dtype=torch.long)
        return out

    reranker.tokenize = tokenize_compat
    return reranker


def paired(base: list[str], new: list[str], true: list[str]) -> dict:
    fixed = [i for i, (b, n, t) in enumerate(zip(base, new, true)) if n == t and b != t]
    broken = [i for i, (b, n, t) in enumerate(zip(base, new, true)) if n != t and b == t]
    f, k = len(fixed), len(broken)
    thr = 2 * (f + k) ** 0.5
    return {"fixed": f, "broken": k, "threshold": round(thr, 1),
            "verdict": ("ЗНАЧИМО ЛУЧШЕ" if f > k else "ЗНАЧИМО ХУЖЕ") if f + k and abs(f - k) >= thr else "шум",
            "fixed_idx": fixed, "broken_idx": broken}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="Qwen/Qwen3-VL-Reranker-8B")
    p.add_argument("--ref-tokens", type=int, default=1024, help="чьи векторы эталонов дают кандидатов")
    p.add_argument("--query-tokens", type=int, default=2560, help="чьи векторы кадров дают кандидатов")
    p.add_argument("--rr-tokens", type=int, default=2560, help="разрешение картинок внутри реранкера")
    p.add_argument("--k", type=int, default=5, help="сколько кандидатов переставлять")
    p.add_argument("--attn", default="sdpa")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    refs = read_csv(DATA / "refs.csv")
    frames = read_csv(DATA / "frames.csv")
    meta = {r["Slug"]: r for r in read_csv(DATA / "catalog_meta.csv")}
    slugs = np.array([r["slug"] for r in refs])
    ref_path = {r["slug"]: DATA / r["path"] for r in refs}

    def title(slug: str) -> str:
        m = meta.get(slug, {})
        parts = [m.get("Винодельня", ""), m.get("Название", ""), m.get("Категория", ""), m.get("Сорт винограда", "")]
        return ". ".join(x for x in parts if x) or slug

    cand = load_cached(args.ref_tokens, refs, frames)
    qry = load_cached(args.query_tokens, refs, frames)
    base = load_cached(1024, refs, frames)
    sims = qry["frame"] @ cand["ref"].T
    base_pred = slugs[np.argmax(base["frame"] @ base["ref"].T, axis=1)]

    keep = [i for i, f in enumerate(frames) if f["true_slug"] in set(slugs)]
    if args.limit:
        keep = keep[:args.limit]
    print(f"Кадров {len(keep)} (с эталоном в индексе), кандидаты: эталоны {args.ref_tokens} x "
          f"запрос {args.query_tokens}, top-{args.k}; реранкер {args.model} на {args.rr_tokens} токенах")

    reranker = load_reranker(args.model, args.rr_tokens, args.attn)
    rows, latencies = [], []
    started = time.time()
    for n, i in enumerate(keep, start=1):
        fr = frames[i]
        order = np.argsort(-sims[i])[:args.k]
        cands = [str(slugs[j]) for j in order]
        query = load_frame(DATA / fr["path"])
        documents = [{"text": title(s), "image": load_image(ref_path[s])} for s in cands]
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        scores = reranker.process({"instruction": INSTRUCTION, "query": {"image": query}, "documents": documents})
        torch.cuda.synchronize()
        latencies.append((time.perf_counter() - t0) * 1000)
        scores = [float(s) for s in scores]
        rows.append({"key": fr["key"], "path": fr["orig"], "set": fr["set"], "true": fr["true_slug"],
                     "base_1024": str(base_pred[i]), "emb": cands[0], "reranked": cands[int(np.argmax(scores))],
                     "candidates": [[s, round(float(sims[i][j]), 4), round(sc, 4)]
                                    for s, j, sc in zip(cands, order, scores)]})
        if n % 25 == 0:
            print(f"  {n}/{len(keep)}  медиана {np.median(latencies):.0f} мс на кадр, "
                  f"прошло {(time.time() - started) / 60:.1f} мин", flush=True)

    true = [r["true"] for r in rows]
    emb = [r["emb"] for r in rows]
    rr = [r["reranked"] for r in rows]
    b = [r["base_1024"] for r in rows]
    in_topk = sum(r["true"] in [c[0] for c in r["candidates"]] for r in rows)
    sets = sorted({r["set"] for r in rows})

    print(f"\n=== Реранкер {args.model}, кадров {len(rows)} ===")
    print(f"верное вино в top-{args.k}: {in_topk}/{len(rows)} — потолок для реранкера")
    head = "".join(("%s n=%d" % (s, sum(r["set"] == s for r in rows))).rjust(22) for s in sets)
    print(f"{'вариант':34s}{head}{'всего':>10s}")
    for name, pred in ((f"база 1024x1024", b), (f"эмбеддер {args.ref_tokens}x{args.query_tokens}", emb),
                       (f"+ реранкер", rr)):
        cells = "".join(("%d/%d" % (sum(p_ == r["true"] for p_, r in zip(pred, rows) if r["set"] == s),
                                     sum(r["set"] == s for r in rows))).rjust(22) for s in sets)
        acc = sum(p_ == t for p_, t in zip(pred, true)) / len(rows)
        print(f"{name:34s}{cells}{acc:>10.1%}")

    report = {}
    for label, before in ((f"реранкер против эмбеддера {args.ref_tokens}x{args.query_tokens}", emb),
                          ("реранкер против базы 1024x1024", b)):
        pr = paired(before, rr, true)
        report[label] = {k: v for k, v in pr.items() if not k.endswith("_idx")}
        print(f"\n{label}: исправлено {pr['fixed']}, сломано {pr['broken']}, "
              f"порог {pr['threshold']} -> {pr['verdict']}")
        for i in pr["fixed_idx"]:
            print(f"    + {rows[i]['key'][-40:]:42s} {rows[i]['true'][:50]}")
        for i in pr["broken_idx"]:
            print(f"    - {rows[i]['key'][-40:]:42s} {rows[i]['true'][:38]} -> {rows[i]['reranked'][:38]}")

    lat = np.array(latencies)
    gpu = torch.cuda.get_device_name(0)
    print(f"\nЛатентность реранкера на кадр ({gpu}, top-{args.k}): медиана {np.median(lat):.0f} мс, "
          f"p95 {np.percentile(lat, 95):.0f} мс, макс {lat.max():.0f} мс "
          f"(плюс эмбеддер — см. hires_eval)")

    tag = f"{args.ref_tokens}x{args.query_tokens}_{args.model.split('/')[-1]}_k{args.k}"
    out = RESULTS / f"rerank_{tag}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"args": vars(args), "gpu": gpu, "n": len(rows), "in_topk": in_topk,
                   "paired": report, "latency_ms_median": float(np.median(lat)),
                   "latency_ms_p95": float(np.percentile(lat, 95)), "rows": rows},
                  f, ensure_ascii=False, indent=1)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
