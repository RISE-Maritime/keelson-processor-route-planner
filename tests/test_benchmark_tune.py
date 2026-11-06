"""Weight-sweep tuning tests (benchmark/tune.py) — pure driver over a stub plan_fn."""

from __future__ import annotations

from route_planner.benchmark.tune import grid_candidates, sweep_weights
from route_planner.pathfinding import PlannedRoute

REFERENCE = [(57.600, 11.60), (57.600, 11.70), (57.600, 11.80)]


def test_grid_candidates_cartesian_product():
    cands = grid_candidates({"clearance": [1.5, 2.5], "off_ais_corridor": [0.0, 3.0]})
    assert len(cands) == 4
    assert {"clearance": 2.5, "off_ais_corridor": 3.0} in cands


def test_sweep_picks_best_matching_weights():
    # Stub planner: higher "clearance" -> route closer to the reference centreline.
    def plan_fn(weights):
        offset = {0.0: 0.010, 1.0: 0.003, 2.5: 0.0}[weights["clearance"]]
        coords = [(lat + offset, lon) for lat, lon in REFERENCE]
        return [PlannedRoute(waypoints=coords)]

    candidates = grid_candidates({"clearance": [0.0, 1.0, 2.5]})
    results = sweep_weights(REFERENCE, candidates, plan_fn)

    assert results[0]["weights"] == {"clearance": 2.5}
    assert results[0]["frechet_m"] < results[-1]["frechet_m"]


def test_sweep_skips_empty_plans():
    def plan_fn(weights):
        return [] if weights["clearance"] == 0.0 else [PlannedRoute(waypoints=REFERENCE)]

    results = sweep_weights(REFERENCE, grid_candidates({"clearance": [0.0, 2.5]}), plan_fn)
    assert len(results) == 1
    assert results[0]["weights"] == {"clearance": 2.5}
