# CLAUDE.md — keelson-processor-route-planner

Guidance for Claude Code (and humans) working in this repo. Keep it current.

## What this project is

A **keelson processor**: a long-running Python service that generates safe, scored **voyage-route
alternatives** for archipelago navigation, validates them against S-57 chart data, and emits them
as standard `keelson.Route` artifacts over keelson RPC + pub/sub. It exposes a **FastAPI HTTP API**
(direct/testing use) and a **keelson RoutePlanner RPC queryable** (the path Crowsnest uses), plus a
pub/sub publication layer. It runs on our backend as a Docker container.

> **Scope & safety.** Voyage planning + validation only — **NOT** real-time COLREG collision
> avoidance and **NOT** autonomous execution / autopilot control. A generated route is never
> "guaranteed safe": it always carries chart source/edition, limitations, validation status,
> warnings, assumptions, the vessel profile, the generation time, and an edited-after-validation
> flag. See [docs/DESIGN.md](docs/DESIGN.md#12-safety--provenance-rule). Mind the UI wording
> guardrails in DESIGN §10.

**Status:** P1 skeleton. The repo is scaffolded as a keelson processor and runs: it connects to
Zenoh, declares a liveliness token, serves `GET /health`, and idles under graceful shutdown. No
planning logic yet. See [docs/ROADMAP.md](docs/ROADMAP.md) for the phase plan (P0–P12).

## Where to look first
- [docs/ROADMAP.md](docs/ROADMAP.md) — phases P0–P12, the MVP slice (P1–P8), optimization goals.
- [docs/DESIGN.md](docs/DESIGN.md) — authoritative architecture, the engines, the safety rule.
- [docs/KEELSON-INTEGRATION.md](docs/KEELSON-INTEGRATION.md) — `keelson.Route` mapping, RPC sketch,
  key-space, subjects.
- [docs/CHART-ADAPTER.md](docs/CHART-ADAPTER.md) — chart-server query contract + S-57 normalization.

## Architecture (target shape)

```
Crowsnest route-editor ──"Auto-plan"──▶ RoutePlanner RPC (rpc/) ─┐   HTTP: api/ (POST /api/routes/plan…)
                                                                 ▼
        chart_adapter/ ──▶ navigability/ ──▶ pathfinding/ (A*) ──▶ model/ (→ keelson.Route)
            │ (S-57 fetch+normalize)   (cost surface)    │                    │
            ▼                                            ▼                    ▼
        chart-server (PostGIS / /api/* / chart/**)   validation/        publish/ (route_planned/route/route_status)
```

Source lives under `src/route_planner/`; `bin/route_planner` is a thin wrapper that calls
`route_planner.app:main`. Subpackages (`api`, `rpc`, `chart_adapter`, `navigability`,
`pathfinding`, `model`, `validation`, `publish`) map 1:1 to the phases in ROADMAP — most are empty
stubs today.

## Common commands
```bash
# Install (editable, with dev tools). If keelson==0.5.1 won't resolve, install it from the
# monorepo first: uv pip install ../keelson/sdks/python
uv pip install -e ".[dev]"

# Run locally against a router (built-in defaults: realm=rise, entity=route-planner, http :8000)
route_planner --connect tcp/localhost:7447
curl http://localhost:8000/health

# Lint / test
ruff check bin/ src/ tests/
pytest

# Local container against an already-running host router, HTTP published on :8000
docker compose -f docker-compose.dev.yml up --build
```

## Keelson integration cheatsheet (reuse the SDK — do not reinvent)
- Scaffolding (`keelson.scaffolding`): `add_common_arguments`, `create_zenoh_config`,
  `setup_logging`, `GracefulShutdown`, `declare_liveliness_token`, `LivelinessMonitor`,
  `make_configurable` (RPC config). Already used in `app.py`.
- Keys: `construct_pubsub_key(realm, entity_id, subject, source_id, …)`, `construct_rpc_key(...)`,
  `construct_liveliness_key(...)`. Key-space hardcodes `/@v0/`.
- Framing: `enclose` / `uncover` for the Envelope; `get_subject_schema` +
  `decode_protobuf_payload_from_type_name` to decode payloads.
- **Adopt `keelson.Route`** (RTZ-1.2 / S-421 superset) as the canonical artifact — do NOT invent
  parallel `voyage_route` / `route_geojson` protobufs. Planner-specific data goes in a route
  `Extensions` entry (`manufacturer: "se.rise"`, `name: "route-planner"`).

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
