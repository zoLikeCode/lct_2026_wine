"""Готовит папку data/ для обучения эмбеддера на RunPod.

Что делает:
  1. копирует catalog_photos (эталоны + доп. фото по slug);
  2. выбирает эталон на каждый slug (catalog_reference > bottle > label) —
     это то, что ляжет в индекс;
  3. остальные фото slug-а — запросы для обучения (реальные ракурсы той же
     бутылки); 15% slug-ов с доп. фото откладываются на валидацию целиком;
  4. находит файлы, байт-в-байт общие для РАЗНЫХ slug (в каталоге их 131
     группа): такие фото не используются как запросы, а slug-и, делящие
     файлы, не считаются негативами друг для друга — иначе лосс учит
     противоречие «одна картинка = два разных вина»;
  5. копирует честные тестовые выборки, которые в обучение не попадают
     никогда: фото организаторов (размеченные) и собственные own_* фото;
  6. проверяет, что ни один тестовый файл не совпадает с файлами каталога.

Пути в манифестах — относительно data/.
"""

import argparse
import csv
import hashlib
import json
import random
import shutil
from collections import defaultdict
from pathlib import Path

IMAGE_EXTS = {"webp", "jpg", "jpeg", "png"}


def kind_of(name: str) -> str | None:
    n = name.lower()
    if n.startswith("own_") or n.startswith("."):
        return None
    stem, dot, ext = n.rpartition(".")
    if not dot or ext not in IMAGE_EXTS:
        return None
    if stem.endswith("catalog_reference"):
        return "reference"
    if stem.endswith("_bottle"):
        return "bottle"
    if stem.endswith("_front_label") or stem.endswith("_label"):
        return "label"
    return None


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    home = Path.home()
    p = argparse.ArgumentParser()
    p.add_argument("--catalog-photos", default=str(home / "Downloads/catalog_photos"))
    p.add_argument("--field-photos", default=str(home / "Downloads/Реальные фото"))
    p.add_argument("--field-labels", default=str(home / "Desktop/lct_2025_wine/outputs/field_labels.csv"))
    p.add_argument("--own-root", default=str(home / "Downloads/images"))
    p.add_argument("--catalog-csv", default=str(home / "Downloads/Датасет/strapi_output0709.csv"))
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "data"))
    p.add_argument("--val-frac", type=float, default=0.15)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    src = Path(args.catalog_photos).expanduser()
    out = Path(args.out).expanduser()
    (out / "manifests").mkdir(parents=True, exist_ok=True)

    # ---- 1. каталог фото ----
    files: dict[str, list[tuple[str, str, str]]] = {}  # slug -> [(name, kind, md5)]
    for slug_dir in sorted(d for d in src.iterdir() if d.is_dir()):
        items = []
        for f in sorted(slug_dir.iterdir()):
            k = kind_of(f.name)
            if k and f.is_file():
                items.append((f.name, k, md5(f)))
        if items:
            files[slug_dir.name] = items

    # ---- 2. общие файлы между разными slug ----
    hash_slugs: dict[str, set[str]] = defaultdict(set)
    for slug, items in files.items():
        for _, _, h in items:
            hash_slugs[h].add(slug)
    shared_hashes = {h for h, s in hash_slugs.items() if len(s) > 1}

    parent = {s: s for s in files}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for h in shared_hashes:
        slugs = sorted(hash_slugs[h])
        for s in slugs[1:]:
            parent[find(s)] = find(slugs[0])
    groups = defaultdict(list)
    for s in files:
        groups[find(s)].append(s)
    shared_groups = sorted([sorted(g) for g in groups.values() if len(g) > 1])

    # ---- 3-4. эталон и запросы ----
    order = {"reference": 0, "bottle": 1, "label": 2}
    references, extras = {}, {}
    for slug, items in files.items():
        ref = sorted(items, key=lambda x: (order[x[1]], x[0]))[0]
        references[slug] = ref
        extras[slug] = [it for it in items if it is not ref and it[2] not in shared_hashes and it[2] != ref[2]]

    rng = random.Random(args.seed)
    multi = sorted(s for s, e in extras.items() if e)
    val_slugs = set(rng.sample(multi, max(1, int(len(multi) * args.val_frac))))

    def rel(slug, name):
        return f"catalog_photos/{slug}/{name}"

    with open(out / "manifests/references.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["slug", "path", "kind"])
        for slug in sorted(references):
            name, kind, _ = references[slug]
            w.writerow([slug, rel(slug, name), kind])

    n_train = n_val = 0
    with open(out / "manifests/train_queries.csv", "w", newline="", encoding="utf-8") as ft, \
         open(out / "manifests/val_queries.csv", "w", newline="", encoding="utf-8") as fv:
        wt, wv = csv.writer(ft), csv.writer(fv)
        wt.writerow(["slug", "path", "kind"])
        wv.writerow(["slug", "path", "kind"])
        for slug in sorted(extras):
            for name, kind, _ in extras[slug]:
                if slug in val_slugs:
                    wv.writerow([slug, rel(slug, name), kind]); n_val += 1
                else:
                    wt.writerow([slug, rel(slug, name), kind]); n_train += 1

    (out / "manifests/shared_groups.json").write_text(
        json.dumps(shared_groups, ensure_ascii=False, indent=1), encoding="utf-8")

    meta = {}
    csv_path = Path(args.catalog_csv).expanduser()
    if csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                s = row.get("Slug")
                if s and s not in meta:
                    meta[s] = {"name": row.get("Название вина", "").strip(),
                               "winery": row.get("Винодельня", "").strip(),
                               "grape": row.get("Сорт винограда", "").strip(),
                               "category": row.get("Категория", "").strip()}
    (out / "manifests/catalog_meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # ---- 5. тестовые выборки ----
    catalog_hashes = set(hash_slugs)
    leaks = []
    eval_dir = out / "eval"
    (eval_dir / "field").mkdir(parents=True, exist_ok=True)
    n_field = 0
    field_labels = Path(args.field_labels).expanduser()
    field_photos = Path(args.field_photos).expanduser()
    with open(field_labels, encoding="utf-8") as f, \
         open(eval_dir / "field_labels.csv", "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["slug", "path"])
        for row in csv.DictReader(f):
            if not row.get("true_slug"):
                continue
            srcf = field_photos / row["image_path"]
            if not srcf.exists():
                print("  нет файла:", srcf)
                continue
            if md5(srcf) in catalog_hashes:
                leaks.append(str(srcf))
            shutil.copy2(srcf, eval_dir / "field" / srcf.name)
            w.writerow([row["true_slug"], f"eval/field/{srcf.name}"])
            n_field += 1

    n_own = 0
    own_root = Path(args.own_root).expanduser()
    with open(eval_dir / "own_labels.csv", "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["slug", "path"])
        for slug_dir in sorted(d for d in own_root.iterdir() if d.is_dir()):
            for fpath in sorted(slug_dir.iterdir()):
                if not fpath.name.startswith("own_"):
                    continue
                if md5(fpath) in catalog_hashes:
                    leaks.append(str(fpath))
                dst = eval_dir / "own" / slug_dir.name
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fpath, dst / fpath.name)
                w.writerow([slug_dir.name, f"eval/own/{slug_dir.name}/{fpath.name}"])
                n_own += 1

    # ---- 1'. копия каталога ----
    dst_cat = out / "catalog_photos"
    for slug, items in files.items():
        (dst_cat / slug).mkdir(parents=True, exist_ok=True)
        for name, _, _ in items:
            target = dst_cat / slug / name
            if not target.exists():
                shutil.copy2(src / slug / name, target)

    summary = {
        "slugs_with_reference": len(references),
        "train_queries": n_train, "val_queries": n_val, "val_slugs": len(val_slugs),
        "shared_file_groups": len(shared_groups),
        "slugs_in_shared_groups": sum(len(g) for g in shared_groups),
        "shared_files_excluded": len(shared_hashes),
        "eval_field": n_field, "eval_own": n_own,
        "test_leaks_into_catalog": len(leaks),
    }
    (out / "manifests/summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if leaks:
        raise SystemExit(f"УТЕЧКА: тестовые фото совпадают с каталогом: {leaks[:5]}")


if __name__ == "__main__":
    main()
