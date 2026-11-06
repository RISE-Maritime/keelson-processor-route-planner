"""Weight-profile tuning — search cost weights that best reproduce a reference route.

The search is a pure driver: it takes a ``plan_fn(weight_overrides) -> [generated_route, ...]``
callable, so it unit-tests with a stub and runs live from the notebook (where ``plan_fn`` re-plans
through the real pipeline with ``cost.WEIGHT_PROFILES['custom']`` patched to the candidate). Scoring
reuses ``benchmark.runner``. The winner is meant to be hand-promoted into
``cost.WEIGHT_PROFILES['pilot_matched']``.
"""

from __future__ import annotations

import itertools

from .runner import benchmark_profiles

__all__ = ["grid_candidates", "sweep_weights"]


def grid_candidates(grid: dict) -> list:
    """Cartesian product of ``{weight_name: [values]}`` -> list of override dicts.

    e.g. ``grid_candidates({"clearance": [1.5, 2.5], "off_ais_corridor": [0, 3]})`` -> 4 dicts.
    """
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def sweep_weights(
    reference_coords, candidates, plan_fn, *, projection=None, reference_route=None
) -> list:
    """Score each candidate weight-override dict against the reference; return best-first.

    For each candidate, ``plan_fn(candidate)`` returns the generated alternatives; the best (min
    Fréchet) alternative's score is kept. Results sort issue-free-first, then by Fréchet, so
    ``result[0]["weights"]`` is the profile to promote. Candidates that plan nothing are skipped.
    """
    results = []
    for cand in candidates:
        routes = plan_fn(cand) or []
        rows = benchmark_profiles(
            reference_coords, {"candidate": routes},
            projection=projection, reference_route=reference_route,
        )
        if not rows:
            continue
        best = min(rows, key=lambda r: r["frechet_m"])
        results.append({"weights": cand, **best})
    results.sort(key=lambda r: (r.get("gen_n_issues", 0), r["frechet_m"]))
    return results
