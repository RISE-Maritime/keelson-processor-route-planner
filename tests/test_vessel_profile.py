"""Vessel profile <-> RouteInfo + Extensions mapping tests (P1)."""

from __future__ import annotations

from route_planner.model import vessel_profile as vp

# The DESIGN §4 indicative profile.
PROFILE = {
    "name": "small_usv_1_8m_5kt",
    "length_m": 1.8,
    "beam_m": 0.8,
    "draught_m": 0.25,
    "preferred_speed_kn": 5.0,
    "max_speed_kn": 6.0,
    "min_under_keel_clearance_m": 0.5,
    "route_margin_m": 15.0,
    "turn_radius_m": 5.0,
}


def test_route_info_from_profile_maps_vessel_fields():
    info = vp.route_info_from_profile(PROFILE, optimization_goal="archipelago_conservative")
    assert round(info.vessel_loa_m, 3) == 1.8
    assert round(info.vessel_beam_m, 3) == 0.8
    assert round(info.vessel_draft_m, 3) == 0.25
    assert round(info.max_speed_knots, 3) == 6.0
    assert info.optimization_method == "archipelago_conservative"


def test_planner_only_fields_go_to_extensions_attributes():
    entry = vp.planner_extension_entry(PROFILE, constraints={"prefer_fairways": True})
    assert entry.manufacturer == "se.rise"
    assert entry.name == "route-planner"
    assert entry.attributes["route_margin_m"] == "15.0"
    assert entry.attributes["turn_radius_m"] == "5.0"
    assert entry.attributes["constraint.prefer_fairways"] == "True"
    assert "return_alternatives" not in entry.attributes  # PlanRouteRequest.max_alternatives now
    assert vp.GOAL_ATTRIBUTE not in entry.attributes


def test_plan_request_types_an_enum_goal():
    from keelson.interfaces.RoutePlanner_pb2 import OPTIMIZATION_GOAL_SAFEST

    req = vp.plan_request([(57.70, 11.80), (57.71, 11.82), (57.72, 11.86)], profile=PROFILE,
                          goal="safest", max_alternatives=3)
    assert req.goal == OPTIMIZATION_GOAL_SAFEST
    assert len(req.waypoints) == 3
    assert req.waypoints[0].latitude_deg == 57.70
    assert req.max_alternatives == 3
    assert round(req.constraints.vessel_loa_m, 3) == PROFILE["length_m"]  # float32 field
    assert vp.GOAL_ATTRIBUTE not in vp.planner_entry(req.extensions).attributes


def test_plan_request_routes_a_deployment_goal_through_extensions():
    from keelson.interfaces.RoutePlanner_pb2 import OPTIMIZATION_GOAL_UNSPECIFIED

    req = vp.plan_request([(57.70, 11.80), (57.72, 11.86)], profile=PROFILE, goal="pilot_matched")
    assert req.goal == OPTIMIZATION_GOAL_UNSPECIFIED
    assert vp.planner_entry(req.extensions).attributes[vp.GOAL_ATTRIBUTE] == "pilot_matched"


def test_goal_label_covers_every_optimization_goal():
    from keelson.interfaces.RoutePlanner_pb2 import OptimizationGoal

    from route_planner.pathfinding.cost import WEIGHT_PROFILES

    assert vp.goal_label(OptimizationGoal.Value("OPTIMIZATION_GOAL_UNSPECIFIED")) is None
    for name, value in OptimizationGoal.items():
        if name != "OPTIMIZATION_GOAL_UNSPECIFIED":
            assert vp.goal_label(value) in WEIGHT_PROFILES, name


def test_profile_from_route_roundtrip():
    from keelson.payloads.Route_pb2 import Route

    route = Route(route_id="r1")
    route.info.CopyFrom(vp.route_info_from_profile(PROFILE))
    route.extensions.entries.append(vp.planner_extension_entry(PROFILE))

    back = vp.profile_from_route(route)
    assert round(float(back["length_m"]), 3) == 1.8
    assert back["route_margin_m"] == "15.0"  # extension attributes come back as strings
    assert back["name"] == "small_usv_1_8m_5kt"
