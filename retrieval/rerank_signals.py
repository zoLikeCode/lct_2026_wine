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


def orb_inlier_score(query_img: Image.Image, candidate_img: Image.Image, size: int = 400) -> float:
    """Доля geometrically-verified inlier-матчей (0..1) после RANSAC-гомографии."""
    import cv2
    orb, bf = _get_orb()

    def prep(im):
        im = im.convert("RGB").resize((size, size))
        return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2GRAY)

    q, c = prep(query_img), prep(candidate_img)
    kp1, des1 = orb.detectAndCompute(q, None)
    kp2, des2 = orb.detectAndCompute(c, None)
    if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
        return 0.0

    matches = bf.knnMatch(des1, des2, k=2)
    good = [m for pair in matches if len(pair) == 2 for m, n in [pair] if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return 0.0

    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if mask is None:
        return 0.0
    return float(mask.sum()) / orb.getMaxFeatures()


_OCR_READER = None


def get_ocr_reader():
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr
        _OCR_READER = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
    return _OCR_READER


def ocr_text(image: Image.Image, min_conf: float = 0.3) -> str:
    reader = get_ocr_reader()
    results = reader.readtext(np.array(image.convert("RGB")), detail=1)
    tokens = [text for _, text, conf in results if conf >= min_conf]
    return " ".join(tokens)


def ocr_match_score(query_text: str, rerank_target: dict) -> float:
    """Fuzzy keyword-spotting: query_text против совокупности текстовых полей каталога."""
    target = rerank_target.get("combined_text", "").strip()
    if not query_text.strip() or not target:
        return 0.0
    return fuzz.token_set_ratio(query_text, target) / 100.0
