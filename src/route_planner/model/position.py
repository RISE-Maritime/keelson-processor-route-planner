"""A waypoint position, read and written whichever keelson message carries it.

keelson moved ``Waypoint.position`` from a ``foxglove.LocationFix`` (``latitude`` /
``longitude``) to a ``keelson.Coordinate`` (``latitude_deg`` / ``longitude_deg``). Read and write
through here so the planner runs on either SDK, and a future rename is a one-file change (as
``model/enums.py`` does for the enum values).
"""

from __future__ import annotations


def _names(position) -> tuple[str, str]:
    descriptor = getattr(position, "DESCRIPTOR", None)
    if descriptor is not None:
        has = "latitude_deg" in descriptor.fields_by_name
    else:  # a plain object standing in for one (tests, the benchmark)
        has = hasattr(position, "latitude_deg")
    return ("latitude_deg", "longitude_deg") if has else ("latitude", "longitude")


def lat_lon(position) -> tuple[float, float]:
    """``(latitude, longitude)`` in degrees."""
    lat, lon = _names(position)
    return float(getattr(position, lat)), float(getattr(position, lon))


def set_lat_lon(position, latitude: float, longitude: float) -> None:
    lat, lon = _names(position)
    setattr(position, lat, float(latitude))
    setattr(position, lon, float(longitude))
