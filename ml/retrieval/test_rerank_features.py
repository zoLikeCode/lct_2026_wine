"""Focused tests for reranker feature extraction."""

import pytest

from retrieval.rerank_features import FEATURE_NAMES, candidate_features


def _lookup():
    return {
        "a": {"winery": "Фанагория", "photo_match_method": "parser_reference",
              "rerank_target": {"year": "2024", "sweetness_ru": ["сухое"], "abv_tokens": ["13"]}},
        "b": {"winery": "Фанагория", "photo_match_method": "parser_bottle",
              "rerank_target": {"year": None, "sweetness_ru": [], "abv_tokens": []}},
        "c": {"winery": "Абрау-Дюрсо", "photo_match_method": "photo_column",
              "rerank_target": {"year": "2023", "sweetness_ru": [], "abv_tokens": ["12"]}},
    }


def _candidates():
    return [{"slug": "a", "score": 0.80}, {"slug": "b", "score": 0.79}, {"slug": "c", "score": 0.60}]


def _feature(rows, index, name):
    return rows[index][FEATURE_NAMES.index(name)]


def test_returns_one_row_per_candidate_with_all_features():
    rows = candidate_features(_candidates(), _lookup(), set())

    assert len(rows) == 3
    assert all(len(row) == len(FEATURE_NAMES) for row in rows)


def test_margins_describe_local_competition():
    rows = candidate_features(_candidates(), _lookup(), set())

    assert _feature(rows, 0, "margin_to_top1") == pytest.approx(0.0)
    assert _feature(rows, 1, "margin_to_top1") == pytest.approx(-0.01)
    # отрыв от следующего: у top-1 он крошечный, у третьего — нулевой (он последний)
    assert _feature(rows, 0, "margin_to_next") == pytest.approx(0.01)
    assert _feature(rows, 2, "margin_to_next") == pytest.approx(0.0)
    assert _feature(rows, 0, "top1_gap") == pytest.approx(0.01)


def test_same_winery_flag_excludes_top1_itself():
    rows = candidate_features(_candidates(), _lookup(), set())

    assert _feature(rows, 0, "same_winery_as_top1") == 0.0
    assert _feature(rows, 1, "same_winery_as_top1") == 1.0
    assert _feature(rows, 2, "same_winery_as_top1") == 0.0
    assert _feature(rows, 1, "winery_count_in_list") == 2.0


def test_near_duplicate_flags_use_pair_membership():
    pairs = {frozenset(("a", "b"))}
    rows = candidate_features(_candidates(), _lookup(), pairs)

    assert _feature(rows, 1, "near_dup_with_top1") == 1.0
    assert _feature(rows, 2, "near_dup_with_top1") == 0.0
    assert _feature(rows, 0, "near_dup_siblings_in_list") == 1.0


def test_metadata_completeness_flags():
    rows = candidate_features(_candidates(), _lookup(), set())

    assert [_feature(rows, 0, n) for n in ("has_year", "has_sweetness", "has_abv")] == [1.0, 1.0, 1.0]
    assert [_feature(rows, 1, n) for n in ("has_year", "has_sweetness", "has_abv")] == [0.0, 0.0, 0.0]
    assert _feature(rows, 0, "reference_is_catalog") == 1.0
    assert _feature(rows, 1, "reference_is_catalog") == 0.0


def test_unknown_slug_does_not_crash():
    rows = candidate_features([{"slug": "missing", "score": 0.5}], {}, set())

    assert len(rows) == 1
    assert _feature(rows, 0, "has_year") == 0.0


def test_empty_candidate_list():
    assert candidate_features([], _lookup(), set()) == []
