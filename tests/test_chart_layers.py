"""chart_adapter normalization tests (P2) — pure, no I/O."""

from __future__ import annotations

from route_planner.chart_adapter import layers as L


def _feature(objclass, coords, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": list(coords)},
        "properties": {"objclass": objclass, **props},
    }


def test_objclass_to_layer_mapping():
    assert L.OBJCLASS_TO_LAYER["depare"] == L.DEPTH_AREAS
    assert L.OBJCLASS_TO_LAYER["lndare"] == L.LAND
    assert L.OBJCLASS_TO_LAYER["uwtroc"] == L.ROCKS
    assert L.OBJCLASS_TO_LAYER["resare"] == L.RESTRICTED_AREAS
    assert L.OBJCLASS_TO_LAYER["tsslpt"] == L.TSS


def test_feature_from_geojson_builds_shapely_and_pulls_attrs():
    gj = _feature("depare", (11.8, 57.66), drval1=5.0, drval2=10.0, chart_scale=50000, lnam="X1")
    feat = L.feature_from_geojson(gj)
    assert feat.layer == L.DEPTH_AREAS
    assert feat.geometry.geom_type == "Point"
    assert (feat.drval1, feat.drval2) == (5.0, 10.0)
    assert feat.chart_scale == 50000.0
    assert feat.lnam == "X1"


def test_objclass_is_case_insensitive():
    # chart-server returns UPPERCASE acronyms (e.g. "DEPARE"); they must still map.
    assert L.feature_from_geojson(_feature("DEPARE", (11.8, 57.66))).layer == L.DEPTH_AREAS
    assert L.feature_from_geojson(_feature("LNDARE", (11.8, 57.66))).layer == L.LAND


def test_unknown_objclass_passes_through():
    feat = L.feature_from_geojson(_feature("ZZZZ", (0, 0)))
    assert feat.layer == "zzzz"


def test_feature_from_row_parses_geojson_string():
    feat = L.feature_from_row(L.SOUNDINGS, {"depth": 3.2, "lnam": "S9"}, '{"type":"Point","coordinates":[11.8,57.6]}')
    assert feat.layer == L.SOUNDINGS
    assert feat.depth == 3.2
    assert feat.geometry.x == 11.8


def test_normalized_chart_groups_and_tracks_provenance():
    chart = L.NormalizedChart()
    chart.add(L.feature_from_geojson(_feature("depare", (11.8, 57.6), chart_scale=20000, source_chart="SE1")))
    chart.add(L.feature_from_geojson(_feature("lndare", (11.9, 57.7), chart_scale=90000, source_chart="SE2")))
    chart.add(L.feature_from_geojson(_feature("lndare", (12.0, 57.8), chart_scale=50000, source_chart="SE1")))
    assert len(chart.layer(L.LAND)) == 2
    assert len(chart.layer(L.DEPTH_AREAS)) == 1
    assert chart.count() == 3
    assert chart.provenance.cells == {"SE1", "SE2"}
    assert chart.provenance.worst_chart_scale == 90000.0  # coarsest = largest denominator


def test_normalized_chart_skips_null_geometry():
    chart = L.NormalizedChart()
    chart.add(L.ChartFeature(layer=L.LAND, geometry=None, attrs={}))
    assert chart.count() == 0


def test_depthinfo_from_api():
    info = L.DepthInfo.from_api(
        {
            "onLand": False,
            "depthArea": {"drval1": 5.0, "drval2": 10.0},
            "nearestSounding": {"depth": 6.1, "distance_m": 12.0},
            "dredgedArea": None,
        }
    )
    assert info.on_land is False
    assert (info.drval1, info.drval2) == (5.0, 10.0)
    assert info.nearest_sounding_m == 6.1
