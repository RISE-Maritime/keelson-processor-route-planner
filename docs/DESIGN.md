# Design Note — keelson-processor-route-planner

Status: **design captured, no service code yet.** This note is the authoritative architecture
record produced from a 3-repo inspection (chart-server, Keelson, Crowsnest). Companion docs:
[ROADMAP.md](ROADMAP.md), [KEELSON-INTEGRATION.md](KEELSON-INTEGRATION.md),
[CHART-ADAPTER.md](CHART-ADAPTER.md).

---

## 1. Purpose & scope

Generate **safe, scored voyage-route alternatives** for archipelago navigation from a start/end
(+ optional via-points), a vessel profile, and an optimization goal; validate them against S-57
chart data; and hand them to operators as standard `keelson.Route` artifacts.

**In scope:** route generation, scoring, validation, GeoJSON geometry, Keelson publication,
optional conversion to a `VehicleMission.Mission` (later), optional MAVLink export (later).

**Explicitly out of scope:** real-time COLREG collision avoidance; tactical maneuvering;
autonomous execution / autopilot control; arming, mode-switching, or auto-uploading missions.
The planner *describes* COLREG context on a leg (the `keelson.Leg` carries `colreg_situation` /
`colreg_traffic_intensity` annotation fields) but never performs tactical avoidance.

---

## 2. Findings that shaped this design (premises corrected during inspection)

The original brief assumed a greenfield 12-phase build with new protobufs and a new UI. Inspection
showed most of that already exists, which is why this design is "wire the new generator into what's
there," not "build everything."

1. **Keelson already defines the full route/voyage model.** `keelson/messages/payloads/Route.proto`
   is an explicit **RTZ-1.2 + S-421 superset** with `RouteInfo`, `Waypoints`/`Waypoint`/`Leg`,
   `Schedules`, `ActionPoints`, `HazardZone`, `RouteChallenge`, `RouteIssue`, `RouteSignature`,
   a `RouteStatus` lifecycle enum, and an `Extensions` (vendor `Any`) field on every container.
   `subjects.yaml` already maps `route`, `route_planned`, `route_active`, `route_status`,
   `voyage`/`voyage_active`, `route_execution`, `route_change_event`,
   `route_edit_authority`/`route_edit_request`. → **We adopt `keelson.Route`; we do NOT invent
   `voyage_route` / `route_geojson` protobufs, and no new subject is needed for the MVP.**
2. **Keelson key-space hardcodes `/@v0/`** (no major-version variable). Use the SDK helpers
   `construct_pubsub_key(...)` / `construct_rpc_key(...)` and `enclose`/`uncover` for Envelope
   framing. (Details in [KEELSON-INTEGRATION.md](KEELSON-INTEGRATION.md).)
3. **chart-server already serves the query surface.** `query-bridge/http_app.py` (aiohttp) exposes
   `/api/{identify,radius,bbox,depth,grounding-line,layers,catalog}` behind nginx `:8080`, and
   `query-bridge/zenoh_handlers.py` declares a Keelson-framed `chart/**` queryable. PostGIS holds
   the S-57 tables directly. (Details in [CHART-ADAPTER.md](CHART-ADAPTER.md).)
4. **Crowsnest is not greenfield.** `crowsnest-dev/src/apps/route-editor/` already has a waypoint
   editor, drag-on-chart, turn radius/XTD, `NoGoZonesPanel`, `HazardZonesPanel`, and an automatic
   `useRouteSafetyScan` (`src/services/routeSafetyScan.js`) that sweeps every leg/turn through
   `chartQueryRadius`. Its route model is already `keelson.Route`-shaped, and `useRouteSync`
   publishes to Zenoh. Crowsnest reaches chart-server over **Zenoh RPC** (SharedWorker →
   `ws://localhost:10000`), not HTTP.

**Net:** the genuinely new capability is **automatic route generation** (navigability engine +
pathfinding). Validation, no-go authoring, route editing, and publication mostly exist.

---

## 3. Service shape

A standalone Python service following the Keelson-connector layout:

