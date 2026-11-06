"""Navigation risk of a planned route, recorded on the route (risk.py, over navrisk.route)."""

from __future__ import annotations

import json

import pytest

navrisk_route = pytest.importorskip("navrisk.route")

from keelson.payloads.Route_pb2 import Route  # noqa: E402
from navrisk.evaluation.synthetic_chart import Shoal, SyntheticChart, SyntheticChartSpec  # noqa: E402

from route_planner import risk  # noqa: E402
from route_planner.model import enums as E  # noqa: E402
from route_planner.model.position import set_lat_lon  # noqa: E402

# A channel with a drying shoal on the second leg; synthetic geometry, not an ENC.
SPEC = SyntheticChartSpec(
    coverage=(11.70, 57.60, 11.95, 57.80), channel=(11.78, 57.66, 11.86, 57.76),
    shoals=(Shoal(lon=11.8205, lat=57.7102, radius_m=60.0, depth_m=0.0),),
)


def route(radius_m=40.0, gate=None):
    r = Route(route_id="r1")
    r.info.vessel_loa_m = 24.0
    r.info.vessel_beam_m = 6.5
    r.info.vessel_draft_m = 2.2
    for i, (lat, lon, radius) in enumerate([(57.690, 11.820, 60.0), (57.705, 11.820, radius_m),
                                            (57.715, 11.822, None), (57.740, 11.830, None)]):
        wp = r.waypoints.waypoint.add()
        wp.id = str(i + 1)
        set_lat_lon(wp.position, lat, lon)
        wp.planned_sog_knots = 8.0
        if radius:
            wp.radius_m = radius
    if gate is not None:
        e = r.extensions.entries.add(manufacturer="se.rise", name=risk.RISK_EXTENSION, version="1")
        e.attributes["grounding_risk_max"] = str(gate)
    return r


def entry(r):
    return next(e for e in r.extensions.entries if e.name == risk.RISK_EXTENSION)


@pytest.fixture(scope="module")
def assessed():
    return risk.annotate(route(radius_m=20.0, gate=0.05), SyntheticChart(SPEC))


def test_the_profile_is_recorded_on_the_route(assessed):
    a = entry(assessed).attributes
    assert a["status"] == "assessed"
    assert a["dim.grounding.measurable"] == "true" and float(a["dim.grounding.max"]) > 0.05
    legs = json.loads(a["legs_json"])
    worst = max(legs, key=lambda leg: leg["dims"]["grounding"]["max"] or 0)
    assert (worst["from"], worst["to"]) == ("2", "3"), "the leg over the shoal"


def test_collision_is_not_measurable_on_a_plan(assessed):
    a = entry(assessed).attributes
    assert a["dim.collision.measurable"] == "false"
    assert "no traffic forecast" in a["dim.collision.note"]
    assert "dim.collision.max" not in a


def test_a_failed_declared_gate_is_a_challenge(assessed):
    ids = {c.id: c for c in assessed.challenges}
    gate = ids["challenge-navrisk-gate-grounding"]
    assert gate.severity == E.WARNING and "over the declared limit 0.05" in gate.description
    assert list(gate.waypoint_ids) == ["2", "3"]
    assert entry(assessed).attributes["gates.pass_fail"] == "FAIL"


def test_a_turn_tighter_than_the_envelope_is_a_challenge(assessed):
    turn = next(c for c in assessed.challenges if c.id == "challenge-navrisk-turn-2")
    assert turn.type == E.TECHNICAL_LIMIT and "tighter than" in turn.description


def test_reassessing_replaces_its_own_challenges_and_keeps_the_limits(assessed):
    again = risk.annotate(assessed, SyntheticChart(SPEC))
    navrisk_ids = [c.id for c in again.challenges if c.id.startswith("challenge-navrisk-")]
    assert len(navrisk_ids) == len(set(navrisk_ids))
    assert entry(again).attributes["grounding_risk_max"] == "0.05"


def test_without_a_chart_the_water_is_not_measurable_never_zero():
    a = entry(risk.annotate(route(), None)).attributes
    for name in ("grounding", "manoeuvre", "fairway"):
        assert a[f"dim.{name}.measurable"] == "false" and f"dim.{name}.max" not in a
    assert a["level"] == "NOT ASSESSED"


def test_no_undeclared_gate_is_applied():
    a = entry(risk.annotate(route(), SyntheticChart(SPEC))).attributes
    assert a["gates.pass_fail"] == ""
    assert "grounding" in a["gates.not_declared"].split(",")


def test_a_route_without_speeds_is_not_assessed_and_says_why():
    r = route()
    for wp in r.waypoints.waypoint:
        wp.ClearField("planned_sog_knots")
    a = entry(risk.annotate(r, None)).attributes
    assert a["status"] == "not_assessed" and "no planned speed" in a["note"]


def test_run_validate_carries_the_risk_profile():
    from _planhelpers import FakeAdapter, deep_chart

    from route_planner import planner

    r = route()
    out = planner.run_validate(FakeAdapter(deep_chart()), r)
    assert entry(out).attributes["status"] == "assessed"


def test_the_platform_the_particulars_came_from_is_kept():
    r = route()
    e = r.extensions.entries.add(manufacturer="se.rise", name=risk.RISK_EXTENSION, version="1")
    e.attributes["platform"] = "slipway-usv-alpha"
    e.attributes["particulars_basis"] = "registry; pilot card signed 2026-09-20"
    a = entry(risk.annotate(r, None)).attributes
    assert a["platform"] == "slipway-usv-alpha" and a["particulars_basis"].startswith("registry")


def test_the_series_can_be_drawn_on_a_chart(assessed):
    series = json.loads(entry(assessed).attributes["series_json"])
    combined = series["combined"]
    assert combined and all({"latitude", "longitude", "level"} <= set(p) for p in combined)
    worst = max(combined, key=lambda p: p["value"] or 0)
    assert abs(worst["latitude"] - 57.7102) < 0.003 and abs(worst["longitude"] - 11.8205) < 0.003


def test_the_sailed_path_is_recorded_for_drawing(assessed):
    path = json.loads(entry(assessed).attributes["path_json"])
    assert len(path) > 2 and all(len(v) == 3 for v in path)
    assert all(b[0] > a[0] for a, b in zip(path, path[1:])), "in planned time"
