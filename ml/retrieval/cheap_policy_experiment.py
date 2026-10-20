"""Paired field-set comparison of cheap bottle selection / image-view policies.

Runs current production prediction, low-confidence detector fallbacks,
retrieval-based multi-box selection, whole+crop embedding fusion, and mild
exposure TTA on the same labeled field photos. Does not change production
defaults. Results are written under ignored outputs/.

    python -m retrieval.cheap_policy_experiment
"""

import csv
import json
import statistics
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageEnhance, ImageOps
from ultralytics import YOLO

from data_prep import config
from .backbones import load_backbone
from .cheap_policy import fuse_embeddings, select_by_confidence, select_by_similarity
from .index import EmbeddingIndex
from .predict import WineFinder
from .preprocess_field import BOTTLE_PAD_FRAC, BOTTLE_WEIGHTS, enhance

PHOTOS_DIR = Path("~/Downloads/Реальные фото").expanduser()
DETECTOR_MIN_CONF = 0.05
CONFIDENCE_THRESHOLDS = (0.5, 0.35, 0.2, 0.1)
FUSION_WEIGHTS = (0.25, 0.5, 0.75)
TTA_BRIGHTNESS = (0.92, 1.0, 1.08)
BACKBONE_NAME = "siglip2_so400m512"
BATCH_SIZE = 2


def _rank(index: EmbeddingIndex, embedding: np.ndarray) -> list[dict]:
    return [{"slug": r.slug, "score": r.score} for r in index.search(embedding, top_k=5)]


def _crop_box(image: Image.Image, xyxy: list[float]) -> Image.Image:
    x0, y0, x1, y1 = xyxy
    width, height = image.size
    pad_x = (x1 - x0) * BOTTLE_PAD_FRAC
    pad_y = (y1 - y0) * BOTTLE_PAD_FRAC
    bounds = (
        max(0, int(x0 - pad_x)), max(0, int(y0 - pad_y)),
        min(width, int(x1 + pad_x)), min(height, int(y1 + pad_y)),
    )
    return image.crop(bounds)


def _encode_in_chunks(backbone, images: list[Image.Image]) -> tuple[list[np.ndarray], float]:
    """Encode many proposals without the overhead of one model call per crop."""
    embeddings = []
    started = time.perf_counter()
    for offset in range(0, len(images), BATCH_SIZE):
        batch = images[offset:offset + BATCH_SIZE]
        embeddings.extend(backbone.encode_batch(batch))
    return embeddings, (time.perf_counter() - started) * 1000


def _metrics(rows: list[dict], policy: str) -> dict:
    n = len(rows)
    hits1 = sum(row["predictions"][policy][0]["slug"] == row["true_slug"] for row in rows)
    hits5 = sum(any(c["slug"] == row["true_slug"] for c in row["predictions"][policy]) for row in rows)
    return {"top1": hits1 / n, "top1_count": hits1, "top5": hits5 / n, "top5_count": hits5}


def _paired_changes(rows: list[dict], policy: str, baseline: str = "production") -> dict:
    fixed, broken, same = [], [], []
    for row in rows:
        base_ok = row["predictions"][baseline][0]["slug"] == row["true_slug"]
        new_ok = row["predictions"][policy][0]["slug"] == row["true_slug"]
        (fixed if new_ok and not base_ok else broken if base_ok and not new_ok else same).append(row["image"])
    return {"fixed": fixed, "broken": broken, "otherwise_same": len(same)}


