"""Internal model + keelson.Route serialization (P5 — stub).

A thin internal planning struct (grid path, per-cell cost breakdown, candidate scoring) lives only
during a single ``plan`` call; the canonical artifact is ``keelson.Route``. Planner-specific data
goes into a route ``Extensions`` entry (``manufacturer: "se.rise"``, ``name: "route-planner"``).
See docs/KEELSON-INTEGRATION.md and docs/ROADMAP.md (P5). Not implemented in the P1 skeleton.
"""
