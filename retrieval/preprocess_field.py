"""Дешёвая необучаемая предобработка полевых фото перед эмбеддингом.

Два независимых шага, оба честно провалидированы на 56 полевых фото
(§11.19 findings) — в отличие от пяти отклонённых методов реранкинга
(§11.8-11.9, §11.12, §11.16, §11.18), это первый положительный результат
кроме самого backbone (§11.14):

1. Кроп по БУТЫЛКЕ целиком, не по этикетке (label-кроп отклонён в §11.16 —
   выбрасывает форму/стекло). Детекция `bottle` у обученного YOLO11
   (`roboflow/wine_label_yolo11m_best.pt`) надёжна на большинстве полевых
   фото. Кроп с широким отступом убирает с кадра соседние бутылки на
   полке — источник путаницы OCR/ORB в §11.18 — сохраняя контекст целевой
   бутылки. При неуверенной детекции — используется целое фото (fallback).
2. Баланс белого «серый мир» + CLAHE-контраст — сам по себе даёт чистый
   ноль (1 исправление / 1 поломка), но в связке с кропом даёт
   дополнительный прирост (§11.19).
"""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

BOTTLE_WEIGHTS = Path(__file__).resolve().parent.parent / "roboflow" / "wine_label_yolo11m_best.pt"
BOTTLE_CONF_THRESHOLD = 0.5
BOTTLE_PAD_FRAC = 0.15  # заметно больше, чем у отклонённого label-кропа (0.05)

_BOTTLE_MODEL = None


def _gray_world_white_balance(arr: np.ndarray) -> np.ndarray:
    result = arr.astype(np.float32)
    means = result.reshape(-1, 3).mean(axis=0)
    gray = means.mean()
    for c in range(3):
        if means[c] > 1e-3:
            result[:, :, c] *= gray / means[c]
    return np.clip(result, 0, 255)


def _clahe_contrast(arr: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    lab = cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_RGB2LAB)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB).astype(np.float32)


def enhance(image: Image.Image) -> Image.Image:
    arr = np.array(image.convert("RGB"))
    arr = _gray_world_white_balance(arr)
    arr = _clahe_contrast(arr)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _get_bottle_model():
    global _BOTTLE_MODEL
    if _BOTTLE_MODEL is None:
        from ultralytics import YOLO
        _BOTTLE_MODEL = YOLO(str(BOTTLE_WEIGHTS))
    return _BOTTLE_MODEL


def crop_to_bottle(image: Image.Image) -> Image.Image | None:
    """None, если детектор не нашёл `bottle`-бокс с уверенностью >= порога —
    вызывающий код должен откатиться на целое фото."""
    model = _get_bottle_model()
    # CPU намеренно: параллельно на MPS обычно идёт инференс эмбеддера,
    # два процесса на MPS одновременно уже один раз замедляли всё в разы
    # (см. §11.16).
    result = model.predict(source=image, verbose=False, device="cpu")[0]
    best = None
    for box in result.boxes:
        if model.names[int(box.cls[0])] != "bottle":
            continue
        conf = float(box.conf[0])
        if conf < BOTTLE_CONF_THRESHOLD:
            continue
        if best is None or conf > best[0]:
            best = (conf, box.xyxy[0].tolist())
    if best is None:
        return None

    x0, y0, x1, y1 = best[1]
    w, h = image.size
    pad_x, pad_y = (x1 - x0) * BOTTLE_PAD_FRAC, (y1 - y0) * BOTTLE_PAD_FRAC
    x0, y0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
    x1, y1 = min(w, x1 + pad_x), min(h, y1 + pad_y)
    return image.crop((int(x0), int(y0), int(x1), int(y1)))


def preprocess(image: Image.Image) -> Image.Image:
    """Полный конвейер перед эмбеддингом: EXIF-ориентация -> bottle-кроп
    (с fallback на целое фото) -> баланс белого/контраст. Официально
    провалидированный прирост +1.8пп top-1 на 56 полевых фото
    (83.9% -> 85.7%, §11.19 findings)."""
    img = ImageOps.exif_transpose(image.convert("RGB")).convert("RGB")
    cropped = crop_to_bottle(img)
    base = cropped if cropped is not None else img
    return enhance(base)
