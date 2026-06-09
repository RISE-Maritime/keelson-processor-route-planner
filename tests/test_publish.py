"""RoutePublisher tests (P6) — Envelope-framed pub/sub round-trips over a peer session."""

from __future__ import annotations

import time

import keelson
import zenoh
from keelson.payloads.Route_pb2 import Route, RouteStatusUpdate

from route_planner.publish import RoutePublisher

CONF = {"realm": "rise", "entity_id": "route-planner", "source_id": "route-planner/0"}


def _collect(session, subject):
    key = keelson.construct_pubsub_key(CONF["realm"], CONF["entity_id"], subject, CONF["source_id"])
    received = []
    sub = session.declare_subscriber(key, lambda s: received.append(bytes(s.payload.to_bytes())))
    return received, sub


def _wait(received):
    for _ in range(60):
        if received:
            return True
        time.sleep(0.05)
    return False


def test_publish_planned_roundtrip():
    session = zenoh.open(zenoh.Config())
    received, sub = _collect(session, "route_planned")
    pub = RoutePublisher(session, CONF)
    try:
        pub.publish_planned(Route(route_id="r1", route_edition_number=1))
        assert _wait(received), "no route_planned sample received"
        _recv, _enc, payload = keelson.uncover(received[0])
        assert Route.FromString(payload).route_id == "r1"
    finally:
        pub.close()
        sub.undeclare()
        session.close()


def test_publish_status_roundtrip():
    from keelson.payloads.Route_pb2 import ACKNOWLEDGED

    session = zenoh.open(zenoh.Config())
    received, sub = _collect(session, "route_status")
    pub = RoutePublisher(session, CONF)
    try:
        pub.publish_status("r1", 2, ACKNOWLEDGED, changed_by="operator", note="selected")
        assert _wait(received), "no route_status sample received"
        _recv, _enc, payload = keelson.uncover(received[0])
        update = RouteStatusUpdate.FromString(payload)
        assert update.route_id == "r1"
        assert update.route_edition_number == 2
        assert update.status == ACKNOWLEDGED
    finally:
        pub.close()
        sub.undeclare()
        session.close()
