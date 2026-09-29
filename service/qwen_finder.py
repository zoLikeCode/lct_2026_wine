"""Прод-предсказатель на Qwen3-VL-Embedding-8B + LoRA v2 (qwen_finetune/STATUS_gpu_runs_20260929.md).

Конфигурация, измеренная на 379 кадрах (93.4% top-1, 98.9% top-5):
  * эталоны — 1024 визуальных токена, у вина несколько эталонов (основной + доп. фото
    каталога), схожесть вина = максимум по его эталонам; индекс готовит
    qwen_finetune/build_index.py -> results/index_prod.npz;
  * запрос — целый кадр (EXIF-поворот, без кропа и без уменьшения) на 2560 токенах;
  * косинус, top-1. Без реранкера и детекции этикетки.

Порог «на фото нет вина»: максимум схожести ниже порога -> вина нет. На тесте фото вина
не ниже 0.69, фото без вина (COCO, сок) не выше 0.64; рекомендованный порог 0.60.

Пути (переменные окружения):
  QWEN_DIR      папка qwen_finetune с common.py, adapter/, Qwen3-VL-Embedding/, data/
                (на поде — /workspace/qwen_hires); по умолчанию папка над service/,
                если в ней есть common.py, иначе <repo>/qwen_finetune
  QWEN_ADAPTER  адаптер LoRA, по умолчанию $QWEN_DIR/adapter
  QWEN_INDEX    индекс, по умолчанию $QWEN_DIR/results/index_prod.npz
  QWEN_QUERY_TOKENS  визуальных токенов на запрос, по умолчанию 2560
"""

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
# service/ лежит либо в репозитории (рядом с qwen_finetune/), либо прямо в папке пакета
# на поде (/workspace/qwen_hires/service) — тогда common.py на уровень выше.
_DEFAULT_DIR = REPO_ROOT if (REPO_ROOT / "common.py").exists() else REPO_ROOT / "qwen_finetune"
QWEN_DIR = Path(os.environ.get("QWEN_DIR", _DEFAULT_DIR))
ADAPTER = Path(os.environ.get("QWEN_ADAPTER", QWEN_DIR / "adapter"))
INDEX = Path(os.environ.get("QWEN_INDEX", QWEN_DIR / "results" / "index_prod.npz"))
QUERY_TOKENS = int(os.environ.get("QWEN_QUERY_TOKENS", "2560"))


@dataclass
class Prediction:
    slug: str
    score: float
    record: dict = field(default_factory=dict)


def _load_catalog() -> dict:
    """Карточки вин для /v1/search. Необязательны: без них отдаётся только slug."""
    for path in (QWEN_DIR / "catalog_cards.json", REPO_ROOT / "outputs" / "catalog_resolved.json"):
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return {r["slug"]: r for r in json.load(f)}
    return {}


class QwenFinder:
    def __init__(self):
        import torch
        import torch.nn.functional as F
        from peft import PeftModel

        sys.path.insert(0, str(QWEN_DIR))
        from common import INSTRUCTION, load_embedder

        if not INDEX.exists():
            raise SystemExit(f"нет индекса {INDEX} — запустите qwen_finetune/build_index.py")
        b = np.load(INDEX, allow_pickle=False)
        self.slugs = [str(s) for s in b["slugs"]]
        self.owner = torch.from_numpy(b["owner"].astype(np.int64))
        self._torch, self._F, self._instruction = torch, F, INSTRUCTION

        self.emb = load_embedder(max_pixels=QUERY_TOKENS * 32 * 32)
        self.emb.model = PeftModel.from_pretrained(self.emb.model, str(ADAPTER)).eval()
        self.device = self.emb.model.device
        self.vecs = torch.from_numpy(b["vecs"].astype(np.float32)).to(self.device)
        self.owner = self.owner.to(self.device)
        self.lookup = _load_catalog()
        self._lock = threading.Lock()

    def embed(self, image: Image.Image):
        torch = self._torch
        conv = self.emb.format_model_input(image=image, instruction=self._instruction)
        inputs = {k: v.to(self.device) for k, v in self.emb._preprocess_inputs([conv]).items()}
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            out = self.emb.model(**inputs, use_cache=False)
            e = self.emb._pooling_last(out.last_hidden_state, inputs["attention_mask"])
        return self._F.normalize(e.float(), dim=-1)[0]

    def wine_scores(self, image: Image.Image):
        """Схожесть кадра с каждым вином каталога: максимум по эталонам вина."""
        torch = self._torch
        with self._lock:
            q = self.embed(image)
            s = self.vecs @ q
            per_wine = torch.full((len(self.slugs),), -1.0, device=self.device)
            per_wine.scatter_reduce_(0, self.owner, s, reduce="amax")
        return per_wine

    def predict(self, image: Image.Image, top_k: int = 5) -> list[Prediction]:
        per_wine = self.wine_scores(image)
        val, idx = per_wine.topk(top_k)
        return [Prediction(self.slugs[i], float(v), self.lookup.get(self.slugs[i], {}))
                for v, i in zip(val.tolist(), idx.tolist())]
