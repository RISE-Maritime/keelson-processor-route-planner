"""Internal chart types + S-57 normalization (pure, IO-free, unit-testable).

The navigability engine (P3) consumes Shapely geometries grouped by internal layer, not raw S-57.
This module defines those types and the mapping from S-57 (chart-server `objclass` / PostGIS table)
to the internal layers listed in docs/CHART-ADAPTER.md §6. No I/O, no zenoh, no DB — just parsing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

# Internal layer names (docs/CHART-ADAPTER.md §6).
LAND = "land"
DEPTH_AREAS = "depth_areas"
DEPTH_CONTOURS = "depth_contours"
SOUNDINGS = "soundings"
ROCKS = "rocks"
WRECKS = "wrecks"
OBSTRUCTIONS = "obstructions"
RESTRICTED_AREAS = "restricted_areas"
CAUTION_AREAS = "caution_areas"
ANCHORAGES = "anchorages"
FAIRWAYS = "fairways"
DREDGED_AREAS = "dredged_areas"
TSS = "traffic_separation"
BUILT_UP = "built_up"

# S-57 table/objclass -> internal layer. The corridor bulk read iterates these tables (as
# edits.effective_<table>); the HTTP path maps each feature's `objclass` through the same dict.
# Order matters only for deterministic iteration.
CORRIDOR_TABLES: tuple[tuple[str, str], ...] = (
    ("lndare", LAND),
    ("coalne", LAND),
    ("depare", DEPTH_AREAS),
    ("depcnt", DEPTH_CONTOURS),
    ("soundg", SOUNDINGS),
    ("uwtroc", ROCKS),
    ("wrecks", WRECKS),
    ("obstrn", OBSTRUCTIONS),
    ("resare", RESTRICTED_AREAS),
    ("ctnare", CAUTION_AREAS),
    ("achare", ANCHORAGES),
    ("fairwy", FAIRWAYS),
    ("drgare", DREDGED_AREAS),
    ("tsslpt", TSS),
    ("tselne", TSS),
    ("tssbnd", TSS),
    ("buaare", BUILT_UP),
)

OBJCLASS_TO_LAYER: dict[str, str] = {table: layer for table, layer in CORRIDOR_TABLES}

# Depth-ish / classifying attributes the navigability engine reads off a feature.
_NUMERIC_ATTRS = ("drval1", "drval2", "valdco", "valsou", "depth", "watlev", "chart_scale", "cscl")


def _num(props: dict, key: str):
    """Return props[key] as float, or None if absent/empty/non-numeric."""
    value = props.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_geometry(geom) -> BaseGeometry | None:
    """Parse a GeoJSON geometry (dict or JSON string) into a Shapely geometry."""
    if geom is None:
        return None
    if isinstance(geom, str):
        geom = json.loads(geom)
    if not geom:
        return None
    return shape(geom)


@dataclass
class ChartFeature:
    """One normalized S-57 feature: a Shapely geometry (EPSG:4326) + its attributes + provenance."""

    layer: str
    geometry: BaseGeometry | None
    attrs: dict = field(default_factory=dict)

    @property
    def objnam(self):
        return self.attrs.get("objnam")

    @property
    def lnam(self):
        return self.attrs.get("lnam")

    @property
    def source_cell(self):
        # ingest stamps the source cell under one of these (best-effort).
        return self.attrs.get("source_chart") or self.attrs.get("dsnm")

    @property
    def chart_scale(self):
        return _num(self.attrs, "chart_scale") or _num(self.attrs, "cscl")

    @property
    def drval1(self):
        return _num(self.attrs, "drval1")

    @property
    def drval2(self):
        return _num(self.attrs, "drval2")

    @property
    def valdco(self):
        return _num(self.attrs, "valdco")

    @property
    def valsou(self):
        return _num(self.attrs, "valsou")

    @property
    def depth(self):
        return _num(self.attrs, "depth")

    @property
    def restrn(self):
        return self.attrs.get("restrn")


@dataclass
class Provenance:
    """Chart provenance accumulated over a fetch (cells touched, coverage, worst scale)."""

    cells: set = field(default_factory=set)
    coverage_bbox: tuple | None = None  # (west, south, east, north)
    worst_chart_scale: float | None = None  # coarsest = largest scale denominator

    def observe(self, feature: ChartFeature) -> None:
        if feature.source_cell:
            self.cells.add(feature.source_cell)
        scale = feature.chart_scale
        if scale is not None:
            self.worst_chart_scale = (
                scale if self.worst_chart_scale is None else max(self.worst_chart_scale, scale)
            )


@dataclass
class NormalizedChart:
    """Normalized S-57 features for a region, grouped by internal layer, with provenance."""

    by_layer: dict = field(default_factory=dict)
    provenance: Provenance = field(default_factory=Provenance)

    def add(self, feature: ChartFeature) -> None:
        if feature.geometry is None:
            return
        self.by_layer.setdefault(feature.layer, []).append(feature)
        self.provenance.observe(feature)

    def layer(self, name: str) -> list:
        return self.by_layer.get(name, [])

    def all_features(self) -> list:
        return [f for features in self.by_layer.values() for f in features]

    def count(self) -> int:
        return sum(len(features) for features in self.by_layer.values())


@dataclass
class DepthInfo:
    """Normalized /api/depth result for a single point."""

    on_land: bool = False
    drval1: float | None = None  # depth-area shallow bound
    drval2: float | None = None  # depth-area deep bound
    nearest_sounding_m: float | None = None
    nearest_sounding_distance_m: float | None = None

    @classmethod
    def from_api(cls, payload: dict) -> DepthInfo:
        area = payload.get("depthArea") or {}
        sounding = payload.get("nearestSounding") or {}
        return cls(
            on_land=bool(payload.get("onLand")),
            drval1=_num(area, "drval1"),
            drval2=_num(area, "drval2"),
            nearest_sounding_m=_num(sounding, "depth"),
            nearest_sounding_distance_m=_num(sounding, "distance_m"),
        )


def feature_from_geojson(gj: dict) -> ChartFeature:
    """Build a ChartFeature from a chart-server GeoJSON Feature (layer from its `objclass`).

    chart-server returns `objclass` as an UPPERCASE S-57 acronym (e.g. "DEPARE"); match it
    case-insensitively against the (lowercase) table-name keys.
    """
    props = dict(gj.get("properties") or {})
    raw = props.get("objclass")
    key = raw.lower() if isinstance(raw, str) else None
    layer = OBJCLASS_TO_LAYER.get(key, key or "unknown")
    return ChartFeature(layer=layer, geometry=parse_geometry(gj.get("geometry")), attrs=props)


def feature_from_row(layer: str, props: dict, geometry) -> ChartFeature:
    """Build a ChartFeature from a PostGIS row (props dict + GeoJSON geometry str/dict)."""
    return ChartFeature(layer=layer, geometry=parse_geometry(geometry), attrs=dict(props))
