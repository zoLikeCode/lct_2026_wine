"""Field-set sweep of bottle-crop margins and geometry-only box selection.

This is a diagnostic experiment; production defaults remain unchanged.

    python -m retrieval.bbox_policy_experiment
"""

import csv
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps
from ultralytics import YOLO

from data_prep import config
from .backbones import load_backbone
from .cheap_policy import expand_box, select_by_confidence, select_by_geometry
from .index import EmbeddingIndex
from .predict import WineFinder
from .preprocess_field import BOTTLE_WEIGHTS, enhance

PHOTOS_DIR = Path("~/Downloads/Реальные фото").expanduser()
BACKBONE_NAME = "siglip2_so400m512"
DETECTOR_MIN_CONF = 0.05
CURRENT_CONFIDENCE = 0.50
PAD_FRACTIONS = (0.00, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40)
GEOMETRY_MIN_CONFIDENCES = (0.05, 0.25)
BATCH_SIZE = 2


def _encode_chunks(backbone, images: list[Image.Image]) -> list[np.ndarray]:
    result = []
    for start in range(0, len(images), BATCH_SIZE):
        result.extend(backbone.encode_batch(images[start:start + BATCH_SIZE]))
    return result


def _rank(index: EmbeddingIndex, embedding: np.ndarray) -> list[dict]:
    return [{"slug": r.slug, "score": r.score} for r in index.search(embedding, top_k=5)]


def _metrics(rows: list[dict], policy: str) -> dict:
    n = len(rows)
    top1 = sum(r["predictions"][policy][0]["slug"] == r["true_slug"] for r in rows)
    top5 = sum(any(c["slug"] == r["true_slug"] for c in r["predictions"][policy]) for r in rows)
    fixed, broken = [], []
    for row in rows:
        base_ok = row["predictions"]["production"][0]["slug"] == row["true_slug"]
        new_ok = row["predictions"][policy][0]["slug"] == row["true_slug"]
        if new_ok and not base_ok:
            fixed.append(row["image"])
        elif base_ok and not new_ok:
            broken.append(row["image"])
    return {"top1": top1 / n, "top1_count": top1,
            "top5": top5 / n, "top5_count": top5,
            "fixed": fixed, "broken": broken}


def main() -> None:
    with (config.OUTPUTS_DIR / "field_labels.csv").open(encoding="utf-8") as f:
        labels = list(csv.DictReader(f))

    finder = WineFinder(backbone=BACKBONE_NAME, orb_weight=0.0,
                        differentiator_weight=0.0, preprocess=True)
    index = finder.index
    backbone = finder.backbone
    detector = YOLO(str(BOTTLE_WEIGHTS))
    pad_policies = [f"pad_{pad:.2f}" for pad in PAD_FRACTIONS]
    geometry_policies = [f"{strategy}_conf{threshold:.2f}"
                         for threshold in GEOMETRY_MIN_CONFIDENCES
                         for strategy in ("largest", "center")]
    policies = ["production", *pad_policies, *geometry_policies]
    rows = []

    for number, label in enumerate(labels, start=1):
        image_path = PHOTOS_DIR / label["image_path"]
        if not image_path.exists():
            print(f"skip missing: {image_path}", flush=True)
            continue
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source.convert("RGB")).convert("RGB")

        production = finder.predict(image, top_k=5)
        predictions = {"production": [{"slug": p.slug, "score": p.score} for p in production]}
        detector_result = detector.predict(source=image, verbose=False, device="cpu",
                                           conf=DETECTOR_MIN_CONF)[0]
        detections = []
        for box in detector_result.boxes:
            if detector.names[int(box.cls[0])] == "bottle":
                detections.append({"confidence": float(box.conf[0]),
                                   "xyxy": box.xyxy[0].tolist()})
        confidences = [d["confidence"] for d in detections]
        default_box = select_by_confidence(confidences, CURRENT_CONFIDENCE)

        # Encode only needed (proposal, padding) combinations, sharing the 15%
        # crop between the margin sweep and geometry policies.
        crop_keys = {(default_box, pad) for pad in PAD_FRACTIONS} if default_box is not None else set()
        for threshold in GEOMETRY_MIN_CONFIDENCES:
            for strategy in ("largest", "center"):
                selected = select_by_geometry(detections, image.size, strategy, threshold)
                if selected is not None:
                    crop_keys.add((selected, 0.15))
        crop_keys = sorted(crop_keys)
        crop_images = []
        for detection_index, pad in crop_keys:
            bounds = expand_box(detections[detection_index]["xyxy"], image.width, image.height, pad)
            crop_images.append(enhance(image.crop(bounds)))
        crop_embeddings = _encode_chunks(backbone, crop_images)
        embedding_by_key = dict(zip(crop_keys, crop_embeddings))

        for pad, policy in zip(PAD_FRACTIONS, pad_policies):
            predictions[policy] = (predictions["production"] if default_box is None
                                   else _rank(index, embedding_by_key[(default_box, pad)]))

        for threshold in GEOMETRY_MIN_CONFIDENCES:
            for strategy in ("largest", "center"):
                policy = f"{strategy}_conf{threshold:.2f}"
                selected = select_by_geometry(detections, image.size, strategy, threshold)
                predictions[policy] = (predictions["production"] if selected is None
                                       else _rank(index, embedding_by_key[(selected, 0.15)]))

        rows.append({"image": label["image_path"], "true_slug": label["true_slug"],
                     "n_boxes": len(detections), "selected_confidence_box":
                     detections[default_box] if default_box is not None else None,
                     "predictions": predictions})
        if number % 5 == 0 or number == len(labels):
            print(f"{number}/{len(labels)} processed", flush=True)
        if backbone.device.type == "mps" and number % 10 == 0:
            torch.mps.empty_cache()

    output = {"n": len(rows), "backbone": BACKBONE_NAME,
              "detector_min_conf": DETECTOR_MIN_CONF,
              "pad_fractions": PAD_FRACTIONS,
              "geometry_min_confidences": GEOMETRY_MIN_CONFIDENCES,
              "policies": {p: _metrics(rows, p) for p in policies}, "rows": rows}
    out_path = config.OUTPUTS_DIR / "bbox_policy_experiment.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out_path}")
    for policy, result in output["policies"].items():
        print(f"{policy:20s} top1={result['top1']:.1%} ({result['top1_count']}/{len(rows)}) "
              f"top5={result['top5']:.1%} ({result['top5_count']}/{len(rows)}) "
              f"fixed={len(result['fixed'])} broken={len(result['broken'])}")


if __name__ == "__main__":
    main()
