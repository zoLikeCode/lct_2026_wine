"""Прогон 1: как ещё поднять top-1 без дообучения. Всё попарно против текущего прода.

База (production, §11.45): Qwen3-VL-Embedding-8B + LoRA v2, эталоны 1024 токена,
запрос — ЦЕЛЫЙ кадр на 2560 токенах, один эталон на вино.

Варианты (меняется только то, что написано):
  A  gt_crop      — запрос: вырезанная этикетка по ручной рамке (только кадры с рамкой)
  A+ gt_fuse      — сумма схожестей целого кадра и вырезки по ручной рамке
  B  yolo_crop    — запрос: вырезка по нашему YOLO (wine_label_yolo11m_best.pt); нет детекции -> целый кадр
  C  yolo_fuse    — сумма схожестей целого кадра и вырезки YOLO
  D  multiref     — целый кадр, но у вина несколько эталонов (+ доп. фото каталога), берётся максимум
  CD yolo_fuse+multiref

Правило значимости §11.37: |исправлено - сломано| >= 2*sqrt(исправлено + сломано).

Запуск на поде (из папки qwen_hires, после setup.sh):
    pip install -q ultralytics
    export HF_HOME=/workspace/hf OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
    python exp_eval.py 2>&1 | tee results/run1.log

Все векторы кешируются в results/run1_*.npz — повторный запуск не пересчитывает.
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
ADAPTER = HERE / "adapter"
YOLO = HERE / "yolo" / "wine_label_yolo11m_best.pt"


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_frame(path):
    with Image.open(path) as raw:
        return ImageOps.exif_transpose(raw).convert("RGB")


def crop(img, box, pad=0.10):
    """box — x1,y1,x2,y2 в долях от кадра после EXIF-поворота; поля pad от размера рамки."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    x1, x2 = max(0.0, x1 - pad * w), min(1.0, x2 + pad * w)
    y1, y2 = max(0.0, y1 - pad * h), min(1.0, y2 + pad * h)
    W, H = img.size
    return img.crop((int(x1 * W), int(y1 * H), max(int(x2 * W), int(x1 * W) + 8), max(int(y2 * H), int(y1 * H) + 8)))


def build(tokens):
    emb = load_embedder(max_pixels=tokens * 32 * 32)
    from peft import PeftModel
    emb.model = PeftModel.from_pretrained(emb.model, str(ADAPTER)).eval()
    return emb


def encode(emb, images):
    convs = [emb.format_model_input(image=im, instruction=INSTRUCTION) for im in images]
    inputs = {k: v.to(emb.model.device) for k, v in emb._preprocess_inputs(convs).items()}
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        out = emb.model(**inputs, use_cache=False)
    e = emb._pooling_last(out.last_hidden_state, inputs["attention_mask"])
    return F.normalize(e.float(), dim=-1).cpu().numpy()


def free(emb):
    del emb
    gc.collect()
    torch.cuda.empty_cache()


# ------------------------------------------------------------------ шаги

