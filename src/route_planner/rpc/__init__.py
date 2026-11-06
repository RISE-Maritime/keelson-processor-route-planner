"""Keelson ``route_planner/v1`` RPC server — the sole integration layer.

Route planning is request/response, so a Keelson **queryable** is the right fit (no HTTP). The
contract is keelson's ``interfaces/RoutePlanner.proto``, registered as ``route_planner/v1``, with the
three properties the proto cannot state in keelson's protocol-specification.md §6.7. The four
procedures are served by ``keelson.scaffolding.serve_rpc`` on

    {base}/@v0/{entity_id}/@rpc/route_planner/v1/{procedure}/{source_id}

which also declares the interface-level liveliness token (§3.5; it advertises the whole interface,
so all four procedures are always served, §3.6) and wraps every call in audit logging and a typed
``INTERNAL`` reply should a handler raise.

- ``plan_route(PlanRouteRequest)`` — runs chart_adapter → navigability → pathfinding → serialize,
  stores + publishes each alternative on ``route`` (edition 1 + ``latest``), and **replies one
  ``keelson.Route`` per alternative** (Zenoh multi-reply — clients must query with
  ``consolidation=NONE`` or the same-key replies collapse to one).
- ``validate_route(keelson.Route)`` — rebuilds navigability and replies the route annotated with
  ``issues`` / ``challenges`` / ``status``.
- ``get_route(keelson.RouteRef)`` — replies that exact edition, or ``NOT_FOUND``. A dangling
  edition is never resolved to the latest.
- ``select_route(keelson.RouteRef)`` — **mutates**: advances status into a new edition and runs the
  §6.4 choreography (edition, ``latest``, ``route_change_event``) plus ``route_status``.

Framing: requests and replies are bare protobuf, not Envelope-wrapped. Failures reply a typed
``keelson.interfaces.ErrorResponse`` via ``reply_err``.
"""

from __future__ import annotations

import logging

from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

logger = logging.getLogger(__name__)

#: The interface these procedures belong to, as keelson's interfaces.yaml registers it.
INTERFACE, INTERFACE_VERSION = "route_planner", "v1"
PROCEDURES: tuple[str, ...] = ("plan_route", "validate_route", "get_route", "select_route")

Code = ErrorResponse.Code


def _parse(op, message_type):
    """Parse the bare-protobuf request, or reply INVALID_ARGUMENT and return ``None``."""
    try:
        return message_type.FromString(op.request_bytes)
    except Exception as exc:  # noqa: BLE001
        op.reply_err(
            f"{op.procedure}: cannot parse {message_type.DESCRIPTOR.full_name}: {exc}",
            Code.INVALID_ARGUMENT,
        )
        return None


def _parse_ref(op):
    """Parse a ``RouteRef``; it must name both a route and an edition."""
    from keelson.payloads.Route_pb2 import RouteRef

    ref = _parse(op, RouteRef)
    if ref is None:
        return None
    if not ref.route_id or not ref.route_edition_number:
        op.reply_err(
            f"{op.procedure}: RouteRef must carry route_id and route_edition_number",
            Code.INVALID_ARGUMENT,
        )
        return None
    return ref


def _not_found(op, ref) -> None:
    op.reply_err(
        f"{op.procedure}: no route {ref.route_id!r} edition {ref.route_edition_number}",
        Code.NOT_FOUND,
    )


def make_handlers(store, adapter, publisher) -> dict:
    """The four ``route_planner/v1`` handlers, keyed by procedure name, for ``serve_rpc``."""
    from keelson.interfaces.RoutePlanner_pb2 import PlanRouteRequest
    from keelson.payloads.Route_pb2 import Route

    from ..model.store import StaleEdition
    from ..planner import PlanError, PlanUnsupported, run_plan, run_validate

    def plan_route(op) -> None:
        request = _parse(op, PlanRouteRequest)
        if request is None:
            return
        try:
            routes, meta = run_plan(adapter, request)
        except PlanUnsupported as exc:
            op.reply_err(f"plan_route: {exc}", Code.UNSUPPORTED)
            return
        except PlanError as exc:
            op.reply_err(f"plan_route: {exc}", Code.INVALID_ARGUMENT)
            return
        if not routes:
            op.reply_err("plan_route: no navigable route found for this request", Code.UNAVAILABLE)
            return
        for route in routes:
            store.put(route)
            try:
                publisher.publish_route(route)
            except Exception:  # noqa: BLE001
                logger.exception("publishing planned route %s failed", route.route_id)
            op.reply_ok(route)  # one reply per alternative
        logger.info(
            "plan_route replied %d alternative(s); warnings=%s", len(routes), meta.get("warnings")
        )

    def validate_route(op) -> None:
        route = _parse(op, Route)
        if route is None:
            return
        try:
            op.reply_ok(run_validate(adapter, route))
        except PlanError as exc:
            op.reply_err(f"validate_route: {exc}", Code.INVALID_ARGUMENT)

    def get_route(op) -> None:
        ref = _parse_ref(op)
        if ref is None:
            return
        route = store.get(ref.route_id, ref.route_edition_number)
        if route is None:
            _not_found(op, ref)
            return
        op.reply_ok(route)

    def select_route(op) -> None:
        ref = _parse_ref(op)
        if ref is None:
            return
        try:
            selected = store.select(ref.route_id, ref.route_edition_number)
        except StaleEdition as exc:
            op.reply_err(f"select_route: {exc}", Code.INVALID_STATE)
            return
        if selected is None:
            _not_found(op, ref)
            return
        try:
            publisher.publish_edition_bump(
                selected, ref.route_edition_number, reason="select_route", summary="route selected"
            )
            publisher.publish_status(
                selected.route_id, selected.route_edition_number, selected.status
            )
        except Exception:  # noqa: BLE001
            logger.exception("publishing selection of %s failed", ref.route_id)
        op.reply_ok(selected)

    return {
        "plan_route": plan_route,
        "validate_route": validate_route,
        "get_route": get_route,
        "select_route": select_route,
    }


def serve(session, conf: dict, store, adapter, publisher):
    """Serve ``route_planner/v1``; returns keelson's ``RpcServer`` (queryables + liveliness token).

    ``source_id`` is the responder id. ``store`` backs get/select; ``adapter`` (chart_adapter) +
    ``publisher`` (pub/sub) drive plan_route.
    """
    from keelson.scaffolding import serve_rpc

    return serve_rpc(
        session,
        base_path=conf["realm"],
        entity_id=conf["entity_id"],
        responder_id=conf["source_id"],
        interface=INTERFACE,
        version=INTERFACE_VERSION,
        handlers=make_handlers(store, adapter, publisher),
        log=logger,
    )


def close(server) -> None:
    """Undeclare everything ``serve`` declared."""
    for queryable in server.queryables:
        queryable.undeclare()
    if server.liveliness_token is not None:
        server.liveliness_token.undeclare()
