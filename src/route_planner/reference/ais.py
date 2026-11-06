"""Historical AIS ingestion — real sailed tracks from the database as benchmark ground truth.

Read-only over an AIS positions table (mirrors ``chart_adapter/postgis.py``: lazy psycopg3 pool,
SELECT-only, a pure SQL builder so the query is unit-testable without a DB). Column/table names are
configurable via ``AisSchema`` because AIS schemas vary; defaults match a typical
``mmsi, ts, lon, lat, sog, cog, draught, ship_type`` layout.

The cleaning + derivation functions are pure (numpy/shapely/pyproj — no DB, no zenoh), so they unit
-test against synthetic in-memory rows. They produce the three things the benchmark wants:

- **ground-truth voyages** — per-MMSI, per-leg cleaned polylines (``voyage_lines``);
- **a density/corridor surface** — where ships actually sail (``density_surface`` / ``corridor_polygon``)
  for the Phase-F cost prior;
- **mined scenarios** — clustered endpoint pairs + vessel-profile stats (``mine_scenarios``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString

from ..navigability.geo import Projection

logger = logging.getLogger(__name__)

_KN_TO_MS = 0.514444
_DEFAULT_CAP = 2_000_000

__all__ = [
    "AisSchema",
    "AisTrack",
    "AisDensity",
    "tracks_sql",
    "AisSource",
    "rows_to_tracks",
    "clean_tracks",
    "voyage_lines",
    "density_surface",
    "corridor_polygon",
    "corridor_wgs84",
    "mine_scenarios",
]


@dataclass(frozen=True)
class AisSchema:
    """Table + column names for the AIS positions source (override to match your DB)."""

    table: str = "ais.positions"
    mmsi_col: str = "mmsi"
    time_col: str = "ts"
    lon_col: str = "lon"
    lat_col: str = "lat"
    sog_col: str | None = "sog"  # knots; None if absent
    draught_col: str | None = "draught"  # metres; None if absent
    ship_type_col: str | None = "ship_type"  # None if absent

    def select_columns(self) -> list:
        cols = [self.mmsi_col, self.time_col, self.lon_col, self.lat_col]
        cols += [c for c in (self.sog_col, self.draught_col, self.ship_type_col) if c]
        return cols


@dataclass
class AisTrack:
    """One vessel's cleaned voyage segment. ``coords`` is [(lat, lon)] in time order."""

    mmsi: str
    coords: list = field(default_factory=list)  # [(lat, lon)]
    times: list = field(default_factory=list)  # epoch seconds, aligned with coords
    draught_m: float | None = None
    ship_type: str | None = None

    @property
    def start(self):
        return self.coords[0]

    @property
    def end(self):
        return self.coords[-1]


@dataclass
class AisDensity:
    """Rasterized AIS presence over a metric grid (centred on a Projection)."""

    grid: np.ndarray  # (rows, cols) visit counts
    projection: Projection
    cell_size_m: float
    x0: float  # metric x of column 0 (left edge)
    y0: float  # metric y of row 0 (bottom edge)


def tracks_sql(schema: AisSchema = AisSchema(), cap: int = _DEFAULT_CAP) -> str:
    """Parametrized bbox+time SELECT, ordered by (mmsi, time). Pure — params bound separately.

    Bind order: (west, south, east, north, start_epoch, end_epoch). Identifiers come from
    ``AisSchema`` (your config), not user input, so interpolating them is safe.
    """
    cols = ", ".join(schema.select_columns())
    return (
        f"SELECT {cols} FROM {schema.table} "
        f"WHERE {schema.lon_col} BETWEEN %s AND %s "
        f"AND {schema.lat_col} BETWEEN %s AND %s "
        f"AND {schema.time_col} BETWEEN to_timestamp(%s) AND to_timestamp(%s) "
        f"ORDER BY {schema.mmsi_col}, {schema.time_col} "
        f"LIMIT {int(cap)}"
    )


class AisSource:
    """Lazily-opened psycopg3 pool over the AIS DB (read-only). Mirrors PostgisSource."""

    def __init__(self, database_url: str, schema: AisSchema = AisSchema(), cap: int = _DEFAULT_CAP):
        self._url = database_url
        self._schema = schema
        self._cap = cap
        self._pool = None

    def _get_pool(self):
        if self._pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                self._url, min_size=1, max_size=4, open=True, kwargs={"row_factory": dict_row}
            )
        return self._pool

    def fetch_positions(self, bbox, start_epoch: float, end_epoch: float) -> list:
        """Read raw AIS rows in ``bbox`` (west, south, east, north) and [start, end] epoch seconds."""
        west, south, east, north = bbox
        sql = tracks_sql(self._schema, self._cap)
        with self._get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (west, east, south, north, start_epoch, end_epoch))
            return cur.fetchall()

    def tracks(self, bbox, start_epoch: float, end_epoch: float, **clean_kwargs) -> list:
        """Fetch + clean in one call -> list[AisTrack]."""
        rows = self.fetch_positions(bbox, start_epoch, end_epoch)
        return clean_tracks(rows_to_tracks(rows, self._schema), **clean_kwargs)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None


