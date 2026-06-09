"""Startup smoke tests for the P1 skeleton.

Exercise the parts that need no live Zenoh session: package import, CLI parsing, config assembly
(defaults / file / CLI overrides), and the FastAPI /health endpoint.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

import route_planner
from route_planner import app as app_mod
from route_planner import config as config_mod
from route_planner.api import create_app


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
    assert conf["http_host"] == "0.0.0.0"
    assert conf["http_port"] == 8000
    assert conf["chart_server_url"]  # a default is always present


def test_config_cli_overrides():
    args = types.SimpleNamespace(
        config=None,
        realm="other",
        entity_id="rp2",
        source_id="rp2/9",
        http_host="127.0.0.1",
        http_port=9001,
    )
    conf = config_mod.load(args)
    assert conf["realm"] == "other"
    assert conf["http_port"] == 9001


def test_config_example_file_loads():
    example = Path(__file__).resolve().parent.parent / "config" / "example.yaml"
    args = types.SimpleNamespace(config=str(example))
    conf = config_mod.load(args)
    assert conf["realm"] == "rise"
    assert conf["http_port"] == 8000


def test_config_rejects_bad_port():
    args = types.SimpleNamespace(config=None, http_port="not-an-int")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(args)


def test_health_endpoint():
    from fastapi.testclient import TestClient

    client = TestClient(create_app(config_mod.load(None)))
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "route-planner"
