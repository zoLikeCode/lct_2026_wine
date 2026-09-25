"""Боевой путь предсказания: фото -> slug.

Конфигурация подобрана экспериментами (см. docs/findings.md §11):
индекс SigLIP2 so400m 512px по эталонным фото целиком (без детекции), top-5
по косинусной близости (§11.14: +2.5пп top-1 / +1.6пп top-5 против base на
том же разрешении, честная валидация на 2544 реальных фото; латентность
p95 1405мс — в 2.1x запасе от SLA 3000мс). ORB-реранкинг ОТКЛЮЧЁН
(ORB_WEIGHT=0.0) — на нескольких независимых наборах реальных фото (Archive:
2544 запроса, полка: 100 фото) он либо не даёт эффекта, либо статистически
значимо вредит (79.1% -> 77.3% top-1 на прежнем индексе при весе 0.8;
оптимальный вес по перебору — ровно 0). Прирост +8пп, измеренный раньше, был
артефактом синтетической валидации, где запрос — искажённая копия того же
файла, что и в индексе: там ORB буквально узнаёт свои же пиксели. ORB-кеш и
обвязка оставлены в коде для дальнейших экспериментов (например, обученные
матчеры вместо ORB), но по умолчанию не участвуют в ответе.

Препроцессинг запроса ВКЛЮЧЁН по умолчанию (`retrieval/preprocess_field.py`):
кроп по бутылке целиком (не по этикетке — то отклонено, §11.16) с fallback
на целое фото при неуверенной детекции, плюс баланс белого/CLAHE-контраст.
Единственный положительный результат реранкинга/препроцессинга кроме
самого backbone: +1.8пп top-1 на честных 56 полевых фото, 83.9% -> 85.7%
(§11.19 findings).

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
from .differentiator_rerank import net_votes
from .index import EmbeddingIndex
from .preprocess_field import preprocess as preprocess_image
from .rerank_signals import describe, ocr_text, orb_inlier_score_cached

BACKBONE = "siglip2_so400m512"
TOP_K = 5
ORB_WEIGHT = 0.0
DIFFERENTIATOR_WEIGHT = 0.0  # §11.18 findings — под честной оценкой перед включением по умолчанию
PREPROCESS = True  # §11.19 findings — bottle-кроп + баланс белого/контраст, единственный положительный результат


@dataclass
class Prediction:
    slug: str
    score: float
    embedding_score: float
    orb_score: float
    record: dict


class WineFinder:
    def __init__(self, backbone: str = BACKBONE, orb_weight: float = ORB_WEIGHT,
                 differentiator_weight: float = DIFFERENTIATOR_WEIGHT, preprocess: bool = PREPROCESS):
        with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
            records = json.load(f)
        self.lookup = {r["slug"]: r for r in records if r["photo_file"]}
        self.index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{backbone}.npz")
        self.backbone = load_backbone(backbone)
        self.orb_weight = orb_weight
        self.differentiator_weight = differentiator_weight
        self.preprocess = preprocess

        # ORB выключен по умолчанию (§11.9/11.14 findings — вредит на реальных
        # фото), поэтому кеш не обязателен: используется только если явно
        # запросили ненулевой вес.
        self.orb_cache = None
        if orb_weight != 0.0:
            cache_path = config.OUTPUTS_DIR / "orb_cache.pkl"
            if not cache_path.exists():
                raise SystemExit("нет outputs/orb_cache.pkl — сначала python -m retrieval.orb_cache")
            with open(cache_path, "rb") as f:
                self.orb_cache = pickle.load(f)["cache"]

    def predict(self, image: Image.Image, top_k: int = TOP_K) -> list[Prediction]:
        if self.preprocess:
            image = preprocess_image(image)
        emb = self.backbone.encode(image)
        candidates = self.index.search(emb, top_k=top_k)

        points_q = des_q = None
        if self.orb_cache is not None:
            points_q, des_q = describe(image)

        votes = {}
        if self.differentiator_weight != 0.0:
            query_text = ocr_text(image)
            votes = net_votes(query_text, [self.lookup[cand.slug] for cand in candidates])

        scored = []
        for cand in candidates:
            orb = 0.0
            if self.orb_cache is not None:
                points_c, des_c = self.orb_cache.get(cand.slug, (None, None))
                orb = orb_inlier_score_cached(points_q, des_q, points_c, des_c)
            vote = votes.get(cand.slug, 0)
            scored.append(Prediction(
                slug=cand.slug,
                score=cand.score + self.orb_weight * orb + self.differentiator_weight * vote,
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
