"""Классические приёмы image retrieval поверх готовых эмбеддингов.

Ни один из них не требует нового инференса и не меняет бэкбон — это чистая
линейная алгебра над уже посчитанными векторами. В проекте они ни разу не
проверялись, хотя девять более сложных методов уже отклонены.

- **Отбеливание (PCA-whitening).** Измерения CLIP-эмбеддингов сильно
  скоррелированы, и несколько доминирующих направлений съедают косинус.
  Преобразование декоррелирует пространство и выравнивает дисперсии.
  Параметр `alpha` задаёт силу: 0.5 — полное отбеливание, меньше — частичное
  (в литературе по ретривалу частичное обычно лучше полного).
- **Query expansion (alphaQE).** Верхние ответы подмешиваются в запрос с
  весами по схожести, после чего индекс опрашивается повторно. Помогает
  ровно в нашей ситуации: верный ответ рядом, но не первый.
- **Database-side augmentation.** То же самое, но офлайн и со стороны
  индекса: каждый эталон смешивается с ближайшими соседями.

Осторожность с последними двумя: они подтягивают кандидата к его соседям, а
соседи у нас — сиблинги одной линейки, которые мы как раз и путаем. Так что
эффект заранее не очевиден и может оказаться отрицательным.
"""

import numpy as np


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.clip(norms, 1e-8, None)


def fit_whitening(index_embeddings: np.ndarray, n_components: int | None = None,
                  alpha: float = 0.5, eps: float = 1e-6) -> tuple[np.ndarray, np.ndarray]:
    """Возвращает (среднее, матрицу преобразования). Обучается ТОЛЬКО на
    векторах индекса — полевые кадры в этом не участвуют."""
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    mean = index_embeddings.mean(axis=0)
    centered = index_embeddings - mean
    covariance = centered.T @ centered / max(len(centered), 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues, eigenvectors = eigenvalues[order], eigenvectors[:, order]
    if n_components is not None:
        eigenvalues, eigenvectors = eigenvalues[:n_components], eigenvectors[:, :n_components]
    scale = np.power(np.clip(eigenvalues, eps, None), -alpha)
    return mean, eigenvectors * scale


def apply_whitening(embeddings: np.ndarray, mean: np.ndarray, transform: np.ndarray) -> np.ndarray:
    return l2_normalize((embeddings - mean) @ transform)


def query_expansion(queries: np.ndarray, index_embeddings: np.ndarray,
                    top_k: int = 3, alpha: float = 3.0) -> np.ndarray:
    """q' = normalize(q + sum(sim_i^alpha * d_i)) по top_k ближайшим."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    similarities = queries @ index_embeddings.T
    top = np.argsort(-similarities, axis=1)[:, :top_k]
    expanded = queries.copy()
    for row, neighbours in enumerate(top):
        weights = np.clip(similarities[row, neighbours], 0.0, None) ** alpha
        expanded[row] += weights @ index_embeddings[neighbours]
    return l2_normalize(expanded)


def database_augmentation(index_embeddings: np.ndarray, top_k: int = 2,
                          alpha: float = 3.0) -> np.ndarray:
    """Каждый эталон смешивается с `top_k` ближайшими соседями (кроме себя)."""
    similarities = index_embeddings @ index_embeddings.T
    np.fill_diagonal(similarities, -np.inf)
    top = np.argsort(-similarities, axis=1)[:, :top_k]
    augmented = index_embeddings.copy()
    for row, neighbours in enumerate(top):
        weights = np.clip(similarities[row, neighbours], 0.0, None) ** alpha
        augmented[row] += weights @ index_embeddings[neighbours]
    return l2_normalize(augmented)
