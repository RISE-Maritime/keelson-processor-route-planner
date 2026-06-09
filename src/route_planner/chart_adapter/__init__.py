"""Chart adapter — fetch + normalize S-57 features. The ONLY module that touches the chart server.

Two access paths (docs/CHART-ADAPTER.md):
- **bulk** corridor reads → direct PostGIS (psycopg3, SELECT-only over `edits.effective_<layer>`);
- **targeted** checks (identify / depth / grounding-line / catalog) → chart-server HTTP (httpx).

Output is a `NormalizedChart` of Shapely geometries grouped by internal layer, for the navigability
engine (P3). NO planning logic. Never writes `public.*`. Construction does no I/O (the DB pool and
HTTP client connect lazily), so a `ChartAdapter` is safe to build at startup and call from the sync
`plan_route` zenoh handler (P4).

Bounding boxes are ``(west, south, east, north)`` (min_lon, min_lat, max_lon, max_lat).
"""

from __future__ import annotations

import logging

from .http import ChartServerHttp
from .layers import (
    CORRIDOR_TABLES,
    ChartFeature,
    DepthInfo,
    NormalizedChart,
    Provenance,
)
from .postgis import PostgisSource

logger = logging.getLogger(__name__)

# chart-server HTTP per-query feature cap (mirrors chart-server FEATURE_CAP); used to decide when an
# HTTP corridor tile must be subdivided.
HTTP_FEATURE_CAP = 500

__all__ = [
    "ChartAdapter",
    "ChartFeature",
    "DepthInfo",
    "NormalizedChart",
    "Provenance",
]


def _quadrants(bbox):
    west, south, east, north = bbox
    mid_lon = (west + east) / 2
    mid_lat = (south + north) / 2
    return (
        (west, south, mid_lon, mid_lat),
        (mid_lon, south, east, mid_lat),
        (west, mid_lat, mid_lon, north),
        (mid_lon, mid_lat, east, north),
    )


class ChartAdapter:
    """Facade over the PostGIS + HTTP chart sources, built from the runtime config."""

    def __init__(self, conf: dict) -> None:
        database_url = conf.get("database_url")
        self._db = PostgisSource(database_url) if database_url else None
        self._http = ChartServerHttp(conf.get("chart_server_url") or "http://localhost:8080")

    # --- bulk -----------------------------------------------------------------

    def fetch_corridor(self, bbox, tables=CORRIDOR_TABLES) -> NormalizedChart:
        """Normalized chart features over a corridor bbox. Direct PostGIS if configured, else HTTP."""
        if self._db is not None:
            return self._db.fetch_corridor(bbox, tables)
        return self._http_corridor(bbox)

    def _http_corridor(self, bbox, max_depth: int = 3) -> NormalizedChart:
        """HTTP fallback: /api/bbox with quadtree subdivision to beat the 500-feature cap."""
        chart = NormalizedChart()
        chart.provenance.coverage_bbox = tuple(bbox)
        seen = set()

        def visit(tile, depth):
            features = self._http.bbox(tile)
            if len(features) >= HTTP_FEATURE_CAP and depth < max_depth:
                for quad in _quadrants(tile):
                    visit(quad, depth + 1)
                return
            for feature in features:
                key = (feature.layer, feature.lnam)
                if feature.lnam is not None and key in seen:
                    continue
                seen.add(key)
                chart.add(feature)

        visit(tuple(bbox), 0)
        return chart

    # --- targeted -------------------------------------------------------------

    def identify(self, lat: float, lon: float, radius: float = 50.0) -> list:
        return self._http.identify(lat, lon, radius)

    def depth_at(self, lat: float, lon: float) -> DepthInfo:
        return self._http.depth(lat, lon)

    def grounding_line(self, lat: float, lon: float, draft: float, ukc: float = 0.5) -> dict:
        return self._http.grounding_line(lat, lon, draft, ukc)

    def catalog(self) -> Provenance:
        """Chart provenance (contributing cells + coverage bbox) from /api/catalog."""
        payload = self._http.catalog()
        bbox = payload.get("coverage_bbox")
        return Provenance(
            cells={c.get("name") for c in (payload.get("cells") or []) if c.get("name")},
            coverage_bbox=tuple(bbox) if bbox else None,
        )

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
        self._http.close()
