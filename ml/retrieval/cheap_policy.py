"""Small pure helpers for evaluating inference-time image selection policies."""

import numpy as np


def select_by_confidence(confidences: list[float], threshold: float) -> int | None:
    """Index of the highest-confidence detection meeting threshold, if any."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    eligible = [i for i, confidence in enumerate(confidences) if confidence >= threshold]
    return max(eligible, key=lambda i: confidences[i]) if eligible else None


def select_by_similarity(similarities: list[float]) -> int | None:
    """Index of the detection whose crop has the strongest retrieval match."""
    return max(range(len(similarities)), key=lambda i: similarities[i]) if similarities else None


def fuse_embeddings(whole: np.ndarray, crop: np.ndarray, crop_weight: float) -> np.ndarray:
    """Unit-normalized weighted sum of whole-frame and bottle-crop embeddings."""
    if not 0.0 <= crop_weight <= 1.0:
        raise ValueError("crop_weight must be between 0 and 1")
    whole = np.asarray(whole, dtype=np.float32).reshape(-1)
    crop = np.asarray(crop, dtype=np.float32).reshape(-1)
    if whole.shape != crop.shape:
        raise ValueError("whole and crop embeddings must have the same shape")

    whole_norm = float(np.linalg.norm(whole))
    crop_norm = float(np.linalg.norm(crop))
    if whole_norm <= 1e-8 or crop_norm <= 1e-8:
        raise ValueError("embeddings must have non-zero norm")
    whole = whole / whole_norm
    crop = crop / crop_norm
    mixed = (1.0 - crop_weight) * whole + crop_weight * crop
    mixed_norm = float(np.linalg.norm(mixed))
    # Opposing vectors can cancel; in that degenerate case keep the whole view.
    return whole if mixed_norm <= 1e-8 else mixed / mixed_norm


def expand_box(xyxy: list[float], image_width: int, image_height: int,
               pad_fraction: float) -> tuple[int, int, int, int]:
    """Add padding proportional to box dimensions and clip to image bounds."""
    if len(xyxy) != 4:
        raise ValueError("xyxy must contain four coordinates")
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")
    if not 0.0 <= pad_fraction <= 1.0:
        raise ValueError("pad_fraction must be between 0 and 1")
    x0, y0, x1, y1 = map(float, xyxy)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("box must have positive width and height")
    pad_x = (x1 - x0) * pad_fraction
    pad_y = (y1 - y0) * pad_fraction
    return (
        max(0, int(x0 - pad_x)), max(0, int(y0 - pad_y)),
        min(image_width, int(x1 + pad_x)), min(image_height, int(y1 + pad_y)),
    )


def select_by_geometry(detections: list[dict], image_size: tuple[int, int], strategy: str,
                       min_confidence: float = 0.05) -> int | None:
    """Choose an eligible box by area or center distance; confidence breaks ties."""
    if strategy not in {"largest", "center"}:
        raise ValueError("strategy must be 'largest' or 'center'")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    width, height = image_size
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")

    eligible = [i for i, detection in enumerate(detections)
                if detection["confidence"] >= min_confidence]
    if not eligible:
        return None

    if strategy == "largest":
        def key(i):
            x0, y0, x1, y1 = detections[i]["xyxy"]
            return ((x1 - x0) * (y1 - y0), detections[i]["confidence"])
        return max(eligible, key=key)

    center_x, center_y = width / 2, height / 2
    def key(i):
        x0, y0, x1, y1 = detections[i]["xyxy"]
        box_x, box_y = (x0 + x1) / 2, (y0 + y1) / 2
        distance = ((box_x - center_x) / width) ** 2 + ((box_y - center_y) / height) ** 2
        return (-distance, detections[i]["confidence"])
    return max(eligible, key=key)