```
keelson-processor-route-planner/
├── bin/
│   └── route_planner            # main executable (argparse CLI; FastAPI + Keelson session)
├── route_planner/
│   ├── api/                     # FastAPI routes (HTTP planning API)
│   ├── rpc/                     # Keelson queryable handlers (RoutePlanner RPC)
│   ├── chart_adapter/           # fetch + normalize S-57 (NO planning logic)
│   ├── navigability/            # navigable/blocked polygons + cost surface
│   ├── pathfinding/             # graph build + A* / Dijkstra + via segmentation
│   ├── model/                   # internal planning structs + keelson.Route (de)serialization
│   ├── validation/             # RouteChallenge / RouteIssue + provenance
│   └── publish/                 # Keelson pub/sub
├── tests/                       # pytest (unit + e2e against a live Zenoh session)
├── pyproject.toml
├── requirements.txt             # keelson, fastapi, uvicorn, asyncpg, shapely, networkx/scipy, ...
└── Dockerfile                   # vendored Keelson SDK, connector base image
```

Two integration layers:

- **Planning API** — request/response, so both HTTP and Keelson RPC fit naturally:
  - HTTP: `POST /api/routes/plan`, `POST /api/routes/{id}/validate`, `POST /api/routes/{id}/select`,
    `GET /api/routes/{id}`, `GET /api/routes/{id}/geojson`, `GET|POST /api/vessel-profiles`.
  - Keelson RPC queryable: `plan_route`, `validate_route`, `get_route`, `select_route`,
    `export_vehicle_mission`.
- **Publication layer** — Keelson pub/sub: proposed/selected `keelson.Route` on
  `route_planned` / `route`; `RouteStatusUpdate` on `route_status`; optional debug overlays on the
  existing `navigable_waters_geojson` / `no_go_zone_geojson` subjects.

**Runtime config (CLI / env):** `--realm` (base path), `--entity-id`, `--source-id`, Zenoh
`--connect tcp/host:7447`, `DATABASE_URL` (PostGIS), `CHART_SERVER_URL` (HTTP fallback).

**Local-dev wiring:** add a `route-planner` service to chart-server's `docker-compose.yml`
(`depends_on: postgis healthy`, `ZENOH_CONNECT=tcp/host.docker.internal:7447`,
`DATABASE_URL=postgresql://charts:charts@postgis:5432/charts`); optional nginx
`location /api/routes/` proxy. Crowsnest should prefer the **Zenoh RPC** path (matches its existing
transport); the HTTP API is for direct/testing use.

---

## 4. Planning request & response (API contract)

The request mirrors the brief; the response returns `keelson.Route` alternatives (rendered to JSON
over HTTP, protobuf over RPC). Indicative request:

```json
{
  "start": { "lat": 57.661, "lon": 11.804 },
  "end":   { "lat": 57.689, "lon": 11.912 },
  "via":   [ { "lat": 57.67, "lon": 11.85, "name": "optional via point" } ],
  "vessel_profile": {
    "name": "small_usv_1_8m_5kt", "length_m": 1.8, "beam_m": 0.8, "draught_m": 0.25,
    "preferred_speed_kn": 5.0, "max_speed_kn": 6.0, "min_under_keel_clearance_m": 0.5,
    "route_margin_m": 15.0, "turn_radius_m": 5.0
  },
  "optimization_goal": "archipelago_conservative",
  "constraints": {
    "avoid_traffic": false, "prefer_fairways": true, "avoid_narrow_passages": false,
    "avoid_open_sea": true, "avoid_user_no_go_zones": true, "allow_unknown_depth": false
  },
  "return_alternatives": 3
}
```

Each alternative is a `keelson.Route` (see the field-by-field mapping in
[KEELSON-INTEGRATION.md](KEELSON-INTEGRATION.md)). The HTTP `geojson` endpoint derives a
`LineString` / `FeatureCollection` from the route's waypoints for quick visualization; Crowsnest
consumes the `keelson.Route` directly and renders geometry itself.

---

## 5. `chart_adapter` (fetch + normalize only)

No planning logic — only fetch and normalize. Dual-mode, defaulting to whichever is cheaper:

- **Direct PostGIS** (read-only `charts` role; `public.*` + `edits.effective_*`) for **bulk**
  corridor pulls used to build navigable polygons.
- **chart-server HTTP / Zenoh** for **targeted** checks (`grounding-line`, `depth`, `identify`,
  `catalog` for provenance).

