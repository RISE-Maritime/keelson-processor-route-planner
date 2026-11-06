"""planner tests — PlanRouteRequest parsing + orchestration with a fake adapter."""

from __future__ import annotations

import pytest
from _planhelpers import FakeAdapter, deep_chart, plan_request, route_between

from route_planner.model import vessel_profile as vp
from route_planner.planner import (
    PlanError,
    PlanUnsupported,
    corridor_bbox,
    parse_plan_request,
    route_context,
    run_plan,
)


def test_parse_plan_request_extracts_inputs():
    req = plan_request(
        (57.70, 11.80), (57.72, 11.86),
        goal="archipelago_conservative", n_alternatives=3, constraints={"prefer_fairways": True},
        via=[(57.71, 11.83)],
    )
    parsed = parse_plan_request(req)
    assert parsed["start"] == (57.70, 11.80)
    assert parsed["end"] == (57.72, 11.86)
    assert parsed["via"] == [(57.71, 11.83)]
    assert parsed["optimization_goal"] == "archipelago_conservative"
    assert parsed["n_alternatives"] == 3
    assert parsed["constraints"].get("prefer_fairways") is True
    assert round(float(parsed["profile"]["length_m"]), 3) == 1.8
    assert parsed["profile"]["turn_radius_m"] == "5.0"  # from the extension entry


def test_parse_plan_request_defaults():
    req = plan_request((57.70, 11.80), (57.72, 11.86), goal=None, n_alternatives=0)
    parsed = parse_plan_request(req)
    assert parsed["optimization_goal"] == "shortest"  # OPTIMIZATION_GOAL_UNSPECIFIED
    assert parsed["n_alternatives"] == 1  # 0 = the planner's own default


def test_parse_plan_request_deployment_goal_via_extension():
    parsed = parse_plan_request(plan_request((57.70, 11.80), (57.72, 11.86), goal="usv_trial_safe"))
    assert parsed["optimization_goal"] == "usv_trial_safe"


def test_parse_plan_request_rejects_unknown_extension_goal():
    req = plan_request((57.70, 11.80), (57.72, 11.86), goal="nonsense")
    with pytest.raises(PlanError, match="unknown optimization goal"):
        parse_plan_request(req)


def test_parse_plan_request_rejects_two_goals():
    from keelson.interfaces.RoutePlanner_pb2 import OPTIMIZATION_GOAL_FASTEST

    req = plan_request((57.70, 11.80), (57.72, 11.86), goal="pilot_matched")
    req.goal = OPTIMIZATION_GOAL_FASTEST
    with pytest.raises(PlanError, match="both"):
        parse_plan_request(req)


def test_parse_plan_request_requires_two_waypoints():
    req = vp.plan_request([(57.70, 11.80)], profile={})
    with pytest.raises(PlanError) as exc:
        parse_plan_request(req)
    assert not isinstance(exc.value, PlanUnsupported)


def test_parse_plan_request_closed_topology_is_unsupported():
    from keelson.payloads.Route_pb2 import ROUTE_TOPOLOGY_CLOSED

    req = plan_request((57.70, 11.80), (57.72, 11.86))
    req.topology = ROUTE_TOPOLOGY_CLOSED
    with pytest.raises(PlanUnsupported):
        parse_plan_request(req)


def test_route_context_reads_the_route():
    ctx = route_context(route_between((57.70, 11.80), (57.72, 11.86), goal="safest"))
    assert ctx["optimization_goal"] == "safest"
    assert ctx["start"] == (57.70, 11.80)


def test_corridor_bbox_pads_around_points():
    west, south, east, north = corridor_bbox([(57.70, 11.80), (57.72, 11.86)])
    assert west < 11.80
    assert east > 11.86
    assert south < 57.70
    assert north > 57.72


def test_run_plan_produces_serializable_routes():
    from route_planner.model.enums import RECOMMENDED

    adapter = FakeAdapter(deep_chart())
    routes, meta = run_plan(adapter, plan_request((57.70, 11.80), (57.72, 11.86), n_alternatives=2))
    assert routes
    assert len(routes) <= 2  # MUST NOT reply with more than max_alternatives
    assert all(len(r.waypoints.waypoint) >= 2 for r in routes)
    assert routes[0].status == RECOMMENDED
    assert routes[0].SerializeToString()
    assert meta["n_alternatives"] == len(routes)
    assert adapter.last_bbox is not None  # the adapter was asked for the corridor


def test_run_plan_never_exceeds_max_alternatives():
    routes, _ = run_plan(FakeAdapter(deep_chart()), plan_request((57.70, 11.80), (57.72, 11.86), n_alternatives=1))
    assert len(routes) == 1


def test_run_plan_requires_two_waypoints():
    with pytest.raises(PlanError):
        run_plan(FakeAdapter(deep_chart()), vp.plan_request([(57.70, 11.80)], profile={}))
