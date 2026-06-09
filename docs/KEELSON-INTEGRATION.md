# Keelson Integration

How the route planner speaks Keelson: the canonical `keelson.Route` model, the planner→Route field
mapping, the proposed `RoutePlanner` RPC interface, the key-space, and the publication subjects.
Verified against `keelson/messages/payloads/Route.proto`, `keelson/messages/subjects.yaml`, and
`keelson/sdks/python/keelson/` (SDK v0.5.1).

---

## 1. Canonical model: `keelson.Route`

`keelson.Route` (in `messages/payloads/Route.proto`) is an explicit **RTZ-1.2 + S-421 superset**.
We adopt it as-is. Top-level shape (abridged):

```
Route
  string        version
  string        route_id                     # UUID
  uint32        route_edition_number          # bump on every edit
  string        route_format_version          # e.g. "RTZ-1.2", "S-421-FC1.0"
  RouteStatus   status                        # INITIAL|PLANNED|RECOMMENDED|ACKNOWLEDGED|...
  Timestamp     created_at / modified_at
  RouteInfo     info                          # name, author, validity window, vessel constraints,
                                              #   operational limits, optimization_method, AutonomyLevel
  Waypoints     waypoints                     # defaults + repeated Waypoint (each with outgoing Leg)
  Schedules     schedules                     # repeated Schedule (ETA/ETD windows, env forecast, fuel)
  ActionPoints  action_points                 # reporting / UKCM / pilot-boarding triggers
  HazardZone[]  hazard_zones                  # STATIC_NOGO | DYNAMIC_NOGO | TRAFFIC | WEATHER | ...
  RouteChallenge[] challenges                  # typed structured risk (SHALLOW_WATER, TRAFFIC_DENSITY, ...)
  RouteIssue[]  issues                        # free-form issues w/ severity
  RouteSignature[] signatures                  # ed25519 / ecdsa attestations
  Extensions    extensions                    # vendor Any payloads (escape hatch)
```

Key sub-messages we use heavily:

- **`RouteInfo`** — `name`, `author`, `author_organization`, `valid_from`/`valid_to`, and
  **vessel-class plan constraints** `vessel_loa_m` / `vessel_beam_m` / `vessel_draft_m` /
  `vessel_air_draft_m`; operational limits (`max_wave_height_m`, `max_wind_speed_mps`,
  `min_speed_knots`, `max_speed_knots`, …); `optimization_method` (free-form label);
  `autonomy_level` (`AutonomyLevel`); `change_history`.
- **`Waypoint`** — `id`, `name`, `position` (`foxglove.LocationFix`), `planned_sog_knots`,
  `radius_m`, `rate_of_turn_degps`, `wheel_over_distance_m`, `operational_context`
  (`OPEN_SEA`/`COASTAL`/`ARCHIPELAGO`/`PORT_APPROACH`/`PORT_AREA`/`RIVER_WATERS`/`LOCK`),
  outgoing `leg`.
- **`Leg`** — XTD limits (`xtd_port_m`/`xtd_starboard_m` + monitoring variants),
  `safety_contour_m`, `safety_depth_m`, `ukc_static_m`/`ukc_dynamic_m`, planned draughts,
  `geometry_type` (`LOXODROME`/`ORTHODROME`), `sog_*`/`stw_*` envelopes, dimensional limits
  (`air_draft_max_m`, `beam_max_m`, …), COLREG annotations (`colreg_situation`,
  `colreg_traffic_intensity`).
- **`HazardZone`** — `type`, `severity`, `description`, point+radius **or** `polygon`
  (`foxglove.GeoJSON`), validity window.
- **`RouteChallenge`** — `type` (`RouteChallengeType`), `severity` (`SeverityLevel`),
  `description`, `waypoint_ids[]`.
- **`Extensions.Entry`** — `manufacturer`, `name`, `version`, `payload` (`Any`),
  `attributes` (`map<string,string>`).

> Note: lifecycle/authoring state lives on `RouteStatus`; execution state (sailing/paused/
> completed) lives on `keelson.Voyage.VoyageStatus` and is intentionally **not** duplicated on
> `Route`. The planner only deals with the authoring side.

---

## 2. Planner → `keelson.Route` mapping

