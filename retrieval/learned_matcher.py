"""Обученный локальный матчер (DISK + LightGlue) с геометрической проверкой.

Зачем это после отклонённого ORB (§11.8-11.9). ORB провалился по двум
причинам, и только одна из них общая для любых матчеров:

- общая: в полевом кадре присутствуют чужие бутылки, и соответствия могут
  лечь на соседа, а не на целевую этикетку;
- специфичная для ORB: его бинарные дескрипторы слабы на гладких
  малотекстурных участках, из которых этикетка в основном и состоит,
  и разваливаются при смене освещения и ракурса.

Обученные матчеры снимают вторую причину. Кроме того, здесь сигнал
используется иначе: не как фиксированная добавка к косинусу (так ORB и
вредил), а как ПРИЗНАК реранкера, который может научиться, когда ему
доверять, — §11.29 показал, что именно нового сигнала о запросе и не
хватало.

Возвращается доля inlier-ов после RANSAC-гомографии: число геометрически
согласованных соответствий, нормированное на число предложенных.
"""

import numpy as np
import torch
from PIL import Image

_DEVICE = None
_DISK = None
_MATCHER = None
IMAGE_SIDE = 512
MAX_KEYPOINTS = 1024
RANSAC_THRESHOLD = 4.0


def _device() -> torch.device:
    """CPU намеренно: детектор DISK использует `kthvalue`, не реализованный
    на MPS (PyTorch 2.6). Частичный фолбэк на CPU для одной операции дороже
    и капризнее, чем просто посчитать всё на CPU — модель небольшая.
    Переопределяется переменной окружения WINE_MATCHER_DEVICE."""
    global _DEVICE
    if _DEVICE is None:
        import os
        _DEVICE = torch.device(os.environ.get("WINE_MATCHER_DEVICE", "cpu"))
    return _DEVICE


def _models():
    global _DISK, _MATCHER
    if _DISK is None:
        import kornia.feature as KF
        _DISK = KF.DISK.from_pretrained("depth").to(_device()).eval()
        _MATCHER = KF.LightGlueMatcher("disk").to(_device()).eval()
    return _DISK, _MATCHER


def _tensor(image: Image.Image) -> torch.Tensor:
    resized = image.convert("RGB").resize((IMAGE_SIDE, IMAGE_SIDE), Image.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    return torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).to(_device())


@torch.inference_mode()
def describe(image: Image.Image):
    """Ключевые точки и дескрипторы одного изображения (кешируемо)."""
    disk, _ = _models()
    features = disk(_tensor(image), MAX_KEYPOINTS, pad_if_not_divisible=True)[0]
    return features.keypoints, features.descriptors


@torch.inference_mode()
def inlier_ratio(query, candidate) -> float:
    """Доля геометрически согласованных соответствий между двумя наборами
    признаков. 0.0, если соответствий слишком мало для гомографии."""
    import cv2
    import kornia.feature as KF

    kp_q, desc_q = query
    kp_c, desc_c = candidate
    if len(kp_q) < 8 or len(kp_c) < 8:
        return 0.0

    _, matcher = _models()
    lafs_q = KF.laf_from_center_scale_ori(kp_q[None])
    lafs_c = KF.laf_from_center_scale_ori(kp_c[None])
    _, indices = matcher(desc_q, desc_c, lafs_q, lafs_c)
    if indices.shape[0] < 8:
        return 0.0

    points_q = kp_q[indices[:, 0]].cpu().numpy().astype(np.float32)
    points_c = kp_c[indices[:, 1]].cpu().numpy().astype(np.float32)
    _, mask = cv2.findHomography(points_q.reshape(-1, 1, 2), points_c.reshape(-1, 1, 2),
                                 cv2.RANSAC, RANSAC_THRESHOLD)
    if mask is None:
        return 0.0
    return float(mask.sum()) / float(MAX_KEYPOINTS)
