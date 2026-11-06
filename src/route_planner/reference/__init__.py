"""Reference-route ingestion — external ground truth for benchmarking/tuning the planner.

Two sources, both read-only and zenoh-free:
- ``rtz``  — official pilot-planned routes (Sjöfartsverket Gothenburg, RTZ-1.x).
- ``ais``  — historical AIS tracks from the database (real sailed paths).

These feed the ``benchmark`` package, which scores generated routes against them. Nothing here
declares a queryable or touches the live session; keelson imports stay lazy.
"""

from __future__ import annotations
