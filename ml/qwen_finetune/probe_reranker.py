"""Целевая проверка Qwen3-VL-Reranker на кадрах, где ошиблись ОБЕ модели.

Зачем именно такая выборка. Из 254 честных кадров Qwen+LoRA ошибается на 14,
и 13 из них SigLIP2 тоже не берёт (§11.42). Разбор показал, что у 9 из этих
13 верное вино лежит в top-5 Qwen на 2-5 месте, а различаются пары названием
СОРТА на этикетке: KAFFA Каберне Фран против Каберне Совиньон, Собер Баш
Ркацители против Красностопа, Бельбек Мускат против Пино Нуара.

Эмбеддер обязан уместить это различие в один вектор. Реранкер — кросс-энкодер:
он получает фото запроса и кандидата В ОДНОМ контексте и может прямо сверить
надпись на этикетке с названием кандидата. Кандидат подаётся и картинкой, и
текстом, потому что различие именно текстовое.

Проверка узкая: если реранкер не вытаскивает эти случаи, он не поможет нигде,
и направление закрывается без полного прогона. Если вытаскивает — полный
прогон делается на GPU.

    python qwen_finetune/probe_reranker.py --model Qwen/Qwen3-VL-Reranker-2B
"""

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "Qwen3-VL-Embedding"))

INSTRUCTION = ("Given a photo of a wine bottle, decide whether the candidate is the exact same wine: "
               "the same producer, wine name, grape variety, sweetness and vintage printed on the label.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen3-VL-Reranker-2B")
    parser.add_argument("--attn", default="eager",
                        help="sdpa падает на MPS: Metal не разворачивает grouped-query attention")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    import torch
    from data_prep import config
    from src.models.qwen3_vl_reranker import Qwen3VLReranker

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = {r["slug"]: r for r in json.load(f)}

    def photo_of(slug: str) -> Path | None:
        record = records.get(slug)
        if not record or not record["photo_file"]:
            return None
        path = record["photo_file"]
        return Path(path if path.startswith("/") else str(config.UPLOADS_DIR / path))

    def title_of(slug: str) -> str:
        record = records.get(slug, {})
        parts = [record.get("winery", ""), record.get("name", ""), record.get("grape", "")]
        return ", ".join(p for p in parts if p)

    cases = []
    with open(config.OUTPUTS_DIR / "qwen_verification_own.json", encoding="utf-8") as f:
        for row in json.load(f)["rows"]:
            if row.get("siglip") and row["qwen"] != row["true"] and row["siglip"] != row["true"]:
                top5 = [s for s, _ in row.get("qwen_top5", [])]
                if row["true"] in top5:  # реранкер физически не может достать то, чего нет в списке
                    cases.append({"path": row["path"], "true": row["true"], "top5": top5})
    if args.limit:
        cases = cases[:args.limit]
    print(f"Кадров для проверки: {len(cases)} (обе модели ошиблись, верное вино в top-5 Qwen)")

    model = Qwen3VLReranker(model_name_or_path=args.model, torch_dtype=torch.bfloat16,
                            attn_implementation=args.attn)
    model.model = model.model.to(args.device)
    model.device = torch.device(args.device)
    if hasattr(model, "score_linear"):
        model.score_linear = model.score_linear.to(args.device)

    # Репозиторий Qwen написан под transformers 4.57, у нас 5.x: там
    # `mm_token_type_ids` отдаётся списком, а новый `get_rope_index` индексирует
    # его булевой маской — списки так индексировать нельзя. Приводим к тензору.
    original_tokenize = model.tokenize

    def tokenize_compat(pairs, **kwargs):
        out = original_tokenize(pairs, **kwargs)
        if isinstance(out.get("mm_token_type_ids"), list):
            out["mm_token_type_ids"] = torch.tensor(out["mm_token_type_ids"], dtype=torch.long)
        return out

    model.tokenize = tokenize_compat
    print(f"Реранкер на {args.device}: {args.model}")

    fixed = kept_wrong = 0
    results = []
    started = time.perf_counter()
    for i, case in enumerate(cases, start=1):
        with Image.open(case["path"]) as raw:
            query_image = ImageOps.exif_transpose(raw).convert("RGB")
        documents = []
        for slug in case["top5"]:
            path = photo_of(slug)
            document = {"text": title_of(slug)}
            if path and path.exists():
                with Image.open(path) as ref:
                    document["image"] = ref.convert("RGB")
            documents.append(document)

        scores = model.process({"instruction": INSTRUCTION,
                                "query": {"image": query_image},
                                "documents": documents})
        best = case["top5"][max(range(len(scores)), key=lambda j: scores[j])]
        ok = best == case["true"]
        fixed += ok
        kept_wrong += not ok
        results.append({"path": case["path"], "true": case["true"], "was": case["top5"][0],
                        "reranked": best, "scores": scores, "top5": case["top5"]})
        mark = "ИСПРАВИЛ" if ok else "не помог"
        print(f"  [{mark}] {Path(case['path']).name[:34]:36s} "
              f"было {case['top5'][0][:28]:30s} стало {best[:28]}", flush=True)
        print(f"      ({(time.perf_counter() - started) / i:.0f} с/кадр)", flush=True)

    print(f"\nИсправлено {fixed} из {len(cases)}")
    out = config.OUTPUTS_DIR / "reranker_probe.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"model": args.model, "fixed": fixed, "n": len(cases), "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
