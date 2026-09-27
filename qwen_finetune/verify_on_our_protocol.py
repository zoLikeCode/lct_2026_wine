"""Независимая проверка дообученного Qwen3-VL-Embedding на нашем протоколе.

Что делает иначе, чем `eval.py` коллеги, и зачем:

1. **Одинаковое поле кандидатов.** Индекс Qwen содержит 2083 вина, наш —
   2103. Оба ограничиваются пересечением, а кадры, чьё верное вино в него
   не входит, исключаются из метрики. Иначе разница между моделями смешалась
   бы с разницей между индексами.
2. **Наши кадры и наша разметка** — 258 полевых фото с поправками §11.33
   (две переразметки по читаемой этикетке, три исключения).
3. **Попарный критерий Макнемара** — то, чего в отчёте коллеги нет, и о чём
   он сам написал: значимо при |исправлено − сломано| >= 2·√(сумма).
4. **Qwen получает сырой кадр**, как в его пайплайне, а не наш `enhance` —
   чтобы не портить чужую модель нашей нормализацией.

    python qwen_finetune/verify_on_our_protocol.py --adapter ~/Downloads/v2_best/runs/v2/best
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

QWEN_INDEX = Path("~/Downloads/v2_best/results/embeddings_qwen3vl8b_v2_best.npz").expanduser()
SETS = (("202 команды", "~/Downloads/real_photos", "field_labels_own.csv"),
        ("56 организатора", "~/Downloads/Реальные фото", "field_labels.csv"))


def load_queries() -> list[tuple[Path, str, str]]:
    from data_prep import config
    out = []
    for title, photos_dir, labels_name in SETS:
        root = Path(photos_dir).expanduser()
        with open(config.OUTPUTS_DIR / labels_name, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("true_slug") and (root / row["image_path"]).exists():
                    out.append((root / row["image_path"], row["true_slug"], title))
    return out


def siglip_predictions(queries, allowed: set[str]) -> dict[str, str]:
    """Ответы текущего production на тех же кадрах и том же поле кандидатов,
    из кеша эмбеддингов — пересчитывать ничего не нужно."""
    from data_prep import config
    from retrieval.predict import BACKBONE, PREPROCESS

    data = np.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}.npz")
    slugs = np.array([str(s) for s in data["slugs"]])
    keep = np.array([s in allowed for s in slugs])
    index = data["embeddings"][keep].astype(np.float32)
    index /= np.clip(np.linalg.norm(index, axis=1, keepdims=True), 1e-8, None)
    kept_slugs = slugs[keep]

    cached = {}
    for name in ("own", "org"):
        path = config.OUTPUTS_DIR / f"query_embeddings_{name}_{BACKBONE}_{PREPROCESS}.npz"
        blob = np.load(path, allow_pickle=True)
        for image_path, emb in zip(blob["image_paths"], blob["embeddings"]):
            cached[str(image_path)] = emb

    answers = {}
    for path, _, _ in queries:
        key = f"{path.parent.name}/{path.name}"
        emb = cached.get(key)
        if emb is None:
            continue
        emb = emb / max(np.linalg.norm(emb), 1e-8)
        answers[str(path)] = str(kept_slugs[int(np.argmax(index @ emb))])
    return answers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default="qwen_verification.json")
    args = parser.parse_args()

    from data_prep import config
    from qwen_finetune.common import INSTRUCTION, load_embedder

    blob = np.load(QWEN_INDEX, allow_pickle=True)
    qwen_slugs = np.array([str(s) for s in blob["slugs"]])
    qwen_index = blob["embeddings"].astype(np.float32)
    qwen_index /= np.clip(np.linalg.norm(qwen_index, axis=1, keepdims=True), 1e-8, None)

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        our_slugs = {r["slug"] for r in json.load(f) if r["photo_file"]}
    allowed = set(qwen_slugs) & our_slugs
    print(f"Поле кандидатов: Qwen {len(qwen_slugs)}, наш {len(our_slugs)}, общее {len(allowed)}")

    queries = load_queries()
    usable = [q for q in queries if q[1] in allowed]
    print(f"Кадров: {len(queries)}, пригодны (верное вино в общем поле): {len(usable)}")
    if args.limit:
        usable = usable[:args.limit]

    siglip = siglip_predictions(usable, allowed)
    keep = np.array([s in allowed for s in qwen_slugs])
    qwen_index, qwen_kept = qwen_index[keep], qwen_slugs[keep]

    embedder = load_embedder()
    embedder.model = __import__("peft").PeftModel.from_pretrained(embedder.model, args.adapter)
    embedder.model.eval()
    device = embedder.model.device
    print(f"Модель загружена на {device}")

    rows, latencies = [], []
    for i, (path, true_slug, title) in enumerate(usable, start=1):
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
        started = time.perf_counter()
        conv = embedder.format_model_input(image=image, instruction=INSTRUCTION)
        inputs = {k: v.to(device) for k, v in embedder._preprocess_inputs([conv]).items()}
        with torch.no_grad():
            out = embedder.model(**inputs, use_cache=False)
        emb = embedder._pooling_last(out.last_hidden_state, inputs["attention_mask"]).float()
        emb = (emb / emb.norm(dim=-1, keepdim=True)).cpu().numpy()[0]
        latencies.append((time.perf_counter() - started) * 1000)

        rows.append({"path": str(path), "set": title, "true": true_slug,
                     "qwen": str(qwen_kept[int(np.argmax(qwen_index @ emb))]),
                     "siglip": siglip.get(str(path))})
        if i % 10 == 0:
            print(f"  {i}/{len(usable)}  ({np.median(latencies) / 1000:.1f} с/кадр)", flush=True)

    paired = [r for r in rows if r["siglip"] is not None]
    fixed = sum(r["qwen"] == r["true"] and r["siglip"] != r["true"] for r in paired)
    broken = sum(r["qwen"] != r["true"] and r["siglip"] == r["true"] for r in paired)
    threshold = 2 * (fixed + broken) ** 0.5

    print(f"\n{'набор':18s} {'n':>4s} {'SigLIP2':>9s} {'Qwen+LoRA':>11s}")
    for title in [s[0] for s in SETS] + ["ВСЕГО"]:
        part = paired if title == "ВСЕГО" else [r for r in paired if r["set"] == title]
        if not part:
            continue
        s = sum(r["siglip"] == r["true"] for r in part) / len(part)
        q = sum(r["qwen"] == r["true"] for r in part) / len(part)
        print(f"{title:18s} {len(part):>4d} {s:>8.1%} {q:>10.1%}")

    print(f"\nисправлено {fixed}, сломано {broken}, |разница| {abs(fixed - broken)} "
          f"против порога {threshold:.1f} -> "
          f"{'ЗНАЧИМО' if abs(fixed - broken) >= threshold else 'шум'}")
    print(f"латентность Qwen: медиана {np.median(latencies):.0f} мс")

    with open(config.OUTPUTS_DIR / args.out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "fixed": fixed, "broken": broken,
                   "threshold": threshold, "latency_ms": latencies}, f, ensure_ascii=False, indent=2)
    print(f"Сохранено: {config.OUTPUTS_DIR / args.out}")


if __name__ == "__main__":
    main()
