"""Internal model — keelson.Route is the canonical artifact (keelson protos are primary).

P1 contracts live here:
- store.py           in-memory ``RouteStore`` (route_id -> keelson.Route)
- vessel_profile.py  vessel profile <-> RouteInfo + route-planner Extensions mapping

A thin internal planning struct (grid path, per-cell cost breakdown, candidate scoring) lives only
during a single ``plan`` call; planner-specific data rides in a route ``Extensions`` entry
(``manufacturer: "se.rise"``, ``name: "route-planner"``). The internal-struct ⇄ keelson.Route
serialization beyond this mapping is P5. See docs/KEELSON-INTEGRATION.md §2-3.
"""
