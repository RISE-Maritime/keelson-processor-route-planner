"""chart_adapter PostGIS source tests (P2).

The SQL builder + row normalization are tested without a DB. A live integration test runs only when
DATABASE_URL points at a reachable chart database.
"""

from __future__ import annotations

import os

import pytest

from route_planner.chart_adapter import layers as L
from route_planner.chart_adapter.postgis import PostgisSource, corridor_sql


def test_corridor_sql_targets_effective_view_with_envelope():
    sql = corridor_sql("depare")
    assert "edits.effective_depare" in sql
    assert "ST_MakeEnvelope(%s, %s, %s, %s, 4326)" in sql
    assert "ST_AsGeoJSON(t.wkb_geometry)" in sql
    assert "to_jsonb(t) - 'wkb_geometry'" in sql
    assert "LIMIT" in sql


def test_corridor_sql_rejects_unknown_table():
    with pytest.raises(ValueError):
        corridor_sql("evil'; DROP TABLE public.depare; --")


def test_feature_from_row_builds_depth_area():
    feat = L.feature_from_row(
        L.DEPTH_AREAS,
        {"drval1": 0.0, "drval2": 5.0, "chart_scale": 50000, "source_chart": "SE5KAB"},
        '{"type":"Polygon","coordinates":[[[11,57],[12,57],[12,58],[11,57]]]}',
    )
    assert feat.layer == L.DEPTH_AREAS
    assert (feat.drval1, feat.drval2) == (0.0, 5.0)
    assert feat.chart_scale == 50000.0
    assert feat.source_cell == "SE5KAB"
    assert feat.geometry.geom_type == "Polygon"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL; live chart DB not available",
)
def test_postgis_corridor_live():
    src = PostgisSource(os.environ["DATABASE_URL"])
    try:
        chart = src.fetch_corridor((11.7, 57.6, 11.95, 57.72))
        assert isinstance(chart, L.NormalizedChart)
        # Smoke: provenance bbox recorded; features (if any) carry a layer + geometry.
        assert chart.provenance.coverage_bbox == (11.7, 57.6, 11.95, 57.72)
        for feat in chart.all_features()[:5]:
            assert feat.geometry is not None
            assert feat.layer
    finally:
        src.close()
