"""Общее для train.py и eval.py: загрузка Qwen3-VL-Embedding, кодирование
картинок (с градиентом и без), манифесты, метрики.

Модель грузится через официальный класс `Qwen3VLEmbedder` из репозитория
QwenLM/Qwen3-VL-Embedding (клонируется setup.sh), чтобы формат промпта,
препроцессинг картинок и last-token pooling совпадали с тем, на чём модель
обучена. Своего здесь только прямой вызов модели с градиентом — штатный
`process()` обёрнут в no_grad.
"""

import csv
import json
import os
import random
import sys
from pathlib import Path

import torch
import torch.nn.functional as F

torch.set_num_threads(int(os.environ.get("TORCH_THREADS", "8")))
from PIL import Image, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("WINE_DATA", ROOT / "data"))
REPO = Path(os.environ.get("QWEN_EMB_REPO", ROOT / "Qwen3-VL-Embedding"))
MODEL_ID = os.environ.get("QWEN_EMB_MODEL", "Qwen/Qwen3-VL-Embedding-8B")

# Одна инструкция для запроса и эталона: задача симметричная (фото -> фото).
# По карточке модели инструкция под задачу даёт +1..5% относительно дефолтной.
INSTRUCTION = ("Represent this photo of a wine bottle to find the exact same wine: "
               "producer, wine name, grape variety, sweetness and vintage on the label")

# ~1024 визуальных токена (32x32 px на токен после merge 2x2 патчей 16px).
# Медиана меньшей стороны наших фото ~400px, так что почти все фото
# проходят без уменьшения; поднимать имеет смысл только для крупных фото с полки.
DEFAULT_MAX_PIXELS = 1024 * 32 * 32


# ---------------------------------------------------------------- модель

def load_embedder(max_pixels: int = DEFAULT_MAX_PIXELS, attn: str = "sdpa"):
    if not (REPO / "src/models/qwen3_vl_embedding.py").exists():
        raise SystemExit(f"нет репозитория Qwen3-VL-Embedding в {REPO} — запустите setup.sh")
    sys.path.insert(0, str(REPO))
    from src.models.qwen3_vl_embedding import Qwen3VLEmbedder
    return Qwen3VLEmbedder(
        model_name_or_path=MODEL_ID,
        max_pixels=max_pixels,
        torch_dtype=torch.bfloat16,
        attn_implementation=attn,
    )


def encode(embedder, images: list, instruction: str = INSTRUCTION) -> torch.Tensor:
    """L2-нормированные эмбеддинги [B, D] в fp32. Градиент — если вызвано
    вне torch.no_grad() и у модели есть обучаемые параметры (LoRA)."""
    convs = [embedder.format_model_input(image=img, instruction=instruction) for img in images]
    inputs = embedder._preprocess_inputs(convs)
    inputs = {k: v.to(embedder.model.device) for k, v in inputs.items()}
    with torch.autocast("cuda", dtype=torch.bfloat16):
        out = embedder.model(**inputs, use_cache=False)
    emb = embedder._pooling_last(out.last_hidden_state, inputs["attention_mask"])
    return F.normalize(emb.float(), dim=-1)


@torch.no_grad()
def encode_paths(embedder, paths: list[str], batch_size: int = 8, desc: str = "") -> torch.Tensor:
    was_training = embedder.model.training
    embedder.model.eval()
    chunks = []
    for i in range(0, len(paths), batch_size):
        imgs = [load_image(DATA / p) for p in paths[i:i + batch_size]]
        chunks.append(encode(embedder, imgs).cpu())
        if desc and (i // batch_size) % 25 == 0:
            print(f"  [{desc}] {min(i + batch_size, len(paths))}/{len(paths)}", flush=True)
    if was_training:
        embedder.model.train()
    return torch.cat(chunks)


# ---------------------------------------------------------------- данные

def load_image(path) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            # прозрачный фон у части эталонов (png) -> белый, как на витрине
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        return im.convert("RGB")


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_references() -> tuple[list[str], list[str]]:
    rows = read_csv(DATA / "manifests/references.csv")
    return [r["slug"] for r in rows], [r["path"] for r in rows]


def load_query_set(name: str) -> list[dict]:
    """val — отложенные доп. фото каталога; field — фото организаторов;
    own — собственные полевые фото. field/own в обучении не участвуют."""
    path = {"val": DATA / "manifests/val_queries.csv",
            "train": DATA / "manifests/train_queries.csv",
            "field": DATA / "eval/field_labels.csv",
            "own": DATA / "eval/own_labels.csv"}[name]
    return read_csv(path)


def load_shared_groups() -> list[list[str]]:
    return json.loads((DATA / "manifests/shared_groups.json").read_text(encoding="utf-8"))


def load_meta() -> dict:
    p = DATA / "manifests/catalog_meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---------------------------------------------------------------- аугментации

def light_augment(img: Image.Image, rng: random.Random) -> Image.Image:
    """Мягкие искажения для реальных доп. фото: они уже «другой ракурс»,
    сильно портить их не нужно."""
    w, h = img.size
    if rng.random() < 0.5:  # случайный кроп 80-100%
        s = rng.uniform(0.8, 1.0)
        cw, ch = int(w * s), int(h * s)
        x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
        img = img.crop((x, y, x + cw, y + ch))
    if rng.random() < 0.5:
        img = img.rotate(rng.uniform(-8, 8), resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
    if rng.random() < 0.5:
        from PIL import ImageEnhance
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.7, 1.3))
        img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.75, 1.25))
        img = ImageEnhance.Color(img).enhance(rng.uniform(0.7, 1.3))
    if rng.random() < 0.25:
        img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.3, 1.2)))
    if rng.random() < 0.4:
        import io
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=rng.randint(40, 90))
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
    return img


def field_simulation(img: Image.Image, rng: random.Random) -> Image.Image:
    """Имитация фото с полки для вин, у которых есть только эталон:
    цилиндр, поворот на фоне, блик (augment_realistic из основного репо)."""
    import cv2
    cv2.setNumThreads(1)
    from augment_realistic import simulate_realistic_photo
    return simulate_realistic_photo(img, seed=rng.randint(0, 2**31 - 1))


# ---------------------------------------------------------------- метрики

def rank_metrics(query_emb: torch.Tensor, ref_emb: torch.Tensor, ref_slugs: list[str],
                 true_slugs: list[str], k: int = 5) -> tuple[dict, list[list[tuple[str, float]]]]:
    sims = query_emb @ ref_emb.T
    topv, topi = sims.topk(k, dim=1)
    tops = [[(ref_slugs[j], float(v)) for j, v in zip(row_i.tolist(), row_v.tolist())]
            for row_i, row_v in zip(topi, topv)]
    in_index = set(ref_slugs)
    top1 = sum(t[0][0] == s for t, s in zip(tops, true_slugs))
    topk = sum(any(c == s for c, _ in t) for t, s in zip(tops, true_slugs))
    missing = sum(s not in in_index for s in true_slugs)
    n = len(true_slugs)
    return {"n": n, "top1": top1 / n if n else 0.0, f"top{k}": topk / n if n else 0.0,
            "top1_count": top1, f"top{k}_count": topk, "not_in_index": missing}, tops
