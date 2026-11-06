"""Validation engine — populate RouteIssue / RouteChallenge + status + provenance (P7).

`validate(route, nav)` checks a ``keelson.Route``'s geometry against a P3 ``NavigabilityResult`` and
records the result on the route itself: hard-block crossings → ``RouteIssue(CRITICAL)``, soft-cost
exposure / snapped endpoints / sharp turns → typed ``RouteChallenge(WARNING)``, overall ``status``,
and a validation summary on the ``route-planner`` Extensions entry. Runs after generation and after
any manual edit. Pure shapely/keelson, no I/O. A route is never "guaranteed safe" (DESIGN §9, §12).
"""

from __future__ import annotations

import logging
import math
import time

from shapely.geometry import LineString

from ..model import enums as E
from ..model.position import lat_lon
from ..model.vessel_profile import EXTENSION_NAME, MANUFACTURER

logger = logging.getLogger(__name__)

SHARP_TURN_DEG = 60.0  # heading change beyond this at an interior waypoint is flagged

# Hard-block component -> issue description.
_HARD_DESC = {
    "land": "route crosses land",
    "unsafe_depth": "route crosses water shallower than the safety depth",
    "hazards": "route passes within the safety margin of a rock/wreck/obstruction",
    "restricted": "route enters an entry-prohibited area",
    "no_go": "route crosses a no-go zone",
    "unknown": "route crosses uncharted-depth water",
}


def _grid_cell(nav) -> float:
    """The pathfinder's grid cell size for this nav — the route's discretization tolerance.

    Mirrors pathfinding.grid.CostGrid.from_navigability so validation tolerates exactly the slack
    the planner allows (route within navigable.buffer(cell_size)).
    """
    from ..pathfinding.grid import MAX_DIM

    margin = nav.params.route_margin_m if nav.params else 50.0
    cell = max(float(margin), 1.0)
    geom = nav.navigable
    if geom is not None and not geom.is_empty:
        minx, miny, maxx, maxy = geom.bounds
        cell = max(cell, max(maxx - minx, maxy - miny) / MAX_DIM)
    return cell


def _challenge_type(kind):
    return {
        "shallow_but_legal": E.SHALLOW_WATER,
        "outside_fairway": E.REGULATORY_LIMIT,
        "restricted_soft": E.REGULATORY_LIMIT,
        "near_land_margin": E.TECHNICAL_LIMIT,
        "poor_chart_quality": E.TECHNICAL_LIMIT,
        "unknown_depth": E.TECHNICAL_LIMIT,
        "traffic_density": E.TRAFFIC_DENSITY,
    }.get(kind, E.OTHER)


def _planner_entry(route, create: bool = True):
    for entry in route.extensions.entries:
        if entry.manufacturer == MANUFACTURER and entry.name == EXTENSION_NAME:
            return entry
    if not create:
        return None
    entry = route.extensions.entries.add()
    entry.manufacturer = MANUFACTURER
    entry.name = EXTENSION_NAME
    entry.version = "0.1"
    return entry


def _segments(points_m, ids):
    return [
        (ids[i], ids[i + 1], LineString([points_m[i], points_m[i + 1]]))
        for i in range(len(points_m) - 1)
    ]


def _crossing_ids(segments, geom) -> list:
    crossed = set()
    for id_a, id_b, seg in segments:
        if seg.intersects(geom):
            crossed.add(id_a)
            crossed.add(id_b)
    return sorted(crossed)


def _turn_angle_deg(a, b, c) -> float:
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    cos_ang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos_ang))


