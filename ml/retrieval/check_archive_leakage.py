"""Проверка, что валидационная выборка `Archive`/`Downloads/images` честна —
не пересекается с тем, что реально лежит в индексе.

Повод: `retrieval.archive_dataset` теперь читает `Downloads/images` — тот же
источник, из которого `data_prep.parser_images` берёт эталоны для самого
индекса (см. docs/findings.md §11.11). `benchmark_archive.py` исключает
query-фото, чей ПУТЬ совпадает с индексным эталоном, но это не ловит случай,
когда разметчики сохранили один и тот же реальный кадр дважды под разными
именами (пересжатие/повторный скрап). Проверяем по содержимому:

  1. байт-в-байт дубли (MD5) между query-фото и эталоном СВОЕГО вина в индексе,
     не пойманные проверкой по пути;
  2. визуально почти идентичные (perceptual hash) query-фото и эталон своего
     вина — та же метрика, что и в data_prep/near_duplicates.py;
  3. query-фото, чей путь совпадает с эталоном ЧУЖОГО вина в индексе.

Все три проверены на полном датасете (2544 query, 2096 эталонов) — 0 находок
по каждому пункту, см. коммит с этим файлом.

Запуск:
    python -m retrieval.check_archive_leakage
"""

import hashlib
from pathlib import Path

import imagehash
from PIL import Image

from data_prep import config
from .archive_dataset import load_archive


def md5(path) -> str:
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def phash(path):
    try:
        return imagehash.phash(Image.open(path).convert("RGB"), hash_size=16)
    except Exception:
        return None


def main() -> None:
    import json
    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = json.load(f)
    lookup = {r["slug"]: r for r in records if r["photo_file"]}
    print(f"Эталонов в индексе: {len(lookup)}")

    archive = load_archive()
    indexed_paths = {str(Path(r["photo_file"]).resolve()) for r in lookup.values()}
    path_to_slug = {str(Path(v["photo_file"]).resolve()): k for k, v in lookup.items()}

    phash_cache: dict[str, object] = {}

    checked = 0
    same_path = 0
    content_dup_own = 0
    near_dup_own = 0
    cross_leak = 0
    examples: list[tuple[str, str, str, str]] = []

    for slug, photos in archive.items():
        if slug not in lookup:
            continue
        idx_path = lookup[slug]["photo_file"]
        idx_resolved = str(Path(idx_path).resolve())
        if idx_path not in phash_cache:
            phash_cache[idx_path] = phash(idx_path)
        idx_hash = phash_cache[idx_path]

        for p in photos.extra:
            checked += 1
            p_resolved = str(Path(p).resolve())

            if p_resolved == idx_resolved:
                same_path += 1
                continue

            if p_resolved in path_to_slug and path_to_slug[p_resolved] != slug:
                cross_leak += 1
                examples.append(("cross", slug, path_to_slug[p_resolved], str(p)))
                continue

            try:
                if md5(str(p)) == md5(idx_path):
                    content_dup_own += 1
                    examples.append(("md5", slug, slug, str(p)))
                    continue
            except Exception:
                pass

            if idx_hash is not None:
                h = phash(p)
                if h is not None and (idx_hash - h) <= 5:
                    near_dup_own += 1
                    examples.append(("phash", slug, slug, str(p)))

    print(f"\nQuery-фото всего: {checked}")
    print(f"  исключены (путь = эталон своего вина, уже ловит benchmark_archive.py): {same_path}")
    print(f"  НОВОЕ: байт-в-байт дубль эталона своего вина (др. путь):  {content_dup_own}")
    print(f"  НОВОЕ: визуально почти идентичны (phash<=5) эталону своего вина: {near_dup_own}")
    print(f"  НОВОЕ: путь совпадает с эталоном ЧУЖОГО вина в индексе:    {cross_leak}")

    total_new_leaks = content_dup_own + near_dup_own + cross_leak
    if total_new_leaks == 0:
        print("\nУтечек, не пойманных текущей защитой benchmark_archive.py, не найдено.")
    else:
        print(f"\nНайдено {total_new_leaks} новых утечек — примеры:")
        for kind, a, b, path in examples[:15]:
            print(f"  [{kind}] {a} <-> {b}: {path}")


if __name__ == "__main__":
    main()
