"""Focused tests for text-detection based label localization."""

import pytest

from retrieval import label_region as lr


def test_normalize_boxes_handles_easyocr_axis_order_and_quads():
    # EasyOCR horizontal boxes come as [x_min, x_max, y_min, y_max], not xyxy.
    horizontal = [[10, 50, 20, 40]]
    free = [[(5, 5), (25, 7), (24, 17), (4, 15)]]

    assert lr.normalize_boxes(horizontal, free) == [(10, 20, 50, 40), (4, 5, 25, 17)]


def test_normalize_boxes_drops_degenerate_boxes():
    assert lr.normalize_boxes([[10, 10, 20, 40]], []) == []


def test_scale_boxes_maps_back_to_full_resolution():
    assert lr.scale_boxes([(10, 20, 30, 40)], 2.0) == [(20, 40, 60, 80)]
    with pytest.raises(ValueError):
        lr.scale_boxes([(10, 20, 30, 40)], 0.0)


def test_dominant_cluster_prefers_largest_text_area_not_box_count():
    # Two small boxes near the cap, one large label line lower down.
    boxes = [(0, 0, 10, 5), (0, 6, 10, 11), (0, 60, 90, 90)]

    assert lr.dominant_text_cluster(boxes, image_height=100) == [(0, 60, 90, 90)]


def test_dominant_cluster_groups_lines_separated_by_small_gaps():
    boxes = [(0, 50, 80, 60), (0, 63, 80, 73), (0, 95, 20, 99)]

    assert lr.dominant_text_cluster(boxes, image_height=100) == [(0, 50, 80, 60), (0, 63, 80, 73)]


def test_band_mode_keeps_full_width_and_pads_vertically():
    boxes = [(20, 40, 60, 60)]

    assert lr.label_region(boxes, (100, 100), "band", 0.25) == (0, 35, 100, 65)


def test_tight_mode_pads_both_axes_and_clips():
    boxes = [(20, 40, 60, 60)]

    assert lr.label_region(boxes, (100, 100), "tight", 0.25) == (10, 35, 70, 65)


def test_label_region_returns_none_without_text():
    assert lr.label_region([], (100, 100), "band", 0.1) is None


def test_label_region_rejects_unknown_mode():
    with pytest.raises(ValueError):
        lr.label_region([(0, 0, 10, 10)], (100, 100), "whole", 0.1)
