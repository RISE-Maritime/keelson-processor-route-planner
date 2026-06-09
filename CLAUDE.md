# CLAUDE.md — keelson-processor-route-planner

Guidance for Claude Code (and humans) working in this repo. Keep it current.

## What this project is

A **keelson processor**: a long-running Python service that generates safe, scored **voyage-route
alternatives** for archipelago navigation, validates them against S-57 chart data, and emits them
as standard `keelson.Route` artifacts over keelson RPC + pub/sub. Its request/response transport is
a **keelson RoutePlanner RPC queryable** (the path Crowsnest uses) — **the only transport, no
HTTP** — plus a pub/sub publication layer. It runs on our backend as a Docker container.

> **Scope & safety.** Voyage planning + validation only — **NOT** real-time COLREG collision
> avoidance and **NOT** autonomous execution / autopilot control. A generated route is never
> "guaranteed safe": it always carries chart source/edition, limitations, validation status,
> warnings, assumptions, the vessel profile, the generation time, and an edited-after-validation
> flag. See [docs/DESIGN.md](docs/DESIGN.md#12-safety--provenance-rule). Mind the UI wording
> guardrails in DESIGN §10.

**Status:** P1 skeleton. The repo is scaffolded as a keelson processor and runs: it connects to
Zenoh, declares a liveliness token (the health signal), and declares the RoutePlanner RPC
queryables (each replies "not implemented yet") under graceful shutdown. No planning logic yet. See
[docs/ROADMAP.md](docs/ROADMAP.md) for the phase plan (P0–P12).

## Where to look first
- [docs/ROADMAP.md](docs/ROADMAP.md) — phases P0–P12, the MVP slice (P1–P8), optimization goals.
- [docs/DESIGN.md](docs/DESIGN.md) — authoritative architecture, the engines, the safety rule.
- [docs/KEELSON-INTEGRATION.md](docs/KEELSON-INTEGRATION.md) — `keelson.Route` mapping, RPC sketch,
  key-space, subjects.
- [docs/CHART-ADAPTER.md](docs/CHART-ADAPTER.md) — chart-server query contract + S-57 normalization.

## Architecture (target shape)

```
Crowsnest route-editor ──"Auto-plan" (Keelson RPC)──▶ RoutePlanner queryable (rpc/)
                                                                 ▼
        chart_adapter/ ──▶ navigability/ ──▶ pathfinding/ (A*) ──▶ model/ (→ keelson.Route)
            │ (S-57 fetch+normalize)   (cost surface)    │                    │
            ▼                                            ▼                    ▼
        chart-server (PostGIS / /api/* / chart/**)   validation/        publish/ (route_planned/route/route_status)
```

Source lives under `src/route_planner/`; `bin/route_planner` is a thin wrapper that calls
`route_planner.app:main`. Subpackages (`rpc`, `chart_adapter`, `navigability`, `pathfinding`,
`model`, `validation`, `publish`) map 1:1 to the phases in ROADMAP — most are empty stubs today.
`rpc/` is the integration layer (declares the queryables); there is no HTTP `api/`.

## Common commands
```bash
# Install. keelson MUST come from the monorepo SDK (the PyPI wheel lacks payloads.Route_pb2,
# our canonical contract type). Install it first, then our package:
uv pip install ../keelson/sdks/python   # monorepo keelson (Route_pb2 ✓)
uv pip install -e ".[dev]"              # our pkg + ruff/pytest (keelson already satisfied)

# Run locally against a router (built-in defaults: realm=rise, entity=route-planner)
route_planner --connect tcp/localhost:7447
# declares a liveliness token + the RoutePlanner RPC queryables; query one with a keelson RPC
# client at e.g. rise/@v0/route-planner/@rpc/plan_route/route-planner/0 (skeleton: replies error)

# Lint / test
ruff check bin/ src/ tests/
pytest

# Local container against an already-running host router
docker compose -f docker-compose.dev.yml up --build
```

## Keelson integration cheatsheet (reuse the SDK — do not reinvent)
- Scaffolding (`keelson.scaffolding`): `add_common_arguments`, `create_zenoh_config`,
  `setup_logging`, `GracefulShutdown`, `declare_liveliness_token` (all wired in `app.py`); plus
  `LivelinessMonitor` and `make_configurable` (runtime RPC config) available for later phases.
- Keys: `construct_pubsub_key(realm, entity_id, subject, source_id, …)`, `construct_rpc_key(...)`,
  `construct_liveliness_key(...)`. Key-space hardcodes `/@v0/`.
- Framing: **pub/sub** is Envelope-framed (`enclose`/`uncover`; `get_subject_schema` +
  `decode_protobuf_payload_from_type_name`). **RPC is bare protobuf, no Envelope** — parse with
  `Msg.FromString(bytes(query.payload.to_bytes()))`, reply `query.reply(key, msg.SerializeToString())`,
  error `query.reply_err(ErrorResponse(error_description=...).SerializeToString())`. See `rpc/`.
- **Adopt `keelson.Route`** (RTZ-1.2 / S-421 superset) as the canonical artifact — do NOT invent
  parallel `voyage_route` / `route_geojson` protobufs. Planner-specific data goes in a route
  `Extensions` entry (`manufacturer: "se.rise"`, `name: "route-planner"`). `keelson.Route` is the
  **canonical RPC contract** (request + response; see docs/KEELSON-INTEGRATION.md §3) — it needs the
  keelson **monorepo** SDK, since the PyPI wheel lacks `payloads.Route_pb2`.

Reference upstream files (read-only, in sibling repos):
- `/Users/ted/Documents/CODE/keelson/messages/payloads/Route.proto` — the route/voyage model.
- `/Users/ted/Documents/CODE/keelson/messages/subjects.yaml` — subject → payload registry.
- `/Users/ted/Documents/CODE/keelson/sdks/python/keelson/scaffolding/` — scaffolding utilities.
- `/Users/ted/Documents/CODE/chart-server/` — S-57 chart service (PostGIS + query-bridge).
- `/Users/ted/Documents/CODE/crowsnest-dev/src/apps/route-editor/` — the consumer UI + `useRouteSafetyScan`.

## Conventions
- Match keelson processor style: argparse + `add_common_arguments`, `setup_logging`,
  `GracefulShutdown`, one liveliness token per process. Keelson/zenoh imports stay lazy inside
  functions so `--help` and pure-import tests don't need a live session.
- `chart_adapter` **never writes `public.*`** (ENC data stays reproducible from source `.000` cells).
- Hard constraints are enforced before cost optimization; a returned route never crosses blocked
  geometry. Validation runs after generation **and** after every manual edit.
- Pure geometry/math stays zenoh-free so it is unit-testable in isolation.
- Python 3.13+, `ruff` for lint+format (line-length 100), `pytest`. Minimal comments; markdown
  file links over bare backtick paths; small focused diffs.

## Out of scope (don't build unless asked)
- COLREG collision avoidance, tactical maneuvering, autonomous execution, arming/mode-switching/
  auto-uploading missions (DESIGN §1).
- `VehicleMission.Mission` export (P9), MAVLink export (P10), PostGIS persistence + AIS/weather
  cost layers (P11) — all deferred past the MVP.
