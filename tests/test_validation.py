"""validation engine tests (P7) — RouteIssue / RouteChallenge / status, pure."""

from __future__ import annotations

from shapely.geometry import box

from route_planner.chart_adapter.layers import DEPTH_AREAS, LAND, ChartFeature, NormalizedChart
from route_planner.navigability import build_navigability, nav_params_from_profile
from route_planner.validation import validate

PROFILE = {"draught_m": 0.25, "min_under_keel_clearance_m": 0.5, "route_margin_m": 15.0}
CORRIDOR = (11.80, 57.70, 11.90, 57.76)
# A box (lon 11.84–11.86, lat 57.72–57.74) the cross-route below passes straight through.
HAZARD_BOX = box(11.84, 57.72, 11.86, 57.74)


def _nav(*extra):
    """Deep water over the whole corridor + extras (extras overlap deep — for soft/clean cases)."""
    chart = NormalizedChart()
    chart.add(ChartFeature(DEPTH_AREAS, box(*CORRIDOR), {"drval1": 10, "drval2": 20}))
    for feature in extra:
        chart.add(feature)
    chart.provenance.coverage_bbox = CORRIDOR
    return build_navigability(chart, nav_params_from_profile(PROFILE, "shortest"))


def _nav_carved(hazard):
    """Deep water over the corridor EXCEPT HAZARD_BOX (realistic: a hazard is a hole in depare)."""
    chart = NormalizedChart()
    chart.add(ChartFeature(DEPTH_AREAS, box(*CORRIDOR).difference(HAZARD_BOX), {"drval1": 10, "drval2": 20}))
    chart.add(hazard)
    chart.provenance.coverage_bbox = CORRIDOR
    return build_navigability(chart, nav_params_from_profile(PROFILE, "shortest"))


def _route(waypoints, *, snapped_start=False, snapped_end=False):
    from keelson.payloads.Route_pb2 import RECOMMENDED, Route

    route = Route(route_id="r1", status=RECOMMENDED)
    for i, (lat, lon) in enumerate(waypoints):
        wp = route.waypoints.waypoint.add()
        wp.id = i + 1
        wp.position.latitude = lat
        wp.position.longitude = lon
    entry = route.extensions.entries.add()
    entry.manufacturer = "se.rise"
    entry.name = "route-planner"
    entry.version = "0.1"
    entry.attributes["snapped_start"] = str(snapped_start).lower()
    entry.attributes["snapped_end"] = str(snapped_end).lower()
    return route


def _attrs(route):
    return next(e for e in route.extensions.entries if e.name == "route-planner").attributes


def _cross_route():
    return _route([(57.73, 11.82), (57.73, 11.88)])  # straight through HAZARD_BOX


def test_clean_route_has_no_issues():
    from keelson.payloads.Route_pb2 import RECOMMENDED

    route = _route([(57.705, 11.81), (57.755, 11.89)])
    validate(route, _nav())
    assert len(route.issues) == 0
    assert _attrs(route)["validation_status"] == "ok"
    assert route.status == RECOMMENDED


def test_land_crossing_is_critical():
    from keelson.payloads.Route_pb2 import CRITICAL, ROUTE_ISSUES

    route = _cross_route()
    validate(route, _nav_carved(ChartFeature(LAND, HAZARD_BOX, {"objnam": "islet"})))
    assert any(i.id == "issue-land" and i.severity == CRITICAL for i in route.issues)
    assert route.status == ROUTE_ISSUES
    assert _attrs(route)["validation_status"] == "issues"


def test_unsafe_depth_is_critical():
    route = _cross_route()
    validate(route, _nav_carved(ChartFeature(DEPTH_AREAS, HAZARD_BOX, {"drval1": 0, "drval2": 1})))
    assert any(i.id == "issue-unsafe_depth" for i in route.issues)


def test_shallow_but_legal_is_a_soft_challenge():
    from keelson.payloads.Route_pb2 import SHALLOW_WATER, WARNING

    route = _cross_route()
    validate(route, _nav(ChartFeature(DEPTH_AREAS, HAZARD_BOX, {"drval1": 1.0, "drval2": 2.0})))
    assert len(route.issues) == 0
    challenges = [c for c in route.challenges if c.type == SHALLOW_WATER]
    assert challenges
    assert challenges[0].severity == WARNING
    assert list(challenges[0].waypoint_ids)


def test_snapped_endpoint_challenge():
    route = _route([(57.705, 11.81), (57.755, 11.89)], snapped_start=True)
    validate(route, _nav())
    assert any(c.id == "challenge-snapped" for c in route.challenges)


def test_sharp_turn_challenge():
    route = _route([(57.705, 11.81), (57.705, 11.86), (57.74, 11.86)])  # ~90° dog-leg
    validate(route, _nav())
    assert any(c.id == "challenge-sharp-turn" for c in route.challenges)


def test_run_plan_routes_are_validated():
    from _planhelpers import FakeAdapter, deep_chart, request_route

    from route_planner.planner import run_plan

    routes, _meta = run_plan(FakeAdapter(deep_chart()), request_route((57.70, 11.80), (57.72, 11.86)))
    assert routes
    assert "validation_status" in _attrs(routes[0])
