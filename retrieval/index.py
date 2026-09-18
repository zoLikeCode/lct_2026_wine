"""Простой in-memory ANN-индекс на cosine similarity (numpy). Для ~1600
векторов полный перебор занимает доли миллисекунды — pgvector имеет смысл
только на этапе реального сервиса, не для бенчмарков."""

from dataclasses import dataclass

import numpy as np


@dataclass
class SearchResult:
    slug: str
    score: float


class EmbeddingIndex:
    def __init__(self, slugs: np.ndarray, embeddings: np.ndarray):
        # embeddings уже L2-нормированы бэкбоном, но на всякий случай нормируем ещё раз
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        self.embeddings = embeddings / np.clip(norms, 1e-8, None)
        self.slugs = slugs

    @classmethod
    def load(cls, npz_path) -> "EmbeddingIndex":
        data = np.load(npz_path)
        return cls(data["slugs"], data["embeddings"])

    def search(self, query_emb: np.ndarray, top_k: int = 10, exclude_slug: str | None = None) -> list[SearchResult]:
        q = query_emb / max(np.linalg.norm(query_emb), 1e-8)
        scores = self.embeddings @ q
        order = np.argsort(-scores)
        results = []
        for idx in order:
            slug = str(self.slugs[idx])
            if slug == exclude_slug:
                continue
            results.append(SearchResult(slug, float(scores[idx])))
            if len(results) >= top_k:
                break
        return results

    def rank_of(self, query_emb: np.ndarray, target_slug: str) -> int:
        """1-based ранг target_slug в полном ранжировании по этому запросу."""
        q = query_emb / max(np.linalg.norm(query_emb), 1e-8)
        scores = self.embeddings @ q
        order = np.argsort(-scores)
        for rank, idx in enumerate(order, start=1):
            if str(self.slugs[idx]) == target_slug:
                return rank
        return -1
