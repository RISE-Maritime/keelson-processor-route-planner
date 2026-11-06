"""AIS ingestion tests (reference/ais.py) — pure cleaning/derivation on synthetic rows, no DB."""

from __future__ import annotations

import pytest

from route_planner.reference.ais import (
    AisSchema,
    AisTrack,
    clean_tracks,
    corridor_polygon,
    density_surface,
    mine_scenarios,
    rows_to_tracks,
    tracks_sql,
    voyage_lines,
)


def _row(mmsi, ts, lat, lon, sog=8.0, draught=7.5, ship_type="cargo"):
    return {
        "mmsi": mmsi,
        "ts": ts,
        "lat": lat,
        "lon": lon,
        "sog": sog,
        "draught": draught,
        "ship_type": ship_type,
    }


def _moving_rows(mmsi, n, *, lat=57.60, lon0=11.60, dlon=0.006, t0=0.0, dt=60.0, sog=8.0):
    return [_row(mmsi, t0 + i * dt, lat, lon0 + i * dlon, sog=sog) for i in range(n)]


def test_tracks_sql_shape():
    sql = tracks_sql(AisSchema())
    assert "FROM ais.positions" in sql
    assert "lon BETWEEN %s AND %s" in sql
    assert "lat BETWEEN %s AND %s" in sql
    assert "ORDER BY mmsi, ts" in sql


def test_tracks_sql_respects_custom_schema():
    schema = AisSchema(table="t", mmsi_col="m", time_col="t_utc", lon_col="x", lat_col="y", sog_col=None)
    sql = tracks_sql(schema)
    assert "FROM t" in sql and "x BETWEEN" in sql and "ORDER BY m, t_utc" in sql


def test_rows_to_tracks_groups_by_mmsi():
    rows = _moving_rows("1", 5) + _moving_rows("2", 5)
    tracks = rows_to_tracks(rows)
    assert {t.mmsi for t in tracks} == {"1", "2"}
    assert all(len(t.coords) == 5 for t in tracks)
    assert tracks[0].draught_m == pytest.approx(7.5)


def test_clean_drops_moored_track():
    rows = _moving_rows("1", 8, sog=0.0)  # never moving
    cleaned = clean_tracks(rows_to_tracks(rows))
    assert cleaned == []


def test_clean_segments_on_time_gap():
    seg1 = _moving_rows("1", 6, t0=0.0)
    seg2 = _moving_rows("1", 6, t0=5000.0, lon0=11.70)  # >600 s gap from seg1
    cleaned = clean_tracks(rows_to_tracks(seg1 + seg2), min_points=5)
    assert len(cleaned) == 2


def test_clean_keeps_one_moving_segment():
    cleaned = clean_tracks(rows_to_tracks(_moving_rows("1", 10)))
    assert len(cleaned) == 1
    assert len(voyage_lines(cleaned)) == 1
    assert len(cleaned[0].coords) >= 2


def test_density_and_corridor_polygon():
    # Three near-parallel tracks down the same lane => overlapping cells.
    tracks = [
        clean_tracks(rows_to_tracks(_moving_rows(str(k), 10, lat=57.60 + k * 0.0002)))[0]
        for k in range(3)
    ]
    bbox = (11.59, 57.59, 11.67, 57.62)
    density = density_surface(tracks, bbox, cell_size_m=50.0)
    assert density.grid.sum() > 0
    corridor = corridor_polygon(density, min_count=2, buffer_m=25.0)
    assert not corridor.is_empty
    assert corridor.area > 0


def test_mine_scenarios_clusters_endpoints():
    tracks = [
        AisTrack(
            mmsi=str(k),
            coords=[(57.50 + 0.0001 * k, 11.63), (57.62, 11.70), (57.71, 11.95 + 0.0001 * k)],
            times=[0.0, 600.0],
            draught_m=7.0 + k,
            ship_type="tanker",
        )
        for k in range(3)
    ]
    scenarios = mine_scenarios(tracks, cluster_radius_m=500.0, min_trips=2)
    assert len(scenarios) == 1
    sc = scenarios[0]
    assert sc["n_trips"] == 3
    assert sc["ship_types"] == ["tanker"]
    assert sc["start"][0] == pytest.approx(57.50, abs=0.01)
    assert sc["end"][1] == pytest.approx(11.95, abs=0.01)
