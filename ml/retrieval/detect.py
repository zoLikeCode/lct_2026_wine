"""Настоящая детекция этикетки (zero-shot object detection) вместо наивного
autocrop по цвету фона, который дал отрицательный результат (см.
docs/findings.md §8.1). По прямому свидетельству экспертов, у компании именно
детекция + работа только с кропом этикетки ощутимо подняла качество.

Модель: OWL-ViT-base-patch32 — в 4 раза меньше патчей, чем у OWLv2-base-
patch16-ensemble (который оказался практически непригоден по скорости: ~2.8-
12.6с/фото, что на 1636 каталожных фото — часы, а не минуты).
"""

import torch
from PIL import Image

QUERIES = ["wine bottle label", "wine bottle"]
LABEL_PRIORITY = {"wine bottle label": 1.0, "wine bottle": 0.5}
MODEL_ID = "google/owlvit-base-patch32"

_processor = None
_model = None
_device = None


def _load():
    global _processor, _model, _device
    if _model is None:
        from transformers import OwlViTForObjectDetection, OwlViTProcessor
        from .backbones import get_device
        _device = get_device()
        _processor = OwlViTProcessor.from_pretrained(MODEL_ID)
        _model = OwlViTForObjectDetection.from_pretrained(MODEL_ID).to(_device).eval()
    return _processor, _model, _device


@torch.no_grad()
def detect_label_crop(image: Image.Image, pad_frac: float = 0.03, min_score: float = 0.03) -> Image.Image:
    """Возвращает кроп по лучшему найденному боксу (предпочитая 'wine bottle
    label' над просто 'wine bottle'). Если ничего не найдено — возвращает
    исходное изображение без изменений."""
    processor, model, device = _load()
    img = image.convert("RGB")

    inputs = processor(text=[QUERIES], images=img, return_tensors="pt").to(device)
    out = model(**inputs)
    target_sizes = torch.tensor([img.size[::-1]])
    results = processor.post_process_grounded_object_detection(
        out, target_sizes=target_sizes, threshold=min_score
    )[0]

    best_box, best_score = None, -1.0
    for box, score, label_idx in zip(results["boxes"], results["scores"], results["labels"]):
        query = QUERIES[label_idx]
        weighted = float(score) * LABEL_PRIORITY[query]
        if weighted > best_score:
            best_score, best_box = weighted, box.tolist()

    if best_box is None:
        return img

    x0, y0, x1, y1 = best_box
    w, h = img.size
    pad_x, pad_y = (x1 - x0) * pad_frac, (y1 - y0) * pad_frac
    x0, y0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
    x1, y1 = min(w, x1 + pad_x), min(h, y1 + pad_y)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return img
    return img.crop((int(x0), int(y0), int(x1), int(y1)))
