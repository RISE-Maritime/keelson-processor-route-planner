"""pathfinding.plan tests (P4) — end-to-end over a synthetic navigability result, + live skipif."""

from __future__ import annotations

import os

import pytest
from shapely.geometry import LineString, Point, box

from route_planner.chart_adapter.layers import DEPTH_AREAS, ChartFeature, NormalizedChart
from route_planner.navigability import build_navigability, nav_params_from_profile
from route_planner.pathfinding import plan

CORRIDOR = (11.80, 57.70, 11.90, 57.76)  # west, south, east, north
PROFILE = {"draught_m": 0.25, "min_under_keel_clearance_m": 0.5, "route_margin_m": 15.0}


def _nav(blocker=None):
    chart = NormalizedChart()
    chart.add(ChartFeature(DEPTH_AREAS, box(11.80, 57.70, 11.90, 57.76), {"drval1": 10, "drval2": 20}))
    if blocker is not None:
        chart.add(ChartFeature(DEPTH_AREAS, blocker, {"drval1": 0, "drval2": 1}))  # shallow barrier
    chart.provenance.coverage_bbox = CORRIDOR
    return build_navigability(chart, nav_params_from_profile(PROFILE))


def _metric_line(nav, route):
    return LineString([nav.projection.forward(lon, lat) for lat, lon in route.waypoints])


def test_plan_open_water_direct():
    nav = _nav()
    routes = plan(nav, (57.705, 11.81), (57.755, 11.89), optimization_goal="shortest")
    assert len(routes) == 1
    r = routes[0]
    assert r.status_hint == "RECOMMENDED"
    assert len(r.waypoints) >= 2
    assert r.metrics["distance_m"] > 0
    assert nav.navigable.buffer(50).covers(_metric_line(nav, r))  # stays in the water


def test_plan_via_point_pulls_route_through_it():
    nav = _nav()
    routes = plan(nav, (57.740, 11.81), (57.740, 11.89), via=[(57.706, 11.85)])
    line = _metric_line(nav, routes[0])
    vx, vy = nav.projection.forward(11.85, 57.706)
    assert line.distance(Point(vx, vy)) < 150  # the path dips south through the via


def test_plan_routes_around_barrier():
    nav = _nav(blocker=box(11.844, 57.70, 11.847, 57.745))  # shallow wall up to y=57.745
    routes = plan(nav, (57.710, 11.81), (57.710, 11.89))
    assert routes
    assert max(lat for lat, _lon in routes[0].waypoints) > 57.74  # detoured north of the wall


def test_plan_alternatives_distinct():
    nav = _nav()
    routes = plan(nav, (57.705, 11.81), (57.755, 11.89), n_alternatives=3)
    assert 1 <= len(routes) <= 3
    if len(routes) >= 2:
        assert routes[0].waypoints != routes[1].waypoints


def test_plan_snaps_start_outside_navigable():
    nav = _nav()
    routes = plan(nav, (57.69, 11.79), (57.75, 11.89))  # start SW of the corridor (off-water)
    assert routes
    assert routes[0].snapped_start is True


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL; live chart DB not available",
)
def test_plan_over_live_corridor():
    from route_planner.chart_adapter import ChartAdapter

    adapter = ChartAdapter(
        {"database_url": os.environ["DATABASE_URL"], "chart_server_url": "http://localhost:8080"}
    )
    try:
        chart = adapter.fetch_corridor((11.78, 57.66, 11.90, 57.72))
    finally:
        adapter.close()
    nav = build_navigability(chart, nav_params_from_profile(PROFILE, "archipelago_conservative"))
    routes = plan(nav, (57.68, 11.80), (57.70, 11.88), optimization_goal="archipelago_conservative", n_alternatives=2)
    assert isinstance(routes, list)  # may be empty if the snapped endpoints land in disjoint water
    if routes:
        r = routes[0]
        assert len(r.waypoints) >= 2
        assert r.metrics["distance_m"] > 0
        assert nav.navigable.buffer(nav.params.route_margin_m * 2).covers(_metric_line(nav, r))
