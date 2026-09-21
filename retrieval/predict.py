"""Боевой путь предсказания: фото -> slug.

Конфигурация подобрана экспериментами (см. docs/findings.md §11):
индекс SigLIP2 512px по эталонным фото целиком (без детекции), top-5 по
косинусной близости. ORB-реранкинг ОТКЛЮЧЁН (ORB_WEIGHT=0.0) — на двух
независимых наборах реальных фото (Archive: 1452 запроса, полка: 100 фото)
он либо не даёт эффекта, либо статистически значимо вредит (79.1% -> 77.3%
top-1 на Archive при весе 0.8; оптимальный вес по перебору — ровно 0).
Прирост +8пп, измеренный раньше, был артефактом синтетической валидации,
где запрос — искажённая копия того же файла, что и в индексе: там ORB
буквально узнаёт свои же пиксели. ORB-кеш и обвязка оставлены в коде для
дальнейших экспериментов (например, обученные матчеры вместо ORB), но по
умолчанию не участвуют в ответе.

Ответ всегда top-1: метрика кейса — accuracy без штрафа за ошибку, поэтому
отказ «не найдено» только теряет баллы. Порог уверенности возвращается
отдельным полем, чтобы интерфейс мог решать сам.

Запуск как CLI (для отладки):
    python -m retrieval.predict path/to/photo.jpg [...]
"""

import json
import pickle
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .rerank_signals import describe, orb_inlier_score_cached

BACKBONE = "siglip2_512"
TOP_K = 5
ORB_WEIGHT = 0.0


@dataclass
class Prediction:
    slug: str
    score: float
    embedding_score: float
    orb_score: float
    record: dict


class WineFinder:
    def __init__(self, backbone: str = BACKBONE, orb_weight: float = ORB_WEIGHT):
        with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
            records = json.load(f)
        self.lookup = {r["slug"]: r for r in records if r["photo_file"]}
        self.index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{backbone}.npz")
        self.backbone = load_backbone(backbone)
        self.orb_weight = orb_weight

        cache_path = config.OUTPUTS_DIR / "orb_cache.pkl"
        if not cache_path.exists():
            raise SystemExit("нет outputs/orb_cache.pkl — сначала python -m retrieval.orb_cache")
        with open(cache_path, "rb") as f:
            self.orb_cache = pickle.load(f)["cache"]

    def predict(self, image: Image.Image, top_k: int = TOP_K) -> list[Prediction]:
        emb = self.backbone.encode(image)
        candidates = self.index.search(emb, top_k=top_k)

        points_q, des_q = describe(image)
        scored = []
        for cand in candidates:
            points_c, des_c = self.orb_cache.get(cand.slug, (None, None))
            orb = orb_inlier_score_cached(points_q, des_q, points_c, des_c)
            scored.append(Prediction(
                slug=cand.slug,
                score=cand.score + self.orb_weight * orb,
                embedding_score=cand.score,
                orb_score=orb,
                record=self.lookup[cand.slug],
            ))
        scored.sort(key=lambda p: -p.score)
        return scored


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        raise SystemExit("укажите пути к фото")

    finder = WineFinder()
    for path in paths:
        with Image.open(path) as img:
            results = finder.predict(img.convert("RGB"))
        print(f"\n=== {path.name} ===")
        for i, p in enumerate(results, start=1):
            rec = p.record
            mark = " <-- ОТВЕТ" if i == 1 else ""
            print(f"  {i}. score={p.score:.4f} (emb={p.embedding_score:.4f} orb={p.orb_score:.4f}){mark}")
            print(f"     {rec['winery']} — {rec['name']}")
            print(f"     {p.slug}")
        gap = results[0].score - results[1].score if len(results) > 1 else 0.0
        print(f"  отрыв top-1 от top-2: {gap:.4f}")


if __name__ == "__main__":
    main()
