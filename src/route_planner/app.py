"""Entry point: parse args, load config, open zenoh, declare RPC queryables, idle until shutdown.

P1 skeleton: this wires argument parsing, config, the zenoh session, a liveliness token (the
keelson-native health signal), and the ``RoutePlanner`` RPC queryables, then idles under graceful
shutdown. Each queryable replies "not implemented yet" until the chart_adapter / navigability /
pathfinding engines and pub/sub publishers are filled in across P2-P8 (see docs/ROADMAP.md). The
sole integration transport is Keelson RPC - there is no HTTP layer.
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
    """Declare the liveliness token + RoutePlanner RPC queryables, then block until shutdown.

    P1: the queryables reply "not implemented yet". The planning engines and pub/sub publishers are
    added in later phases (docs/ROADMAP.md).
    """
    from keelson.scaffolding import GracefulShutdown, declare_liveliness_token

    realm = conf["realm"]
    entity_id = conf["entity_id"]
    source_id = conf["source_id"]

    store = RouteStore()

    with GracefulShutdown() as shutdown:
        with declare_liveliness_token(session, realm, entity_id, source_id):
            queryables = rpc.install_queryables(session, conf, store)
            logger.info(
                "route-planner connected as %s/%s/%s, %d RPC queryable(s) declared",
                realm,
                entity_id,
                source_id,
                len(queryables),
            )
            try:
                # wait() returns True as soon as a shutdown signal arrives, so the loop stays
                # responsive to SIGINT/SIGTERM rather than sleeping a fixed interval.
                while not shutdown.wait(1.0):
                    pass
            finally:
                for q in queryables:
                    q.undeclare()


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
