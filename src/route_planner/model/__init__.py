"""Internal model — keelson.Route is the canonical artifact (keelson protos are primary).

Modules here:
- store.py           in-memory ``RouteStore`` (route_id -> keelson.Route)               (P1)
- vessel_profile.py  vessel profile <-> RouteInfo + route-planner Extensions mapping     (P1)
- serialize.py       PlannedRoute (pathfinding) -> keelson.Route                         (P5)

The canonical artifact is ``keelson.Route``; planner-specific data rides in a route ``Extensions``
entry (``manufacturer: "se.rise"``, ``name: "route-planner"``). See docs/KEELSON-INTEGRATION.md §2-3.
"""

from .serialize import route_from_planned, routes_from_alternatives
from .store import RouteStore

__all__ = ["RouteStore", "route_from_planned", "routes_from_alternatives"]