| Planner concept | `keelson.Route` target |
|---|---|
| Alternative set | N `Route` artifacts. Best → `status = RECOMMENDED`; others → `PLANNED`. Each gets its own `route_id`; `route_edition_number = 1` on first emit. |
| Waypoint | `Waypoints.waypoint[]` — `position` (`foxglove.LocationFix`), `radius_m` (from `turn_radius_m`), `planned_sog_knots` (from `preferred_speed_kn`), `operational_context` (e.g. `ARCHIPELAGO`). |
| Leg | `Waypoint.leg` — `safety_contour_m` / `safety_depth_m` (from vessel profile), `ukc_static_m` (`min_under_keel_clearance_m`), `geometry_type = LOXODROME`, `sog_min/max_knots`. |
| Vessel profile | `RouteInfo.vessel_loa_m` ← `length_m`, `vessel_beam_m` ← `beam_m`, `vessel_draft_m` ← `draught_m`; speed limits → `min/max_speed_knots`. Margin/turn-radius/UKC that have no `RouteInfo` home go into the `Extensions` block. |
| Optimization goal | `RouteInfo.optimization_method` (e.g. `"archipelago_conservative"`) + `autonomy_level`. |
| Constraints (avoid_traffic, prefer_fairways, …) | Reflected in cost weights (→ `Extensions`) and in which features became hard-blocks vs. soft-costs. |
| Validation: hard failures | `Route.issues[]` (`RouteIssue`, `severity = CRITICAL`). |
| Validation: warnings | `Route.challenges[]` (`RouteChallenge`, typed + `waypoint_ids`). |
| No-go zones | `Route.hazard_zones[]` (`STATIC_NOGO` / `DYNAMIC_NOGO`, `polygon`). |
| Metrics (distance_nm, est_time, min_depth, scores, cost), cost weights, chart evidence, snapped start/end, generation metadata | One `Extensions.Entry` (`manufacturer:"se.rise"`, `name:"route-planner"`, `version:"0.1"`) — typed `Any` payload + `attributes`. **No proto change required.** |
| Provenance (chart source/edition, limitations, vessel profile, generation time, edited-after-validation) | `RouteInfo` (`author`, `valid_*`, `change_history`) + `RouteIssue` + the `Extensions` block. |
| UI line geometry | Derived from `waypoints` by the consumer. Crowsnest renders `keelson.Route` directly → **no `route_geojson` subject needed.** |

**Optional Keelson extensions (only if a real gap appears).** If `Extensions` proves insufficient
for planner metrics, propose a typed `RoutePlannerMetrics` message upstream and attach it via the
`Any` payload first. The user has approved modifying Keelson if needed, but the bar is "Extensions
genuinely can't carry it."

---

## 3. `RoutePlanner` RPC interface — `keelson.Route` is the contract

Route planning is request/response, so a Keelson **queryable** (not a stream) is the right fit.
**We do not vendor a `RoutePlanner.proto`.** keelson's own protobufs are the canonical, primary
definitions: every procedure's request and response is a `keelson.Route` (plus `ErrorResponse` for
errors). Planner inputs that have no native `Route`/`RouteInfo` home ride in a route `Extensions`
entry. (This also sidesteps a hard blocker: the `Route.proto` *source* isn't distributed — only the
generated `Route_pb2` in the monorepo SDK — so a wrapper proto importing it can't be compiled.)

The four procedures (keys `…/@rpc/{procedure}/{source_id}`; `export_vehicle_mission` is a later
phase):

| Procedure | Request (bare protobuf) | Reply(ies) |
|---|---|---|
| `plan_route` | a `keelson.Route` **template**: start/end/via as `Waypoints.waypoint[]` positions; vessel constraints in `RouteInfo`; planner-only inputs (`optimization_goal`, margin, turn radius, UKC, preferred speed, `avoid_*`/`prefer_*`, `return_alternatives`) in an `Extensions.Entry` (`manufacturer="se.rise"`, `name="route-planner"`, `attributes` map). | **one `keelson.Route` per alternative** (Zenoh multi-reply): best `status=RECOMMENDED`, rest `PLANNED`. |
| `validate_route` | a `keelson.Route` to check. | the same `keelson.Route` with `challenges[]` / `issues[]` populated. |
| `get_route` | a `keelson.Route` carrying `route_id`. | the stored `keelson.Route`, or `ErrorResponse(NOT_FOUND)`. |
| `select_route` | a `keelson.Route` carrying `route_id`. | the stored `keelson.Route` with `status` advanced + `route_edition_number` bumped (and, P6, published). |

`plan_route` returning multiple alternatives uses Zenoh's native **multi-reply** (the queryable
calls `query.reply(...)` once per alternative) rather than a wrapper `PlanRouteResponse` message.
Because the alternatives all reply on the same key, **clients must query with
`consolidation=NONE`** or Zenoh collapses them to a single (latest) reply. Each alternative is also
published on `route_planned` (§5) and stored, so `get_route` can fetch any by id afterwards.

