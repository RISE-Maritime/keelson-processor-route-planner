"""Route-vs-route similarity metrics for benchmarking the planner against ground truth.

Given a generated route and a reference route (both as ``[(lat, lon)]`` waypoints), compute how
close they are: discrete **Fréchet** distance, **Hausdorff** distance, **cross-track deviation**
(how far the generated route strays from the reference centreline), and length/waypoint/turn
deltas. All distances are metres, computed in a shared local AEQD frame (reusing
``navigability.geo.Projection`` so the numbers match the rest of the pipeline).

Pure numpy/shapely/pyproj — no zenoh, no keelson. The validation delta (issues/challenges) is a
thin duck-typed helper that reads already-validated ``keelson.Route`` objects without importing the
SDK; the runner supplies them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from shapely.geometry import LineString, Point

from ..navigability.geo import Projection

# Matches validation's sharp-turn threshold so "turn count" means the same thing in both places.
_TURN_THRESHOLD_DEG = 60.0
# Densify step (m) before discrete Fréchet / cross-track sampling — small enough to approximate the
# continuous measures, large enough to keep the Fréchet DP cheap for ~20 km routes.
_SAMPLE_STEP_M = 25.0

__all__ = [
    "RouteComparison",
    "compare_routes",
    "discrete_frechet_m",
    "cross_track_stats_m",
    "turn_count",
    "diff_validation",
]


@dataclass
class RouteComparison:
    """Similarity of a generated route to a reference route. All *_m fields are metres."""

    frechet_m: float
    hausdorff_m: float
    xtd_mean_m: float
    xtd_median_m: float
    xtd_p95_m: float
    xtd_max_m: float
    length_generated_m: float
    length_reference_m: float
    length_ratio: float  # generated / reference
    n_waypoints_generated: int
    n_waypoints_reference: int
    turn_count_generated: int
    turn_count_reference: int

    def to_dict(self) -> dict:
        return asdict(self)


def _coords_ll_to_xy(waypoints, projection: Projection) -> np.ndarray:
    """Project [(lat, lon)] -> metric (x, y) array."""
    return np.array([projection.forward(lon, lat) for lat, lon in waypoints], dtype=float)


def _densify(xy: np.ndarray, step: float) -> np.ndarray:
    """Resample a polyline (metric coords) to ~``step`` spacing, keeping vertices' shape."""
    if len(xy) < 2:
        return xy
    seg = np.diff(xy, axis=0)
    seg_len = np.hypot(seg[:, 0], seg[:, 1])
    out = [xy[0]]
    for i, length in enumerate(seg_len):
        if length <= 0:
            continue
        n = max(1, int(np.ceil(length / step)))
        for k in range(1, n + 1):
            out.append(xy[i] + seg[i] * (k / n))
    return np.asarray(out)


def discrete_frechet_m(a_xy: np.ndarray, b_xy: np.ndarray) -> float:
    """Discrete Fréchet distance (Eiter & Mannila) between two metric polylines, in metres.

    Inputs are densified first so this approximates the continuous Fréchet distance. Iterative DP
    with a rolling row — O(n·m) over the densified point counts.
    """
    p = _densify(np.asarray(a_xy, float), _SAMPLE_STEP_M)
    q = _densify(np.asarray(b_xy, float), _SAMPLE_STEP_M)
    n, m = len(p), len(q)
    if n == 0 or m == 0:
        return float("inf")

    prev = np.empty(m)
    for i in range(n):
        d_i = np.hypot(p[i, 0] - q[:, 0], p[i, 1] - q[:, 1])
        cur = np.empty(m)
        for j in range(m):
            if i == 0 and j == 0:
                cur[j] = d_i[0]
            elif i == 0:
                cur[j] = max(cur[j - 1], d_i[j])
            elif j == 0:
                cur[j] = max(prev[0], d_i[0])
            else:
                cur[j] = max(min(prev[j], prev[j - 1], cur[j - 1]), d_i[j])
        prev = cur
    return float(prev[-1])


