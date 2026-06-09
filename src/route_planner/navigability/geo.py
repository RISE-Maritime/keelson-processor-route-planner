"""Projection + metric geometry helpers (pure, no I/O).

The navigability engine works in a **local metric CRS** so margins/clearances/areas are in metres
(you cannot buffer metres on EPSG:4326 degrees). ``Projection`` maps WGS84 <-> a local
azimuthal-equidistant CRS centred on the corridor; the engine projects once, does all Shapely ops in
metres, and hands the projection downstream (P4 grid + final waypoint export back to lat/lon).
"""

from __future__ import annotations

from pyproj import Transformer
from shapely import force_2d, make_valid
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

WGS84 = "EPSG:4326"


class Projection:
    """WGS84 <-> local azimuthal-equidistant (metres) centred on (lat_0, lon_0)."""

    def __init__(self, lat_0: float, lon_0: float) -> None:
        self.lat_0 = lat_0
        self.lon_0 = lon_0
        crs = f"+proj=aeqd +lat_0={lat_0} +lon_0={lon_0} +units=m +datum=WGS84 +no_defs"
        # always_xy: coordinates are (lon, lat) / (x, y) order, matching Shapely.
        self._fwd = Transformer.from_crs(WGS84, crs, always_xy=True)
        self._inv = Transformer.from_crs(crs, WGS84, always_xy=True)

    @classmethod
    def for_geometry(cls, geom_wgs84: BaseGeometry) -> Projection:
        """Build a projection centred on a WGS84 geometry's centroid."""
        c = geom_wgs84.centroid
        return cls(lat_0=c.y, lon_0=c.x)

    def forward(self, lon: float, lat: float) -> tuple[float, float]:
        return self._fwd.transform(lon, lat)

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        return self._inv.transform(x, y)

    def to_metric(self, geom: BaseGeometry) -> BaseGeometry:
        # *coords tolerates a Z ordinate (e.g. 3D soundings); we project in 2D.
        return transform(lambda *c: self._fwd.transform(c[0], c[1]), geom)

    def to_wgs84(self, geom: BaseGeometry) -> BaseGeometry:
        return transform(lambda *c: self._inv.transform(c[0], c[1]), geom)


def clean(geom: BaseGeometry | None) -> BaseGeometry | None:
    """Repair an invalid geometry (self-intersections etc.). Returns None for empty/None."""
    if geom is None or geom.is_empty:
        return None
    geom = force_2d(geom)  # normalize away any Z (3D soundings etc.) so 2D ops stay consistent
    if not geom.is_valid:
        geom = make_valid(geom)
    return geom if not geom.is_empty else None


def safe_union(geoms) -> BaseGeometry | None:
    """Union an iterable of geometries, repairing invalid inputs first. None if nothing valid."""
    cleaned = [g for g in (clean(g) for g in geoms if g is not None) if g is not None]
    if not cleaned:
        return None
    return clean(unary_union(cleaned))


def buffer_m(geom: BaseGeometry | None, metres: float) -> BaseGeometry | None:
    """Buffer a metric-CRS geometry by ``metres`` (no-op for None / non-positive distance)."""
    if geom is None:
        return None
    if metres <= 0:
        return geom
    return clean(geom.buffer(metres))
