"""chart_adapter HTTP source + facade tests (P2) — httpx.MockTransport, no live server."""

from __future__ import annotations

import httpx
from shapely.geometry import Point

from route_planner.chart_adapter import ChartAdapter
from route_planner.chart_adapter import layers as L
from route_planner.chart_adapter.http import ChartServerHttp

_IDENTIFY_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [11.8, 57.66]},
            # UPPERCASE objclass, as the real chart-server returns it.
            "properties": {"objclass": "UWTROC", "valsou": 1.2, "watlev": 3, "lnam": "R1"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[11, 57], [12, 57], [12, 58], [11, 57]]]},
            "properties": {"objclass": "DEPARE", "drval1": 0.0, "drval2": 5.0, "lnam": "D1"},
        },
    ],
}
_DEPTH = {
    "coordinate": [11.8, 57.66],
    "depthArea": {"drval1": 5.0, "drval2": 10.0},
    "nearestSounding": {"depth": 6.1, "distance_m": 12.0},
    "dredgedArea": None,
    "onLand": False,
}
_CATALOG = {
    "cells": [{"name": "SE5KAB", "edtn": 3, "update_no": 1}, {"name": "SE4XYZ", "edtn": 2}],
    "coverage_bbox": [11.0, 57.0, 12.0, 58.0],
    "total_features": 1234,
}


def _handler(request):
    path = request.url.path
    if path in ("/api/identify", "/api/radius", "/api/bbox"):
        return httpx.Response(200, json=_IDENTIFY_FC)
    if path == "/api/depth":
        return httpx.Response(200, json=_DEPTH)
    if path == "/api/catalog":
        return httpx.Response(200, json=_CATALOG)
    return httpx.Response(404, json={})


def _mock_http():
    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test")
    return ChartServerHttp("http://test", client=client)


def test_http_identify_parses_features():
    feats = _mock_http().identify(57.66, 11.8)
    layers = {f.layer for f in feats}
    assert layers == {L.ROCKS, L.DEPTH_AREAS}
    rock = next(f for f in feats if f.layer == L.ROCKS)
    assert rock.valsou == 1.2
    assert rock.geometry.geom_type == "Point"


def test_http_depth_parses_into_depthinfo():
    info = _mock_http().depth(57.66, 11.8)
    assert info.on_land is False
    assert (info.drval1, info.drval2) == (5.0, 10.0)
    assert info.nearest_sounding_m == 6.1


def test_adapter_catalog_returns_provenance():
    adapter = ChartAdapter({"chart_server_url": "http://test"})
    adapter._http = _mock_http()
    prov = adapter.catalog()
    assert prov.cells == {"SE5KAB", "SE4XYZ"}
    assert prov.coverage_bbox == (11.0, 57.0, 12.0, 58.0)


class _FakeHttp:
    """Returns >=cap features for the root tile (forcing subdivision), small sets for quads."""

    def __init__(self):
        self.calls = 0

    def bbox(self, tile):
        self.calls += 1
        if self.calls == 1:
            return [L.ChartFeature(L.LAND, Point(0, 0), {"lnam": f"r{i}"}) for i in range(500)]
        return [
            L.ChartFeature(L.LAND, Point(0, 0), {"lnam": f"q{self.calls}"}),
            L.ChartFeature(L.LAND, Point(0, 0), {"lnam": "shared"}),
        ]

    def close(self):
        pass


def test_http_corridor_subdivides_and_dedups():
    adapter = ChartAdapter({"chart_server_url": "http://test"})  # no database_url -> HTTP path
    adapter._http = _FakeHttp()
    chart = adapter.fetch_corridor((11.0, 57.0, 12.0, 58.0))
    # root (>=500) subdivided into 4 quads; each quad adds a unique q* + the shared one (deduped).
    assert adapter._http.calls == 5
    assert chart.count() == 5  # q2,q3,q4,q5 + one "shared"
