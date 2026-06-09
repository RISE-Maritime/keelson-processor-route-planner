"""Keelson RoutePlanner RPC queryable — the sole integration layer.

Route planning is request/response, so a Keelson **queryable** is the right fit (no HTTP). This
module declares one queryable per procedure on the RPC key-space
``{realm}/@v0/{entity_id}/@rpc/{procedure}/{source_id}`` and answers it. The canonical contract is
``keelson.Route`` (keelson protos are primary — see docs/KEELSON-INTEGRATION.md §3):

- ``plan_route`` / ``validate_route`` — reply ``ErrorResponse(UNAVAILABLE, ...)``; their engines are
  P2-P4 / P7.
- ``get_route`` / ``select_route`` — request is a ``keelson.Route`` carrying ``route_id``; reply the
  stored ``keelson.Route`` (bare protobuf) or ``ErrorResponse(NOT_FOUND)``. ``select_route`` advances
  status + bumps the edition (publication is P6).

Framing convention (matches keelson connectors — bare protobuf, no Envelope on RPC):
- request:  ``Route.FromString(bytes(query.payload.to_bytes()))``
- success:  ``query.reply(reply_key, msg.SerializeToString())``
- error:    ``query.reply_err(ErrorResponse(error_description=..., code=...).SerializeToString())``
"""

from __future__ import annotations

import logging

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

logger = logging.getLogger(__name__)

# RoutePlanner procedures (docs/KEELSON-INTEGRATION.md §3). export_vehicle_mission is a later phase.
PROCEDURES: tuple[str, ...] = ("plan_route", "validate_route", "get_route", "select_route")

# Procedures whose engine is not built yet -> reply UNAVAILABLE with a pointer to the phase.
_NOT_IMPLEMENTED = {
    "plan_route": "the planner is not implemented yet (see docs/ROADMAP.md P2-P4)",
    "validate_route": "validation is not implemented yet (see docs/ROADMAP.md P7)",
}


def _reply_err(query, description: str, code) -> None:
    """Reply with an ErrorResponse; a failed reply must not take down the queryable thread."""
    try:
        query.reply_err(
            ErrorResponse(error_description=description, code=code).SerializeToString()
        )
    except Exception:  # noqa: BLE001
        logger.exception("RPC reply_err failed for %s", query.key_expr)


def _request_route_id(query) -> str:
    """Parse the request payload as a keelson.Route and return its ``route_id`` (may raise)."""
    from keelson.payloads.Route_pb2 import Route

    raw = query.payload
    route = Route()
    if raw is not None:
        route.ParseFromString(bytes(raw.to_bytes()))
    return route.route_id


def _handle_store_lookup(procedure: str, query, reply_key: str, lookup) -> None:
    """Shared get/select handler: parse route_id, run ``lookup``, reply the Route or an error."""
    try:
        route_id = _request_route_id(query)
    except Exception as exc:  # noqa: BLE001
        _reply_err(
            query,
            f"{procedure}: cannot parse keelson.Route request: {exc}",
            ErrorResponse.INVALID_ARGUMENT,
        )
        return
    if not route_id:
        _reply_err(
            query,
            f"{procedure}: request keelson.Route must carry a route_id",
            ErrorResponse.INVALID_ARGUMENT,
        )
        return
    route = lookup(route_id)
    if route is None:
        _reply_err(query, f"{procedure}: no route {route_id!r}", ErrorResponse.NOT_FOUND)
        return
    query.reply(reply_key, route.SerializeToString())


def _make_callback(procedure: str, reply_key: str, store):
    """Build the zenoh queryable callback for ``procedure`` (runs on the queryable's thread)."""

    def _callback(query) -> None:
        logger.info("RPC %s queried on %s", procedure, query.key_expr)
        try:
            if procedure == "get_route":
                _handle_store_lookup(procedure, query, reply_key, store.get)
            elif procedure == "select_route":
                _handle_store_lookup(procedure, query, reply_key, store.select)
            else:
                _reply_err(
                    query,
                    f"route-planner: {_NOT_IMPLEMENTED[procedure]}",
                    ErrorResponse.UNAVAILABLE,
                )
        except Exception:  # noqa: BLE001
            logger.exception("RPC handler failed for %s", reply_key)
            _reply_err(query, "route-planner: internal error", ErrorResponse.INTERNAL)

    return _callback


def install_queryables(session, conf: dict, store) -> list:
    """Declare one RPC queryable per RoutePlanner procedure; return them for undeclare-on-shutdown.

    Keys are built with ``keelson.construct_rpc_key`` using ``source_id`` as the responder id.
    ``store`` is the in-memory RouteStore the get/select handlers read.
    """
    realm, entity_id, source_id = conf["realm"], conf["entity_id"], conf["source_id"]
    queryables = []
    for procedure in PROCEDURES:
        key = keelson.construct_rpc_key(realm, entity_id, procedure, source_id)
        queryables.append(
            session.declare_queryable(key, _make_callback(procedure, key, store), complete=True)
        )
        logger.info("Declared RPC queryable: %s", key)
    return queryables
