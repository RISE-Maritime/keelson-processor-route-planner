"""Navigation risk of a planned route — navrisk's model, run over the route before it is sailed.

After validation, ``annotate(route, chart)`` runs ``navrisk.route.assess_route`` over the track the
plan implies (keelson-processor-navrisk, method §4.3/§4.5/§4.6 and the NAV-006 turn envelope) and
records what it found on the route itself, so every consumer of the route sees the same answer:

- an Extensions entry ``se.rise`` / ``navrisk-route-risk`` (version 1) with the route's combined
  level, each dimension's worst value (or *not measurable* and why), the §6.4 gates, and the
  per-leg and per-turn profile as compact JSON attributes;
- a ``RouteChallenge`` for each failed gate, each infeasible turn, and each leg whose combined
  level is HIGH.

What a plan cannot say is said, not scored: collision is **not measurable** (a plan has no
traffic), and without a chart connection the water is not measurable either — never zero risk.
navrisk is an optional dependency: without it the entry says ``unavailable`` and nothing else
changes. Pure apart from the chart connection; no zenoh.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Optional

from .model import enums as E
from .model.position import lat_lon
from .model.vessel_profile import MANUFACTURER

logger = logging.getLogger(__name__)

#: The Extensions entry the risk profile is recorded in.
RISK_EXTENSION = "navrisk-route-risk"
RISK_EXTENSION_VERSION = "1"
#: Environment variable naming the S-57 PostGIS database navrisk reads (it opens it read-only);
#: ``DATABASE_URL``, the planner's own, is used when it is not set.
CHART_DSN_ENV = "NAVRISK_CHART_DSN"
#: Samples per route at most: the chart-dimension stride is at least 30 s of planned time and at
#: least duration / this. A declared cost parameter (each sample is a chart query or a cache hit).
MAX_SAMPLES = 200
#: Environment switch: also assess each alternative plan_route returns (slower planning).
ON_PLAN_ENV = "NAVRISK_ON_PLAN"

#: Route extension attributes a caller may set to declare §6.4 gate limits (none by default:
#: the values are navrisk's NAV-013 to establish, so an undeclared gate is not applied).
GATE_ATTRS = ("collision_risk_max", "grounding_risk_max", "manoeuvre_risk_max", "fairway_risk_max")

#: Attributes a caller sets on the request that the result keeps: which platform the particulars
#: were taken from, and where each came from (Crowsnest's platform selector).
REQUEST_ATTRS = ("platform", "particulars_basis")

_chart_source = None


def chart_source():
    """navrisk's PostGIS chart source over the planner's chart database, or ``None``.

    Opened once, read-only. Without a DSN the chart dimensions are not measurable, and say so.
    """
    global _chart_source
    if _chart_source is not None:
        return _chart_source
    dsn = os.environ.get(CHART_DSN_ENV) or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg2

        from navrisk.charts import PostGISChartSource
        from navrisk.charts.cache import ChartCache

        conn = psycopg2.connect(dsn)
        conn.set_session(readonly=True, autocommit=True)
        # Reuse mode: one enlarged query serves the samples that follow along a leg
        # (navrisk docs/LIVE_VS_BATCH.md §5 says what that changes).
        _chart_source = ChartCache(PostGISChartSource(conn), mode="reuse")
    except Exception as exc:  # noqa: BLE001 - the route is still returned, marked unassessed
        logger.warning("navrisk chart source unavailable: %s", exc)
        return None
    return _chart_source


def _opt(msg, name) -> Optional[float]:
    try:
        return float(getattr(msg, name)) if msg.HasField(name) else None
    except ValueError:  # not an optional field in this SDK
        value = getattr(msg, name, None)
        return float(value) if value else None


def plan_of(route):
    """``(waypoints, vessel_data, default_sog_kn, departure)`` from a keelson.Route."""
    from navrisk.route import PlannedWaypoint

    defaults = route.waypoints.defaults
    default_sog = _opt(defaults, "planned_sog_knots")
    default_radius = _opt(defaults, "radius_m")
    waypoints = []
    for wp in route.waypoints.waypoint:
        lat, lon = lat_lon(wp.position)
        waypoints.append(PlannedWaypoint(
            latitude=lat, longitude=lon, id=str(wp.id), name=wp.name,
            sog_kn=_opt(wp, "planned_sog_knots") or default_sog,
            radius_m=_opt(wp, "radius_m") or default_radius,
        ))
    info = route.info
    vessel = {k: v for k, v in (("loa", _opt(info, "vessel_loa_m")), ("woa", _opt(info, "vessel_beam_m")),
                                ("draft", _opt(info, "vessel_draft_m"))) if v}
    fallback = _opt(info, "max_speed_knots") or _opt(info, "min_speed_knots")
    departure = None
    for schedule in route.schedules.schedule if route.HasField("schedules") else ():
        for element in schedule.element:
            stamp = element.etd if element.HasField("etd") else (element.eta if element.HasField("eta") else None)
            if stamp is not None:
                departure = stamp.ToDatetime().isoformat() + "Z"
                break
        if departure:
            break
    return waypoints, vessel, fallback, departure


def _gate_limits(route) -> dict:
    limits = {}
    for entry in route.extensions.entries:
        if entry.name == RISK_EXTENSION:
            for key in GATE_ATTRS:
                if key in entry.attributes:
                    limits[key] = float(entry.attributes[key])
    return limits


def _entry(route):
    for entry in route.extensions.entries:
        if entry.manufacturer == MANUFACTURER and entry.name == RISK_EXTENSION:
            return entry
    entry = route.extensions.entries.add(manufacturer=MANUFACTURER, name=RISK_EXTENSION,
                                          version=RISK_EXTENSION_VERSION)
    return entry


def _fmt(v) -> str:
    return "" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v:.4g}"


def _clean(value, digits: int = 4):
    """Finite floats rounded (NaN -> null). Positions need ``digits=6``: 4 decimals of latitude
    is about 11 m, too coarse to draw a sample where it is."""
    if isinstance(value, float):
        return round(value, digits) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, digits) for v in value]
    return value


def annotate(route, chart=None, *, now_ns: Optional[int] = None):
    """Assess the route with navrisk and record the result on it (see the module docstring)."""
    now_ns = now_ns or time.time_ns()
    try:
        from navrisk import __version__ as navrisk_version
        from navrisk.params import GATE_KEYS, NavRiskParams
        from navrisk.route import assess_route
    except ImportError:
        attrs = _entry(route).attributes
        attrs.clear()
        attrs["status"] = "unavailable"
        attrs["note"] = "navrisk is not installed with this route planner"
        return route

    limits = _gate_limits(route)
    entry = _entry(route)
    kept = {k: entry.attributes[k] for k in (*GATE_ATTRS, *REQUEST_ATTRS) if k in entry.attributes}
    entry.attributes.clear()
    entry.attributes.update(kept)
    attrs = entry.attributes
    try:
        from navrisk.route import DEFAULT_SAMPLE_EVERY_S, legs_of

        waypoints, vessel, fallback, departure = plan_of(route)
        duration_s = legs_of(waypoints, fallback)[-1].end_s if len(waypoints) >= 2 else 0.0
        profile = assess_route(
            waypoints, chart=chart, vessel_data=vessel, default_sog_kn=fallback, departure=departure,
            thresholds=NavRiskParams(gates={k: v for k, v in limits.items() if k in GATE_KEYS}),
            sample_every_s=max(DEFAULT_SAMPLE_EVERY_S, duration_s / MAX_SAMPLES),
        )
    except (ValueError, TypeError) as exc:
        attrs["status"] = "not_assessed"
        attrs["note"] = str(exc)
        return route

    r = profile["route"]
    attrs["status"] = "assessed"
    attrs["navrisk_version"] = navrisk_version
    attrs["assessed_at"] = str(now_ns)
    attrs["method"] = profile["method"]
    attrs["level"] = r["combined"]["level"]
    attrs["risk_combined"] = _fmt(r["combined"]["risk_combined"])
    attrs["risk_fraction"] = _fmt(r["combined"]["risk_fraction"])
    attrs["scored_coverage"] = _fmt(r["combined"]["scored_coverage"])
    attrs["level_coverage_capped"] = "true" if r["combined"]["level_coverage_capped"] else "false"
    for name, d in r["dimensions"].items():
        attrs[f"dim.{name}.measurable"] = "true" if d["measurable"] else "false"
        if d["measurable"]:
            attrs[f"dim.{name}.max"] = _fmt(d["max"])
            attrs[f"dim.{name}.mean"] = _fmt(d["mean"])
        if d.get("provenance"):
            attrs[f"dim.{name}.provenance"] = d["provenance"]
        if d.get("note"):
            attrs[f"dim.{name}.note"] = d["note"]
    gates = r["gates"]
    attrs["gates.pass_fail"] = gates["pass_fail"] or ""
    attrs["gates.failed"] = ",".join(gates["failed"])
    attrs["gates.not_declared"] = ",".join(gates["not_declared"])
    attrs["turns_infeasible"] = str(r["turns_infeasible"])
    attrs["distance_m"] = _fmt(r["distance_m"])
    attrs["duration_s"] = _fmt(r["duration_s"])
    attrs["departure"] = r["departure"]
    attrs["sample_every_s"] = _fmt(r["sample_every_s"])
    legs = [{
        "index": leg["index"], "from": leg["from_id"], "to": leg["to_id"],
        "distance_m": leg["distance_m"], "sog_kn": leg["sog_kn"], "eta_start": leg["eta_start"],
        "level": leg["combined"]["level"], "risk": leg["combined"]["risk_combined"],
        "dims": {n: {"max": d.get("max"), "samples": d.get("measurable_samples", 0), "worst_at": d.get("worst_at")}
                 for n, d in leg["dimensions"].items()},
    } for leg in profile["legs"]]
    attrs["legs_json"] = json.dumps(_clean(legs), separators=(",", ":"))
    attrs["turns_json"] = json.dumps(_clean(profile["turns"]), separators=(",", ":"))
    # Each sample WHERE it is, per dimension and combined: what a station draws on its chart.
    attrs["series_json"] = json.dumps(_clean(profile["series"], 6), separators=(",", ":"))
    # The path the plan is sailed on, [planned_s, lat, lon] per vertex, arcs included: a station draws
    # the samples' risk along it, round each turn as planned, not across the corner.
    attrs["path_json"] = json.dumps(_clean(profile.get("path", []), 6), separators=(",", ":"))
    _challenges(route, profile)
    return route


def _challenges(route, profile) -> None:
    for c in [c for c in route.challenges if c.id.startswith("challenge-navrisk-")]:
        route.challenges.remove(c)
    legs = profile["legs"]
    for name in profile["route"]["gates"]["failed"]:
        worst = max(legs, key=lambda leg: leg["dimensions"].get(name, {}).get("max") or 0.0)
        check = profile["route"]["gates"]["applied"][name]
        c = route.challenges.add()
        c.id = f"challenge-navrisk-gate-{name}"
        c.type = E.OTHER
        c.severity = E.WARNING
        c.description = (f"navigation risk: {name} {check['max']:.2f} over the declared limit "
                         f"{check['limit']:.2f}, worst on leg {worst['from_id']}→{worst['to_id']}")
        c.waypoint_ids.extend([worst["from_id"], worst["to_id"]])
    for t in profile["turns"]:
        if t["feasible"]:
            continue
        c = route.challenges.add()
        c.id = f"challenge-navrisk-turn-{t['waypoint_id']}"
        c.type = E.TECHNICAL_LIMIT
        c.severity = E.WARNING
        why = []
        if t["tighter_than_envelope"]:
            why.append(f"planned radius {t['planned_radius_m']:.0f} m is tighter than the "
                       f"{t['envelope_radius_m']:.0f} m turn envelope ({t['envelope_basis']})")
        if not t["fits_between_legs"]:
            why.append(f"wheel-over {t['wheel_over_m']:.0f} m does not fit in {t['room_m']:.0f} m of leg")
        c.description = f"navigation risk: turn at waypoint {t['waypoint_id']}: " + "; ".join(why)
        c.waypoint_ids.append(t["waypoint_id"])
    for leg in legs:
        if leg["combined"]["level"] != "HIGH":
            continue
        c = route.challenges.add()
        c.id = f"challenge-navrisk-leg-{leg['index'] + 1}"
        c.type = E.OTHER
        c.severity = E.WARNING
        c.description = (f"navigation risk HIGH on leg {leg['from_id']}→{leg['to_id']} "
                         f"(combined {leg['combined']['risk_combined']:.2f}); a model index, not a probability")
        c.waypoint_ids.extend([leg["from_id"], leg["to_id"]])