def main() -> None:
    labels_path = config.OUTPUTS_DIR / "field_labels.csv"
    with labels_path.open(encoding="utf-8") as f:
        labels = list(csv.DictReader(f))

    finder = WineFinder(backbone=BACKBONE_NAME, orb_weight=0.0, differentiator_weight=0.0, preprocess=True)
    index = finder.index
    backbone = finder.backbone
    model = YOLO(str(BOTTLE_WEIGHTS))
    policies = ["production"]
    policies += [f"confidence_{threshold:.2f}" for threshold in CONFIDENCE_THRESHOLDS]
    policies += ["retrieval_selected"]
    policies += [f"fused_{weight:.2f}" for weight in FUSION_WEIGHTS]
    policies += ["tta_brightness"]

    rows = []
    latency_samples: dict[str, list[float]] = {p: [] for p in policies}
    det_counts = []

    for number, label in enumerate(labels, start=1):
        image_path = PHOTOS_DIR / label["image_path"]
        if not image_path.exists():
            print(f"skip missing: {image_path}", flush=True)
            continue
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source.convert("RGB")).convert("RGB")

        # True current production path is the reference, timed end to end.
        started = time.perf_counter()
        production = finder.predict(image, top_k=5)
        latency_samples["production"].append((time.perf_counter() - started) * 1000)
        predictions = {"production": [{"slug": p.slug, "score": p.score} for p in production]}

        # Collect all bottle proposals once, including low-confidence boxes.
        started = time.perf_counter()
        result = model.predict(source=image, verbose=False, device="cpu", conf=DETECTOR_MIN_CONF)[0]
        detections = []
        for box in result.boxes:
            if model.names[int(box.cls[0])] != "bottle":
                continue
            confidence = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            detections.append({"confidence": confidence, "xyxy": xyxy})
        detector_ms = (time.perf_counter() - started) * 1000
        det_counts.append(len(detections))

        full_processed = enhance(image)
        full_started = time.perf_counter()
        full_embedding = backbone.encode(full_processed)
        full_ms = (time.perf_counter() - full_started) * 1000
        full_rank = _rank(index, full_embedding)

        crops = [enhance(_crop_box(image, detection["xyxy"])) for detection in detections]
        crop_embeddings, crop_batch_ms = _encode_in_chunks(backbone, crops)
        # Estimate the one-selected-crop cost from amortized batched time;
        # retrieval-selected uses the cost of scoring every proposal.
        crop_ms_each = crop_batch_ms / max(len(crops), 1)
        confidence_values = [d["confidence"] for d in detections]

        # Lower-threshold policies only fall back to detections below .5;
        # when no crop qualifies, they use the same enhanced whole image.
        for threshold in CONFIDENCE_THRESHOLDS:
            name = f"confidence_{threshold:.2f}"
            selected = select_by_confidence(confidence_values, threshold)
            if selected is None:
                predictions[name] = full_rank
                latency_samples[name].append(detector_ms + full_ms)
            else:
                predictions[name] = _rank(index, crop_embeddings[selected])
                latency_samples[name].append(detector_ms + crop_ms_each)

        # Choose among all candidate bottle crops by each crop's best catalog
        # cosine score (an intentionally simple test of the proposed policy).
        crop_top_scores = [_rank(index, emb)[0]["score"] for emb in crop_embeddings]
        retrieval_selected = select_by_similarity(crop_top_scores)
        if retrieval_selected is None:
            predictions["retrieval_selected"] = full_rank
            latency_samples["retrieval_selected"].append(detector_ms + full_ms)
        else:
            predictions["retrieval_selected"] = _rank(index, crop_embeddings[retrieval_selected])
            latency_samples["retrieval_selected"].append(detector_ms + crop_batch_ms)

        # Whole+crop embedding fusion uses the current confidence-selected box.
        default_crop = select_by_confidence(confidence_values, 0.5)
        if default_crop is None:
            default_crop_embedding = full_embedding
            crop_encode_ms = 0.0
        else:
            default_crop_embedding = crop_embeddings[default_crop]
            crop_encode_ms = crop_ms_each
        for weight in FUSION_WEIGHTS:
            name = f"fused_{weight:.2f}"
            fused = fuse_embeddings(full_embedding, default_crop_embedding, weight)
            predictions[name] = _rank(index, fused)
            latency_samples[name].append(detector_ms + full_ms + crop_encode_ms)

        # Mild exposure TTA on the exact production-preprocessed image. Average
        # normalized embeddings; no horizontal flip, which would mirror label text.
        processed = full_processed if default_crop is None else crops[default_crop]
        tta_started = time.perf_counter()
        tta_images = [ImageEnhance.Brightness(processed).enhance(factor) for factor in TTA_BRIGHTNESS]
        tta_embeddings, _ = _encode_in_chunks(backbone, tta_images)
        tta_embedding = np.mean(tta_embeddings, axis=0)
        predictions["tta_brightness"] = _rank(index, tta_embedding)
        latency_samples["tta_brightness"].append(detector_ms + (time.perf_counter() - tta_started) * 1000)

        rows.append({"image": label["image_path"], "true_slug": label["true_slug"], "n_boxes": len(detections),
                     "predictions": predictions,
                     "selected_by_confidence": detections[default_crop] if default_crop is not None else None,
                     "selected_by_retrieval": detections[retrieval_selected] if retrieval_selected is not None else None})
        if number % 5 == 0 or number == len(labels):
            print(f"{number}/{len(labels)} processed", flush=True)
        if backbone.device.type == "mps" and number % 10 == 0:
            torch.mps.empty_cache()

    output = {
        "backbone": BACKBONE_NAME,
        "n": len(rows),
        "detector_min_conf": DETECTOR_MIN_CONF,
        "confidence_thresholds": CONFIDENCE_THRESHOLDS,
        "fusion_weights": FUSION_WEIGHTS,
        "tta_brightness_factors": TTA_BRIGHTNESS,
        "mean_detected_boxes": statistics.mean(det_counts) if det_counts else 0,
        "policies": {},
        "rows": rows,
    }
    for policy in policies:
        output["policies"][policy] = {
            **_metrics(rows, policy),
            "paired_vs_production": _paired_changes(rows, policy),
            "median_latency_ms": statistics.median(latency_samples[policy]),
            "p95_latency_ms": sorted(latency_samples[policy])[min(int(len(latency_samples[policy]) * 0.95), len(latency_samples[policy]) - 1)],
        }

    output_path = config.OUTPUTS_DIR / "cheap_policy_experiment.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {output_path}")
    for policy, metrics in output["policies"].items():
        print(f"{policy:22s} top1={metrics['top1']:.1%} ({metrics['top1_count']}/{len(rows)}) "
              f"top5={metrics['top5']:.1%} ({metrics['top5_count']}/{len(rows)}) "
              f"median={metrics['median_latency_ms']:.0f}ms p95={metrics['p95_latency_ms']:.0f}ms "
              f"fixed={len(metrics['paired_vs_production']['fixed'])} "
              f"broken={len(metrics['paired_vs_production']['broken'])}")


if __name__ == "__main__":
    main()
