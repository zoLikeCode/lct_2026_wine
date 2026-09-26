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


def test_geometry_selection_supports_largest_and_nearest_center_boxes():
    policy = _policy()
    selector = getattr(policy, "select_by_geometry", None)
    assert callable(selector), "geometry-based box selection has not been implemented yet"
    detections = [
        {"confidence": 0.7, "xyxy": [40, 40, 60, 60]},
        {"confidence": 0.9, "xyxy": [60, 60, 90, 90]},
    ]

    assert selector(detections, (100, 100), "largest", min_confidence=0.05) == 1
    assert selector(detections, (100, 100), "center", min_confidence=0.05) == 0


def test_geometry_selection_filters_low_confidence_proposals():
    policy = _policy()
    selector = getattr(policy, "select_by_geometry", None)
    assert callable(selector), "geometry-based box selection has not been implemented yet"
    detections = [
        {"confidence": 0.04, "xyxy": [0, 0, 100, 100]},
        {"confidence": 0.3, "xyxy": [40, 40, 60, 60]},
    ]

    assert selector(detections, (100, 100), "largest", min_confidence=0.05) == 1
    assert selector(detections, (100, 100), "largest", min_confidence=0.5) is None


def test_expand_box_adds_fractional_padding_and_clips_to_image():
    policy = _policy()
    expand = getattr(policy, "expand_box", None)
    assert callable(expand), "padded box expansion has not been implemented yet"

    assert expand([10, 10, 30, 40], 100, 100, 0.5) == (0, 0, 40, 55)
    assert expand([80, 80, 100, 100], 100, 100, 0.5) == (70, 70, 100, 100)
