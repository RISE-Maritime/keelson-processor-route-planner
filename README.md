# keelson-processor-route-planner

Automatic maritime **voyage route planner** for archipelago navigation. Given a start position,
an end position, optional via-points, a vessel profile, and an optimization goal, it generates
**safe, scored route alternatives**, validates them against S-57 chart data, and emits them as
standard `keelson.Route` artifacts for operator review and (optionally) mission export.

> ⚠️ **Scope & safety.** This is a **voyage-planning and route-validation** system. It is **not**
> real-time COLREG collision avoidance and **not** an autonomous-execution / autopilot controller.
> AIS density, fairways, no-go zones, depth, and S-57 chart features are used as *planning inputs
> only*. A generated route is never "guaranteed safe": every route carries its chart source +
> edition, chart limitations, validation status, warnings, assumptions, the vessel profile used,
> the generation timestamp, and an *edited-after-validation* flag. See
> [docs/DESIGN.md](docs/DESIGN.md#safety--provenance-rule).

## What it does

1. Accepts `start`, `end`, optional `via[]`, a `vessel_profile`, an `optimization_goal`, and
   `constraints`.
2. Queries the existing **S-57 chart server** for relevant features (depth, land, rocks, wrecks,
   obstructions, restricted/caution areas, fairways, TSS, aids, no-go zones).
3. Builds **navigable-water constraints** (hard-block + soft-cost surface) for the vessel.
4. Generates **route alternatives** with A\* pathfinding over a navigable graph/cost surface.
5. **Scores** and **validates** each alternative, attaching machine-readable warnings.
6. Emits each alternative as a `keelson.Route` (best = `RECOMMENDED`, rest = `PLANNED`) and exposes
   GeoJSON geometry for visualization.
7. Publishes the selected route to Keelson subjects and, *only on explicit request*, exports a
   `VehicleMission.Mission` (and later a MAVLink mission).

## Architecture in one paragraph

A standalone Python service (Keelson-connector style) exposing a **Planning API** (a Keelson RPC
queryable — request/response, the only transport; **no HTTP**) and a **publication layer** (Keelson
pub/sub). A `chart_adapter` fetches and normalizes S-57 features from the chart server (bulk reads
direct from PostGIS, targeted checks over the chart server's HTTP/`zenoh` API). A
`navigability_engine` turns those features + the vessel profile into navigable/blocked polygons and
a cost surface. A pathfinder runs A\* over that surface, segmented by via-points. Results serialize
to **`keelson.Route`** (the existing RTZ-1.2 / S-421 superset already defined in Keelson — we do
**not** invent a parallel model) and publish on `route_planned` / `route` / `route_status`. The
[Crowsnest `route-editor`](../crowsnest-dev) gets an **"Auto-plan route"** action that calls the
planner, renders the alternatives, and reuses its existing `useRouteSafetyScan` for re-validation
after manual edits.

```
 Crowsnest route-editor ──"Auto-plan"──▶ RoutePlanner RPC ─┐
                                                           │
        ┌──────────────────────────────────────────────────────────────┐
        │  keelson-processor-route-planner                              │
        │   Planning API (Keelson RPC queryable)                       │
        │      │                                                        │
        │      ▼                                                        │
        │   chart_adapter ──▶ navigability_engine ──▶ pathfinder (A*)   │
        │      │                                          │             │
        │      │                                          ▼             │
        │      │                              serialize → keelson.Route │
        │      ▼                                          │             │
        │   chart-server (PostGIS / /api/* / chart/**)    ▼             │
        └─────────────────────────────────── publish: route_planned ───┘
                                                         route_status
```

## Confirmed design decisions

- **Standalone repo** (this one), wired into chart-server's `docker-compose.yml` for local dev.
- **Adopt `keelson.Route`** as the canonical model (extend Keelson only on a real gap).
- **Extend the Crowsnest `route-editor`** with auto-plan — no parallel UI.
- **Status: P1 skeleton.** The service is scaffolded as a keelson processor and runs — it connects
  to Zenoh, declares a liveliness token, and declares the RoutePlanner RPC queryables (each replies
  "not implemented yet"). No planning logic yet; the engines arrive across P2–P8 (see
  [docs/ROADMAP.md](docs/ROADMAP.md)).

## Quick start

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/). **keelson must be installed from the
monorepo SDK** — the canonical contract type `keelson.Route` (`payloads.Route_pb2`) is not in the
PyPI wheel.

```bash
# Setup. Install keelson from the monorepo first (Route_pb2), then our package + dev tools.
uv pip install ../keelson/sdks/python
uv pip install -e ".[dev]"

# Run against a local keelson/zenoh router (defaults: realm=rise, entity=route-planner)
route_planner --connect tcp/localhost:7447
# On start it declares a liveliness token (the health signal) and the RoutePlanner RPC
# queryables (plan_route / validate_route / get_route / select_route). In the skeleton each
# replies "not implemented yet"; query one with any keelson RPC client, e.g. the key
#   rise/@v0/route-planner/@rpc/plan_route/route-planner/0

# Lint + test
ruff check bin/ src/ tests/
pytest

# Dev container against an already-running host router
docker compose -f docker-compose.dev.yml up --build
```

Runtime config layers (lowest precedence first): built-in defaults < `--config <file.yaml>` (see
[config/example.yaml](config/example.yaml)) < CLI flags. `CHART_SERVER_URL` / `DATABASE_URL` come
from the environment (see [.env.example](.env.example)).

## Navigation risk of a route

With the `risk` extra (`uv pip install -e ".[risk]"`, which takes keelson-processor-navrisk from the
sibling checkout), `validate_route` also runs navrisk's model over the track the route implies
(`src/route_planner/risk.py`, over `navrisk.route`). The result is recorded on the route:

- an Extensions entry `se.rise` / `navrisk-route-risk`, holding the route's level, each dimension's
  worst value or *not measurable* and why, the gates, and per-leg and per-turn profiles as JSON
  attributes;
- a `RouteChallenge` for each failed declared gate, each turn tighter than the turning envelope or
  not fitting between its legs, and each HIGH leg.

Collision is never assessed on a plan: there is no traffic forecast, and the entry says so. Gate
limits are declared per route as `<dimension>_risk_max` attributes on that entry; none is set by
default. The chart database is `NAVRISK_CHART_DSN`, or `DATABASE_URL` when that is unset. It is
opened read-only, and without it the water is not measurable, never zero. `plan_route` assesses its
alternatives too only with `NAVRISK_ON_PLAN=1`, because each assessment queries the chart along the
whole route (about 5 s for 4 km on the local ENCs).

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md) — full architecture, engines, safety rule, deferred phases.
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased plan (P0–P12).
- [docs/KEELSON-INTEGRATION.md](docs/KEELSON-INTEGRATION.md) — `keelson.Route` mapping, RPC sketch,
  key-space, subjects.
- [docs/CHART-ADAPTER.md](docs/CHART-ADAPTER.md) — chart-server query contract & S-57 normalization.

## Related repositories (local dev machine)

| Repo | Role |
|------|------|
| `../chart-server` | S-57 chart service (PostGIS + Martin + tileserver + query-bridge + nginx) |
| `../keelson` | Keelson monorepo — `messages/` (incl. `Route.proto`, `subjects.yaml`), `sdks/python`, `connectors/` |
| `../keelson-router` | Keelson Zenoh router (`tcp/…:7447`, ws `:10000`) |
| `../crowsnest-dev` | Production consumer — chart display + `route-editor` |
