"""LoRA v3: дообучение адаптера v2 на реальных фото и трудных соседях по линейке.

Что меняется относительно v2 (train_v2.py):
  * старт с адаптера v2 (--init-adapter), малый lr — доучиваем, а не учим заново;
  * запросы — в первую очередь ИНТЕРНЕТ-ФОТО В РЕАЛЬНОЙ ОБСТАНОВКЕ (полка/прилавок,
    wine_real_yolo_*): это ближе всего к кадрам организаторов. Плюс доп. фото
    каталога и немного имитаций полки, чтобы не забыть остальные вина;
  * запросы — на 2560 визуальных токенах, эталоны ужаты до бюджета 1024 токенов:
    ровно та асимметрия, что в проде (§11.45);
  * трудные негативы — ближайшие чужие эталоны (обычно та же линейка той же
    винодельни), как в v2, но по 3 на запрос.

Чекпоинт выбирается по ОТЛОЖЕННЫМ интернет-фото (20% вин, разбиение по винам),
тестовые 379 кадров в обучении не участвуют и здесь не смотрятся вовсе —
их считает eval_v3.py уже после обучения.

Запуск (из /workspace/qwen_hires):
    export HF_HOME=/workspace/hf OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
    python train_v3.py --exp v3 2>&1 | tee results/train_v3.log
"""

