"""Stable short aliases for the keelson Route/Severity enum values the planner uses.

keelson commit b653c3c (`prefix Route enum values; share SeverityLevel`) renamed every Route enum
value (``RECOMMENDED`` → ``ROUTE_STATUS_RECOMMENDED``, ``SHALLOW_WATER`` →
``ROUTE_CHALLENGE_TYPE_SHALLOW_WATER`` …) and moved ``SeverityLevel`` into its own ``Severity.proto``
(``keelson.SeverityLevel``, generated module ``keelson.payloads.Severity_pb2``). The numeric values
are stable; this module maps the short names the planner uses to the SDK's current values, so a
future rename/move is a one-file change.

Import enum *values* from here; import message *types* (``Route``, ``Waypoint`` …) from
``keelson.payloads.Route_pb2`` directly.
"""

from __future__ import annotations

import keelson.payloads.Route_pb2 as _R
import keelson.payloads.Severity_pb2 as _S

# RouteStatus
INITIAL = _R.ROUTE_STATUS_INITIAL
PLANNED = _R.ROUTE_STATUS_PLANNED
RECOMMENDED = _R.ROUTE_STATUS_RECOMMENDED
ACKNOWLEDGED = _R.ROUTE_STATUS_ACKNOWLEDGED
TERMINATED = _R.ROUTE_STATUS_TERMINATED
ERRORS = _R.ROUTE_STATUS_ERRORS
INCOMPLETE = _R.ROUTE_STATUS_INCOMPLETE
ROUTE_ISSUES = _R.ROUTE_STATUS_ROUTE_ISSUES

# SeverityLevel (now keelson.SeverityLevel, in Severity.proto). keelson 068f74a unified the
# severity scale to LOW / MEDIUM / HIGH / CRITICAL; the planner's INFO and WARNING map onto LOW and
# MEDIUM there. The older names are used where the installed SDK still has them.
INFO = getattr(_S, "SEVERITY_LEVEL_INFO", None) or _S.SEVERITY_LEVEL_LOW
WARNING = getattr(_S, "SEVERITY_LEVEL_WARNING", None) or _S.SEVERITY_LEVEL_MEDIUM
CRITICAL = _S.SEVERITY_LEVEL_CRITICAL

# RouteChallengeType
SHALLOW_WATER = _R.ROUTE_CHALLENGE_TYPE_SHALLOW_WATER
TRAFFIC_DENSITY = _R.ROUTE_CHALLENGE_TYPE_TRAFFIC_DENSITY
WEATHER_HAZARD = _R.ROUTE_CHALLENGE_TYPE_WEATHER_HAZARD
REGULATORY_LIMIT = _R.ROUTE_CHALLENGE_TYPE_REGULATORY_LIMIT
TECHNICAL_LIMIT = _R.ROUTE_CHALLENGE_TYPE_TECHNICAL_LIMIT
OTHER = _R.ROUTE_CHALLENGE_TYPE_OTHER

# GeometryType
LOXODROME = _R.GEOMETRY_TYPE_LOXODROME
ORTHODROME = _R.GEOMETRY_TYPE_ORTHODROME

# OperationalContext
OPEN_SEA = _R.OPERATIONAL_CONTEXT_OPEN_SEA
COASTAL = _R.OPERATIONAL_CONTEXT_COASTAL
ARCHIPELAGO = _R.OPERATIONAL_CONTEXT_ARCHIPELAGO
PORT_APPROACH = _R.OPERATIONAL_CONTEXT_PORT_APPROACH

# HazardZoneType
STATIC_NOGO = _R.HAZARD_ZONE_TYPE_STATIC_NOGO
DYNAMIC_NOGO = _R.HAZARD_ZONE_TYPE_DYNAMIC_NOGO
