"""Сравнение вариантов матчера по прямой разделяющей способности.

Метрика — доля запросов, где верный кандидат набирает больше inlier-ов, чем
ЛУЧШИЙ из неверных. Она измеряет сигнал напрямую, без реранкера и без
обучающих данных, поэтому варианты сравниваются за минуты, а не за часы.
База (§11.30): 61.8%.

Проверяются две гипотезы о том, почему сигнал шумный:

1. **Разбавление фоном.** В полевом кадре большая часть ключевых точек лежит
   на соседних бутылках и ценниках, а целевая этикетка занимает малую долю.
   Важная асимметрия: кроп вреден ЭМБЕДДЕРУ (теряется контекст бутылки,
   доказано оракулом §11.31), но матчеру контекст не нужен — ему нужна
   этикетка, и фон только мешает.
2. **Нормировка.** В §11.30 inlier-ы делились на фиксированные 1024 точки,
   из-за чего скор зависел от текстурности кадра. Доля inlier-ов среди
   ПРЕДЛОЖЕННЫХ соответствий — величина другой природы.

    python -m retrieval.matcher_variants_experiment --sample 80
"""

import argparse
import json
import random

import numpy as np
import torch
from PIL import Image, ImageOps

from data_prep import config
from .learned_matcher import _models, _tensor
from .predict import PREPROCESS
from .preprocess_field import crop_to_bottle, preprocess


@torch.inference_mode()
def describe_variant(image: Image.Image, keep_aspect: bool, max_keypoints: int):
    """При `keep_aspect` длинная сторона приводится к 512, короткая считается
    по пропорции и округляется до кратного 16 (требование DISK)."""
    disk, _ = _models()
    if keep_aspect:
        width, height = image.size
        scale = 512 / max(width, height)
        size = (max(16, int(width * scale) // 16 * 16), max(16, int(height * scale) // 16 * 16))
        image = image.convert("RGB").resize(size, Image.BILINEAR)
        array = np.asarray(image, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).to(next(disk.parameters()).device)
    else:
        tensor = _tensor(image)
    features = disk(tensor, max_keypoints, pad_if_not_divisible=True)[0]
    return features.keypoints, features.descriptors


@torch.inference_mode()
def match_scores(query, candidate, max_keypoints: int) -> tuple[float, float]:
    """(доля от максимума точек — как в §11.30, доля от предложенных соответствий)."""
    import cv2
    import kornia.feature as KF

    kp_q, desc_q = query
    kp_c, desc_c = candidate
    if len(kp_q) < 8 or len(kp_c) < 8:
        return 0.0, 0.0
    _, matcher = _models()
    _, indices = matcher(desc_q, desc_c, KF.laf_from_center_scale_ori(kp_q[None]),
                         KF.laf_from_center_scale_ori(kp_c[None]))
    proposed = int(indices.shape[0])
    if proposed < 8:
        return 0.0, 0.0
    points_q = kp_q[indices[:, 0]].cpu().numpy().astype(np.float32).reshape(-1, 1, 2)
    points_c = kp_c[indices[:, 1]].cpu().numpy().astype(np.float32).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(points_q, points_c, cv2.RANSAC, 4.0)
    if mask is None:
        return 0.0, 0.0
    inliers = float(mask.sum())
    return inliers / max_keypoints, inliers / proposed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=80)
    parser.add_argument("--seed", type=int, default=13)
    args = parser.parse_args()

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        photo_of = {r["slug"]: (r["photo_file"] if r["photo_file"].startswith("/")
                                else str(config.UPLOADS_DIR / r["photo_file"]))
                    for r in json.load(f) if r["photo_file"]}

    groups = []
    for name in ("rerank_match_own.json", "rerank_match_org.json"):
        with open(config.OUTPUTS_DIR / name, encoding="utf-8") as f:
            groups.extend(json.load(f)["groups"])
    usable = [g for g in groups if g["true_slug"] in g["slugs"]]
    sample = random.Random(args.seed).sample(usable, min(args.sample, len(usable)))
    hard = sum(1 for g in sample if g["slugs"][0] != g["true_slug"])
    print(f"Выборка: {len(sample)} запросов, из них трудных {hard}\n")

    # Пропорции проверены на смоук-тесте и не выглядят фактором: гомография
    # поглощает анизотропное масштабирование, если оно одинаково для обеих
    # картинок. Поэтому в основной прогон идут только две гипотезы, ради
    # которых всё затевалось, — фон и нормировка (вторая считается попутно).
    variants = (
        ("базовый (целый кадр)", False, False, 1024),
        ("кроп по бутылке", False, True, 1024),
    )

    reference_cache: dict[tuple, object] = {}
    results = {}
    for title, keep_aspect, use_crop, max_kp in variants:
        wins_abs = wins_rate = total = 0
        for g in sample:
            with Image.open(g["query"]) as raw:
                image = preprocess(raw, PREPROCESS)
            if use_crop:
                cropped = crop_to_bottle(ImageOps.exif_transpose(image).convert("RGB"))
                if cropped is not None:
                    image = cropped
            query = describe_variant(image, keep_aspect, max_kp)

            abs_scores, rate_scores = [], []
            for slug in g["slugs"]:
                key = (slug, keep_aspect, max_kp)
                if key not in reference_cache:
                    with Image.open(photo_of[slug]) as ref:
                        reference_cache[key] = describe_variant(ref.convert("RGB"), keep_aspect, max_kp)
                a, r = match_scores(query, reference_cache[key], max_kp)
                abs_scores.append(a)
                rate_scores.append(r)

            ti = g["slugs"].index(g["true_slug"])
            for scores, counter in ((abs_scores, "abs"), (rate_scores, "rate")):
                other = max(v for i, v in enumerate(scores) if i != ti)
                if scores[ti] > other:
                    if counter == "abs":
                        wins_abs += 1
                    else:
                        wins_rate += 1
            total += 1

        results[title] = (wins_abs / total, wins_rate / total)
        print(f"  {title:34s} верный выше конкурента: "
              f"по доле от точек {wins_abs / total:.1%}, по доле от соответствий {wins_rate / total:.1%}")

    print("\nБаза из §11.30 на полном наборе: 61.8% (по доле от точек, квадрат)")
    out = config.OUTPUTS_DIR / "matcher_variants.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
