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
- [ ] **P2 — `chart_adapter`.** Direct-PostGIS bulk reads for corridor feature pulls + chart-server
  HTTP/`zenoh` for targeted checks (`grounding-line`, `depth`, `catalog`). Normalize S-57 → internal
  layers. See [CHART-ADAPTER.md](CHART-ADAPTER.md). No planning logic in this module.
- [ ] **P3 — `navigability_engine`.** Navigable/blocked polygons + risk/cost surface from features +
  vessel profile + safety depth + margin + mode + no-go. Hard-block vs. soft-cost split.
- [ ] **P4 — Pathfinding.** Water grid (or triangulation) + **A\*** (Dijkstra fallback); via-point
  segmentation; weighted cost; N alternatives. Hard constraints enforced before cost optimization.
- [ ] **P5 — Serialize → `keelson.Route`.** Map waypoints/legs/profile/goal/validation/no-go to the
  proto; planner-specific metrics/evidence/cost-weights into an `Extensions` entry. See
  [KEELSON-INTEGRATION.md](KEELSON-INTEGRATION.md).
- [ ] **P6 — Keelson integration.** `RoutePlanner` RPC queryable (`plan_route`/`validate_route`/
  `get_route`/`select_route`/`export_vehicle_mission`); publish `route_planned`/`route` +
  `RouteStatusUpdate` on `route_status`. Optional debug overlays on `navigable_waters_geojson` /
  `no_go_zone_geojson`.
- [ ] **P7 — Validation engine.** Populate typed `RouteChallenge[]` + `RouteIssue[]`; provenance
  (chart source/edition, limitations, assumptions, vessel profile, generation time) +
  edited-after-validation flag. Re-validate after every generation and every manual edit.
- [ ] **P8 — Crowsnest route-editor integration.** "Auto-plan route" action (start/end/via by map
  click), goal + vessel-profile + margin selectors, alternatives picker, load selection into
  `atomEditingRoute`, reuse `useRouteSafetyScan`, publish via `useRouteSync`. Wording guardrails.
- [ ] **P9 — (later) `VehicleMission.Mission` export.** Confirmation-gated; no auto-upload / clear /
  arm / set-current / mode-switch.
- [ ] **P10 — (later) MAVLink export** behind `VehicleMission`, with metadata-loss warning.
- [ ] **P11 — (later) PostGIS persistence** (`routes`, `route_versions`, `route_waypoints`,
  `route_legs`, `route_metrics`, `route_validation_checks`, `vessel_profiles`,
  `planner_cost_profiles`, `chart_feature_evidence`); **AIS-density + weather/sea-state cost layers**.
- [ ] **P12 — Tests.** Fixtures: open-water; around-island; archipelago; shallow-area block; via-
  points; start/end outside navigable water (snap + warn); manually-edited route crosses land;
  conservative mode prefers fairways/deep water; traffic-aware avoids dense corridor; RPC plan;
  Keelson publication; Crowsnest render; `VehicleMission`/MAVLink roundtrip.

## MVP definition (first working slice)

P1–P8 with: FastAPI plan endpoint → `chart_adapter` → `navigability_engine` (land/depth/no-go) →
A\* → `keelson.Route` output → validation report → publish on `route_planned` → render + select in
the Crowsnest route-editor. **No** MAVLink upload, **no** AIS/weather cost layers, **no** DB
persistence yet.

## Optimization goals (target set)

`shortest` · `fastest` · `safest` · `prefer_fairways` · `avoid_traffic` ·
`archipelago_conservative` · `usv_trial_safe` · `custom`
