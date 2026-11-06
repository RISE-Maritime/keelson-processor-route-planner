"""Benchmark runner tests (benchmark/runner.py) — pure scoring over fake routes, no chart/DB."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from route_planner.benchmark.runner import (
    benchmark_profiles,
    route_coords,
    score_route,
    summarize,
)
from route_planner.pathfinding import PlannedRoute

REFERENCE = [(57.600, 11.60), (57.600, 11.70), (57.600, 11.80)]


def _keelson_like(coords, *, issues=0, challenges=0):
    """A duck-typed stand-in for keelson.Route (waypoints.waypoint + issues/challenges)."""
    wps = SimpleNamespace(
        waypoint=[SimpleNamespace(position=SimpleNamespace(latitude=lat, longitude=lon)) for lat, lon in coords]
    )
    return SimpleNamespace(waypoints=wps, issues=[None] * issues, challenges=[None] * challenges)


def test_route_coords_handles_all_shapes():
    assert route_coords(REFERENCE) == REFERENCE
    planned = PlannedRoute(waypoints=REFERENCE)
    assert route_coords(planned) == REFERENCE
    assert route_coords(_keelson_like(REFERENCE)) == REFERENCE


def test_score_route_basic():
    gen = PlannedRoute(waypoints=[(57.601, 11.60), (57.601, 11.80)])  # ~111 m north of reference
    row = score_route(REFERENCE, gen)
    assert 90 < row["xtd_mean_m"] < 130
    assert row["gen_n_issues"] == 0


def test_score_route_validation_delta():
    gen = _keelson_like(REFERENCE, issues=1, challenges=2)
    ref = _keelson_like(REFERENCE, issues=0, challenges=1)
    row = score_route(REFERENCE, gen, reference_route=ref)
    assert row["issues_delta"] == 1
    assert row["challenges_delta"] == 1
    assert row["gen_n_issues"] == 1


def test_benchmark_profiles_marks_best_and_summarizes():
    # "good" goal matches the reference closely; "bad" goal deviates far.
    good = [PlannedRoute(waypoints=REFERENCE), PlannedRoute(waypoints=[(57.6005, 11.60), (57.6005, 11.80)])]
    bad = [PlannedRoute(waypoints=[(57.610, 11.60), (57.610, 11.80)])]  # ~1.1 km off
    rows = benchmark_profiles(REFERENCE, {"good": good, "bad": bad})

    assert len(rows) == 3
    best_good = [r for r in rows if r["goal"] == "good" and r["best_for_goal"]]
    assert len(best_good) == 1
    assert best_good[0]["alt_index"] == 0  # the exact-match alternative

    summary = summarize(rows)
    assert summary["best_goal"] == "good"
    assert summary["frechet_m"] < 10


def test_summarize_prefers_issue_free():
    near_with_issue = _keelson_like([(57.6005, 11.60), (57.6005, 11.80)], issues=1)
    far_clean = _keelson_like([(57.603, 11.60), (57.603, 11.80)], issues=0)
    rows = benchmark_profiles(REFERENCE, {"risky": [near_with_issue], "safe": [far_clean]})
    # Both are "best_for_goal" (one alt each); summarize should skip the one with a critical issue.
    assert summarize(rows)["best_goal"] == "safe"


def test_to_dataframe():
    pd = pytest.importorskip("pandas")
    rows = benchmark_profiles(REFERENCE, {"g": [PlannedRoute(waypoints=REFERENCE)]})
    from route_planner.benchmark.runner import to_dataframe

    df = to_dataframe(rows)
    assert isinstance(df, pd.DataFrame)
    assert "frechet_m" in df.columns and len(df) == 1
