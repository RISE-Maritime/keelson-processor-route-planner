"""Benchmark — score generated routes against external reference routes (pilot RTZ / AIS tracks).

``metrics`` is pure geometry (Fréchet, cross-track deviation, length/turn deltas) and has no zenoh
or keelson dependency. ``runner`` drives the planner across weight profiles and aggregates the
metrics into a table for tuning. Offline tooling — nothing here declares a queryable.
"""

from __future__ import annotations

from .metrics import RouteComparison, compare_routes

__all__ = ["RouteComparison", "compare_routes"]