> **Status (P6).** `plan_route` runs the full pipeline and replies/stores/publishes alternatives;
> `select_route` publishes the chosen `route` + a `RouteStatusUpdate` on `route_status`; `get_route`
> reads the store. `validate_route` replies `ErrorResponse(UNAVAILABLE)` — its engine is P7. Typed
> `keelson.Route` requires the **monorepo** SDK (the PyPI `keelson==0.5.x` wheel lacks
> `payloads.Route_pb2`).

---

## 4. Key-space (corrected) & framing

Keelson **hardcodes `/@v0/`** — there is no major-version variable. Always build keys with the SDK
helpers; never hand-format.

```python
import keelson

# pub/sub: {base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}[/@target/{target_id}]
pub_key = keelson.construct_pubsub_key(base_path, entity_id, "route_planned", source_id)

# rpc:     {base_path}/@v0/{entity_id}/@rpc/{procedure}/{responder_id}
rpc_key = keelson.construct_rpc_key(base_path, entity_id, "plan_route", responder_id)
# e.g. rise/@v0/landkrabba_two/@rpc/plan_route/route_planner_0
```

Framing — **pub/sub** payloads are Envelope-framed (`enclose`/`uncover`); **RPC** requests and
replies are **bare protobuf, no Envelope**, matching the keelson connectors
(`connectors/{mavlink,mcap}`, `keelson-processor-supervisor/bin/rpcs.py`):

```python
# pub/sub — Envelope-framed
session.declare_publisher(pub_key).put(keelson.enclose(route.SerializeToString()))

# rpc responder — bare protobuf; reply takes (key, bytes); errors go on the error channel
def on_plan(query):
    req = PlanRouteRequest.FromString(bytes(query.payload.to_bytes()))
    try:
        resp = plan(req)                              # → PlanRouteResponse
        query.reply(rpc_key, resp.SerializeToString())
    except Exception as exc:
        query.reply_err(ErrorResponse(error_description=str(exc)).SerializeToString())
session.declare_queryable(rpc_key, on_plan, complete=True)

# rpc client — request + reply are bare protobuf
for reply in session.get(rpc_key, payload=req.SerializeToString()):
    if reply.ok is not None:
        resp = PlanRouteResponse.FromString(bytes(reply.ok.payload.to_bytes()))
    else:                                              # ErrorResponse on the error channel
        err = ErrorResponse.FromString(bytes(reply.err.payload.to_bytes()))
```

`ErrorResponse` is `keelson.interfaces.ErrorResponse_pb2.ErrorResponse`. Zenoh session opens in
`client` mode toward the Keelson router (`tcp/…:7447`), matching the chart-server query-bridge
convention.

> **SDK note.** Typed `keelson.Route` / `PlanRouteResponse` (de)serialization needs the keelson
> **monorepo** SDK — the pinned PyPI `keelson==0.5.1` wheel ships `construct_rpc_key` and
> `ErrorResponse` but **not** `payloads.Route_pb2`. Switch the dependency before P5
> (e.g. `uv pip install ../keelson/sdks/python`).

---

## 5. Publication subjects (all already in `subjects.yaml`)

| Subject | Type | Planner use |
|---|---|---|
| `route_planned` | `keelson.Route` | Each proposed alternative (status `PLANNED`/`RECOMMENDED`). |
| `route` | `keelson.Route` | A route once selected by the operator. |
| `route_status` | `keelson.RouteStatusUpdate` | Lightweight status broadcasts (`route_id`, `route_edition_number`, `status`, `changed_by`, `note`). |
| `navigable_waters_geojson` | `keelson.TimestampedGeoJSON` | *Optional* debug overlay of the navigable-water polygons. |
| `no_go_zone_geojson` | `keelson.TimestampedGeoJSON` | *Optional* echo of the no-go zones used. |

`route_active` / `voyage` / `route_execution` are **execution-side** subjects owned by the autopilot/
voyage layer — the planner does **not** publish to them. **No route command is ever published to an
autopilot subject.**

---

## 6. Crowsnest consumption (existing transport)

Crowsnest already subscribes to `route`/`route_planned` via `useRouteSync` and talks to chart-server
over Zenoh RPC through a SharedWorker (`ws://localhost:10000`). The "Auto-plan route" action calls
`plan_route` over that same RPC bridge (a small extension of the `chartServerQuery.js` `rpcGet`
helper), receives `PlanRouteResponse.alternatives`, and renders them with the existing route
geometry layers — no new transport and no HTTP dependency.
