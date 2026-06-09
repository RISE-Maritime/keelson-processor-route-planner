"""End-to-end plan_route / select_route over a peer Zenoh session (P6)."""

from __future__ import annotations

import os

import keelson
import pytest
import zenoh
from _planhelpers import PROFILE, FakeAdapter, deep_chart, request_route
from keelson.payloads.Route_pb2 import Route

from route_planner import rpc
from route_planner.model.store import RouteStore
from route_planner.publish import RoutePublisher

CONF = {"realm": "rise", "entity_id": "route-planner", "source_id": "route-planner/0"}


def _rpc_key(procedure):
    return keelson.construct_rpc_key(CONF["realm"], CONF["entity_id"], procedure, CONF["source_id"])


def _ok_routes(session, key, payload):
    routes = []
    # consolidation=NONE so multi-reply alternatives (all on the same reply key) aren't collapsed.
    for reply in session.get(key, payload=payload, consolidation=zenoh.ConsolidationMode.NONE):
        if reply.ok is not None:
            routes.append(Route.FromString(bytes(reply.ok.payload.to_bytes())))
    return routes


def test_plan_route_e2e_with_fake_adapter():
    session = zenoh.open(zenoh.Config())
    store = RouteStore()
    publisher = RoutePublisher(session, CONF)
    queryables = rpc.install_queryables(session, CONF, store, FakeAdapter(deep_chart()), publisher)
    try:
        req = request_route((57.70, 11.80), (57.72, 11.86), goal="shortest", n_alternatives=2)
        routes = _ok_routes(session, _rpc_key("plan_route"), req.SerializeToString())
        assert routes, "plan_route returned no keelson.Route alternatives"
        assert all(len(r.waypoints.waypoint) >= 2 for r in routes)
        assert store.list_ids()  # the alternatives were stored

        # select the recommended route -> reply it (and publish route/route_status)
        rid = routes[0].route_id
        selected = _ok_routes(session, _rpc_key("select_route"), Route(route_id=rid).SerializeToString())
        assert selected and selected[0].route_id == rid
    finally:
        for q in queryables:
            q.undeclare()
        publisher.close()
        session.close()


def test_validate_route_e2e_flags_land_crossing():
    from shapely.geometry import box

    from route_planner.chart_adapter.layers import DEPTH_AREAS, LAND, ChartFeature, NormalizedChart

    # Deep water with a land islet carved out (a hole in depare) the edited route runs through.
    islet = box(11.83, 57.69, 11.85, 57.73)
    chart = NormalizedChart()
    cover = (11.78, 57.68, 11.90, 57.74)
    chart.add(ChartFeature(DEPTH_AREAS, box(*cover).difference(islet), {"drval1": 10, "drval2": 20}))
    chart.add(ChartFeature(LAND, islet, {"objnam": "islet"}))
    chart.provenance.coverage_bbox = cover

    session = zenoh.open(zenoh.Config())
    publisher = RoutePublisher(session, CONF)
    queryables = rpc.install_queryables(session, CONF, RouteStore(), FakeAdapter(chart), publisher)
    try:
        edited = request_route((57.70, 11.80), (57.70, 11.88))  # crosses the islet at lon ~11.84
        replies = _ok_routes(session, _rpc_key("validate_route"), edited.SerializeToString())
        assert replies, "validate_route returned no annotated route"
        route = replies[0]
        assert any(i.id == "issue-land" for i in route.issues)
        from keelson.payloads.Route_pb2 import ROUTE_ISSUES

        assert route.status == ROUTE_ISSUES
    finally:
        for q in queryables:
            q.undeclare()
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
    queryables = rpc.install_queryables(session, CONF, store, adapter, publisher)
    try:
        req = request_route(
            (57.684, 11.800), (57.700, 11.880),
            goal="archipelago_conservative", profile=PROFILE, n_alternatives=3,
        )
        routes = _ok_routes(session, _rpc_key("plan_route"), req.SerializeToString())
        assert routes, "live plan_route returned no alternatives"
        assert routes[0].SerializeToString()
        assert len(routes[0].waypoints.waypoint) >= 2
    finally:
        for q in queryables:
            q.undeclare()
        publisher.close()
        adapter.close()
        session.close()
