"""Plan orchestration — the glue from a plan_route request to keelson.Route alternatives.

`run_plan(adapter, request)` parses a `route_planner/v1` `PlanRouteRequest` (keelson
interfaces/RoutePlanner.proto; docs/KEELSON-INTEGRATION.md §3), computes a corridor, and runs the
P2-P5 pipeline:
chart_adapter → navigability → pathfinding → serialize. Heavy imports are lazy so importing this
module stays cheap; the chart_adapter is the only I/O (the rest is pure geometry).
"""

from __future__ import annotations

from .model.position import lat_lon

import logging
import math

logger = logging.getLogger(__name__)


class PlanError(ValueError):
    """A malformed plan_route request (maps to ErrorResponse INVALID_ARGUMENT)."""


class PlanUnsupported(PlanError):
    """A well-formed request the planner cannot serve (maps to ErrorResponse UNSUPPORTED)."""


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def corridor_bbox(points_ll, pad_m: float = 2000.0):
    """(west, south, east, north) around the lat/lon points, padded by max(pad_m, 0.3·span)."""
    lats = [p[0] for p in points_ll]
    lons = [p[1] for p in points_ll]
    mid_lat = sum(lats) / len(lats)
    cos_lat = max(math.cos(math.radians(mid_lat)), 0.1)
    lat_span_m = (max(lats) - min(lats)) * 111320.0
    lon_span_m = (max(lons) - min(lons)) * 111320.0 * cos_lat
    pad = max(pad_m, 0.3 * max(lat_span_m, lon_span_m))
    d_lat = pad / 111320.0
    d_lon = pad / (111320.0 * cos_lat)
    return (min(lons) - d_lon, min(lats) - d_lat, max(lons) + d_lon, max(lats) + d_lat)


def _constraints(entry) -> dict:
    """The ``constraint.*`` booleans of the route-planner extension entry."""
    from .model.vessel_profile import CONSTRAINT_PREFIX

    if entry is None:
        return {}
    return {
        key[len(CONSTRAINT_PREFIX):]: _to_bool(value)
        for key, value in entry.attributes.items()
        if key.startswith(CONSTRAINT_PREFIX)
    }


def _known_goal(label: str) -> str:
    from .pathfinding.cost import WEIGHT_PROFILES

    if label not in WEIGHT_PROFILES:
        # An unknown label would silently fall back to the default weights (cost.weights_for):
        # the failure mode the closed OptimizationGoal enum exists to prevent.
        raise PlanError(f"unknown optimization goal {label!r}; known: {sorted(WEIGHT_PROFILES)}")
    return label


def _split(points: list) -> dict:
    return {"start": points[0], "end": points[-1], "via": points[1:-1]}


def parse_plan_request(request) -> dict:
    """Extract the planning inputs from a ``route_planner/v1`` ``PlanRouteRequest``.

    Returns start/end/via, profile, optimization_goal (a cost-profile label), constraints and
    n_alternatives. Raises ``PlanError`` (INVALID_ARGUMENT) or ``PlanUnsupported`` (UNSUPPORTED).
    """
    from keelson.payloads.Route_pb2 import ROUTE_TOPOLOGY_CLOSED

    from .model import vessel_profile

    points = [lat_lon(c) for c in request.waypoints]
    if len(points) < 2:
        raise PlanError("PlanRouteRequest must carry >= 2 waypoints (departure .. destination)")
    if request.topology == ROUTE_TOPOLOGY_CLOSED:
        raise PlanUnsupported("closed (circuit) routes are not supported by this planner")

    entry = vessel_profile.planner_entry(request.extensions)
    goal = vessel_profile.goal_label(request.goal)
    if entry is not None and entry.attributes.get(vessel_profile.GOAL_ATTRIBUTE):
        if goal is not None:
            raise PlanError("goal set both in PlanRouteRequest.goal and the planner extension")
        goal = entry.attributes[vessel_profile.GOAL_ATTRIBUTE]

    return {
        **_split(points),
        "profile": vessel_profile.profile_from_parts(request.constraints, request.extensions),
        "optimization_goal": _known_goal(goal or "shortest"),
        "constraints": _constraints(entry),
        # 0 means "the planner's own default", which is one alternative.
        "n_alternatives": max(1, int(request.max_alternatives)),
    }


def route_context(route) -> dict:
    """The planning context of an existing ``keelson.Route`` (for validate_route).

    The goal is the one the route was planned for (``RouteInfo.optimization_method``), and the
    profile/constraints come from its own info + planner extension.
    """
    from .model import vessel_profile

    points = [lat_lon(wp.position) for wp in route.waypoints.waypoint]
    if len(points) < 2:
        raise PlanError("validate_route: the keelson.Route must carry >= 2 waypoints")
    return {
        **_split(points),
        "profile": vessel_profile.profile_from_route(route),
        "optimization_goal": route.info.optimization_method or "shortest",
        "constraints": _constraints(vessel_profile.planner_entry(route.extensions)),
    }


def run_plan(adapter, request):
    """Run the full pipeline for a ``PlanRouteRequest``; return (list[keelson.Route], meta).

    Never more than ``max_alternatives`` routes: the interface says a planner MUST NOT reply with
    more than the caller asked for.
    """
    from .model import serialize
    from .navigability import build_navigability, nav_params_from_profile
    from .pathfinding import plan as pathfind

    req = parse_plan_request(request)
    bbox = corridor_bbox([req["start"], *req["via"], req["end"]])
    logger.info(
        "plan_route: %s -> %s (%d via) goal=%s alts=%d corridor=%s",
        req["start"], req["end"], len(req["via"]), req["optimization_goal"],
        req["n_alternatives"], tuple(round(v, 4) for v in bbox),
    )

    chart = adapter.fetch_corridor(bbox)
    nav = build_navigability(
        chart, nav_params_from_profile(req["profile"], req["optimization_goal"], req["constraints"])
    )
    planned = pathfind(
        nav,
        req["start"],
        req["end"],
        via=req["via"],
        optimization_goal=req["optimization_goal"],
        n_alternatives=req["n_alternatives"],
        preferred_speed_kn=_f(req["profile"].get("preferred_speed_kn")),
    )
    routes = serialize.routes_from_alternatives(
        planned[: req["n_alternatives"]],
        profile=req["profile"],
        optimization_goal=req["optimization_goal"],
        evidence=nav.evidence,
    )
    from .validation import validate

    import os

    from . import risk

    for route in routes:
        validate(route, nav)  # populate issues/challenges/status before reply + publish
        if os.environ.get(risk.ON_PLAN_ENV) == "1":
            _assess_risk(route)
    return routes, {"warnings": nav.warnings, "n_alternatives": len(routes)}


def _assess_risk(route):
    """navrisk over the planned route (risk.py), then the validation summary recounted."""
    from . import risk
    from .validation import _finalize

    risk.annotate(route, risk.chart_source())
    _finalize(route, risk.time.time_ns())
    return route


def run_validate(adapter, request_route):
    """Re-validate a (possibly manually edited) keelson.Route against fresh navigability.

    Rebuilds the chart/navigability picture from the route's own profile + waypoints (no
    pathfinding), then annotates the route in place with issues/challenges/status. Returns the route.
    """
    from .navigability import build_navigability, nav_params_from_profile
    from .validation import validate

    req = route_context(request_route)
    bbox = corridor_bbox([req["start"], *req["via"], req["end"]])
    chart = adapter.fetch_corridor(bbox)
    nav = build_navigability(
        chart, nav_params_from_profile(req["profile"], req["optimization_goal"], req["constraints"])
    )
    return _assess_risk(validate(request_route, nav))
