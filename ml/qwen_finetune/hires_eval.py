"""Двусторонняя проверка разрешения Qwen3-VL-Embedding-8B + LoRA (findings §11.44).

Зачем. На 10 трудных кадрах подъём лимита визуальных токенов с 1024 до 2560
исправил 3 ошибки, но проверка была односторонней: что станет с уже верными
кадрами, неизвестно (у SigLIP2 512 -> 640 дал 8 исправлений и 9 поломок, §11.39).
Здесь на каждом лимите заново кодируются ВСЕ эталоны индекса и ВСЕ полевые
кадры, и каждая комбинация (токены эталона x токены запроса) сравнивается
попарно с базой 1024x1024 по правилу §11.37:
значимо при |исправлено - сломано| >= 2 * sqrt(исправлено + сломано).

Заодно:
  * индекс полный — 2103 вина: 2083 эталона из catalog_photos (на них обучался
    адаптер) + 20 из uploads организаторов. Метрики на старом поле 2083
    печатаются отдельно — для сверки с §11.42 (94.5% на 254 кадрах);
  * латентность одиночного запроса: препроцессинг + прямой проход + поиск по
    индексу, по одному кадру, с синхронизацией CUDA; медиана и p95.

Кадры и разметка — протокол Никиты: 201 кадр команды (own_*, поправки §11.33)
+ 56 кадров организаторов. Кадр подаётся сырым (EXIF-поворот, без enhance).

Запуск на поде, из этой папки:
    export OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
    python hires_eval.py --tokens 1024 2560

Промежуточные векторы пишутся в results/emb_<токены>.npz; повторный запуск
их подхватывает (--force — пересчитать).
"""

import argparse
import csv
import gc
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps

from common import INSTRUCTION, load_embedder, load_image

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_frame(path: Path) -> Image.Image:
    with Image.open(path) as raw:
        return ImageOps.exif_transpose(raw).convert("RGB")


def build_embedder(tokens: int, adapter: str, attn: str, merge: bool):
    embedder = load_embedder(max_pixels=tokens * 32 * 32, attn=attn)
    from peft import PeftModel
    model = PeftModel.from_pretrained(embedder.model, adapter)
    if merge:
        model = model.merge_and_unload()
    embedder.model = model.eval()
    return embedder


def encode(embedder, images: list) -> torch.Tensor:
    convs = [embedder.format_model_input(image=im, instruction=INSTRUCTION) for im in images]
    inputs = embedder._preprocess_inputs(convs)
    inputs = {k: v.to(embedder.model.device) for k, v in inputs.items()}
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        out = embedder.model(**inputs, use_cache=False)
    emb = embedder._pooling_last(out.last_hidden_state, inputs["attention_mask"])
    return F.normalize(emb.float(), dim=-1)


