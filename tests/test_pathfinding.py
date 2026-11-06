"""pathfinding.plan tests (P4) — end-to-end over a synthetic navigability result, + live skipif."""

from __future__ import annotations

import os

import pytest
from shapely.geometry import LineString, Point, box

from route_planner.chart_adapter.layers import DEPTH_AREAS, LAND, ChartFeature, NormalizedChart
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
    assert len(r.waypoints) == 2  # open water -> one straight leg, no staircase waypoints
    assert r.metrics["distance_m"] > 0
    assert nav.navigable.buffer(50).covers(_metric_line(nav, r))  # stays in the water


def test_plan_minimal_waypoints_around_barrier():
    """String-pulling: a detour around a wall collapses to a few straight legs, still in the water."""
    nav = _nav(blocker=box(11.844, 57.70, 11.847, 57.745))  # shallow wall up to y=57.745
    routes = plan(nav, (57.710, 11.81), (57.710, 11.89))
    assert routes
    r = routes[0]
    assert len(r.waypoints) <= 6  # a few corners, not a per-cell staircase
    # simplification keeps the route inside navigable water (margin already baked into the polygon).
    assert nav.navigable.buffer(nav.params.route_margin_m).covers(_metric_line(nav, r))


def _l_channel_nav(goal="archipelago_conservative"):
    """L-shaped water: a south strip + an east strip meeting at the SE corner, NW filled by land.
    A naive start->end shortcut would cut diagonally through that inner-corner land block."""
    chart = NormalizedChart()
    south = box(11.80, 57.70, 11.90, 57.716)  # full-width bottom arm
    east = box(11.872, 57.70, 11.90, 57.76)  # full-height right arm; meets south at the SE corner
    land = box(11.80, 57.716, 11.872, 57.76)  # the NW block both arms wrap around
    for strip in (south, east):
        chart.add(ChartFeature(DEPTH_AREAS, strip, {"drval1": 10, "drval2": 20}))
    chart.add(ChartFeature(LAND, land, {}))
    chart.provenance.coverage_bbox = CORRIDOR
    return build_navigability(chart, nav_params_from_profile(PROFILE, goal))


def test_simplification_stays_in_corridor_around_concave_corner():
    """Safe simplification: string-pulling must round the concave inner corner, not cut it. The
    simplified route stays inside the navigable polygon and never enters the corner land block."""
    nav = _l_channel_nav()
    routes = plan(nav, (57.708, 11.81), (57.752, 11.885))  # SW arm -> NE arm, must round the corner
    assert routes
    line_m = _metric_line(nav, routes[0])
    assert len(routes[0].waypoints) >= 3  # a straight 2-point leg would cut land -> it must bend
    # string-pulling stays inside navigable water (one grid-cell slack, as validation allows);
    # a corner-cut through the land block would miss the navigable polygon by hundreds of metres.
    assert nav.navigable.buffer(nav.params.route_margin_m * 2).covers(line_m)


def test_plan_via_survives_as_waypoint():
    nav = _nav()
    via = (57.706, 11.85)
    routes = plan(nav, (57.740, 11.81), (57.740, 11.89), via=[via])
    vx, vy = nav.projection.forward(via[1], via[0])
    pts = [Point(nav.projection.forward(lon, lat)) for lat, lon in routes[0].waypoints]
    assert min(p.distance(Point(vx, vy)) for p in pts) < 50  # the via is an actual waypoint


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


def _channel_nav(goal="archipelago_conservative"):
    """A horizontal channel (≈445 m tall) between a north + south land mass."""
    chart = NormalizedChart()
    north = box(11.80, 57.732, 11.90, 57.76)
    south = box(11.80, 57.70, 11.90, 57.728)
    deep = box(*CORRIDOR).difference(north).difference(south)  # navigable band, lat 57.728–57.732
    chart.add(ChartFeature(DEPTH_AREAS, deep, {"drval1": 10, "drval2": 20}))
    chart.add(ChartFeature(LAND, north, {}))
    chart.add(ChartFeature(LAND, south, {}))
    chart.provenance.coverage_bbox = CORRIDOR
    return build_navigability(chart, nav_params_from_profile(PROFILE, goal))


def _mid_clearance(nav, route):
    """Distance (m) from the route's mid-length point to the nearest land/blocked geometry."""
    mid = _metric_line(nav, route).interpolate(0.5, normalized=True)
    return mid.distance(nav.blocked)


def test_clearance_keeps_route_mid_channel():
    """The clearance gradient bows the route to the channel centerline, not the near shore."""
    from route_planner.pathfinding import cost

    nav = _channel_nav()
    start, end = (57.7285, 11.82), (57.7285, 11.88)  # both hugging the south shore (≈55 m off)
    saved = cost.WEIGHT_PROFILES["custom"]
    try:
        # clearance OFF -> distance-only -> the route stays on the near shore.
        cost.WEIGHT_PROFILES["custom"] = dict(saved, clearance=0.0)
        flat = plan(nav, start, end, optimization_goal="custom")[0]
        centered = plan(nav, start, end, optimization_goal="archipelago_conservative")[0]
    finally:
        cost.WEIGHT_PROFILES["custom"] = saved
    assert _mid_clearance(nav, flat) < 100  # without clearance it hugs the shore
    assert _mid_clearance(nav, centered) > _mid_clearance(nav, flat) + 100  # clearance bows it off
    assert _mid_clearance(nav, centered) > 140  # toward the ≈222 m channel centerline


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
