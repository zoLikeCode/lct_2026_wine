"""Ансамбль двух наборов эмбеддингов (например 384 и 512).

Гипотеза: разные разрешения видят разное — 512 лучше читает мелкие детали,
384 устойчивее к общей форме и цвету. Конкатенация L2-нормированных векторов
даёт суммирование косинусных близостей, то есть согласие двух «мнений».

Проверяется почти бесплатно: оба набора уже посчитаны, нужно только собрать
из них общий индекс.

Запуск:
    python -m retrieval.ensemble --parts siglip2_384 siglip2_512 --name siglip2_ens
"""

import argparse

import numpy as np

from data_prep import config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts", nargs="+", required=True, help="имена бэкбонов для объединения")
    parser.add_argument("--name", required=True, help="имя итогового набора")
    parser.add_argument("--weights", nargs="*", type=float, default=None,
                        help="веса частей (по умолчанию равные)")
    args = parser.parse_args()

    weights = args.weights or [1.0] * len(args.parts)
    if len(weights) != len(args.parts):
        raise SystemExit("число весов должно совпадать с числом частей")

    slugs_ref = None
    blocks = []
    for part, weight in zip(args.parts, weights):
        data = np.load(config.OUTPUTS_DIR / f"embeddings_{part}.npz")
        slugs, embeddings = list(data["slugs"]), data["embeddings"]
        if slugs_ref is None:
            slugs_ref = slugs
        elif slugs != slugs_ref:
            raise SystemExit(f"порядок slug в {part} не совпадает с {args.parts[0]}")

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        blocks.append(weight * embeddings / np.clip(norms, 1e-8, None))
        print(f"  {part}: {embeddings.shape}, вес {weight}")

    combined = np.concatenate(blocks, axis=1)
    combined /= np.clip(np.linalg.norm(combined, axis=1, keepdims=True), 1e-8, None)

    out_path = config.OUTPUTS_DIR / f"embeddings_{args.name}.npz"
    np.savez(out_path, slugs=np.array(slugs_ref), embeddings=combined)
    print(f"\nИтог: {combined.shape} -> {out_path}")


if __name__ == "__main__":
    main()