import argparse
import csv
import json
import math
import random
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from common import encode, light_augment, load_embedder, load_image

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "up_proj", "down_proj", "gate_proj"]


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fit(img, tokens, unit=32):
    w, h = img.size
    k = min(1.0, (tokens * unit * unit / (w * h)) ** 0.5)
    nw, nh = max(unit, int(w * k) // unit * unit), max(unit, int(h * k) // unit * unit)
    return img.resize((nw, nh), Image.BICUBIC) if (nw, nh) != (w, h) else img


class Queries(Dataset):
    def __init__(self, items, ref_paths, q_tokens, seed):
        self.items, self.ref_paths, self.q_tokens, self.seed = items, ref_paths, q_tokens, seed

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        t, path, kind = self.items[i]
        rng = random.Random(self.seed * 1_000_003 + i)
        if kind == "sim":
            import cv2
            cv2.setNumThreads(1)
            from augment_realistic import simulate_realistic_photo
            q = simulate_realistic_photo(load_image(DATA / self.ref_paths[t]), seed=rng.randint(0, 2**31 - 1))
        else:
            q = light_augment(load_image(DATA / path), rng)
        return t, fit(q, self.q_tokens)


def collate(batch):
    ts, qs = zip(*batch)
    return list(ts), list(qs)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--exp", default="v3")
    p.add_argument("--init-adapter", default=str(HERE / "adapter"))
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--micro-bs", type=int, default=8)
    p.add_argument("--grad-accum", type=int, default=4)
    p.add_argument("--tau", type=float, default=0.05)
    p.add_argument("--neg-per-query", type=int, default=3)
    p.add_argument("--neighbor-pool", type=int, default=8)
    p.add_argument("--inet-repeat", type=int, default=2, help="сколько раз за эпоху показать каждое интернет-фото")
    p.add_argument("--extra-per-epoch", type=int, default=800, help="доп. фото каталога за эпоху")
    p.add_argument("--sim-per-epoch", type=int, default=300, help="имитаций полки за эпоху")
    p.add_argument("--q-tokens", type=int, default=2560)
    p.add_argument("--ref-tokens", type=int, default=1024)
    p.add_argument("--eval-every", type=int, default=20)
    p.add_argument("--eval-bs", type=int, default=16)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    torch.manual_seed(args.seed)
    run_dir = HERE / "runs" / args.exp
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "args.json").write_text(json.dumps(vars(args), indent=2))

    def log(rec):
        rec["time"] = time.strftime("%H:%M:%S")
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        with open(run_dir / "log.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- данные
    refs = read_csv(DATA / "refs.csv")
    ref_slugs = [r["slug"] for r in refs]
    ref_paths = [r["path"] for r in refs]
    sidx = {s: i for i, s in enumerate(ref_slugs)}
    inet = [r for r in read_csv(DATA / "inet_queries.csv") if r["slug"] in sidx]
    inet_train = [r for r in inet if r["split"] == "train"]
    inet_val = [r for r in inet if r["split"] == "val"]
    extras = [r for r in read_csv(DATA / "extra_refs.csv") if r["slug"] in sidx]
    group_of = {}
    for g in json.load(open(DATA / "shared_groups.json", encoding="utf-8")):
        ids = [sidx[s] for s in g if s in sidx]
        for i in ids:
            group_of[i] = ids

    t0 = time.time()
    ref_images = [fit(load_image(DATA / pth), args.ref_tokens) for pth in ref_paths]
    val_images = [fit(load_image(DATA / r["path"]), args.q_tokens) for r in inet_val]
    val_tgt = torch.tensor([sidx[r["slug"]] for r in inet_val])
    log({"event": "data", "refs": len(refs), "inet_train": len(inet_train), "inet_val": len(inet_val),
         "inet_val_wines": len({r['slug'] for r in inet_val}), "extras": len(extras), "sec": round(time.time() - t0)})

    # ---------------- модель
    embedder = load_embedder(max_pixels=args.q_tokens * 32 * 32)
    from peft import PeftModel
    base = embedder.model
    base.config.use_cache = False
    base.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model = PeftModel.from_pretrained(base, args.init_adapter, is_trainable=True)
    for prm in model.parameters():
        if prm.requires_grad:
            prm.data = prm.data.float()
    embedder.model = model
    model.print_trainable_parameters()
    params = [prm for prm in model.parameters() if prm.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0, betas=(0.9, 0.98))

    per_epoch = len(inet_train) * args.inet_repeat + min(args.extra_per_epoch, len(extras)) + args.sim_per_epoch
    steps_per_epoch = math.ceil(per_epoch / (args.micro_bs * args.grad_accum))
    total = steps_per_epoch * args.epochs
    warmup = max(3, int(0.05 * total))

    def lr_at(s):
        if s < warmup:
            return args.lr * (s + 1) / warmup
        return args.lr * 0.5 * (1 + math.cos(math.pi * (s - warmup) / max(1, total - warmup)))

    log({"event": "start", "per_epoch": per_epoch, "steps_total": total})

    @torch.no_grad()
    def encode_all(images):
        was = model.training
        model.eval()
        out = torch.cat([encode(embedder, images[i:i + args.eval_bs]).cpu() for i in range(0, len(images), args.eval_bs)])
        if was:
            model.train()
        return out

    best = {"key": None}

    def run_eval(tag):
        t = time.time()
        R = encode_all(ref_images)
        Q = encode_all(val_images)
        sims = Q @ R.T
        top = sims.topk(5, dim=1).indices
        top1 = (top[:, 0] == val_tgt).float().mean().item()
        top5 = (top == val_tgt[:, None]).any(1).float().mean().item()
        key = (round(top1, 4), round(top5, 4))
        log({"event": "eval", "at": tag, "val_top1": key[0], "val_top5": key[1], "n": len(val_tgt), "sec": round(time.time() - t)})
        if best["key"] is None or key > best["key"]:
            best["key"] = key
            model.save_pretrained(run_dir / "best")
            log({"event": "save_best", "at": tag, "val": key})
        e = R.cuda()
        s = e @ e.T
        s.fill_diagonal_(-2.0)
        for i, grp in group_of.items():
            s[i, grp] = -2.0
        return s.topk(args.neighbor_pool, dim=1).indices.cpu()

    neighbors = run_eval("step0 (= v2)")
    rng = random.Random(args.seed)
    step = 0
    for epoch in range(1, args.epochs + 1):
        er = random.Random(args.seed + epoch)
        items = [(sidx[r["slug"]], r["path"], "inet") for r in inet_train] * args.inet_repeat
        items += [(sidx[r["slug"]], r["path"], "extra") for r in er.sample(extras, min(args.extra_per_epoch, len(extras)))]
        items += [(t, None, "sim") for t in er.sample(range(len(refs)), args.sim_per_epoch)]
        er.shuffle(items)
        loader = DataLoader(Queries(items, ref_paths, args.q_tokens, args.seed + epoch), batch_size=args.micro_bs,
                            shuffle=False, num_workers=args.workers, collate_fn=collate, prefetch_factor=2)
        model.train()
        opt.zero_grad(set_to_none=True)
        st = {"loss": 0.0, "acc": 0.0, "pos": 0.0, "neg": 0.0, "refcos": 0.0, "n": 0}
        t_ep = time.time()
        for micro, (tgt, qs) in enumerate(loader):
            in_batch = set(tgt)
            for t in tgt:
                in_batch.update(group_of.get(t, []))
            negs = []
            for t in tgt:
                pool = [j for j in neighbors[t].tolist() if j not in in_batch and j not in negs]
                negs += rng.sample(pool, min(args.neg_per_query, len(pool)))
            q = encode(embedder, qs)
            pos = encode(embedder, [ref_images[t] for t in tgt])
            if negs:
                with torch.no_grad():
                    neg = encode(embedder, [ref_images[j] for j in negs])
                cand = torch.cat([pos, neg])
            else:
                cand = pos
            cand_ids = tgt + negs
            logits = (q @ cand.T) / args.tau
            mask = torch.zeros_like(logits, dtype=torch.bool)
            for i, t in enumerate(tgt):
                same = set(group_of.get(t, [t]))
                for j, c in enumerate(cand_ids):
                    if j != i and c in same:
                        mask[i, j] = True
            logits = logits.masked_fill(mask, float("-inf"))
            labels = torch.arange(len(tgt), device=logits.device)
            loss = F.cross_entropy(logits, labels)
            (loss / args.grad_accum).backward()
            with torch.no_grad():
                B = len(tgt)
                st["loss"] += loss.item()
                st["acc"] += (logits.argmax(1) == labels).float().mean().item()
                st["pos"] += (q * pos).sum(-1).mean().item()
                no = logits.detach().clone()
                no[labels, labels] = float("-inf")
                st["neg"] += (no.max(1).values * args.tau).mean().item()
                pp = pos @ pos.T
                st["refcos"] += pp[~torch.eye(B, dtype=torch.bool, device=pp.device)].mean().item() if B > 1 else 0.0
                st["n"] += 1
            if (micro + 1) % args.grad_accum == 0:
                for g in opt.param_groups:
                    g["lr"] = lr_at(step)
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                if step % 5 == 0:
                    n = max(st["n"], 1)
                    log({"event": "train", "step": step, "of": total, "lr": round(lr_at(step), 7),
                         "loss": round(st["loss"] / n, 4), "acc": round(st["acc"] / n, 3), "pos_cos": round(st["pos"] / n, 3),
                         "hardest_neg_cos": round(st["neg"] / n, 3), "ref_cos": round(st["refcos"] / n, 3),
                         "sec_per_step": round((time.time() - t_ep) / max(step - (epoch - 1) * steps_per_epoch, 1), 1)})
                    st = {k: 0.0 for k in st}
                local = step - (epoch - 1) * steps_per_epoch
                if args.eval_every and local % args.eval_every == 0 and steps_per_epoch - local > args.eval_every // 2:
                    neighbors = run_eval(f"step{step}")
        model.save_pretrained(run_dir / f"epoch{epoch}")
        neighbors = run_eval(f"epoch{epoch}")
    log({"event": "done", "best_val": best["key"], "best_dir": str(run_dir / "best")})


if __name__ == "__main__":
    main()
