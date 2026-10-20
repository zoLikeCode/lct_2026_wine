"""LoRA-дообучение Qwen3-VL-Embedding-8B — версия 2, без устаревшего банка.

Что было не так в train.py: негативы брались из банка, посчитанного старой
моделью, а запрос и его эталон — свежей. Модели проще сдвинуть все свежие
векторы в одну сторону (схлопывание), чем учиться различать вина: loss рос,
а acc_vs_bank оставался 1.0.

Здесь все векторы в лоссе посчитаны ТЕКУЩЕЙ моделью в том же шаге:
  * запросы и их эталоны — с градиентом;
  * трудные негативы — эталоны ближайших «соседей» каждого вина (обычно та же
    линейка той же винодельни), по --neg-per-query на запрос, без градиента,
    но текущими весами — поэтому сдвиг всех векторов в одну сторону не даёт
    выигрыша;
  * остальные эталоны батча — обычные негативы.
Таблица соседей строится по эмбеддингам эталонов и обновляется после каждой
промежуточной оценки. Лосс считается на запрос внутри микро-батча, поэтому
накопление градиента корректно.

Контроль схлопывания: в логе `ref_cos` — средняя похожесть РАЗНЫХ эталонов
батча. Растёт к 1 -> модель схлопывается, останавливать.

Запуск:
  export OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
  python train_v2.py --exp v2
"""

import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from common import (DATA, DEFAULT_MAX_PIXELS, encode, field_simulation, light_augment,
                    load_embedder, load_image, load_query_set, load_references,
                    load_shared_groups)
from eval import evaluate

LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "up_proj", "down_proj", "gate_proj"]


class QueryDataset(Dataset):
    """Отдаёт (индекс эталона, картинка-запрос). Эталоны не грузит —
    они заранее лежат в памяти главного процесса."""

    def __init__(self, items, ref_paths, slug_idx, sim_prob, seed):
        self.items = items
        self.ref_paths = ref_paths
        self.slug_idx = slug_idx
        self.sim_prob = sim_prob
        self.seed = seed

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        slug, path = self.items[i]
        rng = random.Random(self.seed * 1_000_003 + i)
        t = self.slug_idx[slug]
        if path is None:
            ref = load_image(DATA / self.ref_paths[t])
            q = field_simulation(ref, rng) if rng.random() < self.sim_prob else light_augment(ref, rng)
        else:
            q = light_augment(load_image(DATA / path), rng)
        return t, q


def collate(batch):
    ts, qs = zip(*batch)
    return list(ts), list(qs)


