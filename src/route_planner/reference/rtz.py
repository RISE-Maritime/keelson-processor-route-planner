"""Import RTZ pilot routes -> internal ``PlannedRoute`` and canonical ``keelson.Route``.

The inverse of ``model/serialize.py``: where that exports a generated route, this reads an external
RTZ-1.x file (CIRM ``http://www.cirm.org/RTZ/1/x``) — e.g. the Sjöfartsverket Gothenburg pilot
routes — so we can benchmark our output against professional ground truth.

RTZ shape (namespace-agnostic; we match by local tag name):

    <route><routeInfo routeName=.../><waypoints>
        <defaultWaypoint><leg portsideXTD starboardXTD .../></defaultWaypoint>
        <waypoint id name radius><position lon lat/><leg geometryType .../></waypoint> ...
    </waypoints></route>

``radius`` and ``portsideXTD``/``starboardXTD`` are in **nautical miles**; ``geometryType`` is
``Orthodrome``/``Loxodrome``. Pure stdlib + pyproj — no zenoh, no keelson at import time.
"""

from __future__ import annotations

from ..model.position import set_lat_lon

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from pyproj import Geod

from ..pathfinding import PlannedRoute

_GEOD = Geod(ellps="WGS84")
_NM_TO_M = 1852.0

__all__ = ["RtzWaypoint", "RtzRoute", "parse_rtz", "rtz_to_planned", "rtz_to_route"]


@dataclass
class RtzWaypoint:
    """One RTZ waypoint with its outgoing-leg metadata (distances already converted to metres)."""

    id: int
    name: str
    lat: float
    lon: float
    radius_m: float | None = None
    xtd_port_m: float | None = None
    xtd_starboard_m: float | None = None
    geometry_type: str = "Loxodrome"  # "Loxodrome" | "Orthodrome"


@dataclass
class RtzRoute:
    """A parsed RTZ route: name + ordered waypoints. Source-format detail kept for provenance."""

    name: str
    waypoints: list = field(default_factory=list)  # list[RtzWaypoint]
    format_version: str = "RTZ-1.0"

    @property
    def coords(self) -> list:
        """[(lat, lon)] in waypoint order."""
        return [(wp.lat, wp.lon) for wp in self.waypoints]


def _local(tag: str) -> str:
    """Strip an XML namespace: ``{http://…}waypoint`` -> ``waypoint``."""
    return tag.rsplit("}", 1)[-1]


def _find(elem, name):
    return next((c for c in elem if _local(c.tag) == name), None)


def _findall(elem, name):
    return [c for c in elem if _local(c.tag) == name]


def _root(source) -> ET.Element:
    """Accept a path, a file-like, or a raw XML string/bytes; return the ``<route>`` element."""
    if isinstance(source, (str, Path)) and not _looks_like_xml(source):
        return ET.parse(source).getroot()
    if hasattr(source, "read"):
        return ET.parse(source).getroot()
    data = source.encode() if isinstance(source, str) else source
    return ET.fromstring(data)


def _looks_like_xml(source) -> bool:
    return isinstance(source, str) and source.lstrip()[:1] == "<"


def _nm(value):
    try:
        return float(value) * _NM_TO_M
    except (TypeError, ValueError):
        return None


