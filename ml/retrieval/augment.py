"""Синтетическая имитация "полевого" фото (телефон, у полки, блик, угол) из
чистого эталонного фото каталога — пока нет настоящих полевых фото для валидации.
"""

import random

from PIL import Image, ImageEnhance, ImageFilter


def random_perspective_crop(img: Image.Image, max_shift: float = 0.12) -> Image.Image:
    w, h = img.size
    dx, dy = w * max_shift, h * max_shift

    def jitter(x, y):
        return (x + random.uniform(-dx, dx), y + random.uniform(-dy, dy))

    src = [(0, 0), (w, 0), (w, h), (0, h)]
    dst = [jitter(*p) for p in src]
    coeffs = _find_perspective_coeffs(dst, src)
    return img.transform((w, h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC, fillcolor=(255, 255, 255))


def _find_perspective_coeffs(src_pts, dst_pts):
    import numpy as np
    matrix = []
    for (x, y), (X, Y) in zip(src_pts, dst_pts):
        matrix.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        matrix.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
    A = np.array(matrix)
    B = np.array(dst_pts).reshape(8)
    res = np.linalg.solve(A, B) if A.shape[0] == A.shape[1] else np.linalg.lstsq(A, B, rcond=None)[0]
    return res.tolist()


def add_glare(img: Image.Image, strength: float = 0.5) -> Image.Image:
    import numpy as np
    w, h = img.size
    cx, cy = random.uniform(0.2, 0.8) * w, random.uniform(0.2, 0.8) * h
    radius = random.uniform(0.15, 0.35) * max(w, h)
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    mask = np.clip(1 - dist / radius, 0, 1) ** 2 * strength
    arr = np.array(img).astype(float)
    arr = arr + mask[..., None] * 255
    arr = np.clip(arr, 0, 255).astype("uint8")
    return Image.fromarray(arr)


def simulate_field_photo(img: Image.Image, seed: int | None = None) -> Image.Image:
    """Перспектива + блик + размытие + изменение освещения/контраста —
    имитация фото телефоном у полки под углом."""
    rng = random.Random(seed)
    random.seed(rng.random())

    out = img.copy()
    out = random_perspective_crop(out, max_shift=rng.uniform(0.05, 0.15))

    if rng.random() < 0.6:
        out = add_glare(out, strength=rng.uniform(0.3, 0.6))

    out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.75, 1.25))
    out = ImageEnhance.Contrast(out).enhance(rng.uniform(0.8, 1.2))

    if rng.random() < 0.5:
        out = out.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.5, 1.8)))

    return out
