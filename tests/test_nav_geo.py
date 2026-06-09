"""navigability.geo tests (P3) — projection + metric helpers, pure."""

from __future__ import annotations

import math

from shapely.geometry import Point, Polygon

from route_planner.navigability import geo


def test_projection_roundtrip():
    proj = geo.Projection(lat_0=57.7, lon_0=11.8)
    x, y = proj.forward(11.85, 57.72)
    lon, lat = proj.inverse(x, y)
    assert abs(lon - 11.85) < 1e-6
    assert abs(lat - 57.72) < 1e-6


def test_forward_origin_near_zero():
    proj = geo.Projection(lat_0=57.7, lon_0=11.8)
    x, y = proj.forward(11.8, 57.7)
    assert abs(x) < 1e-3
    assert abs(y) < 1e-3


def test_buffer_m_area_matches_radius():
    proj = geo.Projection(lat_0=57.7, lon_0=11.8)
    point_m = proj.to_metric(Point(11.8, 57.7))
    disk = geo.buffer_m(point_m, 100.0)
    assert math.isclose(disk.area, math.pi * 100**2, rel_tol=0.02)


def test_clean_repairs_self_intersection():
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    assert not bowtie.is_valid
    fixed = geo.clean(bowtie)
    assert fixed is not None
    assert fixed.is_valid


def test_to_metric_geometry_roundtrip():
    poly = Polygon([(11.8, 57.7), (11.9, 57.7), (11.9, 57.8), (11.8, 57.8)])
    proj = geo.Projection.for_geometry(poly)
    back = proj.to_wgs84(proj.to_metric(poly))
    assert abs(back.centroid.x - poly.centroid.x) < 1e-6
    assert abs(back.centroid.y - poly.centroid.y) < 1e-6


def test_safe_union_skips_none_and_empty():
    assert geo.safe_union([None, Polygon()]) is None
    union = geo.safe_union([Point(0, 0).buffer(1), Point(1, 0).buffer(1)])
    assert union is not None
    assert union.area > 0
