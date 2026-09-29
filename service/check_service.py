"""Сквозная проверка запущенного сервиса так же, как это делает скрипт организаторов:
последовательные POST /v1/eval/predict с полем image, ответ {"slug": ...}.

Считает top-1 по кадрам (с дедупликацией по SHA-256, §11.48, и без неё — для сверки
с прогонами) и по винам, долю null,
латентность (среднее, p95, максимум против таймаута 10 с). Результат на тесте должен
совпасть с измеренными 93.4% — это проверка, что сервис собран как пайплайн прогонов.

    python service/check_service.py --frames /workspace/qwen_hires/data/frames_v2.csv
    python service/check_service.py --neg /workspace/negatives        # фото без вина
"""

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import requests

EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def ask(url, path):
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        r = requests.post(url, files={"image": (Path(path).name, f, "image/jpeg")}, timeout=(5, 10))
    dt = time.perf_counter() - t0
    r.raise_for_status()
    body = r.json()
    if isinstance(body, list):
        body = body[0] if body else {}
    return body.get("slug"), dt


def latency(ts):
    ts = sorted(ts)
    return f"латентность: среднее {sum(ts) / len(ts):.2f} с, p95 {ts[int(0.95 * (len(ts) - 1))]:.2f} с, макс {ts[-1]:.2f} с"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8080/v1/eval/predict")
    p.add_argument("--frames", help="csv с колонками path,true_slug[,set]; пути от папки csv")
    p.add_argument("--neg", help="папка с фото без вина")
    p.add_argument("--dups", help="catalog_duplicates.csv (group,slug): ответ из той же группы дублей каталога считать верным")
    p.add_argument("--out", default="service_check.jsonl")
    args = p.parse_args()
    log = open(args.out, "w", encoding="utf-8")
    group = {}
    if args.dups:
        with open(args.dups, encoding="utf-8-sig") as f:
            group = {r["slug"]: r["group"] for r in csv.DictReader(f)}

    if args.frames:
        root = Path(args.frames).parent
        with open(args.frames, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        seen, uniq, row_hash = set(), [], []
        for r in rows:
            h = hashlib.sha256((root / r["path"]).read_bytes()).hexdigest()
            row_hash.append(h)
            if h not in seen:
                seen.add(h)
                uniq.append(r)
                r["_hash"] = h
        print(f"кадров {len(rows)}, уникальных по SHA-256 {len(uniq)}")
        ok_all, ts, by_set, by_wine, nulls, pred_by_hash = [], [], {}, {}, 0, {}
        for i, r in enumerate(uniq, 1):
            slug, dt = ask(args.url, root / r["path"])
            pred_by_hash[r["_hash"]] = slug
            ok = slug == r["true_slug"]
            nulls += slug is None
            ok_all.append(ok)
            ts.append(dt)
            by_set.setdefault(r.get("set", "-"), []).append(ok)
            by_wine.setdefault(r["true_slug"], []).append(ok)
            log.write(json.dumps({"path": r["path"], "true": r["true_slug"], "pred": slug, "ok": ok, "sec": round(dt, 3)}, ensure_ascii=False) + "\n")
            if i % 50 == 0:
                print(f"  {i}/{len(uniq)}: top-1 {sum(ok_all) / i:.1%}", flush=True)
        print(f"\ntop-1 по кадрам: {sum(ok_all) / len(ok_all):.1%} ({sum(ok_all)}/{len(ok_all)}), "
              f"по винам: {sum(sum(v) / len(v) for v in by_wine.values()) / len(by_wine):.1%} ({len(by_wine)} вин), null: {nulls}")
        if group:
            same = lambda a, b: a == b or (a is not None and group.get(a, a) == group.get(b, b))
            ok_dup = sum(same(pred_by_hash[r["_hash"]], r["true_slug"]) for r in uniq)
            print(f"top-1, если дубли каталога считать одним вином: {ok_dup / len(uniq):.1%} ({ok_dup}/{len(uniq)})")
        ok_rows = sum(pred_by_hash[h] == r["true_slug"] for h, r in zip(row_hash, rows))
        print(f"top-1 без дедупликации (как в прогонах, для сверки с 93.4%): {ok_rows / len(rows):.1%} ({ok_rows}/{len(rows)})")
        for s, v in sorted(by_set.items()):
            print(f"  {s:20s} {sum(v) / len(v):.1%} ({sum(v)}/{len(v)})")
        print(latency(ts))

    if args.neg:
        files = sorted(f for f in Path(args.neg).rglob("*") if f.suffix.lower() in EXT)
        ts, nulls = [], 0
        for f in files:
            slug, dt = ask(args.url, f)
            nulls += slug is None
            ts.append(dt)
            log.write(json.dumps({"path": str(f), "pred": slug, "neg": True}, ensure_ascii=False) + "\n")
        print(f"\nфото без вина: {len(files)}, ответ null: {nulls} ({nulls / max(len(files), 1):.0%})")
        if ts:
            print(latency(ts))
    print(f"по кадрам: {args.out}")


if __name__ == "__main__":
    main()
