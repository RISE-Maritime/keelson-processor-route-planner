"""Entry point: parse args, load config, open zenoh, serve route_planner/v1, idle until shutdown.

Wires argument parsing, config, the zenoh session, the source-level liveliness token and the
keelson ``route_planner/v1`` RPC interface (whose own interface-level token ``serve_rpc``
declares), then idles under graceful shutdown. The sole integration transport is Keelson RPC -
there is no HTTP layer.
"""

from __future__ import annotations

import argparse
import logging

from . import config as config_mod
from . import rpc
from .model.store import RouteStore

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args: keelson common args + config / realm / entity / source."""
    parser = argparse.ArgumentParser(
        description=(
            "Keelson processor: generate, validate and publish safe voyage-route alternatives "
            "as keelson.Route artifacts over Keelson RPC. Voyage planning + validation only - "
            "NOT collision avoidance, NOT autonomous execution."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Adds --log-level, --mode, --connect, --listen (keelson scaffolding).
    from keelson.scaffolding import add_common_arguments

    add_common_arguments(parser)
    parser.add_argument("-c", "--config", help="Path to a YAML config file (optional).")
    parser.add_argument("-r", "--realm", help="Keelson realm / base path (default: rise).")
    parser.add_argument("-e", "--entity-id", help="Keelson entity id (default: route-planner).")
    parser.add_argument("-s", "--source-id", help="Keelson source id (default: route-planner/0).")
    return parser.parse_args(argv)


def run(session, conf: dict) -> None:
    """Declare liveliness + serve route_planner/v1, then block until shutdown."""
    from keelson.scaffolding import GracefulShutdown, declare_liveliness

    from .chart_adapter import ChartAdapter
    from .publish import RoutePublisher

    realm = conf["realm"]
    entity_id = conf["entity_id"]
    source_id = conf["source_id"]

    store = RouteStore()
    adapter = ChartAdapter(conf)  # lazy I/O — no DB/HTTP connection until plan_route runs
    publisher = RoutePublisher(session, conf)

    with GracefulShutdown() as shutdown:
        # Source-level token only. The route keys this planner writes (route/{id}/edition/{N},
        # route/{id}/latest, route_status/{id}, route_change_event/{id}/{cid}) carry no producer
        # chunk, so there is no producer prefix to hang a subject-level token on (keelson spec
        # §2.1.1, §6.3). The interface-level token comes from serve_rpc.
        with declare_liveliness(session, realm, entity_id, source_id):
            server = rpc.serve(session, conf, store, adapter, publisher)
            logger.info(
                "route-planner connected as %s/%s/%s, serving %s/%s (%d procedures)",
                realm,
                entity_id,
                source_id,
                rpc.INTERFACE,
                rpc.INTERFACE_VERSION,
                len(server.queryables),
            )
            try:
                # wait() returns True as soon as a shutdown signal arrives, so the loop stays
                # responsive to SIGINT/SIGTERM rather than sleeping a fixed interval.
                while not shutdown.wait(1.0):
                    pass
            finally:
                rpc.close(server)
                publisher.close()
                adapter.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, load config, open zenoh, run until shutdown."""
    args = parse_args(argv)

    from keelson.scaffolding import create_zenoh_config, setup_logging

    setup_logging(level=args.log_level)
    conf = config_mod.load(args)

    import zenoh

    zconf = create_zenoh_config(mode=args.mode, connect=args.connect, listen=args.listen)
    session = zenoh.open(zconf)
    try:
        run(session, conf)
    finally:
        session.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