def validate(route, nav):
    """Populate ``route.issues`` / ``route.challenges`` / ``status`` from a NavigabilityResult."""
    now = time.time_ns()
    del route.issues[:]  # idempotent: re-validation replaces any prior report
    del route.challenges[:]

    ids = [wp.id for wp in route.waypoints.waypoint]
    proj = nav.projection
    points_m = (
        [proj.forward(*reversed(lat_lon(wp.position))) for wp in route.waypoints.waypoint]
        if proj is not None
        else []
    )

    if len(points_m) >= 2:
        segments = _segments(points_m, ids)
        # A hazard only matters where it is genuinely non-navigable water. Tolerate the route being
        # within one grid cell of navigable: the pathfinder only guarantees the (straight-legged,
        # simplified) route within navigable.buffer(cell_size), so legs that graze the margin band
        # at concave corners must not register as criticals — only hazards beyond that band do.
        nav_buf = None
        if nav.navigable is not None and not nav.navigable.is_empty:
            nav_buf = nav.navigable.buffer(_grid_cell(nav))

        for name, geom in (nav.hard_blocks or {}).items():
            if geom is None or geom.is_empty:
                continue
            if nav_buf is not None:
                geom = geom.difference(nav_buf)
            if geom.is_empty:
                continue
            crossed = _crossing_ids(segments, geom)
            if crossed:
                issue = route.issues.add()
                issue.id = f"issue-{name}"
                issue.severity = E.CRITICAL
                issue.raised_by = "route-planner"
                issue.raised_at.FromNanoseconds(now)
                issue.description = (
                    f"{_HARD_DESC.get(name, 'route crosses ' + name)} "
                    f"(near waypoints {', '.join(map(str, crossed))})"
                )

        seen = set()
        for soft in nav.soft_costs or []:
            if soft.kind in seen or soft.geometry is None or soft.geometry.is_empty:
                continue
            crossed = _crossing_ids(segments, soft.geometry)
            if crossed:
                seen.add(soft.kind)
                challenge = route.challenges.add()
                challenge.id = f"challenge-{soft.kind}"
                challenge.type = _challenge_type(soft.kind)
                challenge.severity = E.WARNING
                challenge.description = f"route passes through {soft.kind.replace('_', ' ')}"
                challenge.waypoint_ids.extend(crossed)

    _snapped_challenge(route, ids)
    _turn_challenge(route, points_m, ids)
    _finalize(route, now)
    return route


def _snapped_challenge(route, ids) -> None:
    entry = _planner_entry(route, create=False)
    if entry is None:
        return
    ends = []
    if entry.attributes.get("snapped_start") == "true" and ids:
        ends.append(("start", ids[0]))
    if entry.attributes.get("snapped_end") == "true" and ids:
        ends.append(("end", ids[-1]))
    if ends:
        challenge = route.challenges.add()
        challenge.id = "challenge-snapped"
        challenge.type = E.TECHNICAL_LIMIT
        challenge.severity = E.WARNING
        challenge.description = (
            f"{' and '.join(label for label, _id in ends)} snapped to navigable water"
        )
        challenge.waypoint_ids.extend(wid for _label, wid in ends)


def _turn_challenge(route, points_m, ids) -> None:
    if len(points_m) < 3:
        return
    sharp = [
        ids[i]
        for i in range(1, len(points_m) - 1)
        if _turn_angle_deg(points_m[i - 1], points_m[i], points_m[i + 1]) > SHARP_TURN_DEG
    ]
    if sharp:
        challenge = route.challenges.add()
        challenge.id = "challenge-sharp-turn"
        challenge.type = E.TECHNICAL_LIMIT
        challenge.severity = E.WARNING
        challenge.description = (
            f"sharp turn(s) over {SHARP_TURN_DEG:.0f} deg at waypoint(s) "
            f"{', '.join(map(str, sharp))}"
        )
        challenge.waypoint_ids.extend(sharp)


def _finalize(route, now) -> None:
    critical = any(issue.severity == E.CRITICAL for issue in route.issues)
    if critical:
        route.status = E.ROUTE_ISSUES
    attrs = _planner_entry(route).attributes
    attrs["validation_status"] = "issues" if critical else ("warnings" if route.challenges else "ok")
    attrs["n_issues"] = str(len(route.issues))
    attrs["n_challenges"] = str(len(route.challenges))
    attrs["validated_at"] = str(now)
    attrs["edited_after_validation"] = "false"
