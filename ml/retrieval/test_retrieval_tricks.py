"""Focused tests for classic retrieval post-processing."""

import numpy as np
import pytest

from retrieval.retrieval_tricks import (apply_whitening, database_augmentation, fit_whitening,
                                        l2_normalize, query_expansion)


def _index(seed: int = 0, n: int = 40, dim: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # анизотропные данные: первое измерение доминирует, ровно то, что чинит отбеливание
    matrix = rng.normal(size=(n, dim))
    matrix[:, 0] *= 12.0
    return l2_normalize(matrix)


def test_l2_normalize_handles_zero_rows():
    result = l2_normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))

    assert result[0] == pytest.approx([0.6, 0.8])
    assert np.isfinite(result[1]).all()


def test_whitening_equalises_dominant_direction():
    index = _index()
    mean, transform = fit_whitening(index, alpha=0.5)
    whitened = apply_whitening(index, mean, transform)

    spread_before = index.std(axis=0)
    spread_after = whitened.std(axis=0)
    # до отбеливания одно измерение доминирует, после — разброс выровнен
    assert spread_before.max() / spread_before.min() > 5
    assert spread_after.max() / spread_after.min() < spread_before.max() / spread_before.min()
    assert np.allclose(np.linalg.norm(whitened, axis=1), 1.0)


def test_whitening_can_reduce_dimensionality():
    index = _index()
    mean, transform = fit_whitening(index, n_components=3)

    assert apply_whitening(index, mean, transform).shape == (len(index), 3)


def test_whitening_rejects_bad_alpha():
    with pytest.raises(ValueError):
        fit_whitening(_index(), alpha=1.5)


def test_query_expansion_moves_query_towards_its_neighbour():
    index = l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0]]))
    query = l2_normalize(np.array([[0.9, 0.1]]))

    expanded = query_expansion(query, index, top_k=1, alpha=1.0)

    assert expanded @ index[0] > query @ index[0]
    assert np.allclose(np.linalg.norm(expanded, axis=1), 1.0)


def test_query_expansion_validates_top_k():
    with pytest.raises(ValueError):
        query_expansion(_index(n=2), _index(), top_k=0)


def test_database_augmentation_keeps_shape_and_norm():
    index = _index()
    augmented = database_augmentation(index, top_k=2)

    assert augmented.shape == index.shape
    assert np.allclose(np.linalg.norm(augmented, axis=1), 1.0)


def test_database_augmentation_does_not_blend_vector_with_itself():
    index = l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0]]))
    augmented = database_augmentation(index, top_k=1, alpha=1.0)

    # ортогональные соседи: вес нулевой, вектор остаётся собой
    assert augmented[0] == pytest.approx(index[0])
