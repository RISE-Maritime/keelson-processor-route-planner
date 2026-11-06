"""RTZ pilot-route import tests (reference/rtz.py).

Parses the committed Sjöfartsverket Gothenburg fixtures; the keelson.Route mapping is gated on the
monorepo SDK (payloads.Route_pb2) like test_serialize.py.
"""

from __future__ import annotations

import os

import pytest

from route_planner.reference.rtz import parse_rtz, rtz_to_planned, rtz_to_route

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "pilot_routes")
ALL_RTZ = sorted(f for f in os.listdir(FIXTURES) if f.endswith(".rtz"))
MAIN = os.path.join(FIXTURES, "01_PS1_via_N_Ch_to_Frihamnen.rtz")
_NM = 1852.0


def test_parse_main_route_structure():
    route = parse_rtz(MAIN)
    assert route.name == "01 PS1 via N.Ch to Frihamnen"
    assert route.format_version.startswith("RTZ-")
    assert len(route.waypoints) == 22
    first, last = route.waypoints[0], route.waypoints[-1]
    assert first.name == "VTS South In"
    assert last.name == "Frihamnen"
    assert first.lat == pytest.approx(57.53209383)
    assert first.lon == pytest.approx(11.63149427)


def test_units_converted_to_metres():
    wps = {wp.id: wp for wp in parse_rtz(MAIN).waypoints}
    # wp 2 carries radius=0.3 NM and portsideXTD/starboardXTD=0.27 NM.
    assert wps[2].radius_m == pytest.approx(0.3 * _NM)
    assert wps[2].xtd_port_m == pytest.approx(0.27 * _NM)
    assert wps[2].geometry_type == "Orthodrome"


def test_default_waypoint_leg_merged():
    # wp 1's <leg> sets only geometryType; portside/starboardXTD fall back to <defaultWaypoint> (1 NM).
    wp1 = parse_rtz(MAIN).waypoints[0]
    assert wp1.xtd_port_m == pytest.approx(1.0 * _NM)
    assert wp1.xtd_starboard_m == pytest.approx(1.0 * _NM)


def test_rtz_to_planned_metrics():
    planned = rtz_to_planned(MAIN)
    assert len(planned.waypoints) == 22
    assert len(planned.legs) == 21
    assert planned.metrics["n_waypoints"] == 22
    assert planned.metrics["distance_m"] > 0
    # Sum of leg distances equals the route total (within rounding).
    leg_sum = sum(leg["distance_m"] for leg in planned.legs)
    assert leg_sum == pytest.approx(planned.metrics["distance_m"], rel=1e-3)
    assert all(0 <= leg["bearing_deg"] < 360 for leg in planned.legs)


@pytest.mark.parametrize("fname", ALL_RTZ)
def test_all_fixtures_parse_within_gothenburg(fname):
    route = parse_rtz(os.path.join(FIXTURES, fname))
    assert len(route.waypoints) >= 2
    for wp in route.waypoints:
        assert 57.3 < wp.lat < 57.9, f"{fname}: lat {wp.lat} out of Gothenburg bbox"
        assert 11.4 < wp.lon < 12.1, f"{fname}: lon {wp.lon} out of Gothenburg bbox"


def test_parse_from_xml_string():
    with open(MAIN, encoding="utf-8") as fh:
        text = fh.read()
    assert parse_rtz(text).name == "01 PS1 via N.Ch to Frihamnen"


def test_rejects_non_rtz():
    with pytest.raises(ValueError):
        parse_rtz("<notaroute/>")


# --- keelson.Route mapping (needs the monorepo SDK) ---------------------------------------------

keelson = pytest.importorskip("keelson.payloads.Route_pb2")


def test_rtz_to_route_maps_waypoints_and_provenance():
    from route_planner.model.enums import ORTHODROME

    rtz = parse_rtz(MAIN)
    route = rtz_to_route(rtz, route_id="test-01")

    assert route.route_id == "test-01"
    assert route.info.name == "01 PS1 via N.Ch to Frihamnen"
    assert route.info.author_organization == "Sjöfartsverket"
    assert len(route.waypoints.waypoint) == 22

    wp2 = route.waypoints.waypoint[1]
    assert wp2.radius_m == pytest.approx(0.3 * _NM)
    assert wp2.leg.xtd_port_m == pytest.approx(0.27 * _NM)
    assert wp2.leg.geometry_type == ORTHODROME

    entry = route.extensions.entries[0]
    assert entry.attributes["reference_kind"] == "pilot"
