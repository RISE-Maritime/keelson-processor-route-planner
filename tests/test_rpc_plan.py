"""End-to-end route_planner/v1 over a peer Zenoh session: plan, get, select, validate."""

from __future__ import annotations

import os
import time

import keelson
import pytest
import zenoh
from _planhelpers import PROFILE, FakeAdapter, deep_chart, plan_request, route_between
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.payloads.Route_pb2 import Route, RouteRef

from route_planner import rpc
from route_planner.model.store import RouteStore
from route_planner.publish import RoutePublisher

CONF = {"realm": "rise", "entity_id": "route-planner", "source_id": "route-planner/0"}


def _rpc_key(procedure):
    return keelson.construct_rpc_key(
        CONF["realm"], CONF["entity_id"], "route_planner", "v1", procedure, CONF["source_id"]
    )


def _call(session, procedure, request):
    """Query with consolidation=NONE (multi-reply); return (routes, errors)."""
    routes, errors = [], []
    for reply in session.get(
        _rpc_key(procedure),
        payload=request.SerializeToString(),
        consolidation=zenoh.ConsolidationMode.NONE,
    ):
        if reply.ok is not None:
            routes.append(Route.FromString(bytes(reply.ok.payload.to_bytes())))
        else:
            errors.append(ErrorResponse.FromString(bytes(reply.err.payload.to_bytes())))
    return routes, errors


@pytest.fixture
def served():
    """A peer session serving route_planner/v1 over a fake chart; yields (session, store)."""
    session = zenoh.open(zenoh.Config())
    store = RouteStore()
    publisher = RoutePublisher(session, CONF)
    server = rpc.serve(session, CONF, store, FakeAdapter(deep_chart()), publisher)
    try:
        yield session, store
    finally:
        rpc.close(server)
        publisher.close()
        session.close()


def test_plan_route_multi_replies_and_publishes(served):
    session, store = served
    published = []
    sub = session.declare_subscriber(
        "rise/@v0/route-planner/pubsub/route/**", lambda s: published.append(str(s.key_expr))
    )
    try:
        routes, errors = _call(
            session, "plan_route", plan_request((57.70, 11.80), (57.72, 11.86), n_alternatives=2)
        )
        assert not errors, [e.error_description for e in errors]
        assert routes, "plan_route returned no keelson.Route alternatives"
        assert len(routes) <= 2
        assert all(len(r.waypoints.waypoint) >= 2 for r in routes)
        assert all(r.route_edition_number == 1 for r in routes)
        assert set(store.list_ids()) == {r.route_id for r in routes}
        for _ in range(40):
            if len(published) >= 2 * len(routes):
                break
            time.sleep(0.05)
        rid = routes[0].route_id
        assert f"rise/@v0/route-planner/pubsub/route/{rid}/edition/1" in published
        assert f"rise/@v0/route-planner/pubsub/route/{rid}/latest" in published
    finally:
        sub.undeclare()


def test_plan_route_typed_errors(served):
    from keelson.payloads.Route_pb2 import ROUTE_TOPOLOGY_CLOSED

    from route_planner.model import vessel_profile as vp

    session, _ = served
    _, errors = _call(session, "plan_route", vp.plan_request([(57.70, 11.80)], profile=PROFILE))
    assert [e.code for e in errors] == [ErrorResponse.Code.INVALID_ARGUMENT]

    closed = plan_request((57.70, 11.80), (57.72, 11.86))
    closed.topology = ROUTE_TOPOLOGY_CLOSED
    _, errors = _call(session, "plan_route", closed)
    assert [e.code for e in errors] == [ErrorResponse.Code.UNSUPPORTED]