def _as_epoch(value) -> float:
    """Coerce a DB timestamp (datetime or epoch number) to epoch seconds."""
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    return float(value)


def rows_to_tracks(rows, schema: AisSchema = AisSchema()) -> list:
    """Group raw position rows by MMSI into one raw AisTrack each (assumes rows time-ordered).

    Each row is a mapping (dict_row). Keeps per-point SOG transiently on the track for cleaning.
    """
    by_mmsi: dict = {}
    for row in rows:
        mmsi = str(row[schema.mmsi_col])
        bucket = by_mmsi.setdefault(mmsi, {"coords": [], "times": [], "sog": [], "static": row})
        bucket["coords"].append((float(row[schema.lat_col]), float(row[schema.lon_col])))
        bucket["times"].append(_as_epoch(row[schema.time_col]))
        sog = row.get(schema.sog_col) if schema.sog_col else None
        bucket["sog"].append(float(sog) if sog is not None else None)

    tracks = []
    for mmsi, b in by_mmsi.items():
        static = b["static"]
        track = AisTrack(
            mmsi=mmsi,
            coords=b["coords"],
            times=b["times"],
            draught_m=_num(static.get(schema.draught_col)) if schema.draught_col else None,
            ship_type=static.get(schema.ship_type_col) if schema.ship_type_col else None,
        )
        track._sog = b["sog"]  # transient, consumed by clean_tracks
        tracks.append(track)
    return tracks


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_tracks(
    tracks,
    *,
    min_sog_kn: float = 0.5,
    max_gap_s: float = 600.0,
    min_points: int = 5,
    min_length_m: float = 200.0,
    simplify_m: float = 20.0,
) -> list:
    """Split raw tracks into clean voyage segments.

    Drops near-stationary points (``sog < min_sog_kn`` when SOG is present), cuts a new segment at
    time gaps over ``max_gap_s``, discards segments shorter than ``min_points`` / ``min_length_m``,
    and Douglas-Peucker-simplifies each segment by ``simplify_m`` in a local metric frame.
    """
    out = []
    for track in tracks:
        sog = getattr(track, "_sog", [None] * len(track.coords))
        # Build moving-only (coord, time) stream.
        stream = [
            (c, t)
            for c, t, s in zip(track.coords, track.times, sog)
            if s is None or s >= min_sog_kn
        ]
        if len(stream) < 2:
            continue
        # Segment on time gaps.
        segments, current = [], [stream[0]]
        for (c, t), (_pc, pt) in zip(stream[1:], stream[:-1]):
            if t - pt > max_gap_s:
                segments.append(current)
                current = []
            current.append((c, t))
        segments.append(current)

        for seg in segments:
            if len(seg) < min_points:
                continue
            coords = [c for c, _t in seg]
            times = [t for _c, t in seg]
            simplified = _simplify_ll(coords, simplify_m)
            if _length_m(simplified) < min_length_m:
                continue
            out.append(
                AisTrack(
                    mmsi=track.mmsi,
                    coords=simplified,
                    times=[times[0], times[-1]],
                    draught_m=track.draught_m,
                    ship_type=track.ship_type,
                )
            )
    return out


def _projection_for(coords_ll) -> Projection:
    line = LineString([(lon, lat) for lat, lon in coords_ll])
    return Projection.for_geometry(line)


def _simplify_ll(coords_ll, tolerance_m: float) -> list:
    if len(coords_ll) < 3 or tolerance_m <= 0:
        return coords_ll
    proj = _projection_for(coords_ll)
    xy = [proj.forward(lon, lat) for lat, lon in coords_ll]
    simp = LineString(xy).simplify(tolerance_m, preserve_topology=False)
    return [tuple(reversed(proj.inverse(x, y))) for x, y in simp.coords]


def _length_m(coords_ll) -> float:
    if len(coords_ll) < 2:
        return 0.0
    proj = _projection_for(coords_ll)
    return LineString([proj.forward(lon, lat) for lat, lon in coords_ll]).length


def voyage_lines(tracks) -> list:
    """Ground-truth voyages as [(lat, lon)] polylines (one per cleaned segment)."""
    return [list(t.coords) for t in tracks if len(t.coords) >= 2]


