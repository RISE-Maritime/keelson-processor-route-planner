"""RoutePublisher tests — Envelope-framed pub/sub on keelson's §6.3 key tree, over a peer session."""

from __future__ import annotations

import time

import keelson
import zenoh
from keelson.payloads.Route_pb2 import Route, RouteStatusUpdate
from keelson.payloads.RouteChangeEvent_pb2 import RouteChangeEvent

from route_planner.publish import RoutePublisher

CONF = {"realm": "rise", "entity_id": "route-planner", "source_id": "route-planner/0"}
BASE = "rise/@v0/route-planner/pubsub"


def _collect(session, key_expr):
    """Subscribe; collect (key, payload bytes) in arrival order."""
    received = []
    sub = session.declare_subscriber(
        key_expr, lambda s: received.append((str(s.key_expr), bytes(s.payload.to_bytes())))
    )
    return received, sub


def _wait(received, n=1):
    for _ in range(60):
        if len(received) >= n:
            return True
        time.sleep(0.05)
    return False


def _decode(message_type, raw):
    _recv, _enc, payload = keelson.uncover(raw)
    return message_type.FromString(payload)


def test_publish_route_edition_then_latest():
    session = zenoh.open(zenoh.Config())
    received, sub = _collect(session, f"{BASE}/route/**")
    pub = RoutePublisher(session, CONF)
    try:
        pub.publish_route(Route(route_id="r1", route_edition_number=1))
        assert _wait(received, 2), f"expected edition + latest, got {[k for k, _ in received]}"
        keys = [k for k, _ in received]
        assert keys == [f"{BASE}/route/r1/edition/1", f"{BASE}/route/r1/latest"]
        assert _decode(Route, received[1][1]).route_id == "r1"
    finally:
        pub.close()
        sub.undeclare()
        session.close()


def test_nothing_is_published_on_route_planned():
    session = zenoh.open(zenoh.Config())
    received, sub = _collect(session, f"{BASE}/route_planned/**")
    pub = RoutePublisher(session, CONF)
    try:
        pub.publish_route(Route(route_id="r1", route_edition_number=1))
        time.sleep(0.3)
        assert received == []
    finally:
        pub.close()
        sub.undeclare()
        session.close()


def test_publish_edition_bump_order_and_change_event():
    session = zenoh.open(zenoh.Config())
    received, sub = _collect(session, f"{BASE}/**")
    pub = RoutePublisher(session, CONF)
    try:
        pub.publish_edition_bump(
            Route(route_id="r1", route_edition_number=2), 1, reason="select_route"
        )
        assert _wait(received, 3), f"expected 3 samples, got {[k for k, _ in received]}"
        keys = [k for k, _ in received]
        assert keys[0] == f"{BASE}/route/r1/edition/2"
        assert keys[1] == f"{BASE}/route/r1/latest"
        assert keys[2].startswith(f"{BASE}/route_change_event/r1/")
        event = _decode(RouteChangeEvent, received[2][1])
        assert (event.old_edition, event.new_edition) == (1, 2)
        assert keys[2].endswith(event.change_id)
        assert event.author_id == CONF["source_id"]
        assert event.reason == "select_route"
    finally:
        pub.close()
        sub.undeclare()
        session.close()


def test_publish_status_roundtrip():
    from route_planner.model.enums import ACKNOWLEDGED

    session = zenoh.open(zenoh.Config())
    received, sub = _collect(session, f"{BASE}/route_status/**")
    pub = RoutePublisher(session, CONF)
    try:
        pub.publish_status("r1", 2, ACKNOWLEDGED, note="selected")
        assert _wait(received), "no route_status sample received"
        key, raw = received[0]
        assert key == f"{BASE}/route_status/r1"
        update = _decode(RouteStatusUpdate, raw)
        assert update.route_id == "r1"
        assert update.route_edition_number == 2
        assert update.status == ACKNOWLEDGED
        assert update.changed_by == CONF["source_id"]
    finally:
        pub.close()
        sub.undeclare()
        session.close()
