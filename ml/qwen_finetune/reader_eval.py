"""Прогон 2: Qwen3-VL-8B-Instruct как «читатель этикетки» поверх эмбеддера.

Идея. Почти все ошибки эмбеддера — соседи по линейке одной винодельни (сорт,
сладость), а верное вино в top-5 у ~99% кадров. Эмбеддер сжимает этикетку в
один вектор и теряет мелкий текст. Генеративная VLM видит кадр на 2560
визуальных токенах и список из пяти кандидатов ТЕКСТОМ (винодельня, название,
цвет, сладость, сорт) и отвечает одной цифрой — номером варианта. Ответ
снимается одним прямым проходом: берутся логиты токенов «1».«5», без генерации.
Если модель не уверена (вероятность ответа ниже порога) — остаётся ответ
эмбеддера, чтобы не ломать верные.

Что считается (все кадры frames_v2.csv, попарно против прод-конфигурации):
  1. эмбеддер: эталоны 1024 x запрос 1024 / 2560 / сумма схожестей 1024+2560;
  2. читатель поверх top-5 эмбеддера (эталоны 1024 x запрос 2560):
     - всегда верить читателю;
     - верить при уверенности >= 0.5 / 0.8 (основной, выбран заранее) / 0.95;
     - контроль позиционного смещения: те же 5 кандидатов в перемешанном порядке.

Правило значимости §11.37: |исправлено - сломано| >= 2*sqrt(исправлено + сломано).

Запуск на поде (из /workspace/qwen_hires):
    export HF_HOME=/workspace/hf OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
    python -c "from huggingface_hub import snapshot_download as s; print(s('Qwen/Qwen3-VL-8B-Instruct'))"
    python reader_eval.py 2>&1 | tee results/run2.log
"""

import argparse
import csv
import gc
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
ADAPTER = HERE / "adapter"
PRIMARY_THRESHOLD = 0.8

SWEET = [("ekstra-bryut", "экстра брют"), ("bryut", "брют"), ("polusuhoe", "полусухое"), ("polusladkoe", "полусладкое"),
         ("suhoe", "сухое"), ("sladkoe", "сладкое")]


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_frame(path):
    with Image.open(path) as raw:
        return ImageOps.exif_transpose(raw).convert("RGB")


