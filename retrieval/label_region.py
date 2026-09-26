"""Локализация области этикетки по ДЕТЕКЦИИ текста (не распознаванию).

Мотивация (§11.23 findings): в ветке `saveliy_pipiline` ручная область
этикетки даёт +15пп top-1 стабильно на всех пяти вариантах модели, тогда как
смена/дообучение модели двигают результат на ±5пп. Узкое место — локализация,
не эмбеддер.

Почему это не повторение отклонённых гипотез:
- §11.12/§11.18 отклонили OCR как сигнал РЕРАНКИНГА: там нужно верно
  прочитать текст, и на фото полки OCR читает соседнюю этикетку. Здесь текст
  не читается вообще — нужны только координаты блоков, а детекция текста
  устойчивее распознавания.
- §11.16 отклонил кроп по `label`-боксу YOLO, найденному на ЦЕЛОМ кадре.
  Здесь область ищется внутри уже обрезанной бутылки (§11.19), где соседних
  бутылок почти нет, поэтому тот результат сюда не переносится.

Режимы области:
- `tight` — объединяющий прямоугольник текстового кластера; максимально
  убирает лишнее, но выбрасывает форму бутылки и цвет стекла, на чём
  погорел §11.16.
- `band` — вертикальный диапазон кластера во всю ширину кропа бутылки:
  убирает колпачок/горлышко/дно, сохраняя стекло и силуэт по горизонтали.
"""

Box = tuple[int, int, int, int]


def normalize_boxes(horizontal: list, free: list) -> list[Box]:
    """Приводит выдачу EasyOCR `detect()` к общему виду (x0, y0, x1, y1).

    `horizontal` — элементы вида [x_min, x_max, y_min, y_max] (именно такой
    порядок, не xyxy), `free` — четырёхугольники из точек (x, y).
    """
    boxes: list[Box] = []
    for item in horizontal:
        x_min, x_max, y_min, y_max = (float(v) for v in item)
        boxes.append((int(min(x_min, x_max)), int(min(y_min, y_max)),
                      int(max(x_min, x_max)), int(max(y_min, y_max))))
    for quad in free:
        xs = [float(point[0]) for point in quad]
        ys = [float(point[1]) for point in quad]
        boxes.append((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))))
    return [b for b in boxes if b[2] > b[0] and b[3] > b[1]]


def scale_boxes(boxes: list[Box], scale: float) -> list[Box]:
    """Переводит боксы из уменьшенного для OCR кадра обратно в исходный."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    return [(int(b[0] * scale), int(b[1] * scale), int(b[2] * scale), int(b[3] * scale))
            for b in boxes]


def dominant_text_cluster(boxes: list[Box], image_height: int,
                          max_gap_fraction: float = 0.08) -> list[Box]:
    """Крупнейший по суммарной площади текста кластер блоков, сгруппированных
    по вертикальной близости. Этикетка — плотная пачка строк; колпачок,
    ценник и текст на соседях отделены вертикальными промежутками."""
    if image_height <= 0:
        raise ValueError("image_height must be positive")
    if not 0.0 < max_gap_fraction <= 1.0:
        raise ValueError("max_gap_fraction must be in (0, 1]")
    if not boxes:
        return []

    ordered = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    max_gap = image_height * max_gap_fraction
    clusters: list[list[Box]] = [[ordered[0]]]
    for box in ordered[1:]:
        if box[1] - max(b[3] for b in clusters[-1]) > max_gap:
            clusters.append([box])
        else:
            clusters[-1].append(box)

    return max(clusters, key=lambda c: sum((b[2] - b[0]) * (b[3] - b[1]) for b in c))


def union_box(boxes: list[Box]) -> Box | None:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def central_boxes(boxes: list[Box], image_size: tuple[int, int],
                  x_margin: float = 0.15, top_margin: float = 0.15) -> list[Box]:
    """Отбрасывает блоки у боковых краёв (текст соседних бутылок) и в верхней
    полосе (ценники висят над бутылкой, там же колпачок).

    Проверено на двух кадрах поля: без этого фильтра «самый крупный текстовый
    кластер» выбирает ценник «2998» над бутылкой Фанагории и полосу мелкого
    шрифта вместе с соседями на кадре с «Табией»."""
    width, height = image_size
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    if not 0.0 <= x_margin < 0.5 or not 0.0 <= top_margin < 1.0:
        raise ValueError("margins out of range")

    kept = []
    for box in boxes:
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        if not width * x_margin <= center_x <= width * (1.0 - x_margin):
            continue
        if center_y < height * top_margin:
            continue
        kept.append(box)
    return kept


def label_region(boxes: list[Box], image_size: tuple[int, int], mode: str,
                 margin: float, selection: str = "cluster_area") -> Box | None:
    """Область этикетки из текстовых блоков. None — текст не найден,
    вызывающий код должен остаться на кропе бутылки.

    `selection`: `cluster_area` — крупнейший по площади вертикальный кластер;
    `union_central` — объединение всех блоков после фильтра центральности,
    что не даёт мелкому шрифту и брендингу разъехаться в разные кластеры.
    """
    if mode not in {"tight", "band"}:
        raise ValueError("mode must be 'tight' or 'band'")
    if selection not in {"cluster_area", "union_central"}:
        raise ValueError("selection must be 'cluster_area' or 'union_central'")
    if margin < 0.0:
        raise ValueError("margin must be non-negative")
    width, height = image_size
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")

    if selection == "union_central":
        chosen = central_boxes(boxes, image_size)
    else:
        chosen = dominant_text_cluster(boxes, height)
    box = union_box(chosen)
    if box is None:
        return None

    x0, y0, x1, y1 = box
    pad_y = (y1 - y0) * margin
    y0, y1 = max(0, int(y0 - pad_y)), min(height, int(y1 + pad_y))
    if mode == "band":
        return (0, y0, width, y1)

    pad_x = (x1 - x0) * margin
    x0, x1 = max(0, int(x0 - pad_x)), min(width, int(x1 + pad_x))
    return (x0, y0, x1, y1)
