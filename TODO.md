# TODO — keelson-processor-route-planner

Actionable checklist. Phase scope + rationale live in [docs/ROADMAP.md](docs/ROADMAP.md); this is the
granular tracker. Rule: phase scope changes go in ROADMAP, tick the box here when done. Keep current.

## Now — near-term (from the research-doc audit)

- [x] PRIOR-ART.md: add Šakan ENC survey, EMODnet, VISIR-2, 52°North WRT, pgRouting, scikit-image refs
- [x] serialize: publish per-leg `leg_distances_m` / `leg_bearings_deg` in the route-planner Extensions
- [x] tests: concave simplification-safety corridor test (string-pulling must not cut the inner corner)
- [x] benchmark/perf.py: latency targets + `LatencyReport` + opt-in `pytest -m perf` (`RUN_PERF=1`)

## Phases (status mirror — see [ROADMAP](docs/ROADMAP.md) for scope)

- [x] **P0–P7** — planning + validation end-to-end over Zenoh RPC
  (P1 RPC · P2 chart_adapter · P3 navigability · P4 pathfinding · P5 serialize · P6 keelson · P7 validation)
- [ ] **P8** — Crowsnest route-editor "Auto-plan" UI ← next
- [ ] **P9** — `VehicleMission.Mission` export (confirmation-gated; no auto-upload / arm)
- [ ] **P10** — MAVLink export behind `VehicleMission`
- [ ] **P11** — PostGIS persistence + AIS-density & weather/sea-state cost layers
- [ ] **P12** — comprehensive test fixtures (open-water, around-island, archipelago, shallow-block, …)

## Backlog / ideas (not committed)

- [ ] Evaluate hybrid graph + medial-axis / fairway-graph seeding for narrow channels — a possible
  future divergence from grid `A*` + clearance-gradient; prototype in [notebooks/](notebooks/) first
  (building blocks in [docs/PRIOR-ART.md](docs/PRIOR-ART.md)).
