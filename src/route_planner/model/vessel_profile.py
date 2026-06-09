"""Vessel profile <-> keelson.RouteInfo + Extensions mapping (keelson protos are primary).

There is no parallel vessel-profile message. A vessel profile is expressed using keelson's
``RouteInfo`` (vessel_* dimensions + speed limits + optimization_method) plus a route-planner
``Extensions.Entry`` for the planner-only fields (preferred speed, UKC, route margin, turn radius)
that have no native ``RouteInfo`` home. See docs/KEELSON-INTEGRATION.md §2-3.

keelson imports are lazy (inside the functions) so importing this module never requires the typed
SDK; only callers that actually build/parse a profile do.
"""

from __future__ import annotations

MANUFACTURER = "se.rise"
EXTENSION_NAME = "route-planner"
EXTENSION_VERSION = "0.1"

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


def planner_extension_entry(
    profile: dict, constraints: dict | None = None, return_alternatives: int | None = None
):
    """Build the route-planner ``Extensions.Entry`` holding planner-only profile fields + inputs."""
    from keelson.payloads.Route_pb2 import Extensions

    entry = Extensions.Entry(
        manufacturer=MANUFACTURER, name=EXTENSION_NAME, version=EXTENSION_VERSION
    )
    for key in _EXTENSION_KEYS:
        value = profile.get(key)
        if value is not None:
            entry.attributes[key] = str(value)
    for key, value in (constraints or {}).items():
        entry.attributes[f"constraint.{key}"] = str(value)
    if return_alternatives is not None:
        entry.attributes["return_alternatives"] = str(return_alternatives)
    return entry


def profile_from_route(route) -> dict:
    """Inverse of the builders: reconstruct the profile dict from a Route's RouteInfo + Extensions.

    RouteInfo numbers come back as floats; Extension attributes come back as strings (the proto
    ``attributes`` map is ``<string,string>``).
    """
    info = route.info
    profile: dict = {}
    for profile_key, field in _ROUTE_INFO_FIELDS.items():
        value = getattr(info, field)
        if value:
            profile[profile_key] = value
    for entry in route.extensions.entries:
        if entry.manufacturer == MANUFACTURER and entry.name == EXTENSION_NAME:
            for key in _EXTENSION_KEYS:
                if key in entry.attributes:
                    profile[key] = entry.attributes[key]
    return profile
