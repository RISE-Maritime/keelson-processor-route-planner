"""Navigability engine — navigable/blocked geometry + a weighted soft-cost surface.

Turns a P2 `NormalizedChart` + vessel/mode `NavParams` + no-go polygons into the geometry the
pathfinder (P4) searches: a `navigable` polygon, the `blocked` union, weighted `SoftCost` regions,
chart evidence + data-quality warnings. Pure Shapely/pyproj — no zenoh, no I/O.

All output geometry is in the result's **local metric CRS** (`result.projection`); P4 grids it and
converts final waypoints back to WGS84. Hard constraints are enforced here (`navigable = corridor -
blocked`), so a route confined to `navigable` never crosses a hard-block.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from ..chart_adapter import layers as L
from . import geo, rules
from .rules import NavParams, nav_params_from_profile  # noqa: F401 (re-export)

logger = logging.getLogger(__name__)

# Soft-cost weights (relative penalties; the pathfinder scales them by optimization_goal in P4).
_WEIGHT = {
    "shallow_but_legal": 2.0,
    "outside_fairway": 1.5,
    "near_land_margin": 1.0,
    "poor_chart_quality": 1.0,
    "restricted_soft": 3.0,
    "unknown_depth": 4.0,
}

__all__ = [
    "SoftCost",
    "NavigabilityResult",
    "NavParams",
    "build_navigability",
    "nav_params_from_profile",
]


@dataclass
class SoftCost:
    """A penalized-but-legal region (metric CRS) the pathfinder adds to its cost field."""

    kind: str
    geometry: BaseGeometry
    weight: float


@dataclass
class NavigabilityResult:
    """Navigable/blocked geometry + soft-costs + evidence (geometry in `projection`'s metric CRS)."""

    navigable: BaseGeometry
    blocked: BaseGeometry
    soft_costs: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    projection: geo.Projection | None = None
    params: NavParams | None = None
    corridor: BaseGeometry | None = None


def _empty() -> BaseGeometry:
    return Polygon()


def _or_empty(geom: BaseGeometry | None) -> BaseGeometry:
    return geom if geom is not None else _empty()


def _corridor_wgs84(chart: L.NormalizedChart, corridor) -> BaseGeometry:
    if corridor is not None:
        return corridor
    bbox = chart.provenance.coverage_bbox
    if bbox:
        west, south, east, north = bbox
        return box(west, south, east, north)
    union = geo.safe_union([f.geometry for f in chart.all_features()])
    return union if union is not None else box(-180, -90, 180, 90)


def build_navigability(
    chart: L.NormalizedChart, params: NavParams, corridor=None, no_go=()
) -> NavigabilityResult:
    """Compute navigable/blocked geometry + soft-costs for a corridor. See module docstring."""
    corridor_wgs84 = geo.clean(_corridor_wgs84(chart, corridor))
    proj = geo.Projection.for_geometry(corridor_wgs84)
    corridor_m = geo.clean(proj.to_metric(corridor_wgs84))
    conservative = params.block_unknown_depth
    warnings: list = []

    # Project every feature ONCE, clipped to the corridor in WGS84 first. The PostGIS `&&` filter
    # returns features whose bbox merely overlaps the corridor, so their geometry can extend far
    # outside it; clipping keeps blocked/navigable bounded (and avoids AEQD blow-up on far vertices).
    projected = []  # list[(feature, metric_geometry)]
    for feature in chart.all_features():
        g = geo.clean(feature.geometry)
        if g is None:
            continue
        clipped = geo.clean(g.intersection(corridor_wgs84))
        if clipped is None:
            continue
        gm = geo.clean(proj.to_metric(clipped))
        if gm is not None:
            projected.append((feature, gm))

    def of_layer(*names):
        wanted = set(names)
        return [(f, g) for f, g in projected if f.layer in wanted]

    land = of_layer(L.LAND)
    depare = of_layer(L.DEPTH_AREAS)
    dredged = of_layer(L.DREDGED_AREAS)
    hazards = of_layer(L.ROCKS, L.WRECKS, L.OBSTRUCTIONS)
    restricted = of_layer(L.RESTRICTED_AREAS)
    fairways = of_layer(L.FAIRWAYS)

    def unsafe_depth(feature):
        return feature.drval1 is not None and feature.drval1 < params.safety_depth_m

    def unsafe_hazard(feature):
        return rules.is_unsafe_hazard(feature, params.safety_depth_m, conservative)

    def blocking_restr(feature):
        return bool(rules.parse_restrn(feature.restrn) & rules.BLOCKING_RESTRN)

    # A corridor can span several ENC cells at different scales whose depth-areas/land OVERLAP (a
    # coarse cell marks as shallow/land what a fine cell charts as deep water). Resolve that
    # finest-scale-wins so coarse coverage never overrides finer detail. Charted depth areas already
    # exclude real islands (holes in DEPARE), so navigable water is taken FROM deep areas (positive
    # water) rather than by subtracting land from the corridor.
    deep_water, shallow, land_union = _resolve_by_scale(depare, dredged, land, params.safety_depth_m)
    land_block = geo.buffer_m(land_union, params.route_margin_m)
    all_charted = geo.safe_union([deep_water, shallow])
    if all_charted is None:
        warnings.append("no depth-area (depare) coverage; navigable derived from corridor minus land")
    hazard_block = geo.buffer_m(
        geo.safe_union([g for f, g in hazards if unsafe_hazard(f)]), params.hazard_buffer_m
    )
    restricted_block = geo.safe_union([g for f, g in restricted if blocking_restr(f)])
    nogo_union = geo.safe_union([proj.to_metric(g) for g in no_go]) if no_go else None
    common_blocks = geo.safe_union([shallow, hazard_block, restricted_block, nogo_union])

    # Unknown-depth = corridor minus any charted depth area minus land.
    unknown = corridor_m
    for sub in (all_charted, land_union):
        if sub is not None and unknown is not None:
            unknown = geo.clean(unknown.difference(sub))

    def _minus(base, *subs):
        for sub in subs:
            if base is None:
                return None
            if sub is not None:
                base = geo.clean(base.difference(sub))
        return base

    # Deep charted water is navigable (land NOT subtracted — see above); minus the hard-blocks.
    deep_nav = _minus(geo.clean(_or_empty(deep_water).intersection(_or_empty(corridor_m))), common_blocks)
    if conservative:
        navigable = deep_nav
    else:
        # Outside conservative mode, uncharted water is navigable-but-penalized; land DOES block here.
        unknown_nav = _minus(unknown, land_block, common_blocks)
        navigable = geo.safe_union([deep_nav, unknown_nav])
    navigable = _or_empty(navigable)
    if navigable.is_empty:
        warnings.append("navigable area is empty for this corridor + vessel profile")

    # Blocked = the corridor complement of navigable (land, shallow, hazards, restricted, no-go, …).
    blocked = geo.clean(_or_empty(corridor_m).difference(navigable))

    soft_costs = _soft_costs(
        params,
        navigable,
        depare=depare,
        fairways=fairways,
        land_union=land_union,
        projected=projected,
        restricted=restricted,
        unknown=None if conservative else unknown,
    )

    evidence = {
        "provenance": chart.provenance,
        "hard_block_layers": {
            "land": len(land),
            "shallow_depth_areas": sum(1 for f, _g in depare if unsafe_depth(f)),
            "hazards": sum(1 for f, _g in hazards if unsafe_hazard(f)),
            "restricted": sum(1 for f, _g in restricted if blocking_restr(f)),
            "no_go": len(no_go),
        },
    }
    return NavigabilityResult(
        navigable=navigable,
        blocked=_or_empty(blocked),
        soft_costs=soft_costs,
        evidence=evidence,
        warnings=warnings,
        projection=proj,
        params=params,
        corridor=corridor_m,
    )


def _resolve_by_scale(depare, dredged, land, safety_depth):
    """Resolve overlapping multi-scale depth-areas/land finest-scale-wins.

    Returns (deep_water, shallow, land) unions where finer cells (smaller chart_scale denominator)
    claim their area first, so a coarse cell never overrides finer detail. Features with no
    chart_scale are treated as coarsest (claim only what finer cells leave). Returns metric unions
    (or None).
    """
    items = []  # (scale, class, geom_metric)
    for feature, geom in depare:
        if feature.drval1 is not None and feature.drval1 >= safety_depth:
            cls = "deep"
        else:
            cls = "shallow"  # too-shallow or unknown-depth depth-areas are cautiously non-navigable
        items.append((feature.chart_scale or float("inf"), cls, geom))
    for _feature, geom in dredged:
        items.append((_feature.chart_scale or float("inf"), "deep", geom))
    for feature, geom in land:
        items.append((feature.chart_scale or float("inf"), "land", geom))

    parts = {"deep": [], "shallow": [], "land": []}
    claimed = None
    for scale in sorted({s for s, _c, _g in items}):
        group = [(cls, geom) for s, cls, geom in items if s == scale]
        group_area = geo.safe_union([geom for _c, geom in group])
        if group_area is None:
            continue
        avail = group_area if claimed is None else geo.clean(group_area.difference(claimed))
        if avail is not None and not avail.is_empty:
            for cls in parts:
                cls_geom = geo.safe_union([geom for c, geom in group if c == cls])
                if cls_geom is not None:
                    inter = geo.clean(cls_geom.intersection(avail))
                    if inter is not None:
                        parts[cls].append(inter)
        claimed = geo.safe_union([claimed, group_area])
    return (
        geo.safe_union(parts["deep"]),
        geo.safe_union(parts["shallow"]),
        geo.safe_union(parts["land"]),
    )


def _soft_costs(params, navigable, depare, fairways, land_union, projected, restricted, unknown):
    """Build the geometry-derivable soft-cost regions, each clipped to `navigable`."""
    costs: list = []
    if navigable.is_empty:
        return costs

    def add(kind, region):
        if region is None or region.is_empty:
            return
        clipped = geo.clean(region.intersection(navigable))
        if clipped is not None and not clipped.is_empty:
            costs.append(SoftCost(kind=kind, geometry=clipped, weight=_WEIGHT[kind]))

    add(
        "shallow_but_legal",
        geo.safe_union(
            [
                g
                for f, g in depare
                if f.drval1 is not None
                and params.safety_depth_m <= f.drval1 < params.comfortable_depth_m
            ]
        ),
    )
    if params.prefer_fairways:
        fairway_union = geo.safe_union([g for _f, g in fairways])
        if fairway_union is not None:
            add("outside_fairway", geo.clean(navigable.difference(fairway_union)))

    if land_union is not None:
        outer = geo.buffer_m(land_union, params.route_margin_m * 2)
        inner = geo.buffer_m(land_union, params.route_margin_m)
        if outer is not None and inner is not None:
            add("near_land_margin", geo.clean(outer.difference(inner)))

    poor = geo.safe_union(
        [
            g
            for f, g in projected
            if f.chart_scale is not None and f.chart_scale > params.poor_chart_scale_threshold
        ]
    )
    add("poor_chart_quality", poor)
    add(
        "restricted_soft",
        geo.safe_union([g for f, g in restricted if rules.parse_restrn(f.restrn) & rules.SOFT_RESTRN]),
    )
    add("unknown_depth", unknown)
    return costs
