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
from route_planner.rpc import rpc_key


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

    assert rpc.PROCEDURES == ("plan_route", "validate_route", "get_route", "select_route")
    key = rpc_key("rise", "route-planner", "plan_route", "route-planner/0")
    # The versioned key current keelson builds, as Crowsnest's rpcKeyFor("route_planner/v1") does.
    assert key == "rise/@v0/route-planner/@rpc/route_planner/v1/plan_route/route-planner/0"


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


def _install(session, conf, store):
    """install_queryables with a fake adapter + a real publisher (P6 signature)."""
    from _planhelpers import FakeAdapter, deep_chart

    from route_planner.publish import RoutePublisher

    publisher = RoutePublisher(session, conf)
    queryables = rpc.install_queryables(session, conf, store, FakeAdapter(deep_chart()), publisher)
    return queryables, publisher


def test_rpc_validate_route_rejects_empty_request():
    """validate_route on a request with <2 waypoints -> INVALID_ARGUMENT (it is implemented now)."""
    import zenoh

    from route_planner.model.store import RouteStore

    conf = config_mod.load(None)
    key = rpc_key(conf["realm"], conf["entity_id"], "validate_route", conf["source_id"])

    session = zenoh.open(zenoh.Config())
    try:
        queryables, publisher = _install(session, conf, RouteStore())
        try:
            _oks, errs = _rpc_query(session, key)  # empty Route -> no waypoints
            assert errs, "expected an error reply from validate_route"
            # ErrorResponse no longer carries a code enum; the category is prefixed into the description.
            assert any(e.error_description.startswith("INVALID_ARGUMENT") for e in errs)
        finally:
            for q in queryables:
                q.undeclare()
            publisher.close()
    finally:
        session.close()


def test_rpc_get_route_not_found_then_seeded():
    """get_route returns NOT_FOUND for an unknown id, and the stored Route once seeded."""
    import zenoh
    from keelson.payloads.Route_pb2 import Route

    from route_planner.model.enums import RECOMMENDED
    from route_planner.model.store import RouteStore

    conf = config_mod.load(None)
    key = rpc_key(conf["realm"], conf["entity_id"], "get_route", conf["source_id"])
    store = RouteStore()

    session = zenoh.open(zenoh.Config())
    try:
        queryables, publisher = _install(session, conf, store)
        try:
            # unknown id -> NOT_FOUND
            req_missing = Route(route_id="ghost").SerializeToString()
            oks, errs = _rpc_query(session, key, payload=req_missing)
            assert not oks
            assert any(e.error_description.startswith("NOT_FOUND") for e in errs)

            # seed the store, then the same query returns the Route
            store.put(Route(route_id="r1", status=RECOMMENDED, route_edition_number=1))
            oks, errs = _rpc_query(session, key, payload=Route(route_id="r1").SerializeToString())
            assert oks, f"expected the stored Route, got errors: {[e.error_description for e in errs]}"
            got = Route()
            got.ParseFromString(oks[0])
            assert got.route_id == "r1"
            assert got.status == RECOMMENDED
        finally:
            for q in queryables:
                q.undeclare()
            publisher.close()
    finally:
        session.close()
