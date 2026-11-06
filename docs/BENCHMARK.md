# Benchmarking the planner against pilot routes + AIS

How we find and tune good route-planning weights by scoring generated routes against **ground
truth**: official Sjöfartsverket pilot routes (RTZ) and historical AIS tracks. Offline tooling —
nothing here runs in the live processor. Prior-art context in [PRIOR-ART.md](PRIOR-ART.md); the
engines it tunes are in [DESIGN.md](DESIGN.md).

## Ground truth

1. **Pilot routes (RTZ).** Sjöfartsverket's Gothenburg pilot routes — "intended ideal tracks" drawn
   by professional pilots. Fixtures in [tests/fixtures/pilot_routes/](../tests/fixtures/pilot_routes/)
   (full 44-route bundle downloadable; see that folder's README). Parsed by
   [reference/rtz.py](../src/route_planner/reference/rtz.py) into a `PlannedRoute` (geometry) or a
   `keelson.Route` (provenance-tagged).
2. **Historical AIS.** Real sailed tracks from the AIS database, read by
   [reference/ais.py](../src/route_planner/reference/ais.py) (read-only; column names configurable
   via `AisSchema`). Cleaned tracks yield three things: **ground-truth voyages** (`voyage_lines`), a
   **traffic density/corridor surface** (`density_surface` → `corridor_wgs84`), and **mined
   scenarios** (`mine_scenarios` — recurring start/end pairs + vessel-profile stats).

## Metrics

[benchmark/metrics.py](../src/route_planner/benchmark/metrics.py) compares a generated route to a
reference (all in a shared metric frame):

- **Fréchet distance** (`frechet_m`) — overall shape agreement; the primary score.
- **Cross-track deviation** (`xtd_mean/median/p95/max_m`) — how far the route strays from the
  reference centreline.
- **Hausdorff distance**, **length ratio**, **waypoint count**, **turn count** deltas.
- **Validation delta** — `validate()` on both (same `nav`), diffing `issues`/`challenges`.

## Running a benchmark

Needs a live chart-server (`CHART_SERVER_URL` / `DATABASE_URL`); the AIS bias additionally needs the
AIS DB. From the notebook or a script:

```python
from route_planner import debug

# Score the planner against one pilot route across the default weight profiles.
result = debug.run_benchmark("tests/fixtures/pilot_routes/01_PS1_via_N_Ch_to_Frihamnen.rtz")
debug.benchmark = result            # inspect
from route_planner.benchmark.runner import to_dataframe
to_dataframe(result["rows"])        # per-(goal, alternative) metrics table
result["summary"]                   # best goal by Fréchet with clean validation
```

To score the **AIS-biased** cost surface, build a corridor from AIS density and pass it in with the
opt-in constraint:

```python
from route_planner.reference.ais import AisSource, density_surface, corridor_wgs84

tracks   = AisSource(DATABASE_URL).tracks(bbox, start_epoch, end_epoch)
corridor = corridor_wgs84(density_surface(tracks, bbox), min_count=3, buffer_m=80)
result   = debug.run_benchmark(
    pilot_rtz, ais_corridor=corridor, constraints={"prefer_ais_corridors": True},
)
```

## Tuning to a `pilot_matched` profile

[benchmark/tune.py](../src/route_planner/benchmark/tune.py) sweeps weight overrides and returns the
best-first list. `plan_fn(candidate)` re-plans through the real pipeline with
`cost.WEIGHT_PROFILES["custom"]` patched to the candidate:

```python
from route_planner.benchmark.tune import grid_candidates, sweep_weights
from route_planner.pathfinding import cost

def plan_fn(weights):
    cost.WEIGHT_PROFILES["custom"].update(cost._DEFAULT | weights)
    return debug.run_pipeline(start, end, goal="custom", n_alternatives=3, ...)["routes"]

grid    = {"clearance": [1.5, 2.5, 3.5], "off_ais_corridor": [0, 2, 4], "outside_fairway": [1, 3]}
ranked  = sweep_weights(ref_coords, grid_candidates(grid), plan_fn,
                        projection=nav.projection, reference_route=ref_route)
ranked[0]["weights"]   # promote into cost.WEIGHT_PROFILES["pilot_matched"]
```

The winning weights are hand-promoted into the **`pilot_matched`** profile in
[cost.py](../src/route_planner/pathfinding/cost.py) (currently seed values, used with
`constraints={"prefer_ais_corridors": True}`).

## Verification

`pytest` covers the offline pieces with fixtures/synthetic data (no live DB/chart):
`tests/test_rtz_import.py`, `tests/test_ais_ingest.py`, `tests/test_benchmark_metrics.py`,
`tests/test_benchmark_runner.py`, `tests/test_benchmark_tune.py`, `tests/test_ais_cost_layer.py`.
The end-to-end smoke (import a pilot RTZ, `run_benchmark`, confirm a profile reproduces it with
clean validation) runs in [explore_and_tune.ipynb](../notebooks/explore_and_tune.ipynb).
