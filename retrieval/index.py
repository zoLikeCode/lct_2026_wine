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
        """При нескольких векторах на один slug (мультиреференсный индекс)
        берётся максимум по slug — "лучший ракурс совпал" — прежде чем
        резать по top_k, иначе один и тот же slug мог бы занять несколько
        мест в выдаче за счёт своих же дополнительных векторов."""
        q = query_emb / max(np.linalg.norm(query_emb), 1e-8)
        scores = self.embeddings @ q
        best_per_slug: dict[str, float] = {}
        for idx in range(len(self.slugs)):
            slug = str(self.slugs[idx])
            if slug == exclude_slug:
                continue
            s = float(scores[idx])
            if slug not in best_per_slug or s > best_per_slug[slug]:
                best_per_slug[slug] = s
        ranked = sorted(best_per_slug.items(), key=lambda x: -x[1])
        return [SearchResult(slug, score) for slug, score in ranked[:top_k]]

    def rank_of(self, query_emb: np.ndarray, target_slug: str) -> int:
        """1-based ранг target_slug (по максимуму среди его векторов) в
        полном ранжировании по этому запросу."""
        q = query_emb / max(np.linalg.norm(query_emb), 1e-8)
        scores = self.embeddings @ q
        best_per_slug: dict[str, float] = {}
        for idx in range(len(self.slugs)):
            slug = str(self.slugs[idx])
            s = float(scores[idx])
            if slug not in best_per_slug or s > best_per_slug[slug]:
                best_per_slug[slug] = s
        ranked = sorted(best_per_slug.items(), key=lambda x: -x[1])
        for rank, (slug, _) in enumerate(ranked, start=1):
            if slug == target_slug:
                return rank
        return -1
