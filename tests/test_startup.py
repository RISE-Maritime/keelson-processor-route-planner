"""Startup smoke tests for the P1 skeleton (RPC-only).

Exercise the parts that need no router: package import, CLI parsing, config assembly
(defaults / file / CLI overrides), the RPC procedure registry + key-space, and an end-to-end
self-query against the declared queryables over a local peer Zenoh session.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

import route_planner
from route_planner import app as app_mod
from route_planner import config as config_mod
from route_planner import rpc


def test_package_version():
    assert route_planner.__version__ == "0.1.0"


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        app_mod.parse_args(["--help"])
    assert exc.value.code == 0


def test_config_defaults():
    conf = config_mod.load(None)
    assert conf["realm"] == "rise"
    assert conf["entity_id"] == "route-planner"
    assert conf["source_id"] == "route-planner/0"
    assert conf["chart_server_url"]  # a default is always present


def test_config_cli_overrides():
    args = types.SimpleNamespace(
        config=None,
        realm="other",
        entity_id="rp2",
        source_id="rp2/9",
    )
    conf = config_mod.load(args)
    assert conf["realm"] == "other"
    assert conf["source_id"] == "rp2/9"


def test_config_example_file_loads():
    example = Path(__file__).resolve().parent.parent / "config" / "example.yaml"
    args = types.SimpleNamespace(config=str(example))
    conf = config_mod.load(args)
    assert conf["realm"] == "rise"
    assert conf["source_id"] == "route-planner/0"


def test_config_rejects_empty_realm():
    args = types.SimpleNamespace(config=None, realm="")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(args)


def test_rpc_procedures_and_keyspace():
    import zenoh

    from route_planner.model.store import RouteStore

    assert rpc.PROCEDURES == ("plan_route", "validate_route", "get_route", "select_route")
    assert (rpc.INTERFACE, rpc.INTERFACE_VERSION) == ("route_planner", "v1")
    conf = config_mod.load(None)
    session = zenoh.open(zenoh.Config())
    try:
        server, publisher = _serve(session, conf, RouteStore())
        try:
            keys = sorted(str(q.key_expr) for q in server.queryables)
            # The versioned key, as Crowsnest's rpcKeyFor("route_planner/v1") builds it.
            assert keys == sorted(
                f"rise/@v0/route-planner/@rpc/route_planner/v1/{p}/route-planner/0"
                for p in rpc.PROCEDURES
            )
            assert server.liveliness_token is not None
            tokens = [
                str(r.ok.key_expr)
                for r in session.liveliness().get("rise/@v0/route-planner/@rpc/**")
                if r.ok is not None
            ]
            assert "rise/@v0/route-planner/@rpc/route_planner/v1/*/route-planner/0" in tokens
        finally:
            rpc.close(server)
            publisher.close()
    finally:
        session.close()


def _key(conf, procedure):
    import keelson

    return keelson.construct_rpc_key(
        conf["realm"], conf["entity_id"], rpc.INTERFACE, rpc.INTERFACE_VERSION, procedure,
        conf["source_id"],
    )


def _rpc_query(session, key, payload=None):
    """Run a one-shot RPC query; return (ok_payloads: list[bytes], errors: list[ErrorResponse])."""
    from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

    oks, errs = [], []
    kwargs = {} if payload is None else {"payload": payload}
    for reply in session.get(key, **kwargs):
        if reply.ok is not None:
            oks.append(bytes(reply.ok.payload.to_bytes()))
        elif reply.err is not None:
            err = ErrorResponse()
            err.ParseFromString(bytes(reply.err.payload.to_bytes()))
            errs.append(err)
    return oks, errs


def _serve(session, conf, store):
    """rpc.serve with a fake adapter + a real publisher."""
    from _planhelpers import FakeAdapter, deep_chart

    from route_planner.publish import RoutePublisher

    publisher = RoutePublisher(session, conf)
    server = rpc.serve(session, conf, store, FakeAdapter(deep_chart()), publisher)
    return server, publisher


def test_rpc_validate_route_rejects_empty_request():
    """validate_route on a request with <2 waypoints -> a typed INVALID_ARGUMENT."""
    import zenoh
    from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse

    from route_planner.model.store import RouteStore

    conf = config_mod.load(None)
    session = zenoh.open(zenoh.Config())
    try:
        server, publisher = _serve(session, conf, RouteStore())
        try:
            _oks, errs = _rpc_query(session, _key(conf, "validate_route"))  # empty Route
            assert [e.code for e in errs] == [ErrorResponse.Code.INVALID_ARGUMENT]
        finally:
            rpc.close(server)
            publisher.close()
    finally:
        session.close()


def test_rpc_get_route_not_found_then_seeded():
    """get_route returns NOT_FOUND for an unknown RouteRef, and the stored edition once seeded."""
    import zenoh
    from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
    from keelson.payloads.Route_pb2 import Route, RouteRef

    from route_planner.model.enums import RECOMMENDED
    from route_planner.model.store import RouteStore

    conf = config_mod.load(None)
    key = _key(conf, "get_route")
    store = RouteStore()

    session = zenoh.open(zenoh.Config())
    try:
        server, publisher = _serve(session, conf, store)
        try:
            missing = RouteRef(route_id="ghost", route_edition_number=1).SerializeToString()
            oks, errs = _rpc_query(session, key, payload=missing)
            assert not oks
            assert [e.code for e in errs] == [ErrorResponse.Code.NOT_FOUND]

            store.put(Route(route_id="r1", status=RECOMMENDED, route_edition_number=1))
            ref = RouteRef(route_id="r1", route_edition_number=1).SerializeToString()
            oks, errs = _rpc_query(session, key, payload=ref)
            assert oks, f"expected the stored Route, got errors: {[e.error_description for e in errs]}"
            got = Route.FromString(oks[0])
            assert got.route_id == "r1"
            assert got.status == RECOMMENDED
        finally:
            rpc.close(server)
            publisher.close()
    finally:
        session.close()
