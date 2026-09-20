"""Предпосчёт ORB-дескрипторов эталонных фото каталога.

Замер латентности показал, что реранкинг съедает хвост запроса (p95 1812 мс
при среднем 429 мс), потому что на каждый запрос заново открываются,
декодируются и обсчитываются пять эталонных фото. Эталоны не меняются, поэтому
всё это считается один раз офлайн.

Запуск:
    python -m retrieval.orb_cache
"""

import json
import pickle
import time

import cv2
import numpy as np
from PIL import Image

from data_prep import config

ORB_FEATURES = 500
ORB_SIZE = 400


def compute_descriptors(image: Image.Image, orb=None):
    """Возвращает (координаты ключевых точек, дескрипторы) на нормированном размере."""
    orb = orb or cv2.ORB_create(nfeatures=ORB_FEATURES)
    gray = cv2.cvtColor(np.array(image.convert("RGB").resize((ORB_SIZE, ORB_SIZE))),
                        cv2.COLOR_RGB2GRAY)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if descriptors is None or len(keypoints) < 8:
        return np.zeros((0, 2), dtype=np.float32), None
    points = np.float32([kp.pt for kp in keypoints])
    return points, descriptors


def main() -> None:
    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    indexed = [r for r in records if r["photo_file"]]
    print(f"Считаем ORB-дескрипторы для {len(indexed)} эталонных фото...")

    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    cache: dict[str, tuple] = {}
    empty = 0
    t0 = time.time()

    for i, record in enumerate(indexed, start=1):
        try:
            with Image.open(config.UPLOADS_DIR / record["photo_file"]) as img:
                points, descriptors = compute_descriptors(img, orb)
        except Exception as e:
            print(f"  пропуск {record['slug']}: {e}")
            continue
        if descriptors is None:
            empty += 1
        cache[record["slug"]] = (points, descriptors)
        if i % 400 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(indexed)}  ({el:.0f}s, {el/i*1000:.0f} мс/фото)", flush=True)

    out_path = config.OUTPUTS_DIR / "orb_cache.pkl"
    with open(out_path, "wb") as f:
        pickle.dump({"size": ORB_SIZE, "features": ORB_FEATURES, "cache": cache}, f,
                    protocol=pickle.HIGHEST_PROTOCOL)

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\nГотово за {time.time() - t0:.0f}s. Без дескрипторов: {empty}")
    print(f"Сохранено: {out_path} ({size_mb:.1f} МБ, {len(cache)} записей)")


if __name__ == "__main__":
    main()
