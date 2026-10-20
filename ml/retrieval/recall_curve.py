"""Кривая recall@k на честных полевых наборах — оценка потолка реранкера.

Реранкер физически не может исправить кадр, где верного slug нет в списке
кандидатов, поэтому recall@k задаёт верхнюю границу его выигрыша:
`recall@k - текущий top-1`. Замер отвечает, стоит ли подавать реранкеру
список шире пятёрки.

    python -m retrieval.recall_curve
"""

import csv
import json
from pathlib import Path

from PIL import Image

from data_prep import config
from .backbones import load_backbone
from .index import EmbeddingIndex
from .predict import BACKBONE, PREPROCESS
from .preprocess_field import preprocess

SETS = (
    ("205 фото команды", "~/Downloads/real_photos", "field_labels_own.csv"),
    ("56 фото организатора", "~/Downloads/Реальные фото", "field_labels.csv"),
)
KS = (1, 2, 3, 5, 10, 20, 50, 100)


def main() -> None:
    index = EmbeddingIndex.load(config.OUTPUTS_DIR / f"embeddings_{BACKBONE}.npz")
    backbone = load_backbone(BACKBONE)

    all_ranks: list[int | None] = []
    per_set = {}

    for title, photos_dir, labels_name in SETS:
        root = Path(photos_dir).expanduser()
        with open(config.OUTPUTS_DIR / labels_name, encoding="utf-8") as f:
            labels = [row for row in csv.DictReader(f) if row.get("true_slug")]

        ranks: list[int | None] = []
        for i, label in enumerate(labels, start=1):
            path = root / label["image_path"]
            if not path.exists():
                continue
            with Image.open(path) as raw:
                image = preprocess(raw, PREPROCESS)
            rank = index.rank_of(backbone.encode(image), label["true_slug"])
            ranks.append(rank)
            if i % 50 == 0:
                print(f"  {title}: {i}/{len(labels)}", flush=True)

        per_set[title] = ranks
        all_ranks.extend(ranks)

    def recall(ranks: list[int | None], k: int) -> float:
        return sum(r is not None and r <= k for r in ranks) / len(ranks)

    print(f"\n{'набор':24s} {'n':>4s} " + " ".join(f"@{k:<5d}" for k in KS))
    for title, ranks in list(per_set.items()) + [("ВСЕГО", all_ranks)]:
        cells = " ".join(f"{recall(ranks, k):<6.1%}" for k in KS)
        print(f"{title:24s} {len(ranks):>4d} {cells}")

    top1 = recall(all_ranks, 1)
    print("\nПотолок реранкера (recall@k минус текущий top-1), весь набор:")
    for k in (5, 10, 20, 50):
        print(f"  из top-{k:<3d}: +{(recall(all_ranks, k) - top1) * 100:.1f}пп  ->  {recall(all_ranks, k):.1%}")

    out = config.OUTPUTS_DIR / "recall_curve.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({title: ranks for title, ranks in per_set.items()}, f, ensure_ascii=False)
    print(f"\nСохранено: {out}")


if __name__ == "__main__":
    main()