Normalizes S-57 into internal layers: land/coastline · depth areas/contours/soundings ·
rocks/wrecks/obstructions · restricted/caution/anchorage · fairways/recommended-tracks/TSS ·
bridges & overhead clearance · aids to navigation · harbour areas · user no-go zones (from
`edits.resare_inserts`, the `no_go_zone_geojson` subject, and the request payload).
Full table/column/endpoint contract: [CHART-ADAPTER.md](CHART-ADAPTER.md).

**Never writes `public.*`.** ENC data must stay reproducible from source `.000` cells (chart-server
invariant).

---

## 6. `navigability_engine`

**Inputs:** normalized features, vessel profile, safety depth, route margin, planning mode, no-go
zones. **Outputs:** navigable-water polygons, blocked polygons, a risk/cost surface, chart-evidence
references, and data-quality warnings.

**Hard-block (geometry the route must not cross):**
land · unsafe depth (`< safety_depth`) · rocks/obstructions/wrecks within configured buffer ·
prohibited/restricted areas (per `RESARE.restrn`) · insufficient bridge/overhead clearance (when
air draught known) · user no-go polygons · unknown-depth regions *in conservative mode*.

**Soft-cost (penalized but legal):**
shallow-but-legal water · narrow passages · outside fairways · high traffic density (later, AIS) ·
exposed open water · poor chart quality (CATZOC / compilation scale) · sharp turns (vs.
`turn_radius_m`) · proximity to land below `route_margin_m`.

Geometry handled with Shapely (buffering, polygon difference, containment). The safe-depth envelope
can be cross-checked against chart-server's `/api/grounding-line` for consistency with the chart's
own contour logic.

---

## 7. Pathfinding

Hybrid graph / cost-surface, initial implementation:

- **Discretization:** navigable-water grid (or a triangulation of the navigable polygon) at a
  resolution scaled to `route_margin_m` and chart scale.
- **Search:** **A\*** with a great-circle heuristic; **Dijkstra** fallback when no admissible
  heuristic applies (e.g. heavy soft-cost fields). **Via-points** segment the search into
  sequential start→via→…→end legs.
- **Cost function** (weights vary by `optimization_goal`):

  ```
  cost = distance_weight       * distance
       + time_weight           * estimated_time
       + shallow_water_weight  * shallow_water_penalty
       + safety_margin_weight  * low_margin_penalty
       + fairway_weight        * non_fairway_penalty
       + traffic_weight        * traffic_density_penalty   (later, AIS)
       + narrowness_weight     * narrow_passage_penalty
       + exposure_weight       * open_sea_penalty
       + turn_weight           * turn_penalty
       + chart_quality_weight  * chart_uncertainty_penalty
  ```

- **Hard constraints are enforced before cost optimization** (blocked cells are simply not
  traversable), so a returned route never crosses blocked geometry.
- **Alternatives:** produce `return_alternatives` diverse routes (e.g. penalize the corridor of an
  already-found route, or vary the weight vector), then post-process (line simplification /
  waypoint reduction respecting turn radius).

---

## 8. Internal model & serialization

A thin internal planning struct (grid path, per-cell cost breakdown, candidate scoring) exists only
during a single `plan` call. The **canonical artifact is `keelson.Route`** — there is no parallel
persisted model. Mapping is in [KEELSON-INTEGRATION.md](KEELSON-INTEGRATION.md); planner-specific
data with no native home (cost weights, scoring metrics, chart evidence, snapped start/end,
generation metadata) goes into an `Extensions` entry (`manufacturer: "se.rise"`,
`name: "route-planner"`), to be promoted to a typed Keelson message only if it stabilizes.

---

## 9. Validation engine

Every route is validated **after generation and after any manual edit**. Validation populates the
route's own structured fields rather than a side channel:

- **Hard checks → `RouteIssue` (severity `CRITICAL`) / fail:** no land crossing; no unsafe depth;
  no blocked restricted areas; no rocks/wrecks/obstructions within margin; sufficient bridge
  clearance (if air draught known); no unknown-depth crossing unless `allow_unknown_depth`; turn
  radius respected (if supplied); no segment outside chart coverage without a warning; no crossing
  of a user no-go zone.
- **Soft checks → `RouteChallenge` (typed) / warning:** narrow passages; shallow margins; high
  traffic exposure (later); poor chart quality; start/end snapped to navigable water.

