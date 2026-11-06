"""planner.run_plan tests (P6) — request parsing + orchestration with a fake adapter."""

from __future__ import annotations

import pytest
from _planhelpers import FakeAdapter, deep_chart, request_route

from route_planner.planner import PlanError, corridor_bbox, parse_request, run_plan
from route_planner.model.position import set_lat_lon


def test_parse_request_extracts_inputs():
    req = request_route(
        (57.70, 11.80), (57.72, 11.86),
        goal="archipelago_conservative", n_alternatives=3, constraints={"prefer_fairways": True},
    )
    parsed = parse_request(req)
    assert parsed["start"] == (57.70, 11.80)
    assert parsed["end"] == (57.72, 11.86)
    assert parsed["via"] == []
    assert parsed["optimization_goal"] == "archipelago_conservative"
    assert parsed["n_alternatives"] == 3
    assert parsed["constraints"].get("prefer_fairways") is True
    assert round(float(parsed["profile"]["length_m"]), 3) == 1.8


def test_corridor_bbox_pads_around_points():
    west, south, east, north = corridor_bbox([(57.70, 11.80), (57.72, 11.86)])
    assert west < 11.80
    assert east > 11.86
    assert south < 57.70
    assert north > 57.72


def test_run_plan_produces_serializable_routes():
    from route_planner.model.enums import RECOMMENDED

    adapter = FakeAdapter(deep_chart())
    routes, meta = run_plan(adapter, request_route((57.70, 11.80), (57.72, 11.86), n_alternatives=2))
    assert routes
    assert all(len(r.waypoints.waypoint) >= 2 for r in routes)
    assert routes[0].status == RECOMMENDED
    assert routes[0].SerializeToString()
    assert meta["n_alternatives"] == len(routes)
    assert adapter.last_bbox is not None  # the adapter was asked for the corridor


def test_run_plan_requires_two_waypoints():
    from keelson.payloads.Route_pb2 import Route

    r = Route()
    wp = r.waypoints.waypoint.add()
    set_lat_lon(wp.position, 57.70, 11.80)
    with pytest.raises(PlanError):
        run_plan(FakeAdapter(deep_chart()), r)