def test_get_and_select_by_route_ref(served):
    session, store = served
    routes, _ = _call(session, "plan_route", plan_request((57.70, 11.80), (57.72, 11.86)))
    rid = routes[0].route_id

    got, errors = _call(session, "get_route", RouteRef(route_id=rid, route_edition_number=1))
    assert not errors and got[0].route_id == rid

    # A dangling edition is NOT_FOUND, never resolved to the latest.
    _, errors = _call(session, "get_route", RouteRef(route_id=rid, route_edition_number=7))
    assert [e.code for e in errors] == [ErrorResponse.Code.NOT_FOUND]
    _, errors = _call(session, "get_route", RouteRef(route_id="ghost", route_edition_number=1))
    assert [e.code for e in errors] == [ErrorResponse.Code.NOT_FOUND]
    _, errors = _call(session, "get_route", RouteRef(route_id=rid))  # no edition
    assert [e.code for e in errors] == [ErrorResponse.Code.INVALID_ARGUMENT]

    # select_route mutates: a new edition, the old one kept.
    selected, errors = _call(session, "select_route", RouteRef(route_id=rid, route_edition_number=1))
    assert not errors
    assert selected[0].route_edition_number == 2
    assert store.get(rid, 1) is not None and store.get(rid, 2) is not None

    # Selecting from the superseded edition is refused.
    _, errors = _call(session, "select_route", RouteRef(route_id=rid, route_edition_number=1))
    assert [e.code for e in errors] == [ErrorResponse.Code.INVALID_STATE]


def test_select_route_publishes_the_edition_bump(served):
    session, _ = served
    routes, _ = _call(session, "plan_route", plan_request((57.70, 11.80), (57.72, 11.86)))
    rid = routes[0].route_id
    seen = []
    sub = session.declare_subscriber(
        "rise/@v0/route-planner/pubsub/**", lambda s: seen.append(str(s.key_expr))
    )
    try:
        _call(session, "select_route", RouteRef(route_id=rid, route_edition_number=1))
        for _ in range(40):
            if len(seen) >= 4:
                break
            time.sleep(0.05)
        base = "rise/@v0/route-planner/pubsub"
        assert seen[:2] == [f"{base}/route/{rid}/edition/2", f"{base}/route/{rid}/latest"]
        assert seen[2].startswith(f"{base}/route_change_event/{rid}/")
        assert seen[3] == f"{base}/route_status/{rid}"
    finally:
        sub.undeclare()


def test_validate_route_e2e_flags_land_crossing():
    from shapely.geometry import box

    from route_planner.chart_adapter.layers import DEPTH_AREAS, LAND, ChartFeature, NormalizedChart
    from route_planner.model.enums import ROUTE_ISSUES

    # Deep water with a land islet carved out (a hole in depare) the edited route runs through.
    islet = box(11.83, 57.69, 11.85, 57.73)
    chart = NormalizedChart()
    cover = (11.78, 57.68, 11.90, 57.74)
    chart.add(ChartFeature(DEPTH_AREAS, box(*cover).difference(islet), {"drval1": 10, "drval2": 20}))
    chart.add(ChartFeature(LAND, islet, {"objnam": "islet"}))
    chart.provenance.coverage_bbox = cover

    session = zenoh.open(zenoh.Config())
    publisher = RoutePublisher(session, CONF)
    server = rpc.serve(session, CONF, RouteStore(), FakeAdapter(chart), publisher)
    try:
        edited = route_between((57.70, 11.80), (57.70, 11.88))  # crosses the islet at lon ~11.84
        replies, errors = _call(session, "validate_route", edited)
        assert replies, f"validate_route returned no annotated route: {errors}"
        route = replies[0]
        assert any(i.id == "issue-land" for i in route.issues)
        assert route.status == ROUTE_ISSUES
    finally:
        rpc.close(server)
        publisher.close()
        session.close()


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL; live chart DB not available",
)
def test_plan_route_e2e_live():
    from route_planner.chart_adapter import ChartAdapter

    session = zenoh.open(zenoh.Config())
    store = RouteStore()
    publisher = RoutePublisher(session, CONF)
    adapter = ChartAdapter(
        {**CONF, "database_url": os.environ["DATABASE_URL"], "chart_server_url": "http://localhost:8080"}
    )
    server = rpc.serve(session, CONF, store, adapter, publisher)
    try:
        req = plan_request(
            (57.684, 11.800), (57.700, 11.880),
            goal="archipelago_conservative", profile=PROFILE, n_alternatives=3,
        )
        routes, _ = _call(session, "plan_route", req)
        assert routes, "live plan_route returned no alternatives"
        assert len(routes) <= 3
        assert len(routes[0].waypoints.waypoint) >= 2
    finally:
        rpc.close(server)
        publisher.close()
        adapter.close()
        session.close()
