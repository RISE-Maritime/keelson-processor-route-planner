# Roadmap

Lives in a separate `keelson-processor-route-planner` repo; emits `keelson.Route`
(RTZ-1.2 / S-421 superset) over Keelson RPC + pub/sub. **Voyage planning + validation — NOT
COLREG collision avoidance, NOT autonomous execution.** Full design in [DESIGN.md](DESIGN.md).

## Phases

- [x] **P0 — Repo inspection + design note.** Inspected chart-server, Keelson, Crowsnest; confirmed
  `keelson.Route`, the chart-server query surface, and the existing Crowsnest route-editor /
  safety-scan. Captured in this `docs/` set.
- [x] **P1 — RPC skeleton + contracts.** Service scaffold (Keelson-connector layout); `RoutePlanner`
  RPC queryable (`plan_route`/`validate_route`/`get_route`/`select_route`); liveliness-based health.
  Contracts use **`keelson.Route` as the canonical type** (keelson protos primary — no parallel
  proto): the vessel profile maps to `RouteInfo` + a `route-planner` `Extensions` entry; the
  `plan_route` request is a `keelson.Route` template, responses are `keelson.Route` (multi-reply);
  in-memory route store backs `get_route`/`select_route`. `plan_route`/`validate_route` reply
  `UNAVAILABLE` pending their engines (P2-P4 / P7). *(Keelson RPC is the only transport — no HTTP.
  Requires the keelson monorepo SDK for `payloads.Route_pb2`.)*