def step_refs(refs, extras, bs):
    """Эталоны на 1024: основной индекс (берётся из кеша прошлого прогона) + доп. фото."""
    main_cache = RESULTS / "cache_ref_1024.npz"
    extra_cache = RESULTS / "run1_extra_1024.npz"
    slugs = [r["slug"] for r in refs]
    main = None
    if main_cache.exists():
        b = np.load(main_cache, allow_pickle=True)
        if list(b["ref_slugs"]) == slugs:
            main = b["ref_emb"].astype(np.float32)
            print(f"[эталоны] основной индекс из кеша: {main.shape}")
    ex = None
    if extra_cache.exists():
        b = np.load(extra_cache, allow_pickle=True)
        if list(b["paths"]) == [e["path"] for e in extras]:
            ex = b["emb"]
            print(f"[эталоны] доп. эталоны из кеша: {ex.shape}")
    if main is None or ex is None:
        emb = build(1024)
        t0 = time.time()
        if main is None:
            main = np.concatenate([encode(emb, [load_image(DATA / r["path"]) for r in refs[i:i + bs]])
                                   for i in range(0, len(refs), bs)])
            np.savez(main_cache, ref_slugs=np.array(slugs), ref_emb=main)
        if ex is None:
            chunks = []
            for i in range(0, len(extras), bs):
                chunks.append(encode(emb, [load_image(DATA / e["path"]) for e in extras[i:i + bs]]))
                if (i // bs) % 25 == 0:
                    print(f"  доп. эталоны {min(i + bs, len(extras))}/{len(extras)}", flush=True)
            ex = np.concatenate(chunks)
            np.savez(extra_cache, paths=np.array([e["path"] for e in extras]), emb=ex)
        print(f"[эталоны] посчитано за {time.time() - t0:.0f} с")
        free(emb)
    return main, ex


def step_yolo(frames, conf):
    cache = RESULTS / "run1_yolo.json"
    if cache.exists():
        data = json.load(open(cache, encoding="utf-8"))
        if [d["key"] for d in data["frames"]] == [f["key"] for f in frames]:
            print("[yolo] рамки из кеша")
            return data
    from ultralytics import YOLO as Y
    model = Y(str(YOLO))
    out, lat = [], []
    for f in frames:
        img = load_frame(DATA / f["path"])
        t0 = time.perf_counter()
        res = model.predict(img, conf=conf, verbose=False)[0]
        lat.append((time.perf_counter() - t0) * 1000)
        box = None
        if len(res.boxes):
            k = int(res.boxes.conf.argmax())
            x1, y1, x2, y2 = res.boxes.xyxyn[k].tolist()
            box = [x1, y1, x2, y2, float(res.boxes.conf[k])]
        out.append({"key": f["key"], "box": box})
    data = {"frames": out, "latency_ms_median": float(np.median(lat)), "found": sum(o["box"] is not None for o in out)}
    json.dump(data, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[yolo] этикетка найдена на {data['found']}/{len(frames)}, медиана {data['latency_ms_median']:.0f} мс")
    del model
    return data


def step_queries(frames, gt, yolo):
    cache = RESULTS / "run1_q2560.npz"
    keys = [f["key"] for f in frames]
    if cache.exists():
        b = np.load(cache, allow_pickle=True)
        if list(b["keys"]) == keys:
            print("[запросы] из кеша")
            return {k: b[k] for k in ("full", "gt", "yolo")}, json.loads(str(b["lat"]))
    emb = build(2560)
    img0 = load_frame(DATA / frames[0]["path"])
    for _ in range(3):
        encode(emb, [img0])
    D = None
    res = {"full": [], "gt": [], "yolo": []}
    lat = {"full": [], "crop": []}
    for i, f in enumerate(frames, 1):
        img = load_frame(DATA / f["path"])
        torch.cuda.synchronize(); t0 = time.perf_counter()
        e_full = encode(emb, [img])[0]
        torch.cuda.synchronize(); lat["full"].append((time.perf_counter() - t0) * 1000)
        D = e_full.shape[0]
        res["full"].append(e_full)
        res["gt"].append(encode(emb, [crop(img, gt[i - 1])])[0] if gt[i - 1] else np.full(D, np.nan, np.float32))
        yb = yolo["frames"][i - 1]["box"]
        if yb:
            torch.cuda.synchronize(); t0 = time.perf_counter()
            res["yolo"].append(encode(emb, [crop(img, yb[:4])])[0])
            torch.cuda.synchronize(); lat["crop"].append((time.perf_counter() - t0) * 1000)
        else:
            res["yolo"].append(e_full)
        if i % 50 == 0:
            print(f"  [запросы] {i}/{len(frames)}", flush=True)
    res = {k: np.stack(v).astype(np.float32) for k, v in res.items()}
    lat_s = {k: float(np.median(v)) for k, v in lat.items() if v}
    np.savez(cache, keys=np.array(keys), lat=json.dumps(lat_s), **res)
    free(emb)
    return res, lat_s


# ------------------------------------------------------------------ оценка

def paired(base, new, true):
    fx = [i for i, (b, n, t) in enumerate(zip(base, new, true)) if n == t and b != t]
    br = [i for i, (b, n, t) in enumerate(zip(base, new, true)) if n != t and b == t]
    thr = 2 * (len(fx) + len(br)) ** 0.5
    if fx or br:
        verdict = ("ЗНАЧИМО ЛУЧШЕ" if len(fx) > len(br) else "ЗНАЧИМО ХУЖЕ") if abs(len(fx) - len(br)) >= thr else "шум"
    else:
        verdict = "без изменений"
    return fx, br, thr, verdict


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bs", type=int, default=16)
    p.add_argument("--yolo-conf", type=float, default=0.25)
    args = p.parse_args()
    RESULTS.mkdir(exist_ok=True)

    refs = read_csv(DATA / "refs.csv")
    frames = read_csv(DATA / "frames_v2.csv")
    extras = [e for e in read_csv(DATA / "extra_refs.csv") if (DATA / e["path"]).exists()]
    slugs = np.array([r["slug"] for r in refs])
    sidx = {s: i for i, s in enumerate(slugs)}
    extras = [e for e in extras if e["slug"] in sidx]
    gt = [[float(x) for x in f["box"].split()] if f.get("box") else None for f in frames]
    sets = sorted({f["set"] for f in frames})
    print(f"Эталонов {len(refs)}, доп. эталонов {len(extras)} ({len({e['slug'] for e in extras})} вин), "
          f"кадров {len(frames)} {dict((s, sum(f['set'] == s for f in frames)) for s in sets)}, "
          f"с ручной рамкой {sum(g is not None for g in gt)}")

    main_ref, extra_ref = step_refs(refs, extras, args.bs)
    yolo = step_yolo(frames, args.yolo_conf)
    Q, lat = step_queries(frames, gt, yolo)

    true = [f["true_slug"] for f in frames]
    ex_slug = np.array([sidx[e["slug"]] for e in extras])

    def scores(q, multiref=False):
        s = q @ main_ref.T
        if multiref and len(extras):
            se = q @ extra_ref.T
            for j, k in enumerate(ex_slug):
                s[:, k] = np.maximum(s[:, k], se[:, j])
        return s

    has_gt = np.array([g is not None for g in gt])
    q_gt = np.where(has_gt[:, None], Q["gt"], Q["full"])  # для кадров без рамки — целый кадр
    S = {
        "base (кадр 2560)": scores(Q["full"]),
        "A  ручная рамка, вырезка": scores(q_gt),
        "A+ кадр + ручная рамка": scores(Q["full"]) + scores(q_gt),
        "B  YOLO, вырезка": scores(Q["yolo"]),
        "C  кадр + YOLO": scores(Q["full"]) + scores(Q["yolo"]),
        "D  несколько эталонов": scores(Q["full"], True),
        "CD кадр + YOLO, несколько эталонов": scores(Q["full"], True) + scores(Q["yolo"], True),
    }
    preds = {k: [str(slugs[i]) for i in np.argmax(v, axis=1)] for k, v in S.items()}
    top5 = {k: [[str(slugs[i]) for i in np.argsort(-row)[:5]] for row in v] for k, v in S.items()}

    def table(title, idx):
        print(f"\n=== {title}: кадров {len(idx)} ===")
        n_set = {s: sum(frames[i]["set"] == s for i in idx) for s in sets}
        print(f"{'вариант':38s}" + "".join(("%s n=%d" % (s, n_set[s])).rjust(22) for s in sets if n_set[s])
              + f"{'top1':>8s}{'top5':>8s}   исправлено/сломано vs base")
        base = [preds["base (кадр 2560)"][i] for i in idx]
        tt = [true[i] for i in idx]
        out = {}
        for k in S:
            pr = [preds[k][i] for i in idx]
            t5 = sum(true[i] in top5[k][i] for i in idx)
            cells = "".join(("%d/%d" % (sum(pr[j] == tt[j] for j, i in enumerate(idx) if frames[i]["set"] == s), n_set[s])).rjust(22)
                            for s in sets if n_set[s])
            acc = sum(a == b for a, b in zip(pr, tt))
            fx, br, thr, verdict = paired(base, pr, tt)
            tail = "—" if k.startswith("base") else f"{len(fx)}/{len(br)}  порог {thr:.1f} -> {verdict}"
            print(f"{k:38s}{cells}{acc / len(idx):>8.1%}{t5 / len(idx):>8.1%}   {tail}")
            out[k] = {"n": len(idx), "top1": acc, "top5": t5, "fixed": [frames[idx[j]]["key"] for j in fx],
                      "broken": [[frames[idx[j]]["key"], pr[j]] for j in br], "verdict": verdict}
        return out

    all_idx = list(range(len(frames)))
    summary = {"all": table("Все кадры", all_idx),
               "gt": table("Только кадры с ручной рамкой (честное сравнение для A)", [i for i in all_idx if has_gt[i]])}

    print("\n--- разбор: что исправили и сломали лучшие варианты (все кадры) ---")
    for k, v in summary["all"].items():
        if k.startswith("base") or not (v["fixed"] or v["broken"]):
            continue
        print(f"\n{k}:")
        for key in v["fixed"]:
            print(f"   + {key[-60:]}")
        for key, pr in v["broken"]:
            print(f"   - {key[-50:]:52s} -> {pr[:45]}")

    print(f"\nЛатентность ({torch.cuda.get_device_name(0)}): кадр 2560 — {lat.get('full', 0):.0f} мс, "
          f"вырезка — {lat.get('crop', 0):.0f} мс, YOLO — {yolo['latency_ms_median']:.0f} мс; "
          f"YOLO нашёл этикетку на {yolo['found']}/{len(frames)}")
    rows = [{"key": f["key"], "set": f["set"], "true": f["true_slug"],
             **{k: {"pred": preds[k][i], "top5": top5[k][i]} for k in S}} for i, f in enumerate(frames)]
    json.dump({"summary": summary, "latency_ms": lat, "yolo_latency_ms": yolo["latency_ms_median"], "rows": rows},
              open(RESULTS / "run1_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Сохранено: results/run1_summary.json, results/run1_*.npz")


if __name__ == "__main__":
    main()