def run_tokens(tokens: int, args, refs: list[dict], frames: list[dict]) -> tuple[dict, dict]:
    cache = RESULTS / f"emb_{tokens}{'_merged' if args.merge else ''}.npz"
    ref_slugs = [r["slug"] for r in refs]
    frame_keys = [f["key"] for f in frames]
    if cache.exists() and not args.force:
        blob = np.load(cache, allow_pickle=True)
        if list(blob["ref_slugs"]) == ref_slugs and list(blob["frame_keys"]) == frame_keys:
            print(f"[{tokens}] векторы из кеша {cache.name}")
            stats = json.loads(str(blob["stats"]))
            return {"ref": blob["ref_emb"], "frame": blob["frame_emb"]}, stats
        print(f"[{tokens}] кеш не совпадает с текущими эталонами/кадрами — пересчёт")

    print(f"\n[{tokens}] загрузка модели, max_pixels = {tokens}*32*32", flush=True)
    embedder = build_embedder(tokens, args.adapter, args.attn, args.merge)
    torch.cuda.reset_peak_memory_stats()

    started = time.time()
    chunks = []
    for i in range(0, len(refs), args.bs):
        batch = [load_image(DATA / r["path"]) for r in refs[i:i + args.bs]]
        chunks.append(encode(embedder, batch).cpu())
        if (i // args.bs) % 25 == 0:
            print(f"  [{tokens}] эталоны {min(i + args.bs, len(refs))}/{len(refs)}", flush=True)
    ref_emb = torch.cat(chunks)
    ref_sec = time.time() - started
    index = ref_emb.to("cuda")

    warm = load_frame(DATA / frames[0]["path"])
    for _ in range(args.warmup):
        encode(embedder, [warm])
    torch.cuda.synchronize()

    frame_emb, latencies = [], []
    for j, fr in enumerate(frames, start=1):
        image = load_frame(DATA / fr["path"])  # чтение файла в латентность не входит
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        emb = encode(embedder, [image])
        (emb @ index.T).topk(5, dim=1)
        torch.cuda.synchronize()
        latencies.append((time.perf_counter() - t0) * 1000)
        frame_emb.append(emb.cpu())
        if j % 50 == 0:
            print(f"  [{tokens}] кадры {j}/{len(frames)}, медиана {np.median(latencies):.0f} мс", flush=True)

    lat = np.array(latencies)
    stats = {"tokens": tokens, "gpu": torch.cuda.get_device_name(0), "merged_lora": args.merge,
             "latency_ms_median": float(np.median(lat)), "latency_ms_p95": float(np.percentile(lat, 95)),
             "latency_ms_max": float(lat.max()), "n_frames": len(frames),
             "refs_encode_sec": round(ref_sec, 1), "refs_per_sec": round(len(refs) / ref_sec, 1),
             "peak_mem_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1)}
    ref_np = ref_emb.numpy().astype(np.float32)
    frame_np = torch.cat(frame_emb).numpy().astype(np.float32)
    np.savez(cache, ref_slugs=np.array(ref_slugs), frame_keys=np.array(frame_keys),
             ref_emb=ref_np, frame_emb=frame_np, stats=json.dumps(stats))
    print(f"[{tokens}] эталоны {ref_sec:.0f} с, кадр: медиана {stats['latency_ms_median']:.0f} мс, "
          f"p95 {stats['latency_ms_p95']:.0f} мс, пик памяти {stats['peak_mem_gb']} ГБ", flush=True)

    del embedder, index
    gc.collect()
    torch.cuda.empty_cache()
    return {"ref": ref_np, "frame": frame_np}, stats


def rank(ref_emb, ref_slugs, frame_emb, frames) -> list[dict]:
    sims = frame_emb @ ref_emb.T
    order = np.argsort(-sims, axis=1)[:, :5]
    rows = []
    for fr, idx, sim in zip(frames, order, sims):
        rows.append({"key": fr["key"], "path": fr["orig"], "set": fr["set"], "true": fr["true_slug"],
                     "qwen": str(ref_slugs[idx[0]]),
                     "qwen_top5": [[str(ref_slugs[i]), round(float(sim[i]), 4)] for i in idx]})
    return rows


def paired(base: list[dict], rows: list[dict]) -> dict:
    fixed = [r for b, r in zip(base, rows) if r["qwen"] == r["true"] and b["qwen"] != b["true"]]
    broken = [(b, r) for b, r in zip(base, rows) if r["qwen"] != r["true"] and b["qwen"] == b["true"]]
    f, k = len(fixed), len(broken)
    threshold = 2 * (f + k) ** 0.5
    verdict = "ЗНАЧИМО" if f + k and abs(f - k) >= threshold else "шум"
    return {"fixed": f, "broken": k, "threshold": round(threshold, 1), "verdict": verdict,
            "fixed_frames": [{"key": r["key"], "true": r["true"]} for r in fixed],
            "broken_frames": [{"key": r["key"], "true": r["true"], "now": r["qwen"]} for _, r in broken]}


def report(title: str, allowed: set, refs, frames, embs, tokens) -> dict:
    keep = np.array([r["slug"] in allowed for r in refs])
    slugs = np.array([r["slug"] for r in refs])[keep]
    fidx = [i for i, f in enumerate(frames) if f["true_slug"] in allowed]
    part = [frames[i] for i in fidx]
    sets = sorted({fr["set"] for fr in part})
    set_n = {s: sum(fr["set"] == s for fr in part) for s in sets}

    combos = [(rt, qt) for rt in tokens for qt in tokens]
    base_key = (tokens[0], tokens[0])
    all_rows = {c: rank(embs[c[0]]["ref"][keep], slugs, embs[c[1]]["frame"][fidx], part) for c in combos}
    base = all_rows[base_key]

    print(f"\n=== {title}: эталонов {int(keep.sum())}, кадров {len(part)} ===")
    head = "".join(("%s n=%d" % (s, set_n[s])).rjust(22) for s in sets)
    print(f"{'эталон x запрос':16s}{head}{'всего top1':>12s}{'top5':>8s}   исправлено/сломано vs {base_key[0]}x{base_key[1]}")
    out = {}
    for c in combos:
        rows = all_rows[c]
        per_set = {s: sum(r["qwen"] == r["true"] for r in rows if r["set"] == s) for s in sets}
        top1 = sum(r["qwen"] == r["true"] for r in rows)
        top5 = sum(any(s == r["true"] for s, _ in r["qwen_top5"]) for r in rows)
        cells = "".join(("%d/%d" % (per_set[s], set_n[s])).rjust(22) for s in sets)
        pair = paired(base, rows) if c != base_key else None
        tail = "—" if pair is None else f"{pair['fixed']}/{pair['broken']}  порог {pair['threshold']}  -> {pair['verdict']}"
        print(f"{c[0]:>5d} x {c[1]:<8d}{cells}{top1 / len(rows):>11.1%}{top5 / len(rows):>8.1%}   {tail}")
        out[f"{c[0]}x{c[1]}"] = {"n": len(rows), "top1": top1, "top5": top5, "per_set": per_set, "paired_vs_base": pair}

    for c in combos:
        pair = out[f"{c[0]}x{c[1]}"]["paired_vs_base"]
        if not pair or not (pair["fixed"] or pair["broken"]):
            continue
        print(f"\n  {c[0]}x{c[1]} против {base_key[0]}x{base_key[1]}:")
        for x in pair["fixed_frames"]:
            print(f"    + {x['key'][-40:]:42s} {x['true'][:50]}")
        for x in pair["broken_frames"]:
            print(f"    - {x['key'][-40:]:42s} {x['true'][:40]} -> {x['now'][:40]}")
    return {"summary": out, "rows": {f"{c[0]}x{c[1]}": all_rows[c] for c in combos}}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", default=str(HERE / "adapter"))
    p.add_argument("--tokens", type=int, nargs="+", default=[1024, 2560],
                   help="лимиты визуальных токенов; первый — база для попарного сравнения")
    p.add_argument("--bs", type=int, default=16, help="батч при кодировании эталонов")
    p.add_argument("--attn", default="sdpa")
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--merge", action="store_true",
                   help="влить LoRA в веса — так будет в проде; числа могут сдвинуться в 3-4 знаке")
    p.add_argument("--force", action="store_true", help="не брать векторы из кеша")
    args = p.parse_args()

    if not torch.cuda.is_available():
        raise SystemExit("нужен GPU")
    RESULTS.mkdir(exist_ok=True)
    refs = read_csv(DATA / "refs.csv")
    frames = read_csv(DATA / "frames.csv")
    ref_slugs = [r["slug"] for r in refs]
    assert len(set(ref_slugs)) == len(ref_slugs), "в refs.csv повторяются slug"
    by_source = {s: sum(r["source"] == s for r in refs) for s in sorted({r["source"] for r in refs})}
    missing = [f["key"] for f in frames if f["true_slug"] not in set(ref_slugs)]
    print(f"Эталонов {len(refs)} {by_source}, кадров {len(frames)}, из них без эталона в индексе: {len(missing)}")

    embs, stats = {}, {}
    for t in args.tokens:
        embs[t], stats[t] = run_tokens(t, args, refs, frames)

    full = report("Индекс полный", set(ref_slugs), refs, frames, embs, args.tokens)
    old_field = {r["slug"] for r in refs if r["source"] == "catalog_photos"}
    old = report("Поле 2083 (как в §11.42)", old_field, refs, frames, embs, args.tokens)

    print(f"\n=== Латентность одиночного запроса ({stats[args.tokens[0]]['gpu']}) ===")
    print(f"{'токенов':>8s}{'медиана':>10s}{'p95':>8s}{'макс':>8s}{'эталоны, с':>13s}{'пик ГБ':>9s}")
    for t in args.tokens:
        s = stats[t]
        print(f"{t:>8d}{s['latency_ms_median']:>8.0f}мс{s['latency_ms_p95']:>6.0f}мс{s['latency_ms_max']:>6.0f}мс"
              f"{s['refs_encode_sec']:>13.0f}{s['peak_mem_gb']:>9.1f}")

    tag = "_".join(map(str, args.tokens)) + ("_merged" if args.merge else "")
    with open(RESULTS / f"hires_summary_{tag}.json", "w", encoding="utf-8") as f:
        json.dump({"tokens": args.tokens, "latency": stats, "full_index": full["summary"],
                   "field_2083": old["summary"]}, f, ensure_ascii=False, indent=2)
    for combo, rows in full["rows"].items():
        with open(RESULTS / f"hires_rows_{combo}.json", "w", encoding="utf-8") as f:
            json.dump({"rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\nСохранено: results/hires_summary_{tag}.json, results/hires_rows_*.json, results/emb_*.npz")


if __name__ == "__main__":
    main()
