"""Navigability parameters + S-57 hazard/restriction classification (pure, no I/O).

`NavParams` captures the vessel- and mode-derived thresholds the engine needs; the rest classify raw
S-57 attributes (RESTRN restriction codes, hazard WATLEV/VALSOU) into hard-block vs soft. Kept small
and explicit so the safety semantics are auditable and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

# Planning modes (from the optimization goal) that play it safe: block uncharted depth, wider buffers.
CONSERVATIVE_MODES = frozenset({"archipelago_conservative", "usv_trial_safe", "safest"})

# S-57 RESARE.RESTRN restriction codes (subset we act on; refine as needed).
#   7 = entry prohibited  -> hard-block; 8 = entry restricted -> high soft-cost.
BLOCKING_RESTRN = frozenset({7})
SOFT_RESTRN = frozenset({8})

# S-57 WATLEV (water level) codes that make a rock/wreck/obstruction dangerous regardless of VALSOU:
#   1 partly submerged at high water, 2 always dry, 4 covers & uncovers, 5 awash.
DANGEROUS_WATLEV = frozenset({1, 2, 4, 5})

DEFAULT_UKC_M = 0.5
DEFAULT_MARGIN_M = 10.0
DEFAULT_CHART_SCALE_THRESHOLD = 100_000  # coarser than ~1:100k counts as poor chart quality
# How far off land/shallow the planner *prefers* to stay (the clearance-gradient target). The
# pathfinder rewards clearance up to this distance, so routes bow to mid-channel / channel centerlines.
DEFAULT_CLEARANCE_TARGET_M = 150.0
CONSERVATIVE_CLEARANCE_TARGET_M = 300.0


@dataclass
class NavParams:
    """Vessel- + mode-derived thresholds driving hard-block / soft-cost classification."""

    safety_depth_m: float
    route_margin_m: float = DEFAULT_MARGIN_M
    hazard_buffer_m: float = DEFAULT_MARGIN_M
    comfortable_depth_m: float = 0.0  # shallow-but-legal band upper bound; derived if 0
    block_unknown_depth: bool = False
    prefer_fairways: bool = False
    prefer_ais_corridors: bool = False
    poor_chart_scale_threshold: float = DEFAULT_CHART_SCALE_THRESHOLD
    clearance_target_m: float = DEFAULT_CLEARANCE_TARGET_M
    mode: str = ""

    def __post_init__(self):
        if self.comfortable_depth_m <= 0:
            self.comfortable_depth_m = max(self.safety_depth_m * 2.0, self.safety_depth_m + 2.0)


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def nav_params_from_profile(
    profile: dict, optimization_goal: str | None = None, constraints: dict | None = None
) -> NavParams:
    """Derive NavParams from a planning profile dict (DESIGN §4) + goal + constraint flags."""
    constraints = constraints or {}
    mode = optimization_goal or ""

    draught = _f(profile.get("draught_m"), 0.0)
    ukc = _f(profile.get("min_under_keel_clearance_m"), DEFAULT_UKC_M)
    margin = _f(profile.get("route_margin_m"), DEFAULT_MARGIN_M)

    allow_unknown = constraints.get("allow_unknown_depth")
    if allow_unknown is None:
        block_unknown = mode in CONSERVATIVE_MODES
    else:
        block_unknown = not allow_unknown

    prefer_fairways = bool(
        constraints.get("prefer_fairways", mode in {"prefer_fairways", "archipelago_conservative"})
    )
    # AIS-corridor preference is purely opt-in (needs an AIS corridor supplied to build_navigability).
    prefer_ais_corridors = bool(constraints.get("prefer_ais_corridors", False))

    # Prefer to stay this far off land/shallow (clearance gradient); larger when playing it safe,
    # never below a few route-margins so it survives small/large margins sensibly.
    base_clear = CONSERVATIVE_CLEARANCE_TARGET_M if mode in CONSERVATIVE_MODES else DEFAULT_CLEARANCE_TARGET_M
    clearance_target = _f(constraints.get("clearance_target_m"), base_clear) or base_clear
    clearance_target = max(clearance_target, margin * 4)

    return NavParams(
        safety_depth_m=draught + ukc,
        route_margin_m=margin,
        hazard_buffer_m=margin,
        block_unknown_depth=block_unknown,
        prefer_fairways=prefer_fairways,
        prefer_ais_corridors=prefer_ais_corridors,
        clearance_target_m=clearance_target,
        mode=mode,
    )


def parse_restrn(value) -> set:
    """Parse an S-57 RESTRN value (int, or a comma/semicolon/space-separated string) into a code set."""
    if value is None:
        return set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {int(value)}
    codes = set()
    for token in str(value).replace(";", ",").replace(" ", ",").split(","):
        token = token.strip()
        if token:
            try:
                codes.add(int(float(token)))
            except ValueError:
                continue
    return codes


def is_unsafe_hazard(feature, safety_depth_m: float, conservative: bool) -> bool:
    """Whether a rock/wreck/obstruction must be hard-blocked for this vessel."""
    watlev = feature.attrs.get("watlev")
    watlev_code = _f(watlev)
    if watlev_code is not None and int(watlev_code) in DANGEROUS_WATLEV:
        return True
    valsou = feature.valsou
    if valsou is not None:
        return valsou < safety_depth_m
    # Unknown depth and not flagged dangerous by WATLEV: block only when playing it safe.
    return conservative
