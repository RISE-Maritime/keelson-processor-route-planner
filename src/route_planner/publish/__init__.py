"""Keelson pub/sub publication layer (P6 — stub).

Publishes proposed/selected ``keelson.Route`` on ``route_planned`` / ``route`` and
``RouteStatusUpdate`` on ``route_status``; optional debug overlays on the existing
``navigable_waters_geojson`` / ``no_go_zone_geojson`` subjects. Uses the keelson SDK key helpers
(``construct_pubsub_key`` + ``enclose``). See docs/KEELSON-INTEGRATION.md and docs/ROADMAP.md (P6).
Not implemented in the P1 skeleton.
"""
