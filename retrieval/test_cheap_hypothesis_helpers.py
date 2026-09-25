"""Focused tests for cheap, non-learning inference policies."""

import importlib
import importlib.util

import numpy as np
import pytest


def _policy():
    spec = importlib.util.find_spec("retrieval.cheap_policy")
    assert spec is not None, "retrieval.cheap_policy has not been implemented yet"
    return importlib.import_module("retrieval.cheap_policy")


def test_confidence_threshold_selects_best_eligible_detection():
    policy = _policy()

    assert policy.select_by_confidence([0.42, 0.71, 0.66], threshold=0.5) == 1
    assert policy.select_by_confidence([0.42, 0.47], threshold=0.5) is None
    assert policy.select_by_confidence([0.42, 0.47], threshold=0.4) == 1


def test_retrieval_selection_uses_highest_image_similarity():
    policy = _policy()

    assert policy.select_by_similarity([0.812, 0.854, 0.840]) == 1
    assert policy.select_by_similarity([]) is None


def test_embedding_fusion_is_normalized_and_respects_endpoints():
    policy = _policy()
    whole = np.array([1.0, 0.0], dtype=np.float32)
    crop = np.array([0.0, 1.0], dtype=np.float32)

    np.testing.assert_allclose(policy.fuse_embeddings(whole, crop, 0.0), whole)
    np.testing.assert_allclose(policy.fuse_embeddings(whole, crop, 1.0), crop)
    mixed = policy.fuse_embeddings(whole, crop, 0.5)
    np.testing.assert_allclose(mixed, [2**-0.5, 2**-0.5], atol=1e-6)


@pytest.mark.parametrize("weight", [-0.01, 1.01])
def test_embedding_fusion_rejects_weights_outside_unit_interval(weight):
    policy = _policy()
    with pytest.raises(ValueError):
        policy.fuse_embeddings(np.array([1.0]), np.array([1.0]), weight)
