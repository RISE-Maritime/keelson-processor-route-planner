# Contributing to keelson-processor-route-planner

Thanks for your interest in improving the Keelson route planner.

> **Scope reminder.** This is a **voyage-planning and route-validation** service — *not*
> real-time COLREG collision avoidance and *not* an autonomous-execution / autopilot
> controller. Keep new code, copy, and tests inside that boundary. See
> [docs/DESIGN.md](docs/DESIGN.md#12-safety--provenance-rule).

## Development setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv pip install --system ".[dev]"
```

This installs the runtime dependencies (`keelson`, `eclipse-zenoh`, `fastapi`, `uvicorn`,
`shapely`, `pyproj`, `pyyaml`, `environs`) plus the dev tools (`ruff`, `pytest`, `httpx`).
If `keelson==0.5.1` is not resolvable from your index, install it from the
[keelson monorepo](../keelson/sdks/python) — e.g. `uv pip install --system ../keelson/sdks/python`.

## Running the service

```bash
route_planner --connect tcp/localhost:7447
# health: curl http://localhost:8000/health
```

## Running the test suite

```bash
pytest -v
```

## Lint

```bash
ruff check bin/ src/ tests/
```

CI runs the same checks on every PR; see
[.github/workflows/ci-checks.yml](.github/workflows/ci-checks.yml).

## Architecture and codebase guidance

[CLAUDE.md](CLAUDE.md) documents the package layout, the phase roadmap, and the
keelson-integration conventions; the authoritative design lives in [docs/](docs/). Start there
before larger changes.

## Pull requests

- Branch off `main`. CI runs on every PR regardless of base.
- Keep PRs focused; bundle unrelated cleanups separately.
- Add or update tests in [tests/](tests/) for behaviour changes.
- Make sure `ruff` and `pytest` both pass locally before pushing.