def density_surface(tracks, bbox, *, cell_size_m: float = 50.0) -> AisDensity:
    """Rasterize AIS presence into a metric visit-count grid over ``bbox`` (w, s, e, n).

    Each cleaned segment is densified and its cells incremented (counted once per cell per track, so
    a vessel loitering doesn't dominate). The grid + projection feed ``corridor_polygon`` (Phase F).
    """
    west, south, east, north = bbox
    proj = Projection.for_geometry(
        shapely.geometry.box(west, south, east, north)
    )
    cx0, cy0 = proj.forward(west, south)
    cx1, cy1 = proj.forward(east, north)
    x0, y0 = min(cx0, cx1), min(cy0, cy1)
    x1, y1 = max(cx0, cx1), max(cy0, cy1)
    cols = max(1, int(np.ceil((x1 - x0) / cell_size_m)))
    rows = max(1, int(np.ceil((y1 - y0) / cell_size_m)))
    grid = np.zeros((rows, cols), dtype=np.int32)

    for track in tracks:
        seen = set()
        xy = [proj.forward(lon, lat) for lat, lon in track.coords]
        for (xa, ya), (xb, yb) in zip(xy, xy[1:]):
            n = max(1, int(np.hypot(xb - xa, yb - ya) / cell_size_m))
            for k in range(n + 1):
                t = k / n
                col = int((xa + (xb - xa) * t - x0) / cell_size_m)
                row = int((ya + (yb - ya) * t - y0) / cell_size_m)
                if 0 <= row < rows and 0 <= col < cols:
                    seen.add((row, col))
        for row, col in seen:
            grid[row, col] += 1
    return AisDensity(grid=grid, projection=proj, cell_size_m=cell_size_m, x0=x0, y0=y0)


def corridor_polygon(density: AisDensity, *, min_count: int = 2, buffer_m: float = 0.0):
    """Metric shapely polygon of cells visited by >= ``min_count`` tracks (optionally buffered).

    Consumable directly as a soft-cost geometry in the planner's metric frame (Phase F): routes
    *outside* this polygon get penalized so the planner prefers real traffic lanes.
    """
    rows, cols = density.grid.shape
    boxes = []
    cs = density.cell_size_m
    hot = np.argwhere(density.grid >= min_count)
    for row, col in hot:
        x = density.x0 + col * cs
        y = density.y0 + row * cs
        boxes.append(shapely.geometry.box(x, y, x + cs, y + cs))
    if not boxes:
        return shapely.geometry.Polygon()
    poly = shapely.union_all(boxes)
    return poly.buffer(buffer_m) if buffer_m else poly


def corridor_wgs84(density: AisDensity, *, min_count: int = 2, buffer_m: float = 0.0):
    """``corridor_polygon`` projected back to WGS84 — pass to ``build_navigability(ais_corridor=…)``."""
    return density.projection.to_wgs84(corridor_polygon(density, min_count=min_count, buffer_m=buffer_m))


def mine_scenarios(tracks, *, cluster_radius_m: float = 300.0, min_trips: int = 2) -> list:
    """Cluster voyage endpoints into recurring (start, end) pairs + vessel-profile stats.

    Greedy spatial clustering (no sklearn dep): endpoints within ``cluster_radius_m`` collapse to a
    cluster centroid. Returns scenarios seen on >= ``min_trips`` voyages, each:
    ``{start, end, n_trips, draught_p50_m, draught_max_m, ship_types}``.
    """
    if not tracks:
        return []
    proj = _projection_for([c for t in tracks for c in (t.start, t.end)])
    clusters = _greedy_clusters(
        [pt for t in tracks for pt in (t.start, t.end)], proj, cluster_radius_m
    )

    def label(ll):
        return clusters[_nearest_cluster(ll, clusters, proj)]

    grouped: dict = {}
    for t in tracks:
        key = (label(t.start), label(t.end))
        grouped.setdefault(key, []).append(t)

    scenarios = []
    for (start, end), group in grouped.items():
        if len(group) < min_trips or start == end:
            continue
        draughts = [t.draught_m for t in group if t.draught_m is not None]
        scenarios.append(
            {
                "start": start,
                "end": end,
                "n_trips": len(group),
                "draught_p50_m": round(float(np.median(draughts)), 2) if draughts else None,
                "draught_max_m": round(float(np.max(draughts)), 2) if draughts else None,
                "ship_types": sorted({t.ship_type for t in group if t.ship_type}),
            }
        )
    scenarios.sort(key=lambda s: s["n_trips"], reverse=True)
    return scenarios


def _greedy_clusters(points_ll, proj: Projection, radius_m: float) -> list:
    """Collapse points to cluster centroids (greedy, radius-based). Returns [(lat, lon)] centroids."""
    centroids_xy = []
    members = []
    for lat, lon in points_ll:
        x, y = proj.forward(lon, lat)
        for i, (cx, cy) in enumerate(centroids_xy):
            if np.hypot(x - cx, y - cy) <= radius_m:
                members[i].append((x, y))
                centroids_xy[i] = tuple(np.mean(members[i], axis=0))
                break
        else:
            centroids_xy.append((x, y))
            members.append([(x, y)])
    return [tuple(reversed(proj.inverse(x, y))) for x, y in centroids_xy]


def _nearest_cluster(ll, clusters, proj: Projection) -> int:
    x, y = proj.forward(ll[1], ll[0])
    best, best_d = 0, float("inf")
    for i, (clat, clon) in enumerate(clusters):
        cx, cy = proj.forward(clon, clat)
        d = np.hypot(x - cx, y - cy)
        if d < best_d:
            best, best_d = i, d
    return best
