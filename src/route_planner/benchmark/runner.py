"""Benchmark runner — score generated route alternatives against a reference, across weight profiles.

The scoring core here is pure (duck-typed over PlannedRoute / keelson.Route / raw ``[(lat, lon)]``)
so it unit-tests without a live chart or DB. The live orchestration — running the real pipeline per
``optimization_goal`` and feeding the results in — lives in ``debug.run_benchmark``.
"""

from __future__ import annotations

from ..model.position import lat_lon

from .metrics import compare_routes, diff_validation

__all__ = [
    "route_coords",
    "score_route",
    "benchmark_profiles",
    "summarize",
    "to_dataframe",
]


def route_coords(route) -> list:
    """Extract ``[(lat, lon)]`` from a keelson.Route, a PlannedRoute, or a raw coord list."""
    if isinstance(route, (list, tuple)):
        return [tuple(p) for p in route]
    wps = getattr(route, "waypoints", None)
    if wps is None:
        raise TypeError(f"cannot read waypoints from {type(route).__name__}")
    inner = getattr(wps, "waypoint", None)
    if inner is not None:  # keelson.Route.waypoints.waypoint
        return [lat_lon(wp.position) for wp in inner]
    return [tuple(p) for p in wps]  # PlannedRoute.waypoints


def score_route(reference_coords, generated, *, projection=None, reference_route=None) -> dict:
    """One row: similarity of ``generated`` to the reference + its validation outcome.

    ``reference_route`` (a validated keelson.Route) enables the issues/challenges delta; pass the
    shared ``projection`` (``nav.projection``) so every score is in the same metric frame.
    """
    cmp = compare_routes(route_coords(generated), reference_coords, projection=projection)
    row = cmp.to_dict()
    row["gen_n_issues"] = len(getattr(generated, "issues", []) or [])
    row["gen_n_challenges"] = len(getattr(generated, "challenges", []) or [])
    if reference_route is not None:
        row.update(diff_validation(generated, reference_route))
    return row


def benchmark_profiles(
    reference_coords, results_by_goal, *, projection=None, reference_route=None
) -> list:
    """Score every alternative of every goal; flag the best (min Fréchet) alternative per goal.

    ``results_by_goal``: ``{optimization_goal: [generated_route, ...]}``. Returns flat row dicts
    ready for ``to_dataframe``; routes with < 2 waypoints are skipped.
    """
    rows = []
    for goal, routes in results_by_goal.items():
        for idx, gen in enumerate(routes):
            try:
                row = score_route(
                    reference_coords, gen, projection=projection, reference_route=reference_route
                )
            except (ValueError, TypeError):
                continue
            rows.append({"goal": goal, "alt_index": idx, "n_alternatives": len(routes), **row})
    _mark_best_per_goal(rows)
    return rows


def _mark_best_per_goal(rows) -> None:
    best_idx: dict = {}
    for i, row in enumerate(rows):
        cur = best_idx.get(row["goal"])
        if cur is None or row["frechet_m"] < rows[cur]["frechet_m"]:
            best_idx[row["goal"]] = i
    winners = set(best_idx.values())
    for i, row in enumerate(rows):
        row["best_for_goal"] = i in winners


def summarize(rows) -> dict:
    """Pick the goal whose best alternative best matches the reference with no critical issues.

    Returns ``{best_goal, frechet_m, xtd_mean_m, clean}`` or ``{}`` if there are no rows. Prefers
    issue-free routes; falls back to the overall minimum Fréchet if every candidate has issues.
    """
    best = [r for r in rows if r.get("best_for_goal")]
    if not best:
        return {}
    clean = [r for r in best if r.get("gen_n_issues", 0) == 0]
    pool = clean or best
    win = min(pool, key=lambda r: r["frechet_m"])
    return {
        "best_goal": win["goal"],
        "frechet_m": win["frechet_m"],
        "xtd_mean_m": win["xtd_mean_m"],
        "clean": bool(clean) and win in clean,
    }


def to_dataframe(rows):
    """Rows -> pandas DataFrame (lazy import; pandas is in the ``benchmark`` extra)."""
    import pandas as pd

    return pd.DataFrame(rows)
