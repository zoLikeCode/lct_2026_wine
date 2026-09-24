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


def perspective(img: Image.Image, rng: random.Random, max_shift: float, return_coeffs: bool = False):
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
    coeffs = np.linalg.solve(A, B).tolist()
    out = img.transform((w, h), Image.PERSPECTIVE, coeffs,
                        resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    return (out, coeffs) if return_coeffs else out


def simulate_realistic_photo(img: Image.Image, seed: int | None = None) -> Image.Image:
    """Полный конвейер: цилиндр -> поворот на фоне -> блик -> оптика камеры."""
    out, _ = simulate_realistic_photo_with_mask(img, mask=None, seed=seed)
    return out


# Типичная вертикальная раскладка кадра нашего каталога: колпачок/горлышко
# сверху, дальше сама этикетка, снизу немного стекла. Доля по калибровке
# на нескольких эталонах (не точная сегментация, а грубая эвристика —
# достаточная, чтобы не путать этикетку с колпачком/фольгой).
LABEL_REGION_FRACTION = (0.0, 0.36, 1.0, 0.80)  # (x0, y0, x1, y1) как доля от W,H


def _label_mask_for(size: tuple[int, int]) -> Image.Image:
    """Бинарная маска (L-режим, 0/255) — прямоугольник этикетки на
    оригинальном эталонном фото, до какой-либо аугментации."""
    w, h = size
    x0f, y0f, x1f, y1f = LABEL_REGION_FRACTION
    mask = Image.new("L", (w, h), 0)
    box = (int(w * x0f), int(h * y0f), int(w * x1f), int(h * y1f))
    from PIL import ImageDraw
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask


def mask_to_bbox(mask: Image.Image, min_area: int = 25) -> tuple[int, int, int, int] | None:
    """Bounding box непустой области маски в пикселях (x0,y0,x1,y1) или None,
    если этикетка полностью ушла за кадр/обрезана (площадь < min_area)."""
    arr = np.array(mask)
    ys, xs = np.where(arr > 127)
    if len(xs) < min_area:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def simulate_realistic_photo_with_mask(
    img: Image.Image, mask: Image.Image | None = None, seed: int | None = None,
) -> tuple[Image.Image, Image.Image]:
    """То же самое, что `simulate_realistic_photo`, но параллельно проводит
    маску этикетки через ИДЕНТИЧНУЮ последовательность геометрических
    преобразований (те же случайные параметры), чтобы после аугментации
    можно было получить честный bounding box этикетки на выходном кадре —
    без необходимости аналитически обращать цилиндрический warp/перспективу.

    `mask=None` — авто: прямоугольник по `LABEL_REGION_FRACTION` на входном
    фото. Цветовые эффекты (блик, яркость, шум) на маску не действуют —
    геометрия для них тождественна.
    """
    rng = random.Random(seed)
    out = img.convert("RGB")
    if mask is None:
        mask = _label_mask_for(out.size)

    arc_deg, yaw_deg = rng.uniform(95, 140), rng.uniform(-28, 28)
    out = cylindrical_warp(out, arc_deg=arc_deg, yaw_deg=yaw_deg)
    mask = cylindrical_warp(mask.convert("RGB"), arc_deg=arc_deg, yaw_deg=yaw_deg).convert("L")

    w, h = out.size
    canvas_size = (int(w * 1.5), int(h * 1.25))
    bg = random_background(canvas_size, rng)
    canvas = bg.copy()
    offset = ((canvas.width - w) // 2, (canvas.height - h) // 2)
    canvas.paste(out, offset)
    mask_canvas = Image.new("L", canvas_size, 0)
    mask_canvas.paste(mask, offset)

    rot_deg = rng.uniform(-11, 11)
    out = canvas.rotate(rot_deg, resample=Image.BICUBIC, expand=False)
    mask_canvas = mask_canvas.rotate(rot_deg, resample=Image.NEAREST, expand=False, fillcolor=0)

    persp_shift = rng.uniform(0.03, 0.10)
    out, persp_coeffs = perspective(out, rng, max_shift=persp_shift, return_coeffs=True)
    mask_canvas = mask_canvas.transform(mask_canvas.size, Image.PERSPECTIVE, persp_coeffs,
                                        resample=Image.NEAREST, fillcolor=0)

    cw, ch = out.size
    mx, my = int(cw * rng.uniform(0.06, 0.16)), int(ch * rng.uniform(0.04, 0.12))
    box = (mx, my, cw - mx, ch - my)
    out = out.crop(box)
    mask_canvas = mask_canvas.crop(box)

    if rng.random() < 0.75:
        out = add_glare(out, strength=rng.uniform(0.25, 0.65), rng=rng)

    out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.65, 1.3))
    out = ImageEnhance.Contrast(out).enhance(rng.uniform(0.75, 1.25))
    out = ImageEnhance.Color(out).enhance(rng.uniform(0.7, 1.2))

    if rng.random() < 0.6:
        out = out.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.4, 2.0)))

    if rng.random() < 0.5:
        arr = np.array(out).astype(np.float32)
        arr += np.random.default_rng(seed).normal(0, rng.uniform(2, 9), arr.shape)
        out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    return out, mask_canvas
