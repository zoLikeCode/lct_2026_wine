"""Единый интерфейс поверх разных embedding-бэкбонов, чтобы бенчмарк не знал,
чем именно эмбеддится картинка. Инференс на MPS (Apple Silicon GPU), если
доступен, иначе CPU.
"""

from dataclasses import dataclass

import numpy as np
import torch
from PIL import Image


def get_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


@dataclass
class Backbone:
    name: str
    model: object
    processor: object
    device: torch.device

    @torch.no_grad()
    def encode(self, image: Image.Image) -> np.ndarray:
        return self.encode_batch([image])[0]

    @torch.no_grad()
    def encode_batch(self, images: list[Image.Image]) -> np.ndarray:
        images = [im.convert("RGB") for im in images]
        if self.name == "dinov2":
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            out = self.model(**inputs)
            # CLS token эмбеддинг
            emb = out.last_hidden_state[:, 0, :]
        elif self.name == "dinov2_mean":
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            out = self.model(**inputs)
            # среднее по патч-токенам (без CLS) — альтернативный pooling для retrieval
            emb = out.last_hidden_state[:, 1:, :].mean(dim=1)
        elif self.name == "siglip2":
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            out = self.model.get_image_features(**inputs)
            # некоторые версии transformers оборачивают результат в ModelOutput
            emb = out.pooler_output if hasattr(out, "pooler_output") else out
        else:
            raise ValueError(self.name)
        emb = torch.nn.functional.normalize(emb, dim=-1)
        return emb.cpu().float().numpy()


_CACHE: dict[str, Backbone] = {}


def load_backbone(name: str) -> Backbone:
    """name: 'dinov2' (facebook/dinov2-base) | 'siglip2' (google/siglip2-base-patch16-224)"""
    if name in _CACHE:
        return _CACHE[name]

    device = get_device()

    if name in ("dinov2", "dinov2_mean"):
        from transformers import AutoImageProcessor, AutoModel
        model_id = "facebook/dinov2-base"
        processor = AutoImageProcessor.from_pretrained(model_id)
        model = AutoModel.from_pretrained(model_id).to(device).eval()
    elif name == "siglip2":
        from transformers import AutoProcessor, AutoModel
        model_id = "google/siglip2-base-patch16-224"
        processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModel.from_pretrained(model_id).to(device).eval()
    else:
        raise ValueError(f"unknown backbone: {name}")

    backbone = Backbone(name=name, model=model, processor=processor, device=device)
    _CACHE[name] = backbone
    return backbone
