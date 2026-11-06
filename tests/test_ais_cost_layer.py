"""AIS-corridor soft-cost layer (navigability + cost prior, Phase F). Synthetic chart, pure geometry."""

from __future__ import annotations

from shapely.geometry import Point, box

from route_planner.chart_adapter.layers import DEPTH_AREAS, ChartFeature, NormalizedChart
from route_planner.navigability import build_navigability, nav_params_from_profile

CORRIDOR = (11.80, 57.70, 11.90, 57.76)
PROFILE = {"draught_m": 0.25, "min_under_keel_clearance_m": 0.5, "route_margin_m": 15.0}
# Historical-AIS traffic envelope covering only the western half of the corridor.
AIS_CORRIDOR = box(11.80, 57.70, 11.85, 57.76)


def _chart() -> NormalizedChart:
    chart = NormalizedChart()
    chart.add(ChartFeature(DEPTH_AREAS, box(11.80, 57.70, 11.90, 57.76), {"drval1": 10, "drval2": 20}))
    chart.provenance.coverage_bbox = CORRIDOR
    return chart


def _kinds(result):
    return {sc.kind for sc in result.soft_costs}


def test_off_ais_corridor_emitted_when_opted_in():
    params = nav_params_from_profile(PROFILE, "shortest", {"prefer_ais_corridors": True})
    assert params.prefer_ais_corridors is True
    r = build_navigability(_chart(), params, ais_corridor=AIS_CORRIDOR)
    assert "off_ais_corridor" in _kinds(r)

    soft = next(sc for sc in r.soft_costs if sc.kind == "off_ais_corridor")
    # Eastern half (outside the AIS corridor) is penalized; western half (inside) is not.
    assert soft.geometry.contains(Point(r.projection.forward(11.88, 57.73)))
    assert not soft.geometry.contains(Point(r.projection.forward(11.82, 57.73)))


def test_absent_without_opt_in_flag():
    params = nav_params_from_profile(PROFILE, "shortest", {})  # flag defaults False
    r = build_navigability(_chart(), params, ais_corridor=AIS_CORRIDOR)
    assert "off_ais_corridor" not in _kinds(r)


def test_absent_without_corridor_geometry():
    params = nav_params_from_profile(PROFILE, "shortest", {"prefer_ais_corridors": True})
    r = build_navigability(_chart(), params)  # no ais_corridor supplied
    assert "off_ais_corridor" not in _kinds(r)
