"""Keelson pub/sub publication layer.

Publishes routes on the ``route`` subject in keelson's edition key tree (protocol-specification.md
§6.3), the edition-bump audit record on ``route_change_event`` and a ``RouteStatusUpdate`` on
``route_status``:

    {base}/@v0/{entity}/pubsub/route/{route_id}/edition/{N}         keelson.Route  (immutable)
    {base}/@v0/{entity}/pubsub/route/{route_id}/latest              keelson.Route  (pointer)
    {base}/@v0/{entity}/pubsub/route_change_event/{route_id}/{cid}  keelson.RouteChangeEvent
    {base}/@v0/{entity}/pubsub/route_status/{route_id}              keelson.RouteStatusUpdate

These as-built route keys carry no producer chunk (§6.3, and §2.1.1's note on Sections 6-7), so
the planner's own ``source_id`` appears in the payloads (``author_id`` / ``changed_by``), not the
keys. Lifecycle lives in ``Route.status``: there is no separate subject per status (keelson #141
removed ``route_planned``).

Pub/sub IS Envelope-framed, so payloads go through ``keelson.enclose`` (unlike the bare-protobuf RPC
layer). The keys vary per route, so samples go out through ``keelson.scaffolding.put``, which
applies each subject's QoS profile. The planner never publishes to execution-side subjects
(``voyage`` / ``route_execution``).
"""

from __future__ import annotations

import logging
import time
import uuid

import keelson

logger = logging.getLogger(__name__)

SUBJECT_ROUTE = "route"
SUBJECT_CHANGE = "route_change_event"
SUBJECT_STATUS = "route_status"


class RoutePublisher:
    """Publishes enclosed route payloads on the §6.3 key tree."""

    def __init__(self, session, conf: dict) -> None:
        self._session = session
        self._base, self._entity = conf["realm"], conf["entity_id"]
        self._source_id = conf["source_id"]

    def _put(self, subject: str, source_id: str, message) -> str:
        from keelson.scaffolding import put

        key = keelson.construct_pubsub_key(self._base, self._entity, subject, source_id)
        put(self._session, key, keelson.enclose(message.SerializeToString()))
        return key

    def publish_route(self, route) -> None:
        """Publish an edition, then move ``latest`` to it — in that order (§6.4).

        The edition must exist before ``latest`` points at it, or a consumer following the
        pointer resolves a key that is not there yet.
        """
        rid, edition = route.route_id, route.route_edition_number
        self._put(SUBJECT_ROUTE, f"{rid}/edition/{edition}", route)
        self._put(SUBJECT_ROUTE, f"{rid}/latest", route)

    def publish_change_event(
        self, route_id: str, old_edition: int, new_edition: int, *, reason: str, summary: str = ""
    ) -> str:
        """Append a ``RouteChangeEvent`` to ``route_change_event/{route_id}/{change_id}``."""
        from keelson.payloads.RouteChangeEvent_pb2 import RouteChangeEvent

        event = RouteChangeEvent(
            change_id=str(uuid.uuid4()),
            route_id=route_id,
            old_edition=old_edition,
            new_edition=new_edition,
            author_id=self._source_id,
            reason=reason,
            change_summary=summary,
        )
        event.at.FromNanoseconds(time.time_ns())
        self._put(SUBJECT_CHANGE, f"{route_id}/{event.change_id}", event)
        return event.change_id

    def publish_edition_bump(self, route, old_edition: int, *, reason: str, summary: str = "") -> None:
        """The §6.4 choreography: new edition, ``latest`` pointer, then the change event."""
        self.publish_route(route)
        self.publish_change_event(
            route.route_id, old_edition, route.route_edition_number, reason=reason, summary=summary
        )

    def publish_status(
        self, route_id: str, edition: int, status, changed_by: str | None = None, note: str = ""
    ) -> None:
        """Broadcast a lightweight ``RouteStatusUpdate`` on ``route_status/{route_id}``."""
        from keelson.payloads.Route_pb2 import RouteStatusUpdate

        update = RouteStatusUpdate(
            route_id=route_id,
            route_edition_number=edition,
            status=status,
            changed_by=changed_by or self._source_id,
            note=note,
        )
        update.timestamp.FromNanoseconds(time.time_ns())
        self._put(SUBJECT_STATUS, route_id, update)

    def close(self) -> None:
        """Nothing is declared per route; kept so callers can shut down uniformly."""
