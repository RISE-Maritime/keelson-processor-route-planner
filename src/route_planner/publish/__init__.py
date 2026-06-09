"""Keelson pub/sub publication layer (P6).

Publishes proposed/selected ``keelson.Route`` on ``route_planned`` / ``route`` and a
``RouteStatusUpdate`` on ``route_status``, on the keys
``{realm}/@v0/{entity_id}/pubsub/{subject}/{source_id}``. Pub/sub IS Envelope-framed, so payloads
go through ``keelson.enclose`` (unlike the bare-protobuf RPC layer). See
docs/KEELSON-INTEGRATION.md §5. The planner never publishes to execution-side subjects
(``route_active`` / ``voyage`` / ``route_execution``).
"""

from __future__ import annotations

import logging
import time

import keelson

logger = logging.getLogger(__name__)

SUBJECT_PLANNED = "route_planned"
SUBJECT_ROUTE = "route"
SUBJECT_STATUS = "route_status"


class RoutePublisher:
    """Declares the route pub/sub publishers and publishes enclosed payloads."""

    def __init__(self, session, conf: dict) -> None:
        realm, entity_id, source_id = conf["realm"], conf["entity_id"], conf["source_id"]
        self._session = session
        self._pubs = {
            subject: session.declare_publisher(
                keelson.construct_pubsub_key(realm, entity_id, subject, source_id)
            )
            for subject in (SUBJECT_PLANNED, SUBJECT_ROUTE, SUBJECT_STATUS)
        }
        logger.info("Declared route publishers: %s", ", ".join(self._pubs))

    def publish_planned(self, route) -> None:
        """Publish a proposed alternative on ``route_planned``."""
        self._pubs[SUBJECT_PLANNED].put(keelson.enclose(route.SerializeToString()))

    def publish_selected(self, route) -> None:
        """Publish the operator-selected route on ``route``."""
        self._pubs[SUBJECT_ROUTE].put(keelson.enclose(route.SerializeToString()))

    def publish_status(
        self, route_id: str, edition: int, status, changed_by: str = "operator", note: str = ""
    ) -> None:
        """Broadcast a lightweight ``RouteStatusUpdate`` on ``route_status``."""
        from keelson.payloads.Route_pb2 import RouteStatusUpdate

        update = RouteStatusUpdate(
            route_id=route_id,
            route_edition_number=edition,
            status=status,
            changed_by=changed_by,
            note=note,
        )
        update.timestamp.FromNanoseconds(time.time_ns())
        self._pubs[SUBJECT_STATUS].put(keelson.enclose(update.SerializeToString()))

    def close(self) -> None:
        for pub in self._pubs.values():
            pub.undeclare()
        self._pubs.clear()
