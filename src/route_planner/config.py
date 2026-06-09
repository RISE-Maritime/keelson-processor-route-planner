"""Runtime configuration.

The config is assembled from three layers, lowest precedence first: built-in ``DEFAULTS``, an
optional YAML file (``--config``), then explicit CLI overrides. Deployment URLs
(``CHART_SERVER_URL``, ``DATABASE_URL``) are read from the environment (see .env.example), not
the config file. See docs/DESIGN.md §3.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULTS: dict = {
    "realm": "rise",  # keelson realm / base path
    "entity_id": "route-planner",
    "source_id": "route-planner/0",  # also the RPC responder id
}

# CLI flags that, when supplied, override the file/defaults. argparse dests match these names.
_CLI_OVERRIDES = ("realm", "entity_id", "source_id")


class ConfigError(ValueError):
    """Raised when a config fails validation."""


def _validate(conf: dict) -> None:
    """Validate required fields and types. Raises ConfigError on the first problem."""
    for key in ("realm", "entity_id", "source_id"):
        if not isinstance(conf.get(key), str) or not conf[key]:
            raise ConfigError(f"config field {key!r} must be a non-empty string")


def load(args=None) -> dict:
    """Assemble the runtime config: DEFAULTS < YAML file < CLI args, plus env URLs.

    ``args`` is the parsed ``argparse.Namespace`` (or None for pure defaults).
    """
    conf = dict(DEFAULTS)

    config_path = getattr(args, "config", None)
    if config_path:
        loaded = yaml_safe_load(config_path)
        conf.update({k: v for k, v in loaded.items() if v is not None})

    for key in _CLI_OVERRIDES:
        value = getattr(args, key, None) if args is not None else None
        if value is not None:
            conf[key] = value

    # Deployment URLs from the environment (optional in the P1 skeleton).
    from environs import Env

    env = Env()
    conf["database_url"] = env.str("DATABASE_URL", None)
    conf["chart_server_url"] = env.str("CHART_SERVER_URL", "http://localhost:8080")

    _validate(conf)
    logger.info(
        "Config: realm=%s entity_id=%s source_id=%s chart_server=%s",
        conf["realm"],
        conf["entity_id"],
        conf["source_id"],
        conf["chart_server_url"],
    )
    return conf


def yaml_safe_load(path: str | Path) -> dict:
    """Load a YAML config file into a dict. Raises ConfigError if it is not a mapping."""
    import yaml

    path = Path(path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigError(f"config {path} did not parse to a mapping")
    return loaded
