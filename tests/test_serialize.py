"""PlannedRoute -> keelson.Route serialization tests (P5)."""

from __future__ import annotations

import os

import pytest

from route_planner.model import route_from_planned, routes_from_alternatives
from route_planner.pathfinding import PlannedRoute
from route_planner.model.position import lat_lon

PROFILE = {
    "name": "usv",
    "length_m": 1.8,
    "beam_m": 0.8,
    "draught_m": 0.25,
    "preferred_speed_kn": 5.0,
    "max_speed_kn": 6.0,
    "min_under_keel_clearance_m": 0.5,
    "route_margin_m": 15.0,
    "turn_radius_m": 5.0,
}


def _planned(status_hint="RECOMMENDED"):
    return PlannedRoute(
        waypoints=[(57.70, 11.80), (57.72, 11.84), (57.74, 11.88)],
        legs=[{"distance_m": 100.0, "bearing_deg": 45.0}, {"distance_m": 120.0, "bearing_deg": 50.0}],
        metrics={"distance_m": 220.0, "n_waypoints": 3, "est_time_s": 85.0},
        cost_breakdown={"near_land_margin": 30.0},
        snapped_start=True,
        snapped_end=False,
        optimization_goal="archipelago_conservative",
        status_hint=status_hint,
    )


def test_route_from_planned_maps_fields():
    from route_planner.model.enums import ARCHIPELAGO, LOXODROME, RECOMMENDED

    r = route_from_planned(
        _planned(),
        profile=PROFILE,
        optimization_goal="archipelago_conservative",
        route_id="rid1",
        created_at_ns=1_700_000_000_000_000_000,
    )
    assert r.route_id == "rid1"
    assert r.status == RECOMMENDED
    assert r.created_at.ToNanoseconds() == 1_700_000_000_000_000_000
    assert len(r.waypoints.waypoint) == 3

    wp0 = r.waypoints.waypoint[0]
    assert abs(lat_lon(wp0.position)[0] - 57.70) < 1e-9
    assert abs(lat_lon(wp0.position)[1] - 11.80) < 1e-9
    assert wp0.operational_context == ARCHIPELAGO
    assert wp0.planned_sog_knots == 5.0
    assert wp0.leg.geometry_type == LOXODROME
    assert abs(wp0.leg.safety_depth_m - 0.75) < 1e-6  # draught 0.25 + UKC 0.5
    assert r.waypoints.waypoint[2].leg.geometry_type == 0  # last waypoint has no outgoing leg

    assert r.info.optimization_method == "archipelago_conservative"
    assert abs(r.info.vessel_loa_m - 1.8) < 1e-4
    assert r.info.author_organization == "se.rise"


def test_waypoint_overrides_left_unset_without_a_profile_value():
    """Waypoint speed/radius are `optional` overrides: absent means "route default", never 0."""
    bare = {k: v for k, v in PROFILE.items() if k not in ("preferred_speed_kn", "turn_radius_m")}
    for profile in (bare, {**bare, "preferred_speed_kn": 0, "turn_radius_m": 0}):
        r = route_from_planned(_planned(), profile=profile, optimization_goal="shortest")
        for wp in r.waypoints.waypoint:
            assert not wp.HasField("planned_sog_knots")
            assert not wp.HasField("radius_m")

    r = route_from_planned(_planned(), profile=PROFILE, optimization_goal="shortest")
    assert all(wp.HasField("planned_sog_knots") for wp in r.waypoints.waypoint)
    assert all(isinstance(wp.id, str) and wp.id for wp in r.waypoints.waypoint)


def test_serialize_roundtrip():
    from keelson.payloads.Route_pb2 import Route

    r = route_from_planned(_planned(), profile=PROFILE, optimization_goal="shortest")
    back = Route.FromString(r.SerializeToString())
    assert back.route_id == r.route_id
    assert len(back.waypoints.waypoint) == 3


def test_extensions_carry_metrics_and_profile():
    r = route_from_planned(_planned(), profile=PROFILE, optimization_goal="safest")
    entry = next(e for e in r.extensions.entries if e.manufacturer == "se.rise" and e.name == "route-planner")
    a = entry.attributes
    assert a["optimization_goal"] == "safest"
    assert a["distance_m"] == "220.0"
    assert a["snapped_start"] == "true"
    assert a["snapped_end"] == "false"
    assert a["soft.near_land_margin"] == "30.0"
    assert a["edited_after_validation"] == "false"
    assert a["route_margin_m"] == "15.0"  # carried from the vessel profile


def test_extensions_carry_per_leg_metrics():
    r = route_from_planned(_planned(), profile=PROFILE, optimization_goal="shortest")
    a = next(e for e in r.extensions.entries if e.name == "route-planner").attributes
    dists = a["leg_distances_m"].split(",")
    bearings = a["leg_bearings_deg"].split(",")
    assert len(dists) == len(bearings) == len(_planned().waypoints) - 1  # one leg per waypoint gap
    assert [float(d) for d in dists] == [100.0, 120.0]
    assert [float(b) for b in bearings] == [45.0, 50.0]


def test_evidence_provenance_in_extensions():
    from route_planner.chart_adapter import Provenance

    ev = {"provenance": Provenance(cells={"SE5KAB", "SE4XYZ"}, worst_chart_scale=1_500_000.0)}
    r = route_from_planned(_planned(), profile=PROFILE, optimization_goal="x", evidence=ev)
    a = next(e for e in r.extensions.entries if e.name == "route-planner").attributes
    assert a["chart_cells"] == "SE4XYZ,SE5KAB"  # sorted
    assert a["worst_chart_scale"] == "1500000.0"


def test_routes_from_alternatives_status_and_ids():
    from route_planner.model.enums import PLANNED, RECOMMENDED

    routes = routes_from_alternatives(
        [_planned("RECOMMENDED"), _planned("PLANNED"), _planned("PLANNED")],
        profile=PROFILE,
        optimization_goal="shortest",
    )
    assert len(routes) == 3
    assert routes[0].status == RECOMMENDED
    assert routes[1].status == PLANNED
    assert len({r.route_id for r in routes}) == 3  # distinct ids


def test_random_route_id_when_unset():
    r1 = route_from_planned(_planned(), profile=PROFILE, optimization_goal="x")
    r2 = route_from_planned(_planned(), profile=PROFILE, optimization_goal="x")
    assert r1.route_id and r1.route_id != r2.route_id


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL; live chart DB not available",
)
def test_serialize_live_route():
    from route_planner.chart_adapter import ChartAdapter
    from route_planner.navigability import build_navigability, nav_params_from_profile
    from route_planner.pathfinding import plan

    adapter = ChartAdapter(
        {"database_url": os.environ["DATABASE_URL"], "chart_server_url": "http://localhost:8080"}
    )
    try:
        chart = adapter.fetch_corridor((11.78, 57.66, 11.90, 57.72))
    finally:
        adapter.close()
    nav = build_navigability(chart, nav_params_from_profile(PROFILE, "archipelago_conservative"))
    planned = plan(
        nav, (57.684, 11.800), (57.700, 11.880),
        optimization_goal="archipelago_conservative", n_alternatives=2, preferred_speed_kn=5.0,
    )
    if not planned:
        pytest.skip("no navigable path between the sample points")
    routes = routes_from_alternatives(
        planned, profile=PROFILE, optimization_goal="archipelago_conservative", evidence=nav.evidence
    )
    assert routes
    assert len(routes[0].waypoints.waypoint) >= 2
    assert routes[0].SerializeToString()
