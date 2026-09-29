"""Нормализация фото перед эмбеддингом. Каталожные фото и наша синтетическая
аугментация кладут бутылку на белый/прозрачный фон с рамкой пустого
пространства вокруг — autocrop убирает эту рамку, чтобы ViT не тратил патчи
на пустой фон.
"""

import numpy as np
from PIL import Image


def autocrop_to_content(img: Image.Image, bg_threshold: int = 240, pad_frac: float = 0.04) -> Image.Image:
    """Обрезает по bounding box непустого (не-белого) содержимого."""
    rgb = img.convert("RGB")
    arr = np.array(rgb)
    # пиксель считается "фоном", если все каналы светлее порога
    is_bg = np.all(arr >= bg_threshold, axis=-1)
    is_content = ~is_bg
    if not is_content.any():
        return img  # всё фон — ничего не обрезаем

    rows = np.any(is_content, axis=1)
    cols = np.any(is_content, axis=0)
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]

    h, w = arr.shape[:2]
    pad_y, pad_x = int((y1 - y0) * pad_frac), int((x1 - x0) * pad_frac)
    y0, y1 = max(0, y0 - pad_y), min(h, y1 + pad_y + 1)
    x0, x1 = max(0, x0 - pad_x), min(w, x1 + pad_x + 1)

    return rgb.crop((x0, y0, x1, y1))
