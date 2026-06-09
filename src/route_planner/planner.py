"""Plan orchestration — the glue from a plan_route request to keelson.Route alternatives.

`run_plan(adapter, request_route)` parses the request `keelson.Route` template (per
docs/KEELSON-INTEGRATION.md §3), computes a corridor, and runs the P2-P5 pipeline:
chart_adapter → navigability → pathfinding → serialize. Heavy imports are lazy so importing this
module stays cheap; the chart_adapter is the only I/O (the rest is pure geometry).
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


class PlanError(ValueError):
    """A malformed plan_route request (maps to ErrorResponse INVALID_ARGUMENT)."""


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


def _planner_extension(route):
    from .model.vessel_profile import EXTENSION_NAME, MANUFACTURER

    for entry in route.extensions.entries:
        if entry.manufacturer == MANUFACTURER and entry.name == EXTENSION_NAME:
            return entry
    return None


def parse_request(request_route):
    """Extract (start, end, via, profile, optimization_goal, constraints, n_alternatives)."""
    from .model import vessel_profile

    waypoints = [
        (wp.position.latitude, wp.position.longitude)
        for wp in request_route.waypoints.waypoint
    ]
    if len(waypoints) < 2:
        raise PlanError("plan_route request keelson.Route must carry >= 2 waypoints (start..end)")

    profile = vessel_profile.profile_from_route(request_route)
    optimization_goal = request_route.info.optimization_method or "shortest"

    constraints: dict = {}
    n_alternatives = 1
    entry = _planner_extension(request_route)
    if entry is not None:
        for key, value in entry.attributes.items():
            if key.startswith("constraint."):
                constraints[key[len("constraint."):]] = _to_bool(value)
        n_alternatives = max(1, int(_f(entry.attributes.get("return_alternatives"), 1) or 1))

    return {
        "start": waypoints[0],
        "end": waypoints[-1],
        "via": waypoints[1:-1],
        "profile": profile,
        "optimization_goal": optimization_goal,
        "constraints": constraints,
        "n_alternatives": n_alternatives,
    }


def run_plan(adapter, request_route):
    """Run the full pipeline for a plan_route request; return (list[keelson.Route], meta)."""
    from .model import serialize
    from .navigability import build_navigability, nav_params_from_profile
    from .pathfinding import plan as pathfind

    req = parse_request(request_route)
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
        planned,
        profile=req["profile"],
        optimization_goal=req["optimization_goal"],
        evidence=nav.evidence,
    )
    from .validation import validate

    for route in routes:
        validate(route, nav)  # populate issues/challenges/status before reply + publish
    return routes, {"warnings": nav.warnings, "n_alternatives": len(routes)}


def run_validate(adapter, request_route):
    """Re-validate a (possibly manually edited) keelson.Route against fresh navigability.

    Rebuilds the chart/navigability picture from the route's own profile + waypoints (no
    pathfinding), then annotates the route in place with issues/challenges/status. Returns the route.
    """
    from .navigability import build_navigability, nav_params_from_profile
    from .validation import validate

    req = parse_request(request_route)
    bbox = corridor_bbox([req["start"], *req["via"], req["end"]])
    chart = adapter.fetch_corridor(bbox)
    nav = build_navigability(
        chart, nav_params_from_profile(req["profile"], req["optimization_goal"], req["constraints"])
    )
    return validate(request_route, nav)