def parse_rtz(source) -> RtzRoute:
    """Parse an RTZ file/string into an ``RtzRoute`` (leg defaults merged onto each waypoint)."""
    root = _root(source)
    if _local(root.tag) != "route":
        raise ValueError(f"not an RTZ <route> document: <{_local(root.tag)}>")

    version = root.get("version") or "1.0"
    info = _find(root, "routeInfo")
    name = (info.get("routeName") if info is not None else None) or "unnamed RTZ route"

    wps_el = _find(root, "waypoints")
    if wps_el is None:
        raise ValueError("RTZ <route> has no <waypoints>")

    # <defaultWaypoint><leg .../></defaultWaypoint> supplies fallbacks for unset leg attributes.
    default_leg = {}
    dwp = _find(wps_el, "defaultWaypoint")
    if dwp is not None:
        dleg = _find(dwp, "leg")
        if dleg is not None:
            default_leg = dict(dleg.attrib)

    waypoints = []
    for wp in _findall(wps_el, "waypoint"):
        pos = _find(wp, "position")
        if pos is None:
            continue
        leg = _find(wp, "leg")
        leg_attr = {**default_leg, **(dict(leg.attrib) if leg is not None else {})}
        waypoints.append(
            RtzWaypoint(
                id=int(wp.get("id") or len(waypoints) + 1),
                name=wp.get("name") or "",
                lat=float(pos.get("lat")),
                lon=float(pos.get("lon")),
                radius_m=_nm(wp.get("radius")),
                xtd_port_m=_nm(leg_attr.get("portsideXTD")),
                xtd_starboard_m=_nm(leg_attr.get("starboardXTD")),
                geometry_type=leg_attr.get("geometryType") or "Loxodrome",
            )
        )

    if len(waypoints) < 2:
        raise ValueError(f"RTZ route {name!r} has < 2 waypoints")
    return RtzRoute(name=name, waypoints=waypoints, format_version=f"RTZ-{version}")


def rtz_to_planned(source) -> PlannedRoute:
    """Parsed RTZ -> ``PlannedRoute`` (geometry + geodesic legs/metrics) for metric comparison.

    Mirrors ``pathfinding._build_route``: legs carry geodesic ``distance_m``/``bearing_deg`` on
    WGS84; ``cost_breakdown`` is left empty (filled later by validating against a ``nav``).
    """
    rtz = parse_rtz(source) if not isinstance(source, RtzRoute) else source
    coords = rtz.coords

    legs, total_m = [], 0.0
    for (lat0, lon0), (lat1, lon1) in zip(coords, coords[1:]):
        az_fwd, _, seg = _GEOD.inv(lon0, lat0, lon1, lat1)
        total_m += seg
        legs.append({"distance_m": round(seg, 1), "bearing_deg": round(az_fwd % 360.0, 1)})

    return PlannedRoute(
        waypoints=coords,
        legs=legs,
        metrics={"distance_m": round(total_m, 1), "n_waypoints": len(coords)},
        optimization_goal="reference:pilot",
    )


def rtz_to_route(source, *, route_id: str | None = None):
    """Parsed RTZ -> canonical ``keelson.Route`` annotated as an external pilot reference.

    Carries the RTZ per-waypoint radius/XTD/geometry through to the keelson waypoints/legs, and
    tags provenance (author org "Sjöfartsverket") in a ``route-planner`` Extensions entry so the
    benchmark can tell pilot references apart from generated routes. Requires the keelson SDK.
    """
    import uuid

    from keelson.payloads.Route_pb2 import Route

    from ..model import vessel_profile as vp
    from ..model.enums import COASTAL, LOXODROME, ORTHODROME, PLANNED

    rtz = parse_rtz(source) if not isinstance(source, RtzRoute) else source

    route = Route(
        route_id=route_id or uuid.uuid4().hex,
        route_edition_number=1,
        route_format_version=rtz.format_version,
        version="0.1",
        status=PLANNED,
    )
    route.info.name = rtz.name
    route.info.author = "sjofartsverket-pilot"
    route.info.author_organization = "Sjöfartsverket"

    for wp in rtz.waypoints:
        out = route.waypoints.waypoint.add()
        out.id = str(wp.id)  # Waypoint.id is a string in keelson now
        out.name = wp.name
        set_lat_lon(out.position, wp.lat, wp.lon)
        if wp.radius_m is not None:
            out.radius_m = wp.radius_m
        out.operational_context = COASTAL
        out.leg.geometry_type = ORTHODROME if wp.geometry_type == "Orthodrome" else LOXODROME
        if wp.xtd_port_m is not None:
            out.leg.xtd_port_m = wp.xtd_port_m
        if wp.xtd_starboard_m is not None:
            out.leg.xtd_starboard_m = wp.xtd_starboard_m

    entry = route.extensions.entries.add()
    entry.manufacturer = vp.MANUFACTURER
    entry.name = vp.EXTENSION_NAME
    entry.attributes["reference_kind"] = "pilot"
    entry.attributes["reference_source"] = "sjofartsverket-goteborg"
    return route
