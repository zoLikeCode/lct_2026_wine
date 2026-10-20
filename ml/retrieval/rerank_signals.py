"""Два независимых сигнала реранкинга поверх top-K кандидатов от embedding-поиска:

- ORB + RANSAC geometric verification — локальные признаки, ловят мелкую
  пиксельную разницу, которую эмбеддинг усредняет, а OCR не может прочитать
  при плохом качестве фото.
- OCR + fuzzy-match с `rerank_target` — керуорд-споттинг по году/сладости/
  сорту/названию, а не точное чтение мелких цифр (см. docs/findings.md §6).
"""

import numpy as np
from PIL import Image
from rapidfuzz import fuzz

_ORB = None
_BF = None


def _get_orb():
    global _ORB, _BF
    if _ORB is None:
        import cv2
        _ORB = cv2.ORB_create(nfeatures=500)
        _BF = cv2.BFMatcher(cv2.NORM_HAMMING)
    return _ORB, _BF


def _match_score(points_q, des_q, points_c, des_c, max_features: int) -> float:
    """Доля geometrically-verified inlier-матчей (0..1) после RANSAC-гомографии."""
    import cv2
    if des_q is None or des_c is None or len(points_q) < 8 or len(points_c) < 8:
        return 0.0

    _, bf = _get_orb()
    matches = bf.knnMatch(des_q, des_c, k=2)
    good = [m for pair in matches if len(pair) == 2 for m, n in [pair] if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return 0.0

    src = np.float32([points_q[m.queryIdx] for m in good]).reshape(-1, 1, 2)
    dst = np.float32([points_c[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if mask is None:
        return 0.0
    return float(mask.sum()) / max_features


def describe(image: Image.Image, size: int = 400):
    """Ключевые точки и дескрипторы одного изображения."""
    import cv2
    orb, _ = _get_orb()
    gray = cv2.cvtColor(np.array(image.convert("RGB").resize((size, size))), cv2.COLOR_RGB2GRAY)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if descriptors is None or len(keypoints) < 8:
        return np.zeros((0, 2), dtype=np.float32), None
    return np.float32([kp.pt for kp in keypoints]), descriptors


def orb_inlier_score(query_img: Image.Image, candidate_img: Image.Image, size: int = 400) -> float:
    """Удобная обёртка, когда дескрипторы кандидата не предпосчитаны."""
    orb, _ = _get_orb()
    points_q, des_q = describe(query_img, size)
    points_c, des_c = describe(candidate_img, size)
    return _match_score(points_q, des_q, points_c, des_c, orb.getMaxFeatures())


def orb_inlier_score_cached(points_q, des_q, points_c, des_c) -> float:
    """Боевой путь: дескрипторы эталонов взяты из кеша (см. retrieval/orb_cache.py),
    на запрос считается только его собственный набор точек."""
    orb, _ = _get_orb()
    return _match_score(points_q, des_q, points_c, des_c, orb.getMaxFeatures())


_OCR_READER = None


def get_ocr_reader():
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr
        _OCR_READER = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
    return _OCR_READER


def ocr_text(image: Image.Image, min_conf: float = 0.3, max_side: int = 1280) -> str:
    """Полевые фото приходят по 3000-4600px на сторону — без ресайза EasyOCR
    на CPU уходит в десятки секунд/фото и разрастается по памяти. 1280px
    достаточно, чтобы прочитать текст этикетки в кадре."""
    img = image.convert("RGB")
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
    reader = get_ocr_reader()
    results = reader.readtext(np.array(img), detail=1)
    tokens = [text for _, text, conf in results if conf >= min_conf]
    return " ".join(tokens)


def ocr_match_score(query_text: str, rerank_target: dict) -> float:
    """Fuzzy keyword-spotting: query_text против совокупности текстовых полей каталога."""
    target = rerank_target.get("combined_text", "").strip()
    if not query_text.strip() or not target:
        return 0.0
    return fuzz.token_set_ratio(query_text, target) / 100.0
