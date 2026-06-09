"""Pathfinding — A* over the navigability cost-surface, producing route alternatives.

`plan(nav, start, end, via, optimization_goal, n_alternatives)` discretizes the P3 navigable polygon
into a metric `CostGrid`, snaps the endpoints onto navigable water, runs A* (Dijkstra fallback)
through each start→via→…→end segment, post-processes the cell path into waypoints, and produces N
diverse alternatives by penalizing the corridors of already-found routes. Output is the **internal**
`PlannedRoute` (waypoints in (lat, lon) + metrics + cost breakdown); serialization to `keelson.Route`
is P5. Pure numpy/shapely — no zenoh, no I/O.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import LineString

from .grid import CostGrid
from .search import search

logger = logging.getLogger(__name__)

_KN_TO_MS = 0.514444

__all__ = ["PlannedRoute", "plan", "CostGrid"]


@dataclass
class PlannedRoute:
    """One candidate route. Geometry is (lat, lon) waypoints; the canonical artifact (keelson.Route) is P5."""

    waypoints: list  # list[(lat, lon)]
    legs: list = field(default_factory=list)  # [{distance_m, bearing_deg}]
    metrics: dict = field(default_factory=dict)
    cost_breakdown: dict = field(default_factory=dict)
    snapped_start: bool = False
    snapped_end: bool = False
    optimization_goal: str = ""
    status_hint: str = "PLANNED"


def _bearing_deg(p0, p1) -> float:
    """Initial great-circle bearing from (lat, lon) p0 to p1, degrees [0, 360)."""
    lat0, lon0, lat1, lon1 = map(math.radians, (p0[0], p0[1], p1[0], p1[1]))
    d_lon = lon1 - lon0
    y = math.sin(d_lon) * math.cos(lat1)
    x = math.cos(lat0) * math.sin(lat1) - math.sin(lat0) * math.cos(lat1) * math.cos(d_lon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _segment_cells(grid, snapped):
    """A* through each consecutive pair of snapped cells; concatenated cell path or None."""
    cells = []
    for a, b in zip(snapped, snapped[1:]):
        sub = search(grid, a, b)
        if sub is None:
            return None
        cells.extend(sub if not cells else sub[1:])
    return cells or None


def _postprocess(grid, navigable, cells):
    """Cell path -> simplified metric LineString that stays within navigable water."""
    pts = [grid.cell_to_world(r, c) for r, c in cells]
    line = LineString(pts)
    simplified = line.simplify(grid.cell_size)
    # Don't let a shortcut leave the water; back off the tolerance, else keep the raw cell path.
    if navigable is not None and not navigable.buffer(grid.cell_size).covers(simplified):
        simplified = line.simplify(grid.cell_size * 0.3)
        if not navigable.buffer(grid.cell_size).covers(simplified):
            simplified = line
    return simplified


def _build_route(nav, grid, cells, preferred_speed_kn, snapped_start, snapped_end):
    line_m = _postprocess(grid, nav.navigable, cells)
    coords_m = list(line_m.coords)
    waypoints = [tuple(reversed(nav.projection.inverse(x, y))) for x, y in coords_m]  # (lat, lon)

    legs, total_m = [], 0.0
    for (x0, y0), (x1, y1), p0, p1 in zip(coords_m, coords_m[1:], waypoints, waypoints[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        total_m += seg
        legs.append({"distance_m": round(seg, 1), "bearing_deg": round(_bearing_deg(p0, p1), 1)})

    metrics = {"distance_m": round(total_m, 1), "n_waypoints": len(waypoints)}
    if preferred_speed_kn:
        metrics["est_time_s"] = round(total_m / (preferred_speed_kn * _KN_TO_MS), 1)
    return PlannedRoute(
        waypoints=waypoints,
        legs=legs,
        metrics=metrics,
        cost_breakdown=_soft_exposure(nav, line_m),
        snapped_start=snapped_start,
        snapped_end=snapped_end,
    )


def _soft_exposure(nav, line_m) -> dict:
    """Approx. metres of the route exposed to each soft-cost kind (densified sampling)."""
    if line_m.length <= 0:
        return {}
    step = max(line_m.length / 200.0, 1.0)
    n = int(line_m.length / step) + 1
    pts = [line_m.interpolate(min(i * step, line_m.length)) for i in range(n + 1)]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    out: dict = {}
    for sc in nav.soft_costs:
        if sc.geometry is None or sc.geometry.is_empty:
            continue
        inside = shapely.contains_xy(sc.geometry, xs, ys)
        metres = float(inside.sum()) * step
        if metres > 0:
            out[sc.kind] = round(out.get(sc.kind, 0.0) + metres, 1)
    return out


def plan(
    nav,
    start,
    end,
    via=(),
    optimization_goal: str = "shortest",
    n_alternatives: int = 1,
    preferred_speed_kn: float | None = None,
) -> list:
    """Plan up to ``n_alternatives`` routes start→via→…→end over the P3 navigability result.

    ``start``/``end``/``via`` are (lat, lon). Returns PlannedRoutes ordered best-first (best is
    ``status_hint="RECOMMENDED"``); empty if no navigable path exists.
    """
    grid = CostGrid.from_navigability(nav, optimization_goal)
    points_ll = [start, *via, end]

    snapped, moved = [], []
    for lat, lon in points_ll:
        x, y = nav.projection.forward(lon, lat)
        cell = grid.snap(x, y)
        if cell is None:
            logger.warning("plan: no navigable water to snap (%.5f, %.5f) to", lat, lon)
            return []
        snapped.append(cell)
        moved.append(grid.world_to_cell(x, y) != cell)

    candidates = []
    for _k in range(max(1, n_alternatives)):
        cells = _segment_cells(grid, snapped)
        if cells is None or len(cells) < 2:
            break
        score = grid.path_base_cost(cells)
        route = _build_route(nav, grid, cells, preferred_speed_kn, moved[0], moved[-1])
        route.optimization_goal = optimization_goal
        if not _too_similar(route, candidates):
            candidates.append((score, route))
        grid.add_penalty(cells)  # push the next alternative away from this corridor
        if len(candidates) >= n_alternatives:
            break

    candidates.sort(key=lambda sr: sr[0])
    routes = [route for _score, route in candidates]
    if routes:
        routes[0].status_hint = "RECOMMENDED"
    return routes


def _too_similar(route, candidates) -> bool:
    if not route.waypoints or len(route.waypoints) < 2:
        return True
    line = LineString([(lon, lat) for lat, lon in route.waypoints])
    for _score, other in candidates:
        other_line = LineString([(lon, lat) for lat, lon in other.waypoints])
        # ~110 m in degrees at these latitudes; routes closer than this are near-duplicates.
        if line.hausdorff_distance(other_line) < 0.001:
            return True
    return False
