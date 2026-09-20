"""Единый интерфейс поверх разных embedding-бэкбонов, чтобы бенчмарк не знал,
чем именно эмбеддится картинка. Инференс на MPS (Apple Silicon GPU), если
доступен, иначе CPU.
"""

from dataclasses import dataclass

import numpy as np
import torch
from PIL import Image

from .detect import detect_label_crop
from .normalize import autocrop_to_content


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
    variant: str = "raw"        # "raw" (CLS/pooler) | "mean" (dinov2 patch-mean)
    crop_mode: str = "none"     # "none" | "auto" (наивный autocrop) | "detect" (OWLv2 label detection)

    @torch.no_grad()
    def encode(self, image: Image.Image) -> np.ndarray:
        return self.encode_batch([image])[0]

    @torch.no_grad()
    def encode_batch(self, images: list[Image.Image]) -> np.ndarray:
        images = [im.convert("RGB") for im in images]
        if self.crop_mode == "auto":
            images = [autocrop_to_content(im) for im in images]
        elif self.crop_mode == "detect":
            images = [detect_label_crop(im) for im in images]

        arch = self.name.split("_")[0]  # "dinov2" | "siglip2"
        if arch == "dinov2":
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            out = self.model(**inputs)
            if self.variant == "mean":
                emb = out.last_hidden_state[:, 1:, :].mean(dim=1)  # без CLS
            else:
                emb = out.last_hidden_state[:, 0, :]  # CLS token
        elif arch == "siglip2":
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            out = self.model.get_image_features(**inputs)
            # некоторые версии transformers оборачивают результат в ModelOutput
            emb = out.pooler_output if hasattr(out, "pooler_output") else out
        else:
            raise ValueError(self.name)
        emb = torch.nn.functional.normalize(emb, dim=-1)
        return emb.cpu().float().numpy()

    @torch.no_grad()
    def encode_text(self, texts: list[str]) -> np.ndarray:
        """Текстовые эмбеддинги в том же пространстве, что и картиночные.
        Работает только у мультимодальных бэкбонов (SigLIP2); DINOv2
        самообучаемый и текстового энкодера не имеет."""
        if not self.name.startswith("siglip2"):
            raise ValueError(f"{self.name} не имеет текстового энкодера")
        inputs = self.processor(
            text=texts, return_tensors="pt", padding="max_length", truncation=True
        ).to(self.device)
        out = self.model.get_text_features(**inputs)
        emb = out.pooler_output if hasattr(out, "pooler_output") else out
        emb = torch.nn.functional.normalize(emb, dim=-1)
        return emb.cpu().float().numpy()


_CACHE: dict[str, Backbone] = {}

# name -> (huggingface model id, pooling variant, режим кропа)
_VARIANTS = {
    "dinov2": ("facebook/dinov2-base", "raw", "none"),
    "dinov2_mean": ("facebook/dinov2-base", "mean", "none"),
    "siglip2": ("google/siglip2-base-patch16-224", "raw", "none"),
    "siglip2_crop": ("google/siglip2-base-patch16-224", "raw", "auto"),
    "siglip2_detect": ("google/siglip2-base-patch16-224", "raw", "detect"),
    # Этикетка — мелкий текст и тонкая графика: на 224px «2014» и «2016»
    # физически неразличимы, поэтому разрешение проверяется как отдельная ось.
    "siglip2_384": ("google/siglip2-base-patch16-384", "raw", "none"),
    "siglip2_512": ("google/siglip2-base-patch16-512", "raw", "none"),
    "siglip2_large384": ("google/siglip2-large-patch16-384", "raw", "none"),
}


def load_backbone(name: str) -> Backbone:
    if name in _CACHE:
        return _CACHE[name]
    if name not in _VARIANTS:
        raise ValueError(f"unknown backbone: {name} (available: {list(_VARIANTS)})")

    model_id, variant, crop_mode = _VARIANTS[name]
    device = get_device()

    if model_id.startswith("facebook/dinov2"):
        from transformers import AutoImageProcessor, AutoModel
        processor = AutoImageProcessor.from_pretrained(model_id)
        model = AutoModel.from_pretrained(model_id).to(device).eval()
    else:
        from transformers import AutoProcessor, AutoModel
        processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModel.from_pretrained(model_id).to(device).eval()

    backbone = Backbone(name=name, model=model, processor=processor, device=device, variant=variant, crop_mode=crop_mode)
    _CACHE[name] = backbone
    return backbone
