"""Shared builders for P6 tests (fake chart adapter + plan_route request template)."""

from __future__ import annotations

from shapely.geometry import box

from route_planner.chart_adapter.layers import DEPTH_AREAS, ChartFeature, NormalizedChart
from route_planner.model import vessel_profile as vp

PROFILE = {
    "name": "usv",
    "length_m": 1.8,
    "beam_m": 0.8,
    "draught_m": 0.25,
    "preferred_speed_kn": 5.0,
    "max_speed_kn": 6.0,
    "min_under_keel_clearance_m": 0.5,
    "route_margin_m": 50.0,
    "turn_radius_m": 5.0,
}

# Deep water covering the sample start/end + corridor pad.
COVERAGE = (11.78, 57.68, 11.90, 57.74)  # west, south, east, north


def deep_chart(extra=()) -> NormalizedChart:
    chart = NormalizedChart()
    chart.add(ChartFeature(DEPTH_AREAS, box(*COVERAGE), {"drval1": 10, "drval2": 20}))
    for feature in extra:
        chart.add(feature)
    chart.provenance.coverage_bbox = COVERAGE
    return chart


class FakeAdapter:
    """Stands in for ChartAdapter: fetch_corridor returns a prebuilt chart (no DB/HTTP)."""

    def __init__(self, chart):
        self._chart = chart
        self.last_bbox = None
        self.closed = False

    def fetch_corridor(self, bbox, tables=None):
        self.last_bbox = bbox
        return self._chart

    def close(self):
        self.closed = True


def request_route(start, end, *, goal="shortest", profile=PROFILE, n_alternatives=1, constraints=None):
    """Build a plan_route request keelson.Route template (the client's shape)."""
    from keelson.payloads.Route_pb2 import Route

    route = Route()
    route.info.CopyFrom(vp.route_info_from_profile(profile, goal))
    for lat, lon in [start, end]:
        wp = route.waypoints.waypoint.add()
        wp.position.latitude = lat
        wp.position.longitude = lon
    route.extensions.entries.append(
        vp.planner_extension_entry(profile, constraints=constraints, return_alternatives=n_alternatives)
    )
    return route
