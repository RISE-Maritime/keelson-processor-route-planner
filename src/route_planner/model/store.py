"""In-memory route edition store.

Holds the canonical artifact — ``keelson.Route`` — keyed by ``(route_id, route_edition_number)``,
the pair a ``keelson.RouteRef`` addresses, plus a pointer to each route's latest edition. This is
the planner's process-local mirror of the key tree keelson's protocol-specification.md §6.3
describes (``route/{route_id}/edition/{N}`` and ``route/{route_id}/latest``):

- an edition, once stored, is never rewritten — a change is a new edition (§6.3);
- a lookup names an exact edition and never falls back to ``latest`` (a dangling reference must be
  surfaced, per ``RouteRef``).

PostGIS persistence is a later phase (P11); this store is not durable.

Deliberately keelson-free at import time: it only stores and copies ``Route`` objects handed to it,
so importing this module never requires ``payloads.Route_pb2``.
"""

from __future__ import annotations

import threading


class EditionExists(ValueError):
    """An attempt to rewrite an edition that is already stored."""


class StaleEdition(ValueError):
    """An attempt to mutate a route from an edition that is no longer its latest."""


class RouteStore:
    """Thread-safe ``(route_id, edition) -> keelson.Route`` store with a latest-edition index."""

    def __init__(self) -> None:
        self._editions: dict = {}
        self._latest: dict = {}
        self._lock = threading.Lock()

    def put(self, route):
        """Store a new edition. Raises ``EditionExists`` if that edition is already stored."""
        key = (route.route_id, route.route_edition_number)
        with self._lock:
            if key in self._editions:
                raise EditionExists(f"route {key[0]!r} edition {key[1]} is already stored")
            self._editions[key] = route
            if route.route_edition_number >= self._latest.get(route.route_id, 0):
                self._latest[route.route_id] = route.route_edition_number
        return route

    def get(self, route_id: str, edition: int):
        """Return the exact edition, or ``None`` (an unknown id or a dangling edition)."""
        with self._lock:
            return self._editions.get((route_id, edition))

    def latest(self, route_id: str):
        """Return the latest edition of ``route_id``, or ``None``."""
        with self._lock:
            edition = self._latest.get(route_id)
            return None if edition is None else self._editions[(route_id, edition)]

    def select(self, route_id: str, edition: int):
        """Mark a route as operator-selected: a new edition with status advanced.

        Returns the new edition, or ``None`` if ``(route_id, edition)`` is not stored. Raises
        ``StaleEdition`` if ``edition`` is not the latest — selecting from a superseded edition
        would fork the lineage. The selected edition itself is left untouched.
        """
        from .enums import ACKNOWLEDGED

        with self._lock:
            current = self._editions.get((route_id, edition))
            if current is None:
                return None
            latest = self._latest[route_id]
            if edition != latest:
                raise StaleEdition(
                    f"route {route_id!r} edition {edition} is superseded by edition {latest}"
                )
            selected = type(current)()
            selected.CopyFrom(current)
            selected.status = ACKNOWLEDGED
            selected.route_edition_number = edition + 1
            self._editions[(route_id, edition + 1)] = selected
            self._latest[route_id] = edition + 1
            return selected

    def list_ids(self) -> list:
        """Return the stored route ids."""
        with self._lock:
            return list(self._latest)