def cross_track_stats_m(route_xy: np.ndarray, reference_xy: np.ndarray) -> dict:
    """Distance from each (densified) point of the route to the reference centreline, in metres."""
    ref_line = LineString(reference_xy)
    pts = _densify(np.asarray(route_xy, float), _SAMPLE_STEP_M)
    devs = np.array([ref_line.distance(Point(x, y)) for x, y in pts])
    return {
        "xtd_mean_m": float(devs.mean()),
        "xtd_median_m": float(np.median(devs)),
        "xtd_p95_m": float(np.percentile(devs, 95)),
        "xtd_max_m": float(devs.max()),
    }


def turn_count(xy: np.ndarray, threshold_deg: float = _TURN_THRESHOLD_DEG) -> int:
    """Number of interior vertices whose heading changes by more than ``threshold_deg``."""
    xy = np.asarray(xy, float)
    if len(xy) < 3:
        return 0
    v = np.diff(xy, axis=0)
    headings = np.degrees(np.arctan2(v[:, 1], v[:, 0]))
    delta = np.abs((np.diff(headings) + 180.0) % 360.0 - 180.0)
    return int((delta > threshold_deg).sum())


def compare_routes(generated, reference, *, projection: Projection | None = None) -> RouteComparison:
    """Compare two routes given as ``[(lat, lon)]`` waypoint lists.

    Pass an existing ``projection`` (e.g. ``nav.projection``) to score in the same frame as the
    planner; otherwise a local AEQD frame is built from both routes' combined centroid.
    """
    gen_ll = list(generated)
    ref_ll = list(reference)
    if len(gen_ll) < 2 or len(ref_ll) < 2:
        raise ValueError("both routes need >= 2 waypoints")

    if projection is None:
        both = LineString([(lon, lat) for lat, lon in gen_ll + ref_ll])
        projection = Projection.for_geometry(both)

    gen_xy = _coords_ll_to_xy(gen_ll, projection)
    ref_xy = _coords_ll_to_xy(ref_ll, projection)
    gen_line, ref_line = LineString(gen_xy), LineString(ref_xy)

    xtd = cross_track_stats_m(gen_xy, ref_xy)
    return RouteComparison(
        frechet_m=round(discrete_frechet_m(gen_xy, ref_xy), 2),
        hausdorff_m=round(gen_line.hausdorff_distance(ref_line), 2),
        xtd_mean_m=round(xtd["xtd_mean_m"], 2),
        xtd_median_m=round(xtd["xtd_median_m"], 2),
        xtd_p95_m=round(xtd["xtd_p95_m"], 2),
        xtd_max_m=round(xtd["xtd_max_m"], 2),
        length_generated_m=round(gen_line.length, 1),
        length_reference_m=round(ref_line.length, 1),
        length_ratio=round(gen_line.length / ref_line.length, 4) if ref_line.length else float("inf"),
        n_waypoints_generated=len(gen_ll),
        n_waypoints_reference=len(ref_ll),
        turn_count_generated=turn_count(gen_xy),
        turn_count_reference=turn_count(ref_xy),
    )


def diff_validation(generated_route, reference_route) -> dict:
    """Compare the validation outcome of two already-validated ``keelson.Route``-like objects.

    Duck-typed: reads ``.issues`` / ``.challenges`` (repeated) — no keelson import. The runner
    calls ``validate(route, nav)`` on both first, then passes them here.
    """
    def counts(route):
        return {
            "n_issues": len(getattr(route, "issues", []) or []),
            "n_challenges": len(getattr(route, "challenges", []) or []),
        }

    gen, ref = counts(generated_route), counts(reference_route)
    return {
        "gen_issues": gen["n_issues"],
        "gen_challenges": gen["n_challenges"],
        "ref_issues": ref["n_issues"],
        "ref_challenges": ref["n_challenges"],
        "issues_delta": gen["n_issues"] - ref["n_issues"],
        "challenges_delta": gen["n_challenges"] - ref["n_challenges"],
    }
