"""Latency targets + a timing harness for the planner — a *report*, not a CI gate.

Targets come from the project's performance goals: a single route should plan in a couple of
seconds, a few alternatives with via-points in well under ten, a corridor-local re-validation in
about one. Wall-clock latency is environment-dependent (dev box vs CI vs the live chart-server), so
this records elapsed time against a target and flags breaches — it never hard-fails. Pure stdlib +
the planner (no zenoh, no keelson). Drive it from ``notebooks/explore_and_tune.ipynb`` or the
opt-in ``pytest -m perf`` suite (``RUN_PERF=1``).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from ..pathfinding import plan

# Targets (seconds). Warm/cold refer to the chart-adapter cache state; the synthetic-nav harness
# below times only the plan() step, so it should sit comfortably under WARM_S. COLD_S includes the
# chart fetch and is meaningful only against a live chart-server.
WARM_S = 2.0  # single route, ~20x20 km corridor, warm chart cache
COLD_S = 5.0  # same, cold cache (includes the chart fetch)
THREE_ALT_TWO_VIA_S = 8.0  # 3 alternatives with 2 via-points
REVALIDATE_S = 1.0  # corridor-local re-validation after a manual edit

__all__ = ["WARM_S", "COLD_S", "THREE_ALT_TWO_VIA_S", "REVALIDATE_S", "LatencyReport", "time_plan"]


@dataclass
class LatencyReport:
    """Wall-clock of one planner run vs its target. ``within`` is False when the target is breached."""

    scenario: str
    elapsed_s: float
    target_s: float

    @property
    def within(self) -> bool:
        return self.elapsed_s <= self.target_s

    def to_dict(self) -> dict:
        return {**asdict(self), "within": self.within}


def time_plan(
    nav, start, end, *, scenario: str = "plan", target_s: float = WARM_S, **plan_kwargs
) -> LatencyReport:
    """Run ``plan(nav, start, end, **plan_kwargs)``, timing only the search, and report vs ``target_s``.

    Routes are discarded — callers timing the full chart→nav→plan pipeline should wrap their own
    scope. ``plan_kwargs`` forwards ``via`` / ``optimization_goal`` / ``n_alternatives`` etc.
    """
    t0 = time.perf_counter()
    plan(nav, start, end, **plan_kwargs)
    return LatencyReport(
        scenario=scenario, elapsed_s=round(time.perf_counter() - t0, 3), target_s=target_s
    )
