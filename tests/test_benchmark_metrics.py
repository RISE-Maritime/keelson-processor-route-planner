"""Benchmark similarity-metric tests (benchmark/metrics.py).

Exact answers are checked on metric-space (x, y) inputs where the geometry is hand-computable;
compare_routes is checked relationally on lat/lon routes (projection-built).
"""

from __future__ import annotations

import numpy as np
import pytest

from route_planner.benchmark.metrics import (
    compare_routes,
    cross_track_stats_m,
    diff_validation,
    discrete_frechet_m,
    turn_count,
)


def test_frechet_identical_is_zero():
    line = np.array([(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)])
    assert discrete_frechet_m(line, line) == pytest.approx(0.0, abs=1e-6)


def test_frechet_parallel_offset_equals_gap():
    a = np.array([(0.0, 0.0), (100.0, 0.0)])
    b = np.array([(0.0, 10.0), (100.0, 10.0)])
    assert discrete_frechet_m(a, b) == pytest.approx(10.0, abs=0.5)


def test_cross_track_constant_offset():
    route = np.array([(0.0, 5.0), (100.0, 5.0)])
    reference = np.array([(0.0, 0.0), (100.0, 0.0)])
    stats = cross_track_stats_m(route, reference)
    assert stats["xtd_mean_m"] == pytest.approx(5.0, abs=0.1)
    assert stats["xtd_max_m"] == pytest.approx(5.0, abs=0.1)


def test_turn_count():
    straight = np.array([(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)])
    assert turn_count(straight) == 0
    one_corner = np.array([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)])
    assert turn_count(one_corner) == 1
    two_corners = np.array([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)])
    assert turn_count(two_corners) == 2


def test_compare_identical_routes():
    route = [(57.60, 11.60), (57.62, 11.70), (57.65, 11.80)]
    cmp = compare_routes(route, route)
    assert cmp.frechet_m == pytest.approx(0.0, abs=1.0)
    assert cmp.hausdorff_m == pytest.approx(0.0, abs=1.0)
    assert cmp.xtd_mean_m == pytest.approx(0.0, abs=1.0)
    assert cmp.length_ratio == pytest.approx(1.0, abs=1e-3)
    assert cmp.n_waypoints_generated == cmp.n_waypoints_reference == 3


def test_compare_offset_routes_reports_deviation():
    ref = [(57.600, 11.60), (57.600, 11.80)]
    gen = [(57.601, 11.60), (57.601, 11.80)]  # ~0.001 deg lat north ≈ 111 m
    cmp = compare_routes(gen, ref)
    assert 90.0 < cmp.xtd_mean_m < 130.0
    assert 90.0 < cmp.frechet_m < 130.0
    assert cmp.length_ratio == pytest.approx(1.0, abs=0.05)


def test_compare_requires_two_waypoints():
    with pytest.raises(ValueError):
        compare_routes([(57.6, 11.6)], [(57.6, 11.6), (57.7, 11.7)])


def test_diff_validation():
    class _R:
        def __init__(self, issues, challenges):
            self.issues = issues
            self.challenges = challenges

    gen = _R(issues=[1], challenges=[1, 2, 3])
    ref = _R(issues=[], challenges=[1])
    d = diff_validation(gen, ref)
    assert d["issues_delta"] == 1
    assert d["challenges_delta"] == 2
    assert d["ref_challenges"] == 1