def build_neighbors(ref_emb: torch.Tensor, group_of: dict, k: int) -> torch.Tensor:
    """[N, k] индексы самых похожих ЧУЖИХ эталонов (без себя и без slug-ов,
    делящих с ним одинаковые файлы)."""
    e = ref_emb.cuda()
    sims = e @ e.T
    sims.fill_diagonal_(-2.0)
    for i, grp in group_of.items():
        sims[i, grp] = -2.0
    return sims.topk(k, dim=1).indices.cpu()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--exp", default="v2")
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--lr", type=float, default=5e-5)
    p.add_argument("--micro-bs", type=int, default=16)
    p.add_argument("--grad-accum", type=int, default=2)
    p.add_argument("--tau", type=float, default=0.05)
    p.add_argument("--neg-per-query", type=int, default=2, help="трудных негативов-соседей на запрос")
    p.add_argument("--neighbor-pool", type=int, default=8, help="из скольких ближайших соседей выбирать")
    p.add_argument("--lora-r", type=int, default=32)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    p.add_argument("--sim-prob", type=float, default=0.7)
    p.add_argument("--sim-frac", type=float, default=0.4,
                   help="доля вин, для которых в эпоху генерируется имитация полки")
    p.add_argument("--eval-every", type=int, default=40, help="промежуточная оценка каждые N шагов")
    p.add_argument("--eval-bs", type=int, default=16)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--init-adapter", default=None, help="продолжить с сохранённого LoRA-адаптера")
    args = p.parse_args()

    torch.manual_seed(args.seed)
    run_dir = Path("runs") / args.exp
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "args.json").write_text(json.dumps(vars(args), indent=2))
    log_path = run_dir / "log.jsonl"

    def log(rec):
        rec["time"] = time.strftime("%H:%M:%S")
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- модель + LoRA ----
    embedder = load_embedder(max_pixels=args.max_pixels)
    from peft import LoraConfig, get_peft_model
    base = embedder.model
    base.config.use_cache = False
    base.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    if args.init_adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(base, args.init_adapter, is_trainable=True)
    else:
        model = get_peft_model(base, LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.0,
                                                target_modules=LORA_TARGETS, bias="none"))
    for prm in model.parameters():
        if prm.requires_grad:
            prm.data = prm.data.float()
    embedder.model = model
    model.print_trainable_parameters()

    # ---- данные ----
    ref_slugs, ref_paths = load_references()
    slug_idx = {s: i for i, s in enumerate(ref_slugs)}
    train_rows = [r for r in load_query_set("train") if r["slug"] in slug_idx]
    group_of = {}
    for g in load_shared_groups():
        ids = [slug_idx[s] for s in g if s in slug_idx]
        for i in ids:
            group_of[i] = ids

    t0 = time.time()
    ref_images = [load_image(DATA / pth) for pth in ref_paths]  # ~2083 небольших картинок в RAM
    log({"event": "refs_loaded", "n": len(ref_images), "sec": round(time.time() - t0)})

    params = [prm for prm in model.parameters() if prm.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0, betas=(0.9, 0.98))
    n_sim = int(len(ref_slugs) * args.sim_frac)
    per_epoch = len(train_rows) + n_sim
    if args.limit:
        per_epoch = min(per_epoch, args.limit)
    steps_per_epoch = math.ceil(per_epoch / (args.micro_bs * args.grad_accum))
    total_steps = steps_per_epoch * args.epochs
    warmup = max(5, int(0.05 * total_steps))

    def lr_at(step):
        if step < warmup:
            return args.lr * (step + 1) / warmup
        prog = (step - warmup) / max(1, total_steps - warmup)
        return args.lr * 0.5 * (1 + math.cos(math.pi * prog))

    log({"event": "start", "train_queries": len(train_rows), "sim_per_epoch": n_sim,
         "per_epoch": per_epoch, "opt_steps_total": total_steps})

    best_key = None

    def run_eval(tag):
        nonlocal best_key
        res = evaluate(embedder, ["val", "field", "own"], args.eval_bs)
        log({"event": "eval", "at": tag, **res["results"]})
        key = (res["results"]["val"]["top1"], res["results"]["val"]["top5"])
        if best_key is None or key > best_key:
            best_key = key
            model.save_pretrained(run_dir / "best")
            log({"event": "save_best", "at": tag, "val": key})
        model.train()
        return build_neighbors(res["ref_emb"], group_of, args.neighbor_pool)

    neighbors = run_eval("step0")
    rng = random.Random(args.seed)

    step = 0
    for epoch in range(1, args.epochs + 1):
        items = [(r["slug"], r["path"]) for r in train_rows]
        items += [(s, None) for s in random.Random(args.seed + epoch).sample(ref_slugs, n_sim)]
        random.Random(args.seed + 100 + epoch).shuffle(items)
        if args.limit:
            items = items[: args.limit]
        loader = DataLoader(QueryDataset(items, ref_paths, slug_idx, args.sim_prob, args.seed + epoch),
                            batch_size=args.micro_bs, shuffle=False, num_workers=args.workers,
                            collate_fn=collate, prefetch_factor=2)
        model.train()
        opt.zero_grad(set_to_none=True)
        stats = {"loss": 0.0, "acc": 0.0, "pos_cos": 0.0, "neg_cos": 0.0, "ref_cos": 0.0, "n": 0}
        t_ep = time.time()
        for micro, (tgt, qs) in enumerate(loader):
            # трудные негативы: соседи целевых вин, которых нет среди целей батча
            in_batch = set(tgt)
            for t in tgt:
                in_batch.update(group_of.get(t, []))
            negs = []
            for t in tgt:
                pool = [j for j in neighbors[t].tolist() if j not in in_batch and j not in negs]
                negs += rng.sample(pool, min(args.neg_per_query, len(pool)))

            q = encode(embedder, qs)                                   # [B, D], градиент
            pos = encode(embedder, [ref_images[t] for t in tgt])        # [B, D], градиент
            if negs:
                with torch.no_grad():
                    neg = encode(embedder, [ref_images[j] for j in negs])  # [M, D], текущие веса
                cand = torch.cat([pos, neg])
            else:
                cand = pos
            cand_ids = tgt + negs

            logits = (q @ cand.T) / args.tau                           # [B, B+M]
            mask = torch.zeros_like(logits, dtype=torch.bool)
            for i, t in enumerate(tgt):
                same = set(group_of.get(t, [t]))
                for j, c in enumerate(cand_ids):
                    if j != i and c in same:  # тот же slug ещё раз или общие файлы
                        mask[i, j] = True
            logits = logits.masked_fill(mask, float("-inf"))
            labels = torch.arange(len(tgt), device=logits.device)
            loss = F.cross_entropy(logits, labels)
            (loss / args.grad_accum).backward()

            with torch.no_grad():
                B = len(tgt)
                stats["loss"] += loss.item()
                stats["acc"] += (logits.argmax(1) == labels).float().mean().item()
                stats["pos_cos"] += (q * pos).sum(-1).mean().item()
                neg_only = logits.detach().clone()
                neg_only[labels, labels] = float("-inf")
                stats["neg_cos"] += (neg_only.max(1).values * args.tau).mean().item()
                pp = pos @ pos.T
                off = ~torch.eye(B, dtype=torch.bool, device=pp.device)
                stats["ref_cos"] += pp[off].mean().item() if B > 1 else 0.0
                stats["n"] += 1

            if (micro + 1) % args.grad_accum == 0:
                for g in opt.param_groups:
                    g["lr"] = lr_at(step)
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                if step % 10 == 0:
                    n = max(stats["n"], 1)
                    log({"event": "train", "epoch": epoch, "step": step, "lr": round(lr_at(step), 7),
                         "loss": round(stats["loss"] / n, 4), "acc": round(stats["acc"] / n, 3),
                         "pos_cos": round(stats["pos_cos"] / n, 3),
                         "hardest_neg_cos": round(stats["neg_cos"] / n, 3),
                         "ref_cos": round(stats["ref_cos"] / n, 3),
                         "sec_per_step": round((time.time() - t_ep) / max(step - (epoch - 1) * steps_per_epoch, 1), 1)})
                    stats = {k: 0.0 for k in stats}
                local = step - (epoch - 1) * steps_per_epoch
                if (args.eval_every and local % args.eval_every == 0
                        and steps_per_epoch - local > args.eval_every // 2):  # не дублировать оценку конца эпохи
                    neighbors = run_eval(f"step{step}")

        model.save_pretrained(run_dir / f"epoch{epoch}")
        neighbors = run_eval(f"epoch{epoch}")

    log({"event": "done", "best_val": best_key, "run_dir": str(run_dir)})


if __name__ == "__main__":
    main()
