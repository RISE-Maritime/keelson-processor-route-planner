"""Cost-weight profiles per optimization goal (pure, no I/O).

A profile maps `distance` (base per-metre weight) + each P3 ``SoftCost.kind`` to a multiplier; the
grid's per-cell cost is ``distance + clearance·penalty + Σ region.weight · profile[kind]`` (see
grid.py). Goals trade off directness vs. safety/fairway adherence by scaling the soft-cost kinds +
the `clearance` term (the off-shore / mid-channel preference). `turn` weights the post-processing
turn penalty.
"""

from __future__ import annotations

# Soft-cost kinds emitted by route_planner.navigability (P3).
KINDS = (
    "shallow_but_legal",
    "outside_fairway",
    "near_land_margin",
    "poor_chart_quality",
    "restricted_soft",
    "unknown_depth",
    "off_ais_corridor",
)

# distance is the base weight on raw metres; each kind is a multiplier on that region's weight.
# `clearance` weights the distance-from-land gradient (penalty in [0,1] per cell; see grid.py) — the
# main lever that pulls routes off the shore toward mid-channel / channel centerlines.
_DEFAULT = {
    "distance": 1.0,
    "turn": 1.0,
    "clearance": 1.5,
    "shallow_but_legal": 1.0,
    "outside_fairway": 0.5,
    "near_land_margin": 0.5,
    "poor_chart_quality": 0.3,
    "restricted_soft": 3.0,
    "unknown_depth": 2.0,
    # Penalty for straying outside the historical-AIS corridor. Only bites when the planner is given
    # an AIS corridor and ``prefer_ais_corridors`` is set (opt-in), so it is inert by default.
    "off_ais_corridor": 1.0,
}


def _profile(**overrides) -> dict:
    merged = dict(_DEFAULT)
    merged.update(overrides)
    return merged


WEIGHT_PROFILES = {
    # Minimize distance; barely penalize soft regions (but still avoid restricted/unknown somewhat).
    # Low clearance weight -> a near-direct route (still nudged a little off the shore).
    "shortest": _profile(
        clearance=0.2, shallow_but_legal=0.2, outside_fairway=0.0, near_land_margin=0.1,
        poor_chart_quality=0.0, restricted_soft=2.0, unknown_depth=0.5, turn=0.2,
    ),
    # Like shortest but distance dominates time; soft kept low.
    "fastest": _profile(
        clearance=0.2, shallow_but_legal=0.3, outside_fairway=0.0, near_land_margin=0.1,
        poor_chart_quality=0.0, restricted_soft=2.0, unknown_depth=0.5, turn=0.1,
    ),
    # Maximize clearance/charted-ness; heavily reward mid-channel + penalize shallow/near-land/unknown.
    "safest": _profile(
        clearance=3.0, shallow_but_legal=4.0, outside_fairway=1.0, near_land_margin=3.0,
        poor_chart_quality=2.0, restricted_soft=5.0, unknown_depth=6.0, turn=1.5,
    ),
    "prefer_fairways": _profile(
        clearance=2.5, outside_fairway=4.0, shallow_but_legal=2.0, near_land_margin=2.0, unknown_depth=3.0,
    ),
    # Traffic layer is P11; until then behave conservatively.
    "avoid_traffic": _profile(
        clearance=2.0, shallow_but_legal=2.0, near_land_margin=2.0, unknown_depth=3.0, outside_fairway=1.0,
    ),
    "archipelago_conservative": _profile(
        clearance=2.5, shallow_but_legal=4.0, outside_fairway=1.5, near_land_margin=3.0,
        poor_chart_quality=2.0, restricted_soft=5.0, unknown_depth=6.0, turn=1.5,
    ),
    "usv_trial_safe": _profile(
        clearance=4.0, shallow_but_legal=6.0, outside_fairway=2.0, near_land_margin=5.0,
        poor_chart_quality=3.0, restricted_soft=6.0, unknown_depth=8.0, turn=2.0,
    ),
    # Tuned to reproduce Sjöfartsverket pilot routes + historical-AIS lanes (mid-channel, strong
    # fairway/AIS-corridor adherence). SEED VALUES — refine with the benchmark sweep against the
    # Gothenburg fixtures + AIS (see docs/BENCHMARK.md); use with constraints={"prefer_ais_corridors": True}.
    "pilot_matched": _profile(
        clearance=2.5, shallow_but_legal=3.0, outside_fairway=3.0, near_land_margin=2.5,
        poor_chart_quality=1.0, restricted_soft=5.0, unknown_depth=4.0, off_ais_corridor=3.0, turn=1.0,
    ),
    "custom": _profile(),
}


def weights_for(optimization_goal: str | None) -> dict:
    """Return the cost-weight profile for ``optimization_goal`` (the default for unknown goals)."""
    return dict(WEIGHT_PROFILES.get(optimization_goal or "", _DEFAULT))