`RouteChallenge.type` uses the existing enum (`SHALLOW_WATER`, `TRAFFIC_DENSITY`, `WEATHER_HAZARD`,
`REGULATORY_LIMIT`, `TECHNICAL_LIMIT`, …) with `waypoint_ids` scoping. An overall pass/warning/fail
status maps to `RouteStatus`. Crowsnest's existing `useRouteSafetyScan` provides client-side
re-validation after manual edits — the server engine and the client scan should agree on hazard
categories.

---

## 10. Crowsnest integration (extend `route-editor`)

Add an **"Auto-plan route"** action to the existing `route-editor` app:

1. Pick `start` / `end` / `via` by clicking the chart (reuse the `Chart.jsx` / `SeaChart.jsx`
   `onClick` + context-menu pattern).
2. Choose vessel profile + optimization goal + safety margin in a small auto-plan panel.
3. Call `plan_route` via the SharedWorker **Zenoh-RPC** bridge (extend the `chartServerQuery.js`
   `rpcGet` style).
4. Render the returned `Route` alternatives; let the operator inspect distance/time/min-depth/
   safety score/warnings/chart evidence.
5. On selection, load the chosen route into `atomEditingRoute`; reuse `useRouteSafetyScan` to
   re-validate after manual edits; publish via the existing `useRouteSync`.
6. Reuse `NoGoZonesPanel` / `HazardZonesPanel` for no-go authoring (these become request input
   and/or `HazardZone[]` on the route).

**Wording guardrails (UI copy):** use "proposed route", "route alternative", "selected voyage
route", "validation warning", "export mission", "publish route". **Avoid** "guaranteed safe",
"autonomous execution", "collision-avoidance route", and "autopilot route" except when explicitly
exporting to a vehicle mission.

---

## 11. Deferred phases (designed, not built in the MVP)

- **`VehicleMission.Mission` export.** `Route → VehicleMission.Mission`: each waypoint →
  `MissionItem.waypoint` (`acceptance_radius_m`, `hold_time_s`); planned speed change →
  `MissionItem.change_speed`; optional return → `return_home`; optional hold → `loiter`. **Export
  only.** Endpoints `POST /api/routes/{id}/export/keelson-mission` and `…/upload-vehicle-mission`
  (the upload is confirmation-gated). Never auto-clear, auto-set-current, switch mode, or arm.
- **MAVLink.** Adapter *behind* `VehicleMission` (`Route → VehicleMission.Mission → MAVLink`), with
  a metadata-loss warning (depth margin, fairway preference, validation evidence, UKC, narrow-
  passage warnings, chart references, traffic exposure are not representable in a MAVLink mission).
- **Storage (PostGIS).** `routes`, `route_versions`, `route_waypoints`, `route_legs`,
  `route_metrics`, `route_validation_checks`, `route_requests`, `vessel_profiles`,
  `planner_cost_profiles`, `chart_feature_evidence`, with PostGIS geometry columns. Own DB or a
  `routes` schema; routes also remain Keelson artifacts.
- **Cost layers.** AIS density and weather/sea-state as additional soft-cost fields.

---

## 12. Safety / provenance rule

A generated route is **never** presented as guaranteed safe. Every route always carries, and the UI
always surfaces:

- chart data source + edition (from `/api/catalog` → `public.dsid_versions`);
- chart limitations (coverage gaps, CATZOC / data quality, compilation scale);
- validation status + machine-readable warnings;
- route assumptions (safety depth, margin, planning mode, cost weights);
- the vessel profile used;
- the generation date/time;
- an **edited-after-validation** flag.

Stored across `RouteInfo` (author, validity window, vessel constraints, optimization method,
`change_history`), `RouteIssue` / `RouteChallenge`, and the `route-planner` `Extensions` block.

---

## 13. Testing strategy (P12)

pytest, unit + e2e (e2e against a live Zenoh session, mirroring Keelson SDK/connector test layout).
Fixtures: open-water · around-island · archipelago · shallow-area block · via-points · start/end
outside navigable water (snap + warn) · manually-edited route crossing land · conservative mode
prefers fairways/deep water · traffic-aware avoids dense corridor (later) · RPC plan_route ·
Keelson `route_planned` publication · Crowsnest render · `VehicleMission`/MAVLink roundtrip.
