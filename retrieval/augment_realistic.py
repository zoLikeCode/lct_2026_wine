"""Реалистичная имитация «полевого» фото.

Базовая `augment.simulate_field_photo` накладывает плоскую перспективу, блик и
блюр. Реальные фото из eval устроены иначе (проверено на `eval/queries/`):
этикетка изогнута по цилиндру бутылки, кадр снят с руки под углом, в нём
присутствует посторонний фон и соседние бутылки, часть этикетки уходит в тень.

Поэтому здесь: цилиндрическая деформация с затенением по краям, поворот,
перспектива, фон и обрезка кадра. Нужно, чтобы валидация не переоценивала
качество (см. docs/findings.md).
"""

import random

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def cylindrical_warp(img: Image.Image, arc_deg: float = 110.0, yaw_deg: float = 0.0) -> Image.Image:
    """Оборачивает плоскую картинку вокруг вертикального цилиндра.

    arc_deg — какую дугу бутылки занимает этикетка; yaw_deg — поворот бутылки
    вокруг своей оси (0 = этикетка ровно по центру к камере).
    """
    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]

    arc = np.radians(arc_deg)
    yaw = np.radians(yaw_deg)
    half = arc / 2.0

    # Для каждого столбца результата ищем, какой столбец исходника туда попал.
    xs = np.linspace(-1.0, 1.0, w)
    # обратная проекция: экранная координата -> угол на цилиндре
    theta = np.arcsin(np.clip(xs * np.sin(half), -1.0, 1.0)) + yaw
    src_x = (theta / arc + 0.5) * (w - 1)

    valid = (src_x >= 0) & (src_x <= w - 1)
    src_x = np.clip(src_x, 0, w - 1)

    map_x = np.tile(src_x.astype(np.float32), (h, 1))
    map_y = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))

    warped = cv2.remap(arr, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_REPLICATE)

    # Затенение: край цилиндра отворачивается от света -> темнее (закон Ламберта).
    shade = np.cos(theta - yaw).clip(0.35, 1.0).astype(np.float32)
    warped = (warped * shade[None, :, None]).astype(np.uint8)

    # Столбцы, которых на видимой стороне цилиндра нет, гасим в край.
    if not valid.all():
        warped[:, ~valid] = warped[:, valid][:, :1] if valid.any() else 0

    return Image.fromarray(warped)


def add_glare(img: Image.Image, strength: float, rng: random.Random) -> Image.Image:
    arr = np.array(img).astype(np.float32)
    h, w = arr.shape[:2]
    cx, cy = rng.uniform(0.15, 0.85) * w, rng.uniform(0.1, 0.9) * h
    rx = rng.uniform(0.05, 0.18) * w      # блик на стекле обычно вытянут вертикально
    ry = rng.uniform(0.15, 0.45) * h
    yy, xx = np.mgrid[0:h, 0:w]
    d = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    mask = np.clip(1.0 - d, 0, 1) ** 2 * strength
    arr += mask[..., None] * 255.0
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# Приглушённые тона винной полки: тёмное стекло соседних бутылок, дерево,
# картон, металл стеллажа. Яркие насыщенные цвета в таком кадре не встречаются.
_SHELF_TONES = [
    (46, 38, 32), (28, 30, 26), (84, 62, 44), (120, 96, 68),
    (150, 140, 126), (96, 100, 104), (62, 48, 40), (168, 150, 120),
]


def random_background(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """Грубая имитация полки/стола: горизонтальные полосы приглушённых тонов."""
    w, h = size
    small = np.zeros((max(h // 24, 4), max(w // 24, 4), 3), dtype=np.uint8)
    small[:, :] = rng.choice(_SHELF_TONES)
    for _ in range(rng.randint(3, 7)):
        y0 = rng.randint(0, small.shape[0] - 1)
        y1 = min(small.shape[0], y0 + rng.randint(1, 4))
        tone = np.array(rng.choice(_SHELF_TONES), dtype=np.int16)
        tone = np.clip(tone + rng.randint(-18, 18), 0, 255).astype(np.uint8)
        small[y0:y1, :] = tone
    bg = Image.fromarray(small).resize((w, h), Image.BILINEAR)
    return bg.filter(ImageFilter.GaussianBlur(radius=max(w, h) / 90.0))


def perspective(img: Image.Image, rng: random.Random, max_shift: float) -> Image.Image:
    w, h = img.size
    dx, dy = w * max_shift, h * max_shift
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    dst = [(x + rng.uniform(-dx, dx), y + rng.uniform(-dy, dy)) for x, y in src]
    matrix = []
    for (x, y), (X, Y) in zip(src, dst):
        matrix.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        matrix.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
    A = np.array(matrix, dtype=np.float64)
    B = np.array(dst, dtype=np.float64).reshape(8)
    coeffs = np.linalg.solve(A, B)
    return img.transform((w, h), Image.PERSPECTIVE, coeffs.tolist(),
                         resample=Image.BICUBIC, fillcolor=(255, 255, 255))


def simulate_realistic_photo(img: Image.Image, seed: int | None = None) -> Image.Image:
    """Полный конвейер: цилиндр -> поворот на фоне -> блик -> оптика камеры."""
    rng = random.Random(seed)
    out = img.convert("RGB")

    out = cylindrical_warp(out, arc_deg=rng.uniform(95, 140), yaw_deg=rng.uniform(-28, 28))

    # Бутылку кладём на фон и немного поворачиваем — снимок с руки не бывает ровным.
    w, h = out.size
    canvas = random_background((int(w * 1.5), int(h * 1.25)), rng)
    canvas.paste(out, ((canvas.width - w) // 2, (canvas.height - h) // 2))
    out = canvas.rotate(rng.uniform(-11, 11), resample=Image.BICUBIC, expand=False)

    out = perspective(out, rng, max_shift=rng.uniform(0.03, 0.10))

    # Кадрируем ближе к этикетке, как это делает человек с телефоном.
    cw, ch = out.size
    mx, my = int(cw * rng.uniform(0.06, 0.16)), int(ch * rng.uniform(0.04, 0.12))
    out = out.crop((mx, my, cw - mx, ch - my))

    if rng.random() < 0.75:
        out = add_glare(out, strength=rng.uniform(0.25, 0.65), rng=rng)

    out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.65, 1.3))
    out = ImageEnhance.Contrast(out).enhance(rng.uniform(0.75, 1.25))
    out = ImageEnhance.Color(out).enhance(rng.uniform(0.7, 1.2))

    if rng.random() < 0.6:
        out = out.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.4, 2.0)))

    # Шум матрицы телефона при плохом свете в магазине.
    if rng.random() < 0.5:
        arr = np.array(out).astype(np.float32)
        arr += np.random.default_rng(seed).normal(0, rng.uniform(2, 9), arr.shape)
        out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    return out
