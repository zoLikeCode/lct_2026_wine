"""Проверка гипотезы: ошибки Qwen — от нехватки разрешения, а не от модели.

Десять оставшихся настоящих ошибок (§11.43) — соседи по линейке, которые
различаются МЕЛКИМ текстом сорта на этикетке: KAFFA «Каберне Фран» против
«Каберне Совиньон», Бельбек Мускат против Пино Нуара, Мезыбь Мерло против
Каберне.

Коллега запускал модель с `max_pixels = 1024·32·32`, то есть около тысячи
визуальных токенов на кадр. На фото, где бутылка занимает треть снимка, на
надпись с сортом приходятся считанные пиксели.

Для этой задачи разрешение уже показало себя решающим: §11.38 — понижение с
512 до 384 стоило 12.4пп даже при втрое большей модели, §11.39 — выше
обученного разрешения прироста нет. Здесь проверяется, не упирается ли Qwen
в ту же стену.

Чтобы сравнение было честным, на каждом разрешении заново кодируются И
запрос, И все пять кандидатов: иначе векторы окажутся из разных пространств.

    python qwen_finetune/probe_resolution.py --adapter ~/Downloads/v2_best/runs/v2/best
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--tokens", type=int, nargs="+", default=[1024, 2560],
                        help="лимит визуальных токенов; 1024 — как у коллеги")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--attn", default="eager")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    import torch
    from data_prep import config
    from qwen_finetune.common import INSTRUCTION, load_embedder

    with open(config.OUTPUTS_DIR / "catalog_resolved.json", encoding="utf-8") as f:
        records = {r["slug"]: r for r in json.load(f)}

    def reference_path(slug: str) -> Path | None:
        record = records.get(slug)
        if not record or not record["photo_file"]:
            return None
        path = record["photo_file"]
        return Path(path if path.startswith("/") else str(config.UPLOADS_DIR / path))

    cases = []
    with open(config.OUTPUTS_DIR / "qwen_verification_own.json", encoding="utf-8") as f:
        for row in json.load(f)["rows"]:
            top5 = [s for s, _ in row.get("qwen_top5", [])]
            if row["qwen"] != row["true"] and row["true"] in top5:
                cases.append({"path": row["path"], "true": row["true"], "top5": top5})
    if args.limit:
        cases = cases[:args.limit]
    print(f"Кадров: {len(cases)} (ошибка Qwen, верное вино в его же top-5)\n")

    summary = {}
    for tokens in args.tokens:
        embedder = load_embedder(max_pixels=tokens * 32 * 32, attn=args.attn)
        from peft import PeftModel
        embedder.model = PeftModel.from_pretrained(embedder.model, args.adapter)
        embedder.model.eval().to(args.device)
        device = next(embedder.model.parameters()).device

        def encode(image: Image.Image) -> np.ndarray:
            conv = embedder.format_model_input(image=image, instruction=INSTRUCTION)
            inputs = {k: v.to(device) for k, v in embedder._preprocess_inputs([conv]).items()}
            with torch.no_grad():
                out = embedder.model(**inputs, use_cache=False)
            emb = embedder._pooling_last(out.last_hidden_state, inputs["attention_mask"]).float()
            return (emb / emb.norm(dim=-1, keepdim=True)).cpu().numpy()[0]

        correct = 0
        started = time.perf_counter()
        for i, case in enumerate(cases, start=1):
            with Image.open(case["path"]) as raw:
                query = encode(ImageOps.exif_transpose(raw).convert("RGB"))
            scores = []
            for slug in case["top5"]:
                path = reference_path(slug)
                if path is None or not path.exists():
                    scores.append(-1.0)
                    continue
                with Image.open(path) as ref:
                    scores.append(float(query @ encode(ref.convert("RGB"))))
            best = case["top5"][int(np.argmax(scores))]
            ok = best == case["true"]
            correct += ok
            print(f"  [{'ВЕРНО ' if ok else 'мимо  '}] {Path(case['path']).name[:32]:34s} "
                  f"-> {best[:36]}  ({(time.perf_counter() - started) / i:.0f} с/кадр)", flush=True)

        summary[tokens] = correct
        print(f"  {tokens} токенов: верно {correct} из {len(cases)}\n", flush=True)
        del embedder
        if args.device == "mps":
            torch.mps.empty_cache()

    print("ИТОГ (переупорядочивание внутри top-5 на разных разрешениях):")
    for tokens, correct in summary.items():
        print(f"  {tokens:>5d} визуальных токенов: {correct}/{len(cases)}")
    with open(config.OUTPUTS_DIR / "resolution_probe.json", "w", encoding="utf-8") as f:
        json.dump({"n": len(cases), "by_tokens": summary}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
