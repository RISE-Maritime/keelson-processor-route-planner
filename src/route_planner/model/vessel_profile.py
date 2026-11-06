"""Vessel profile <-> keelson.RouteInfo + Extensions mapping (keelson protos are primary).

There is no parallel vessel-profile message. A vessel profile is expressed using keelson's
``RouteInfo`` (vessel_* dimensions + speed limits) plus a route-planner ``Extensions.Entry`` for
the planner-only fields (preferred speed, UKC, route margin, turn radius) that have no native
``RouteInfo`` home. See docs/KEELSON-INTEGRATION.md §2-3.

The same pair appears in two places: on a produced ``keelson.Route`` (``route.info`` +
``route.extensions``) and on a ``route_planner/v1`` ``PlanRouteRequest`` (``request.constraints`` +
``request.extensions``). ``profile_from_parts`` reads either.

keelson imports are lazy (inside the functions) so importing this module never requires the typed
SDK; only callers that actually build/parse a profile do.
"""

from __future__ import annotations

MANUFACTURER = "se.rise"
EXTENSION_NAME = "route-planner"
EXTENSION_VERSION = "0.1"

# Extension attribute naming a cost profile that is not in keelson's OptimizationGoal enum
# (``pilot_matched``, ``usv_trial_safe``, ``custom``): deployment tunings, which the RoutePlanner
# proto deliberately keeps out of the shared vocabulary and routes through ``extensions``.
GOAL_ATTRIBUTE = "goal"
CONSTRAINT_PREFIX = "constraint."

# Profile fields that map directly onto RouteInfo: profile_key -> RouteInfo field.
_ROUTE_INFO_FIELDS = {
    "length_m": "vessel_loa_m",
    "beam_m": "vessel_beam_m",
    "draught_m": "vessel_draft_m",
    "air_draught_m": "vessel_air_draft_m",
    "max_speed_kn": "max_speed_knots",
    "min_speed_kn": "min_speed_knots",
}

# Profile fields with no RouteInfo home — carried in the Extensions.Entry.attributes (as strings).
_EXTENSION_KEYS = (
    "name",
    "preferred_speed_kn",
    "min_under_keel_clearance_m",
    "route_margin_m",
    "turn_radius_m",
)


def route_info_from_profile(profile: dict, optimization_goal: str | None = None):
    """Build a keelson ``RouteInfo`` from a planning profile dict (DESIGN §4 shape)."""
    from keelson.payloads.Route_pb2 import RouteInfo

    info = RouteInfo()
    for profile_key, field in _ROUTE_INFO_FIELDS.items():
        value = profile.get(profile_key)
        if value is not None:
            setattr(info, field, float(value))
    if optimization_goal:
        info.optimization_method = optimization_goal
    return info


def planner_extension_entry(profile: dict, constraints: dict | None = None, goal: str | None = None):
    """Build the route-planner ``Extensions.Entry`` holding planner-only profile fields + inputs.

    ``goal`` is only for a cost profile outside keelson's ``OptimizationGoal`` (see
    ``GOAL_ATTRIBUTE``); an enum goal goes in ``PlanRouteRequest.goal``.
    """
    from keelson.payloads.Route_pb2 import Extensions

    entry = Extensions.Entry(
        manufacturer=MANUFACTURER, name=EXTENSION_NAME, version=EXTENSION_VERSION
    )
    for key in _EXTENSION_KEYS:
        value = profile.get(key)
        if value is not None:
            entry.attributes[key] = str(value)
    for key, value in (constraints or {}).items():
        entry.attributes[f"{CONSTRAINT_PREFIX}{key}"] = str(value)
    if goal:
        entry.attributes[GOAL_ATTRIBUTE] = goal
    return entry


def planner_entry(extensions):
    """The route-planner ``Extensions.Entry`` in ``extensions``, or ``None``."""
    for entry in extensions.entries:
        if entry.manufacturer == MANUFACTURER and entry.name == EXTENSION_NAME:
            return entry
    return None


def goal_label(goal: int) -> str | None:
    """``OptimizationGoal`` value -> the planner's cost-profile label (``None`` for UNSPECIFIED).

    The enum names are ``OPTIMIZATION_GOAL_<LABEL>``, and every label is one of the planner's cost
    profiles (pathfinding/cost.py), so the mapping is the name with the prefix dropped.
    """
    from keelson.interfaces.RoutePlanner_pb2 import OptimizationGoal

    name = OptimizationGoal.Name(goal)
    if goal == OptimizationGoal.OPTIMIZATION_GOAL_UNSPECIFIED:
        return None
    return name.removeprefix("OPTIMIZATION_GOAL_").lower()


def plan_request(
    waypoints,
    *,
    profile: dict,
    goal: str | None = None,
    max_alternatives: int = 0,
    constraints: dict | None = None,
):
    """Build a ``route_planner/v1`` ``PlanRouteRequest`` from (lat, lon) points + a profile.

    ``goal`` is a cost-profile label. One that keelson's ``OptimizationGoal`` names goes in the
    typed ``goal`` field; any other (a deployment tuning) rides in the extension entry.
    """
    from keelson.interfaces.RoutePlanner_pb2 import OptimizationGoal, PlanRouteRequest

    from .position import set_lat_lon

    request = PlanRouteRequest(max_alternatives=max_alternatives)
    for lat, lon in waypoints:
        set_lat_lon(request.waypoints.add(), lat, lon)
    request.constraints.CopyFrom(route_info_from_profile(profile))
    extension_goal = None
    if goal:
        enum_name = f"OPTIMIZATION_GOAL_{goal.upper()}"
        if enum_name in OptimizationGoal.keys():
            request.goal = OptimizationGoal.Value(enum_name)
        else:
            extension_goal = goal
    request.extensions.entries.append(
        planner_extension_entry(profile, constraints=constraints, goal=extension_goal)
    )
    return request


def profile_from_parts(info, extensions) -> dict:
    """Reconstruct the profile dict from a ``RouteInfo`` + the route-planner extension entry.

    RouteInfo numbers come back as floats; Extension attributes come back as strings (the proto
    ``attributes`` map is ``<string,string>``).
    """
    profile: dict = {}
    for profile_key, field in _ROUTE_INFO_FIELDS.items():
        value = getattr(info, field)
        if value:
            profile[profile_key] = value
    entry = planner_entry(extensions)
    if entry is not None:
        for key in _EXTENSION_KEYS:
            if key in entry.attributes:
                profile[key] = entry.attributes[key]
    return profile


def profile_from_route(route) -> dict:
    """Inverse of the builders for a produced ``keelson.Route``."""
    return profile_from_parts(route.info, route.extensions)
