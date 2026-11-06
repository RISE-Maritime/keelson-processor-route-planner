"""keelson-processor-route-planner.

A keelson processor that generates safe, scored **voyage-route alternatives** for archipelago
navigation, validates them against S-57 chart data, and emits them as standard ``keelson.Route``
artifacts over **Keelson RPC** (the sole integration transport — no HTTP). Voyage planning +
validation only — **not** COLREG collision avoidance, **not** autonomous execution. See
docs/DESIGN.md.

Module map (subpackages are filled in across the phases in docs/ROADMAP.md):
- app.py          wiring: args, config, zenoh session, liveliness, RPC queryables, idle loop (P1)
- config.py       runtime config: defaults < YAML file < CLI args, + env URLs           (P1)
- rpc/            keelson route_planner/v1 server (plan/validate/get/select) via serve_rpc (P1+)
- chart_adapter/  fetch + normalize S-57 features (PostGIS bulk + chart-server HTTP)      (P2)
- navigability/   navigable/blocked polygons + risk/cost surface                          (P3)
- pathfinding/    water-graph build + A*/Dijkstra + via-point segmentation                (P4)
- model/          internal planning structs + keelson.Route (de)serialization            (P5)
- validation/     RouteChallenge / RouteIssue + provenance                                (P7)
- publish/        keelson pub/sub: route editions + latest, route_change_event, route_status (P6)
"""

__version__ = "0.1.0"
