"""navigability engine tests (P3) — synthetic chart, pure geometry.

Navigable water is taken from charted deep depth areas (real islands are holes in DEPARE), so the
land islet here sits in the UNCHARTED part of the corridor — where land does block.
"""

from __future__ import annotations

import os

import pytest
from shapely.geometry import Point, box

from route_planner.chart_adapter.layers import (
    DEPTH_AREAS,
    FAIRWAYS,
    LAND,
    NormalizedChart,
    RESTRICTED_AREAS,
    ROCKS,
    ChartFeature,
)
from route_planner.navigability import build_navigability, nav_params_from_profile

CORRIDOR = (11.80, 57.70, 11.90, 57.76)  # west, south, east, north
PROFILE = {"draught_m": 0.25, "min_under_keel_clearance_m": 0.5, "route_margin_m": 15.0}
NO_GO = box(11.812, 57.745, 11.818, 57.752)  # request no-go (WGS84), inside deep water


def _chart() -> NormalizedChart:
    chart = NormalizedChart()
    # Deep charted water over the left ~60% of the corridor (x in 11.80..11.86).
    chart.add(ChartFeature(DEPTH_AREAS, box(11.80, 57.70, 11.86, 57.76), {"drval1": 10, "drval2": 20}))
    # Unsafe-shallow pocket (drval1 < safety 0.75).
    chart.add(ChartFeature(DEPTH_AREAS, box(11.805, 57.705, 11.815, 57.715), {"drval1": 0, "drval2": 2}))
    # Shallow-but-legal pocket (safety <= drval1 < comfortable).
    chart.add(ChartFeature(DEPTH_AREAS, box(11.830, 57.705, 11.840, 57.715), {"drval1": 1.0, "drval2": 2.0}))
    chart.add(ChartFeature(RESTRICTED_AREAS, box(11.820, 57.720, 11.828, 57.728), {"restrn": "7"}))
    chart.add(ChartFeature(RESTRICTED_AREAS, box(11.840, 57.745, 11.848, 57.752), {"restrn": "8"}))
    chart.add(ChartFeature(ROCKS, Point(11.852, 57.730), {"watlev": 5}))  # awash rock -> blocked
    chart.add(ChartFeature(FAIRWAYS, box(11.80, 57.70, 11.85, 57.76), {"objnam": "fairway"}))
    # Land islet in the UNCHARTED strip (x > 11.86), where land matters.
    chart.add(ChartFeature(LAND, box(11.870, 57.730, 11.876, 57.736), {"objnam": "islet"}))
    chart.provenance.coverage_bbox = CORRIDOR
    return chart


def _contains(result, lon, lat) -> bool:
    return result.navigable.contains(Point(result.projection.forward(lon, lat)))


def test_hard_blocks_excluded_from_navigable():
    r = build_navigability(_chart(), nav_params_from_profile(PROFILE), no_go=[NO_GO])
    assert _contains(r, 11.845, 57.725)  # open deep water -> navigable
    assert not _contains(r, 11.810, 57.710)  # unsafe-shallow pocket
    assert not _contains(r, 11.824, 57.724)  # entry-prohibited (RESTRN 7)
    assert not _contains(r, 11.815, 57.748)  # request no-go zone
    assert not r.navigable.is_empty


def test_blocked_geometry_and_evidence():
    r = build_navigability(_chart(), nav_params_from_profile(PROFILE), no_go=[NO_GO])
    assert r.blocked.contains(Point(r.projection.forward(11.873, 57.733)))  # land islet is blocked
    ev = r.evidence["hard_block_layers"]
    assert ev["land"] == 1
    assert ev["restricted"] == 1  # only the RESTRN-7 one
    assert ev["hazards"] == 1  # the awash rock
    assert ev["no_go"] == 1


def test_non_conservative_unknown_navigable_land_blocks():
    r = build_navigability(_chart(), nav_params_from_profile(PROFILE))  # mode "" -> not conservative
    assert _contains(r, 11.888, 57.740)  # uncharted water is navigable-but-penalized
    assert not _contains(r, 11.873, 57.733)  # ...but the land islet within it still blocks
    kinds = {sc.kind for sc in r.soft_costs}
    assert "unknown_depth" in kinds
    assert "shallow_but_legal" in kinds
    assert "restricted_soft" in kinds  # the RESTRN-8 area


def test_conservative_blocks_unknown_and_flags_fairway():
    params = nav_params_from_profile(PROFILE, optimization_goal="archipelago_conservative")
    r = build_navigability(_chart(), params, no_go=[NO_GO])
    assert _contains(r, 11.845, 57.725)  # charted deep water still navigable
    assert not _contains(r, 11.888, 57.740)  # uncharted strip blocked in conservative mode
    kinds = {sc.kind for sc in r.soft_costs}
    assert "outside_fairway" in kinds  # prefer_fairways on -> water outside the fairway is penalized


def test_finer_scale_depth_wins_over_coarse():
    # A corridor spanning ENC scales: a coarse 1:1.5M cell marks the whole area shallow, a fine
    # 1:50k cell charts a deep channel through it. The fine detail must win.
    chart = NormalizedChart()
    chart.add(ChartFeature(DEPTH_AREAS, box(11.80, 57.70, 11.90, 57.76), {"drval1": 0, "drval2": 2, "chart_scale": 1500000}))
    chart.add(ChartFeature(DEPTH_AREAS, box(11.82, 57.72, 11.88, 57.74), {"drval1": 10, "drval2": 20, "chart_scale": 50000}))
    chart.provenance.coverage_bbox = CORRIDOR
    r = build_navigability(chart, nav_params_from_profile(PROFILE))
    assert _contains(r, 11.85, 57.73)  # inside the fine deep channel -> navigable
    assert not _contains(r, 11.81, 57.71)  # only coarse-shallow here -> not navigable


def test_warning_when_no_depth_coverage():
    chart = NormalizedChart()
    chart.add(ChartFeature(LAND, box(11.86, 57.75, 11.866, 57.756), {"objnam": "islet"}))
    chart.provenance.coverage_bbox = CORRIDOR
    r = build_navigability(chart, nav_params_from_profile(PROFILE))
    assert any("no depth-area" in w for w in r.warnings)
    assert not r.navigable.is_empty  # corridor minus land is still navigable (non-conservative)


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL; live chart DB not available",
)
def test_navigability_over_live_corridor():
    from route_planner.chart_adapter import ChartAdapter

    adapter = ChartAdapter(
        {"database_url": os.environ["DATABASE_URL"], "chart_server_url": "http://localhost:8080"}
    )
    try:
        chart = adapter.fetch_corridor((11.78, 57.66, 11.90, 57.72))
    finally:
        adapter.close()
    r = build_navigability(chart, nav_params_from_profile(PROFILE, "archipelago_conservative"))
    assert r.navigable.area > 0  # charted deep water exists in this archipelago corridor
    assert not r.blocked.is_empty
