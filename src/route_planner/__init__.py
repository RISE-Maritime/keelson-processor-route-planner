"""keelson-processor-route-planner.

A keelson processor that generates safe, scored **voyage-route alternatives** for archipelago
navigation, validates them against S-57 chart data, and emits them as standard ``keelson.Route``
artifacts. Voyage planning + validation only — **not** COLREG collision avoidance, **not**
autonomous execution. See docs/DESIGN.md.

Module map (subpackages are filled in across the phases in docs/ROADMAP.md):
- app.py          wiring: args, config, zenoh session, liveliness, HTTP API, idle loop  (P1)
- config.py       runtime config: defaults < YAML file < CLI args, + env URLs           (P1)
- api/            FastAPI planning API (HTTP) — /health now; planning endpoints later     (P1+)
- rpc/            keelson RoutePlanner queryable (plan/validate/get/select/export)        (P6)
- chart_adapter/  fetch + normalize S-57 features (PostGIS bulk + chart-server HTTP)      (P2)
- navigability/   navigable/blocked polygons + risk/cost surface                          (P3)
- pathfinding/    water-graph build + A*/Dijkstra + via-point segmentation                (P4)
- model/          internal planning structs + keelson.Route (de)serialization            (P5)
- validation/     RouteChallenge / RouteIssue + provenance                                (P7)
- publish/        keelson pub/sub (route_planned / route / route_status)                  (P6)
"""

__version__ = "0.1.0"
