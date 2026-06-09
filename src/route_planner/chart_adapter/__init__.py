"""Chart adapter — fetch + normalize S-57 features (P2 — stub).

Dual-mode: direct read-only PostGIS bulk reads for corridor feature pulls, plus chart-server
HTTP/zenoh for targeted checks (grounding-line / depth / identify / catalog). Normalizes S-57
into internal layers. NO planning logic. Never writes ``public.*``. See docs/CHART-ADAPTER.md and
docs/ROADMAP.md (P2). Not implemented in the P1 skeleton.
"""