def fit_pixels(img, tokens, unit=32):
    """Уменьшает кадр до бюджета tokens*32*32 пикселей, стороны кратны 32 — как smart_resize у Qwen."""
    w, h = img.size
    budget = tokens * unit * unit
    k = min(1.0, (budget / (w * h)) ** 0.5)
    nw, nh = max(unit, int(w * k) // unit * unit), max(unit, int(h * k) // unit * unit)
    return img.resize((nw, nh), Image.BICUBIC) if (nw, nh) != (w, h) else img


# ------------------------------------------------------------------ эмбеддер

def embed_queries(frames, tokens):
    cache = RESULTS / f"run2_q{tokens}.npz"
    keys = [f["key"] for f in frames]
    if cache.exists():
        b = np.load(cache, allow_pickle=True)
        if list(b["keys"]) == keys:
            print(f"[эмбеддер {tokens}] запросы из кеша")
            return b["emb"]
    old = RESULTS / "run1_q2560.npz"
    if tokens == 2560 and old.exists():
        b = np.load(old, allow_pickle=True)
        if list(b["keys"]) == keys:
            print("[эмбеддер 2560] запросы из кеша прогона 1")
            np.savez(cache, keys=np.array(keys), emb=b["full"])
            return b["full"]

    from common import INSTRUCTION, load_embedder
    from peft import PeftModel
    emb = load_embedder(max_pixels=tokens * 32 * 32)
    emb.model = PeftModel.from_pretrained(emb.model, str(ADAPTER)).eval()
    out = []
    for i, f in enumerate(frames, 1):
        conv = emb.format_model_input(image=load_frame(DATA / f["path"]), instruction=INSTRUCTION)
        inputs = {k: v.to(emb.model.device) for k, v in emb._preprocess_inputs([conv]).items()}
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            o = emb.model(**inputs, use_cache=False)
        e = emb._pooling_last(o.last_hidden_state, inputs["attention_mask"])
        out.append(F.normalize(e.float(), dim=-1).cpu().numpy()[0])
        if i % 100 == 0:
            print(f"  [эмбеддер {tokens}] {i}/{len(frames)}", flush=True)
    out = np.stack(out).astype(np.float32)
    np.savez(cache, keys=np.array(keys), emb=out)
    del emb
    gc.collect()
    torch.cuda.empty_cache()
    return out


# ------------------------------------------------------------------ читатель

class Reader:
    def __init__(self, model_id, tokens):
        from transformers import AutoProcessor
        try:
            from transformers import Qwen3VLForConditionalGeneration as M
        except ImportError:
            from transformers import AutoModelForImageTextToText as M
        self.model = M.from_pretrained(model_id, torch_dtype=torch.bfloat16, attn_implementation="sdpa",
                                       device_map="cuda").eval()
        self.proc = AutoProcessor.from_pretrained(model_id)
        self.tokens = tokens
        tok = self.proc.tokenizer
        self.digit_ids = [tok.encode(str(i), add_special_tokens=False)[0] for i in range(1, 6)]

    @torch.no_grad()
    def choose(self, image, options):
        lines = "\n".join(f"{i}. {t}" for i, t in enumerate(options, 1))
        prompt = ("На фото бутылка вина. Внимательно прочитай лицевую этикетку: производитель, название вина, "
                  "сорт винограда, цвет, сладость, год.\n"
                  "Какой из вариантов ниже — именно это вино?\n"
                  f"{lines}\n"
                  f"Ответь одной цифрой от 1 до {len(options)}.")
        img = fit_pixels(image, self.tokens)
        messages = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": prompt}]}]
        text = self.proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.proc(text=[text], images=[img], return_tensors="pt").to("cuda")
        logits = self.model(**inputs).logits[0, -1].float()
        sel = logits[self.digit_ids[:len(options)]]
        return torch.softmax(sel, dim=0).cpu().numpy()


def candidate_text(slug, meta):
    m = meta.get(slug, {})
    sweet = next((ru for key, ru in SWEET if key in slug), "")
    parts = [m.get("Винодельня", ""), m.get("Название", ""), m.get("Категория", ""), sweet,
             ("сорт: " + m["Сорт винограда"]) if m.get("Сорт винограда") else ""]
    return ", ".join(p for p in parts if p) or slug


def run_reader(frames, cands, meta, args):
    cache = RESULTS / f"run2_reader_{args.model.split('/')[-1]}_{args.reader_tokens}.json"
    keys = [f["key"] for f in frames]
    if cache.exists():
        d = json.load(open(cache, encoding="utf-8"))
        if d["keys"] == keys:
            print("[читатель] ответы из кеша")
            return d
    reader = Reader(args.model, args.reader_tokens)
    rng = random.Random(0)
    probs, probs_shuf, perms, lat = [], [], [], []
    for i, f in enumerate(frames, 1):
        img = load_frame(DATA / f["path"])
        opts = [candidate_text(s, meta) for s in cands[i - 1]]
        torch.cuda.synchronize(); t0 = time.perf_counter()
        p = reader.choose(img, opts)
        torch.cuda.synchronize(); lat.append((time.perf_counter() - t0) * 1000)
        perm = list(range(len(opts))); rng.shuffle(perm)
        ps = reader.choose(img, [opts[j] for j in perm])
        back = np.zeros(len(opts)); back[perm] = ps  # вероятность в исходной нумерации
        probs.append(p.tolist()); probs_shuf.append(back.tolist()); perms.append(perm)
        if i % 25 == 0:
            print(f"  [читатель] {i}/{len(frames)}, медиана {np.median(lat):.0f} мс", flush=True)
    d = {"keys": keys, "probs": probs, "probs_shuffled": probs_shuf, "perms": perms,
         "latency_ms_median": float(np.median(lat)), "latency_ms_p95": float(np.percentile(lat, 95))}
    json.dump(d, open(cache, "w", encoding="utf-8"))
    del reader
    gc.collect()
    torch.cuda.empty_cache()
    return d


# ------------------------------------------------------------------ оценка

def paired(base, new, true):
    fx = [i for i, (b, n, t) in enumerate(zip(base, new, true)) if n == t and b != t]
    br = [i for i, (b, n, t) in enumerate(zip(base, new, true)) if n != t and b == t]
    thr = 2 * (len(fx) + len(br)) ** 0.5
    if not fx and not br:
        return fx, br, thr, "без изменений"
    if abs(len(fx) - len(br)) >= thr:
        return fx, br, thr, "ЗНАЧИМО ЛУЧШЕ" if len(fx) > len(br) else "ЗНАЧИМО ХУЖЕ"
    return fx, br, thr, "шум"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="Qwen/Qwen3-VL-8B-Instruct")
    p.add_argument("--reader-tokens", type=int, default=2560)
    p.add_argument("--k", type=int, default=5)
    args = p.parse_args()
    RESULTS.mkdir(exist_ok=True)

    refs = read_csv(DATA / "refs.csv")
    frames = read_csv(DATA / "frames_v2.csv")
    meta = {r["Slug"]: r for r in read_csv(DATA / "catalog_meta.csv")}
    slugs = np.array([r["slug"] for r in refs])
    b = np.load(RESULTS / "cache_ref_1024.npz", allow_pickle=True)
    assert list(b["ref_slugs"]) == list(slugs), "cache_ref_1024.npz не совпадает с refs.csv"
    R = b["ref_emb"].astype(np.float32)
    sets = sorted({f["set"] for f in frames})
    print(f"Эталонов {len(refs)}, кадров {len(frames)} {dict((s, sum(f['set'] == s for f in frames)) for s in sets)}")

    q1024 = embed_queries(frames, 1024)
    q2560 = embed_queries(frames, 2560)

    # несколько эталонов на вино (вариант D прогона 1 — значимо лучше: 14 исправлено / 1 сломано)
    sidx = {s: i for i, s in enumerate(slugs)}
    extras = [e for e in read_csv(DATA / "extra_refs.csv") if e["slug"] in sidx]
    eb = np.load(RESULTS / "run1_extra_1024.npz", allow_pickle=True)
    assert list(eb["paths"]) == [e["path"] for e in extras], "run1_extra_1024.npz не совпадает с extra_refs.csv"
    E, ex_slug = eb["emb"].astype(np.float32), np.array([sidx[e["slug"]] for e in extras])

    def multiref(q):
        s, se = q @ R.T, q @ E.T
        for j, k in enumerate(ex_slug):
            s[:, k] = np.maximum(s[:, k], se[:, j])
        return s

    S = {"1024x1024": q1024 @ R.T, "1024x2560": q2560 @ R.T,
         "1024x1024 + доп. эталоны": multiref(q1024), "1024x2560 + доп. эталоны (прод)": multiref(q2560)}
    S["1024x(1024+2560) + доп. эталоны"] = S["1024x1024 + доп. эталоны"] + S["1024x2560 + доп. эталоны (прод)"]
    true = [f["true_slug"] for f in frames]
    pred = {k: [str(slugs[i]) for i in v.argmax(1)] for k, v in S.items()}
    top5 = {k: np.argsort(-v, axis=1)[:, :args.k] for k, v in S.items()}

    base_key = "1024x2560 + доп. эталоны (прод)"
    cands = [[str(slugs[j]) for j in row] for row in top5[base_key]]
    rd = run_reader(frames, cands, meta, args)
    P = np.array(rd["probs"]); PS = np.array(rd["probs_shuffled"])

    def reader_pred(probs, thr):
        out = []
        for c, pr in zip(cands, probs):
            j = int(np.argmax(pr))
            out.append(c[j] if pr[j] >= thr else c[0])
        return out

    variants = {k: pred[k] for k in S}
    variants["читатель, всегда"] = reader_pred(P, 0.0)
    for thr in (0.5, PRIMARY_THRESHOLD, 0.95):
        variants[f"читатель, уверенность >= {thr}" + (" (основной)" if thr == PRIMARY_THRESHOLD else "")] = reader_pred(P, thr)
    variants["читатель, перемешанный порядок, всегда"] = reader_pred(PS, 0.0)
    variants["читатель, перемешанный, >= 0.8"] = reader_pred(PS, PRIMARY_THRESHOLD)

    base = variants[base_key]
    n_set = {s: sum(f["set"] == s for f in frames) for s in sets}
    print(f"\n=== Все кадры: {len(frames)} ===")
    print(f"верное вино в top-{args.k} эмбеддера (потолок для читателя): "
          f"{sum(t in c for t, c in zip(true, cands))}/{len(frames)}")
    print(f"{'вариант':46s}" + "".join(("%s n=%d" % (s, n_set[s])).rjust(22) for s in sets)
          + f"{'top1':>8s}   исправлено/сломано vs прод")
    summary = {}
    for k, pr in variants.items():
        cells = "".join(("%d/%d" % (sum(pr[i] == true[i] for i in range(len(frames)) if frames[i]["set"] == s), n_set[s])).rjust(22)
                        for s in sets)
        acc = sum(a == t for a, t in zip(pr, true))
        fx, br, thr, verdict = paired(base, pr, true)
        tail = "—" if k == base_key else f"{len(fx)}/{len(br)}  порог {thr:.1f} -> {verdict}"
        print(f"{k:46s}{cells}{acc / len(frames):>8.1%}   {tail}")
        summary[k] = {"top1": acc, "n": len(frames), "fixed": [frames[i]["key"] for i in fx],
                      "broken": [[frames[i]["key"], pr[i]] for i in br], "verdict": verdict}

    for k in ("1024x1024 + доп. эталоны", "1024x(1024+2560) + доп. эталоны", "читатель, всегда",
              f"читатель, уверенность >= {PRIMARY_THRESHOLD} (основной)"):
        v = summary[k]
        if not (v["fixed"] or v["broken"]):
            continue
        print(f"\n{k} против прода:")
        for key in v["fixed"]:
            print(f"   + {key[-60:]}")
        for key, pr in v["broken"]:
            print(f"   - {key[-48:]:50s} -> {pr[:48]}")

    agree = np.mean([int(np.argmax(a)) == int(np.argmax(b)) for a, b in zip(P, PS)])
    first = np.mean([int(np.argmax(a)) == 0 for a in P])
    print(f"\nЧитатель: выбирает 1-й вариант (ответ эмбеддера) в {first:.0%} кадров; "
          f"ответ совпадает при перемешивании в {agree:.0%} кадров")
    print(f"Латентность читателя ({torch.cuda.get_device_name(0)}): медиана {rd['latency_ms_median']:.0f} мс, "
          f"p95 {rd['latency_ms_p95']:.0f} мс (плюс эмбеддер 2560)")
    rows = [{"key": f["key"], "set": f["set"], "true": f["true_slug"], "candidates": cands[i],
             "options": [candidate_text(s, meta) for s in cands[i]], "probs": rd["probs"][i],
             "probs_shuffled": rd["probs_shuffled"][i], **{k: variants[k][i] for k in variants}}
            for i, f in enumerate(frames)]
    json.dump({"summary": summary, "reader_latency_ms": rd["latency_ms_median"], "rows": rows},
              open(RESULTS / "run2_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Сохранено: results/run2_summary.json")


if __name__ == "__main__":
    main()
