"""In-memory route store (P1).

Holds the canonical artifact — ``keelson.Route`` — keyed by ``route_id``. A real planner (P2-P4)
populates it via ``put`` on each ``plan_route``; the RPC ``get_route`` / ``select_route`` handlers
read it. PostGIS persistence is a later phase (P11); this store is process-local and not durable.

Deliberately keelson-free at import time: it only stores and mutates ``Route`` objects handed to it,
so importing this module never requires ``payloads.Route_pb2`` (keeps the skeleton/container booting
even where the typed SDK is absent).
"""

from __future__ import annotations

import threading


class RouteStore:
    """Thread-safe ``route_id -> keelson.Route`` store."""

    def __init__(self) -> None:
        self._routes: dict = {}
        self._lock = threading.Lock()

    def put(self, route):
        """Store (or replace) a route by its ``route_id``. Returns the stored route."""
        with self._lock:
            self._routes[route.route_id] = route
        return route

    def get(self, route_id: str):
        """Return the stored route for ``route_id``, or ``None`` if absent."""
        with self._lock:
            return self._routes.get(route_id)

    def select(self, route_id: str):
        """Mark a route as operator-selected: advance status + bump edition. Returns it or None.

        Provisional lifecycle (finalized with publication in P6): selecting acknowledges the route.
        """
        with self._lock:
            route = self._routes.get(route_id)
            if route is None:
                return None
            from .enums import ACKNOWLEDGED

            route.status = ACKNOWLEDGED
            route.route_edition_number += 1
            return route

    def list_ids(self) -> list:
        """Return the stored route ids."""
        with self._lock:
            return list(self._routes)
