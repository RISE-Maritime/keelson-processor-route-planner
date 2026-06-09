"""Keelson RoutePlanner RPC queryable — the sole integration layer.

Route planning is request/response, so a Keelson **queryable** is the right fit (no HTTP). This
module declares one queryable per procedure on ``{realm}/@v0/{entity_id}/@rpc/{procedure}/{source_id}``
and answers it. The canonical contract is ``keelson.Route`` (keelson protos are primary — see
docs/KEELSON-INTEGRATION.md §3):

- ``plan_route`` — request is a ``keelson.Route`` template (start/end/via waypoints + RouteInfo +
  route-planner Extensions); runs chart_adapter → navigability → pathfinding → serialize, stores +
  publishes each alternative on ``route_planned``, and **replies one ``keelson.Route`` per
  alternative** (Zenoh multi-reply — clients must query with ``consolidation=NONE`` or the
  same-key replies collapse to one).
- ``get_route`` / ``select_route`` — request carries ``route_id``; reply the stored ``keelson.Route``
  or ``ErrorResponse(NOT_FOUND)``. ``select_route`` advances status + bumps the edition and publishes
  on ``route`` + ``route_status``.
- ``validate_route`` — replies ``ErrorResponse(UNAVAILABLE)``; its engine is P7.

Framing (matches keelson connectors — bare protobuf, no Envelope on RPC):
request ``Route.FromString(bytes(query.payload.to_bytes()))``; success ``query.reply(reply_key,
msg.SerializeToString())``; error ``query.reply_err(ErrorResponse(...).SerializeToString())``.
"""

from __future__ import annotations

import logging

import keelson
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

logger = logging.getLogger(__name__)

PROCEDURES: tuple[str, ...] = ("plan_route", "validate_route", "get_route", "select_route")

# validate_route's engine is P7.
_NOT_IMPLEMENTED = {"validate_route": "validation is not implemented yet (see docs/ROADMAP.md P7)"}


def _reply_err(query, description: str, code) -> None:
    """Reply with an ErrorResponse; a failed reply must not take down the queryable thread."""
    try:
        query.reply_err(ErrorResponse(error_description=description, code=code).SerializeToString())
    except Exception:  # noqa: BLE001
        logger.exception("RPC reply_err failed for %s", query.key_expr)


def _request_route(query):
    """Parse the request payload as a keelson.Route (may raise)."""
    from keelson.payloads.Route_pb2 import Route

    route = Route()
    raw = query.payload
    if raw is not None:
        route.ParseFromString(bytes(raw.to_bytes()))
    return route


def _handle_plan_route(query, reply_key, store, adapter, publisher) -> None:
    from ..planner import PlanError, run_plan

    try:
        request = _request_route(query)
    except Exception as exc:  # noqa: BLE001
        _reply_err(query, f"plan_route: cannot parse keelson.Route request: {exc}", ErrorResponse.INVALID_ARGUMENT)
        return
    try:
        routes, meta = run_plan(adapter, request)
    except PlanError as exc:
        _reply_err(query, f"plan_route: {exc}", ErrorResponse.INVALID_ARGUMENT)
        return
    except Exception:  # noqa: BLE001
        logger.exception("plan_route pipeline failed")
        _reply_err(query, "plan_route: internal planning error", ErrorResponse.INTERNAL)
        return
    if not routes:
        _reply_err(query, "plan_route: no navigable route found for this request", ErrorResponse.UNAVAILABLE)
        return
    for route in routes:
        store.put(route)
        try:
            publisher.publish_planned(route)
        except Exception:  # noqa: BLE001
            logger.exception("publish_planned failed for %s", route.route_id)
        query.reply(reply_key, route.SerializeToString())  # one reply per alternative
    logger.info("plan_route replied %d alternative(s); warnings=%s", len(routes), meta.get("warnings"))


def _handle_get_route(query, reply_key, store) -> None:
    _reply_route_id_lookup(query, reply_key, "get_route", store.get)


def _handle_select_route(query, reply_key, store, publisher) -> None:
    def _select_and_publish(route_id):
        route = store.select(route_id)
        if route is not None:
            try:
                publisher.publish_selected(route)
                publisher.publish_status(
                    route.route_id, route.route_edition_number, route.status, changed_by="operator"
                )
            except Exception:  # noqa: BLE001
                logger.exception("publish on select failed for %s", route_id)
        return route

    _reply_route_id_lookup(query, reply_key, "select_route", _select_and_publish)


def _reply_route_id_lookup(query, reply_key, procedure, lookup) -> None:
    """Parse route_id from the request Route, run ``lookup``, reply the Route or an error."""
    try:
        route_id = _request_route(query).route_id
    except Exception as exc:  # noqa: BLE001
        _reply_err(query, f"{procedure}: cannot parse keelson.Route request: {exc}", ErrorResponse.INVALID_ARGUMENT)
        return
    if not route_id:
        _reply_err(query, f"{procedure}: request keelson.Route must carry a route_id", ErrorResponse.INVALID_ARGUMENT)
        return
    route = lookup(route_id)
    if route is None:
        _reply_err(query, f"{procedure}: no route {route_id!r}", ErrorResponse.NOT_FOUND)
        return
    query.reply(reply_key, route.SerializeToString())


def _make_callback(procedure: str, reply_key: str, store, adapter, publisher):
    """Build the zenoh queryable callback for ``procedure`` (runs on the queryable's thread)."""

    def _callback(query) -> None:
        logger.info("RPC %s queried on %s", procedure, query.key_expr)
        try:
            if procedure == "plan_route":
                _handle_plan_route(query, reply_key, store, adapter, publisher)
            elif procedure == "get_route":
                _handle_get_route(query, reply_key, store)
            elif procedure == "select_route":
                _handle_select_route(query, reply_key, store, publisher)
            else:  # validate_route
                _reply_err(query, f"route-planner: {_NOT_IMPLEMENTED[procedure]}", ErrorResponse.UNAVAILABLE)
        except Exception:  # noqa: BLE001
            logger.exception("RPC handler failed for %s", reply_key)
            _reply_err(query, "route-planner: internal error", ErrorResponse.INTERNAL)

    return _callback


def install_queryables(session, conf: dict, store, adapter, publisher) -> list:
    """Declare one RPC queryable per RoutePlanner procedure; return them for undeclare-on-shutdown.

    Keys use ``keelson.construct_rpc_key`` with ``source_id`` as the responder id. ``store`` backs
    get/select; ``adapter`` (chart_adapter) + ``publisher`` (pub/sub) drive plan_route.
    """
    realm, entity_id, source_id = conf["realm"], conf["entity_id"], conf["source_id"]
    queryables = []
    for procedure in PROCEDURES:
        key = keelson.construct_rpc_key(realm, entity_id, procedure, source_id)
        callback = _make_callback(procedure, key, store, adapter, publisher)
        queryables.append(session.declare_queryable(key, callback, complete=True))
        logger.info("Declared RPC queryable: %s", key)
    return queryables
