"""Direct PostGIS source (psycopg3, SELECT-only) for bulk corridor reads.

Reads `edits.effective_<layer>` views (public UNION edits overrides/inserts) so user edits and
`resare` no-go inserts are honored, per docs/CHART-ADAPTER.md §4. Never writes `public.*` — this
source only issues SELECTs. Geometry comes back as GeoJSON via `ST_AsGeoJSON`; every non-geometry
column rides along as a jsonb `props` object (`to_jsonb(t) - 'wkb_geometry'`), so the reader is
tolerant of per-layer column differences.
"""

from __future__ import annotations

import logging

from . import layers as L

logger = logging.getLogger(__name__)

# Direct-DB corridor cap (higher than the chart-server HTTP 500 cap; the DB has no nginx limit).
CORRIDOR_CAP = 20000

_KNOWN_TABLES = {table for table, _layer in L.CORRIDOR_TABLES}


def corridor_sql(table: str, cap: int = CORRIDOR_CAP) -> str:
    """Build the bbox SELECT for one layer's `edits.effective_<table>` view.

    Pure (no DB) so it is unit-testable. Params (bound separately) are (west, south, east, north).
    ``table`` must be a known S-57 layer table — validated to keep it injection-safe.
    """
    if table not in _KNOWN_TABLES:
        raise ValueError(f"unknown chart layer table: {table!r}")
    return (
        "SELECT to_jsonb(t) - 'wkb_geometry' AS props, "
        "ST_AsGeoJSON(t.wkb_geometry) AS geometry "
        f"FROM edits.effective_{table} t "
        "WHERE t.wkb_geometry && ST_MakeEnvelope(%s, %s, %s, %s, 4326) "
        f"LIMIT {int(cap)}"
    )


CATALOG_SQL = (
    "SELECT dsnm AS name, edtn, updn AS update_no "
    "FROM public.dsid_versions ORDER BY dsnm"
)


class PostgisSource:
    """Lazily-opened psycopg3 connection pool over the chart DB (read-only usage)."""

    def __init__(self, database_url: str, cap: int = CORRIDOR_CAP) -> None:
        self._url = database_url
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

    def fetch_corridor(self, bbox, tables=L.CORRIDOR_TABLES) -> L.NormalizedChart:
        """Read every layer table intersecting ``bbox`` (west, south, east, north)."""
        west, south, east, north = bbox
        chart = L.NormalizedChart()
        chart.provenance.coverage_bbox = (west, south, east, north)
        with self._get_pool().connection() as conn:
            for table, layer in tables:
                try:
                    with conn.cursor() as cur:
                        cur.execute(corridor_sql(table, self._cap), (west, south, east, north))
                        rows = cur.fetchall()
                except Exception as exc:  # noqa: BLE001 — a missing/empty layer must not abort the pull
                    conn.rollback()
                    logger.warning("corridor read of edits.effective_%s failed: %s", table, exc)
                    continue
                for row in rows:
                    chart.add(L.feature_from_row(layer, row["props"] or {}, row["geometry"]))
        return chart

    def catalog(self) -> list:
        """Return the ingested chart cells (name/edtn/update_no) for provenance, best-effort."""
        try:
            with self._get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(CATALOG_SQL)
                return cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog read failed: %s", exc)
            return []

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