- [x] **P2 — `chart_adapter`.** Direct-PostGIS bulk reads for corridor feature pulls (sync
  `psycopg3`, SELECT-only over `edits.effective_<layer>`) + chart-server HTTP (sync `httpx`) for
  targeted checks (`identify`, `depth`, `grounding-line`, `catalog`). Normalizes S-57 → internal
  layers as Shapely `ChartFeature`s grouped in a `NormalizedChart` (+ provenance). No planning
  logic. See [CHART-ADAPTER.md](CHART-ADAPTER.md). *(Not wired into `plan_route` yet — that's P4.)*
- [x] **P3 — `navigability_engine`.** `build_navigability(chart, NavParams, no_go)` → navigable/
  blocked polygons + weighted `SoftCost` regions + evidence/warnings, in a local metric CRS
  (pyproj AEQD) carried to P4. Positive-water model (navigable taken from charted deep depth-areas)
  with **finest-scale-wins** deconfliction so coarse ENC cells don't override fine detail; hard-block
  (land/unsafe-depth/hazards/entry-prohibited/no-go/unknown-in-conservative) vs. soft-cost
  (shallow-but-legal, outside-fairway, near-land-margin, poor-chart-quality, restricted, unknown).
  Pure Shapely/pyproj. Verified live over the Göteborg corridor (≈15 km² navigable of a 48 km²
  box). *(Turn penalty → P4; AIS/weather → P11.)*
- [x] **P4 — Pathfinding.** `plan(nav, start, end, via, goal, n)` rasterizes the P3 navigable polygon
  + soft-costs to a metric `CostGrid`, runs **A\*** (heapq, 8-connected; Dijkstra fallback) through
  each via-segment, snaps endpoints to navigable water, weights soft-costs by `optimization_goal`,
  and returns N diverse alternatives (corridor-penalty re-runs) as internal `PlannedRoute`s
  (waypoints + metrics + cost breakdown). Hard constraints enforced before cost (off-water cells are
  `inf`). numpy + `shapely.contains_xy`; no networkx/scipy. Verified live: 3 distinct safe routes
  (~3 nm) through the Göteborg corridor in ~0.6 s. *(→ keelson.Route serialization is P5; RPC wiring
  P6; turn-penalty-in-search + AIS/weather P11.)*
- [x] **P5 — Serialize → `keelson.Route`.** `model/serialize.py`: `route_from_planned` /
  `routes_from_alternatives` map a P4 `PlannedRoute` → `keelson.Route` — waypoints (`position`/
  `planned_sog_knots`/`radius_m`/`operational_context`) + outgoing `Leg`s (`safety_depth_m`/
  `ukc_static_m`/`LOXODROME`/`sog_*`), `RouteInfo` (reusing `vessel_profile`), `status`
  (RECOMMENDED/PLANNED), timestamps, and a `se.rise`/`route-planner` `Extensions` entry carrying
  metrics/cost-breakdown/snap-flags/provenance/generation metadata as attributes. Validation
  (`challenges`/`issues`) + `hazard_zones` left for P7. Verified live: adapter→nav→plan→serialize
  emits valid `keelson.Route` alternatives (~1.8 KB each, full provenance). See
  [KEELSON-INTEGRATION.md](KEELSON-INTEGRATION.md) §2.
- [x] **P6 — Keelson integration.** `plan_route` runs the full pipeline (`planner.run_plan`:
  chart_adapter → navigability → pathfinding → serialize), stores + publishes each alternative on
  `route_planned`, and **multi-replies** one `keelson.Route` per alternative (clients query with
  `consolidation=NONE`). `select_route` publishes the chosen `route` + a `RouteStatusUpdate` on
  `route_status`; `get_route` reads the store; `validate_route` stays `UNAVAILABLE` (P7).
  `RoutePublisher` Envelope-frames pub/sub. Verified live end-to-end over Zenoh RPC: 3 alternatives
  (~3 nm) returned + published in ~3 s. *(`export_vehicle_mission` P9; debug-overlay geojson +
  no-go-from-request deferred.)*
- [x] **P7 — Validation engine.** `validation.validate(route, nav)` checks the route geometry vs the
  navigability picture and populates the route in place: `RouteIssue(CRITICAL)` per hard-block
  crossing (a hazard only counts where it is genuinely non-navigable, tolerating the pathfinder's
  one-cell discretization slack), typed `RouteChallenge(WARNING)` per soft-cost / snapped endpoint /
  sharp turn, `status=ROUTE_ISSUES` when critical, + a validation summary (`validation_status`,
  counts, `validated_at`, `edited_after_validation`) on the route-planner `Extensions`. `run_plan`
  validates every alternative; `validate_route` re-validates an edited route by rebuilding
  navigability. Verified live: clean plans carry only warnings, a land-crossing edit → critical
  issues. *(`hazard_zones[]`/no-go echo + bridge clearance + AIS deferred.)*
- [x] **keelson `route_planner/v1` (keelson #187).** The service now implements keelson's own
  `interfaces/RoutePlanner.proto`: `plan_route` takes a typed `PlanRouteRequest` (Coordinates,
  `RouteInfo` constraints, `OptimizationGoal`, `max_alternatives`, `topology`; planner tunings in
  `extensions`), `get_route`/`select_route` take a `RouteRef` naming an exact edition, errors carry
  `ErrorResponse.Code`, and the four procedures are served by `keelson.scaffolding.serve_rpc` (with
  the interface liveliness token). Publication moved off the removed `route_planned` onto the §6.3
  edition tree (`route/{id}/edition/{N}` + `latest`), and `select_route` makes a new edition with
  the §6.4 choreography (`route_change_event`) + `route_status`. keelson is pinned exactly to
  `0.6.0rc19` from PyPI; the monorepo-SDK install is gone. The P1/P6 notes above describe the
  earlier contract.
- [ ] **P8 — Crowsnest route-editor integration.** "Auto-plan route" action (start/end/via by map
  click), goal + vessel-profile + margin selectors, alternatives picker, load selection into
  `atomEditingRoute`, reuse `useRouteSafetyScan`, publish via `useRouteSync`. Wording guardrails.
- [ ] **P9 — (later) `VehicleMission.Mission` export.** Confirmation-gated; no auto-upload / clear /
  arm / set-current / mode-switch.
- [ ] **P10 — (later) MAVLink export** behind `VehicleMission`, with metadata-loss warning.
- [ ] **P11 — (later) PostGIS persistence** (`routes`, `route_versions`, `route_waypoints`,
  `route_legs`, `route_metrics`, `route_validation_checks`, `vessel_profiles`,
  `planner_cost_profiles`, `chart_feature_evidence`); **AIS-density + weather/sea-state cost layers**.
  *(AIS-density cost prior prototyped offline — `off_ais_corridor` soft cost + benchmark harness;
  see [BENCHMARK.md](BENCHMARK.md).)*
- [ ] **P12 — Tests.** Fixtures: open-water; around-island; archipelago; shallow-area block; via-
  points; start/end outside navigable water (snap + warn); manually-edited route crosses land;
  conservative mode prefers fairways/deep water; traffic-aware avoids dense corridor; RPC plan;
  Keelson publication; Crowsnest render; `VehicleMission`/MAVLink roundtrip.

## MVP definition (first working slice)

P1–P8 with: FastAPI plan endpoint → `chart_adapter` → `navigability_engine` (land/depth/no-go) →
A\* → `keelson.Route` output → validation report → publish on `route` (edition tree) → render + select in
the Crowsnest route-editor. **No** MAVLink upload, **no** AIS/weather cost layers, **no** DB
persistence yet.

## Optimization goals (target set)

`shortest` · `fastest` · `safest` · `prefer_fairways` · `avoid_traffic` ·
`archipelago_conservative` · `usv_trial_safe` · `custom`
