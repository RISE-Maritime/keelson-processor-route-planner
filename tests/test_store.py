"""RouteStore unit tests — an edition store, operating on real keelson.Route objects."""

from __future__ import annotations

import pytest
from keelson.payloads.Route_pb2 import Route

from route_planner.model.enums import ACKNOWLEDGED, PLANNED, RECOMMENDED
from route_planner.model.store import EditionExists, RouteStore, StaleEdition


def _route(route_id: str, status=PLANNED, edition: int = 1) -> Route:
    return Route(route_id=route_id, status=status, route_edition_number=edition)


def test_put_get_roundtrip():
    store = RouteStore()
    store.put(_route("r1"))
    got = store.get("r1", 1)
    assert got is not None
    assert got.route_id == "r1"


def test_get_unknown_returns_none():
    assert RouteStore().get("nope", 1) is None


def test_get_dangling_edition_is_not_resolved_to_latest():
    store = RouteStore()
    store.put(_route("r1", edition=1))
    assert store.get("r1", 2) is None


def test_an_edition_is_never_rewritten():
    store = RouteStore()
    store.put(_route("r1"))
    with pytest.raises(EditionExists):
        store.put(_route("r1", status=RECOMMENDED))


def test_list_ids():
    store = RouteStore()
    store.put(_route("a"))
    store.put(_route("b"))
    assert set(store.list_ids()) == {"a", "b"}


def test_select_makes_a_new_edition_and_keeps_the_old():
    store = RouteStore()
    store.put(_route("r1", status=RECOMMENDED, edition=1))
    selected = store.select("r1", 1)
    assert selected.status == ACKNOWLEDGED
    assert selected.route_edition_number == 2
    assert store.get("r1", 1).status == RECOMMENDED  # edition 1 untouched
    assert store.get("r1", 2) is selected
    assert store.latest("r1") is selected


def test_select_from_a_superseded_edition_is_refused():
    store = RouteStore()
    store.put(_route("r1", status=RECOMMENDED, edition=1))
    store.select("r1", 1)
    with pytest.raises(StaleEdition):
        store.select("r1", 1)


def test_select_unknown_returns_none():
    assert RouteStore().select("nope", 1) is None
