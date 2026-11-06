"""RouteStore unit tests (P1) — operates on real keelson.Route objects."""

from __future__ import annotations

from keelson.payloads.Route_pb2 import Route

from route_planner.model.enums import ACKNOWLEDGED, PLANNED, RECOMMENDED
from route_planner.model.store import RouteStore


def _route(route_id: str, status=PLANNED, edition: int = 1) -> Route:
    return Route(route_id=route_id, status=status, route_edition_number=edition)


def test_put_get_roundtrip():
    store = RouteStore()
    store.put(_route("r1"))
    got = store.get("r1")
    assert got is not None
    assert got.route_id == "r1"


def test_get_unknown_returns_none():
    assert RouteStore().get("nope") is None


def test_list_ids():
    store = RouteStore()
    store.put(_route("a"))
    store.put(_route("b"))
    assert set(store.list_ids()) == {"a", "b"}


def test_select_advances_status_and_bumps_edition():
    store = RouteStore()
    store.put(_route("r1", status=RECOMMENDED, edition=1))
    selected = store.select("r1")
    assert selected.status == ACKNOWLEDGED
    assert selected.route_edition_number == 2


def test_select_unknown_returns_none():
    assert RouteStore().select("nope") is None
