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
from pyproj import Geod
from shapely.geometry import LineString
from shapely.prepared import prep

from .grid import CostGrid
from .search import search

logger = logging.getLogger(__name__)

_KN_TO_MS = 0.514444
# Output leg distances/bearings are geodesic on WGS84 (lat/lon waypoints); internal geometry is
# planar AEQD metres. The metric grid never sees this.
_GEOD = Geod(ellps="WGS84")

# A straight shortcut is accepted only if it costs no more than this factor over the cell sub-path
# it replaces — removing sub-cell staircase noise (cost-neutral) while keeping cost-reducing turns.
_SHORTCUT_TOL = 1.02

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


def _segment_cells(grid, snapped):
    """A* through each consecutive pair of snapped cells.

    Returns ``(cells, anchors)`` — the concatenated cell path and the indices into it of the via
    junctions (every requested via point) so simplification never string-pulls across them — or
    None if any segment is unreachable.
    """
    cells, anchors = [], []
    n_seg = len(snapped) - 1
    for k, (a, b) in enumerate(zip(snapped, snapped[1:])):
        sub = search(grid, a, b)
        if sub is None:
            return None
        cells.extend(sub if not cells else sub[1:])
        if k < n_seg - 1:  # the join is a via point; the final segment's end is the route end
            anchors.append(len(cells) - 1)
    if not cells:
        return None
    return cells, anchors


def _seg_cost(grid, p, q) -> float:
    """Grid traversal cost of the straight leg p->q (sampled at ~cell resolution).

    ``inf`` if the leg leaves navigable water. Same metric as the cell-path cost (Σ step ·
    ½(cost_a+cost_b)) over ``base_cost`` (penalty-free) so the two are directly comparable.
    """
    dist = math.hypot(q[0] - p[0], q[1] - p[1])
    n = max(1, int(dist / grid.cell_size) + 1)
    base = grid.base_cost
    prev, total = None, 0.0
    for k in range(n + 1):
        t = k / n
        row, col = grid.world_to_cell(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
        if not grid.mask[row, col]:
            return math.inf
        cc = base[row, col]
        if prev is not None:
            total += (dist / n) * 0.5 * (prev + cc)
        prev = cc
    return total


def _path_cost(grid, cells, i, j) -> float:
    """Cell-path cost from index i to j over ``base_cost`` (matches _seg_cost's metric)."""
    base = grid.base_cost
    total = 0.0
    for (r0, c0), (r1, c1) in zip(cells[i:j], cells[i + 1 : j + 1]):
        step = grid.cell_size * math.hypot(r1 - r0, c1 - c0)
        total += step * 0.5 * (base[r0, c0] + base[r1, c1])
    return total


def _postprocess(grid, navigable, cells, anchors=()):
    """Cell path -> minimal metric LineString that preserves the route's cost, not just its shape.

    Cost-aware line-of-sight "string pulling": keep an anchor, then accept the straight shortcut to
    each next candidate only while it (a) stays geometrically inside the navigable polygon — an exact
    check that a corner-clipping leg can't pass even when cell-center sampling would miss the thin
    intrusion — AND (b) costs essentially the same as the cell sub-path it would replace (within
    `_SHORTCUT_TOL`). On a flat cost surface every in-water shortcut is cost-neutral, so straight legs
    collapse to their endpoints; where the path deliberately bows (clearance gradient, soft-cost
    avoidance) a shortcut would raise cost and is rejected, so those turns survive. Replaces the old
    Douglas-Peucker + raw-path back-off that left the grid staircase.
    """
    pts = [grid.cell_to_world(r, c) for r, c in cells]
    if navigable is None or navigable.is_empty or len(pts) < 3:
        return LineString(pts)
    safe = prep(navigable)  # prepared: many cheap exact covers() tests
    forced = set(anchors)  # via junctions: never string-pull across a requested via point
    kept = [pts[0]]
    a = 0
    for i in range(1, len(pts) - 1):
        leg = LineString([pts[a], pts[i + 1]])
        if (
            i in forced
            or not safe.covers(leg)
            or _seg_cost(grid, pts[a], pts[i + 1]) > _path_cost(grid, cells, a, i + 1) * _SHORTCUT_TOL
        ):
            kept.append(pts[i])
            a = i
    kept.append(pts[-1])
    return LineString(kept)


def _build_route(nav, grid, cells, anchors, preferred_speed_kn, snapped_start, snapped_end):
    line_m = _postprocess(grid, nav.navigable, cells, anchors)
    coords_m = list(line_m.coords)
    waypoints = [tuple(reversed(nav.projection.inverse(x, y))) for x, y in coords_m]  # (lat, lon)

    legs, total_m = [], 0.0
    for p0, p1 in zip(waypoints, waypoints[1:]):
        # Geod.inv: forward azimuth + geodesic distance on WGS84 from (lat, lon) p0 -> p1.
        az_fwd, _, seg = _GEOD.inv(p0[1], p0[0], p1[1], p1[0])
        total_m += seg
        legs.append({"distance_m": round(seg, 1), "bearing_deg": round(az_fwd % 360.0, 1)})

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
        result = _segment_cells(grid, snapped)
        if result is None or len(result[0]) < 2:
            break
        cells, anchors = result
        score = grid.path_base_cost(cells)
        route = _build_route(nav, grid, cells, anchors, preferred_speed_kn, moved[0], moved[-1])
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
