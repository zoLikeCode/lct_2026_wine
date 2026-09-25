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
