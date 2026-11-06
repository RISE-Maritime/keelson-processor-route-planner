# Keelson Integration

How the route planner speaks Keelson: the canonical `keelson.Route` model, the planner→Route field
mapping, the `route_planner/v1` RPC interface, the key-space, and the publication subjects.
Verified against keelson `0.6.0-pre.19` (PyPI `keelson==0.6.0rc19`, pinned exactly in
`pyproject.toml`): `messages/payloads/Route.proto`, `interfaces/RoutePlanner.proto`,
`messages/subjects.yaml` and protocol-specification.md §3, §6.3, §6.4 and §6.7.

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

## 3. `route_planner/v1` RPC interface

Route planning is request/response, so a Keelson **queryable** (not a stream) is the right fit. The
contract is keelson's own **`interfaces/RoutePlanner.proto`**, registered in keelson's
`interfaces.yaml` as **`route_planner/v1`**; protocol-specification.md §6.7 states the three
properties the proto cannot (below). The planner serves it with `keelson.scaffolding.serve_rpc`
(`rpc/__init__.py`), which declares one queryable per procedure, the interface-level liveliness
token, audit logging, and a typed `INTERNAL` reply if a handler raises.

| Procedure | Request (bare protobuf) | Reply(ies) |
|---|---|---|
| `plan_route` | `PlanRouteRequest`: ordered `waypoints` (`keelson.Coordinate`; first = departure, last = destination, between = via-points), vessel `constraints` (`RouteInfo`), a typed `goal` (`OptimizationGoal`), `max_alternatives` (0 = planner default, one), `topology`, and `extensions`. | **one `keelson.Route` per alternative** (Zenoh multi-reply), never more than `max_alternatives`: best `status=RECOMMENDED`, rest `PLANNED`. |
| `validate_route` | a `keelson.Route` to check. | the same `keelson.Route` with `challenges[]` / `issues[]` / `status` populated. |
| `get_route` | `keelson.RouteRef` (`route_id` + `route_edition_number`). | that exact edition, or `ErrorResponse(NOT_FOUND)`. A dangling edition is never resolved to the latest. |
| `select_route` | `keelson.RouteRef` of the route's latest edition. | a **new edition** with `status` advanced (`ACKNOWLEDGED`); the selected edition is kept unchanged. |

The three properties the proto cannot carry:

* **`plan_route` is a multi-reply.** The handler replies once per alternative on the same key, not a
  list message, so **clients must query with `consolidation=NONE`** or Zenoh collapses the set to
  one reply. Each alternative is also stored and published on `route` (§5), so `get_route` can
  fetch any of them afterwards.
* **Requests and replies are bare protobuf**, not Envelope-wrapped.
* **`select_route` mutates.** It bumps an edition, so it runs the §6.4 choreography (§5 below) — the
  reply is not the whole effect.

**Planner-only inputs** ride in `PlanRouteRequest.extensions` as one `Extensions.Entry`
(`manufacturer="se.rise"`, `name="route-planner"`, string `attributes`): preferred speed, UKC,
route margin, turn radius, the `constraint.*` booleans (`avoid_*` / `prefer_*`), and `goal` — a cost
profile keelson's enum deliberately does not name (`pilot_matched`, `usv_trial_safe`, `custom`).
Each `OptimizationGoal` maps to the planner cost profile of the same name
(`OPTIMIZATION_GOAL_SAFEST` → `safest`); `UNSPECIFIED` means `shortest`. An unknown extension goal
is refused rather than silently falling back to the default weights.

**Errors** are typed `ErrorResponse.Code`s:

| Code | When |
|---|---|
| `INVALID_ARGUMENT` | unparseable request; fewer than two waypoints; a `RouteRef` without an edition; an unknown extension goal, or a goal given twice |
| `UNSUPPORTED` | `topology = CLOSED` — the planner does not plan circuits |
| `UNAVAILABLE` | no navigable route found for the request |
| `NOT_FOUND` | unknown route id, or an edition that is not stored |
| `INVALID_STATE` | `select_route` on an edition that is no longer the latest |
| `INTERNAL` | anything else (from `serve_rpc`'s dispatcher) |

---

## 4. Key-space & framing

Always build keys with the SDK helpers; never hand-format.

```python
import keelson

# pub/sub: {base_path}/@v0/{entity_id}/pubsub/{subject}/{source_id}
edition_key = keelson.construct_pubsub_key(base_path, entity_id, "route", f"{route_id}/edition/{n}")

# rpc:     {base_path}/@v0/{entity_id}/@rpc/{interface}/{version}/{procedure}/{responder_id}
rpc_key = keelson.construct_rpc_key(base_path, entity_id, "route_planner", "v1", "plan_route", source_id)
# e.g. rise/@v0/route-planner/@rpc/route_planner/v1/plan_route/route-planner/0
```

Framing — **pub/sub** payloads are Envelope-framed (`enclose`/`uncover`); **RPC** requests and
replies are **bare protobuf, no Envelope**. A client:

```python
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.payloads.Route_pb2 import Route
from route_planner.model.vessel_profile import plan_request

req = plan_request([(57.70, 11.80), (57.72, 11.86)], profile=profile, goal="safest", max_alternatives=3)
for reply in session.get(rpc_key, payload=req.SerializeToString(),
                         consolidation=zenoh.ConsolidationMode.NONE):
    if reply.ok is not None:
        route = Route.FromString(bytes(reply.ok.payload.to_bytes()))
    else:                                              # ErrorResponse on the error channel
        err = ErrorResponse.FromString(bytes(reply.err.payload.to_bytes()))
        # err.code is an ErrorResponse.Code
```

Zenoh session opens in `client` mode toward the Keelson router (`tcp/…:7447`), matching the
chart-server query-bridge convention.

**Liveliness:** the source-level token, plus the `route_planner/v1` interface token from
`serve_rpc`. No subject-level tokens: the route keys below carry no producer chunk, so there is no
producer prefix to declare one at (protocol-specification.md §2.1.1).

---

## 5. Publication subjects

Routes are published in keelson's edition key tree (protocol-specification.md §6.3). Lifecycle is
`Route.status`, not a separate subject — there is no `route_planned` (removed by keelson #141).

| Key (under `pubsub/`) | Type | Planner use |
|---|---|---|
| `route/{route_id}/edition/{N}` | `keelson.Route` | Every edition, immutable once written: each planned alternative as edition 1 (`PLANNED`/`RECOMMENDED`), each selection as N+1 (`ACKNOWLEDGED`). |
| `route/{route_id}/latest` | `keelson.Route` | Pointer to the highest edition, published right after it. |
| `route_change_event/{route_id}/{change_id}` | `keelson.RouteChangeEvent` | The audit record of an edition bump (`select_route`); `author_id` = the planner's source id. |
| `route_status/{route_id}` | `keelson.RouteStatusUpdate` | Lightweight status broadcast after a selection. |

`select_route` publishes in the §6.4 order: edition N+1, `latest`, `route_change_event`, then
`route_status`. A planned alternative's edition 1 has no prior edition, so it gets no change event.

`voyage` / `route_execution` are **execution-side** subjects owned by the autopilot/voyage layer —
the planner does **not** publish to them. **No route command is ever published to an autopilot
subject.**

---

## 6. Crowsnest consumption

Crowsnest reads routes from the same §6.3 key tree (`services/routeSync.js`) and calls `plan_route`
with `rpcKeyFor("route_planner/v1")` over its SharedWorker multi-reply bridge
(`services/routePlannerRpc.js`). **Its request builder still sends the old `keelson.Route`
template**; it has to send a `PlanRouteRequest` before it can plan against this version.
