"""Interactive exploration + tuning helpers for the route-planning pipeline.

A thin observability layer for the notebooks (``notebooks/explore_and_tune.ipynb``): run the pipeline
against a live chart-server, visualize navigability + route alternatives (Plotly + deck.gl via
``pydeck``), and tune cost weights / ``NavParams``. Not used in production. Viz imports are lazy, so
importing this module (or the package) never requires plotly/pydeck — only the functions that draw do.

Everything reuses the real pipeline modules; there is no parallel planning logic here. All engine
geometry is in the navigability result's local metric CRS (``nav.projection``); we project to WGS84
(lon, lat) for the deck.gl map and keep metric coordinates for the Plotly geometry view.
"""

from __future__ import annotations

from .model.position import lat_lon

import os

from .planner import corridor_bbox

# A small-USV default profile (mirrors tests/_planhelpers.PROFILE). Override per call.
DEFAULT_PROFILE = {
    "name": "explore-usv",
    "length_m": 10,
    "beam_m": 2,
    "draught_m": 1,
    "preferred_speed_kn": 15.0,
    "max_speed_kn": 22.0,
    "min_under_keel_clearance_m": 2,
    "route_margin_m": 30.0,
    "turn_radius_m": 20.0,
}


def make_profile(**overrides) -> dict:
    """Build a vessel/planning profile from DEFAULT_PROFILE + overrides (validates field names).

    Fields (SI metres unless noted): ``name``, ``length_m``, ``beam_m``, ``draught_m``,
    ``min_under_keel_clearance_m``, ``preferred_speed_kn`` (kn), ``max_speed_kn`` (kn),
    ``route_margin_m``, ``turn_radius_m``. Safety depth = ``draught_m + min_under_keel_clearance_m``.
    Raises on an unknown field name so typos surface immediately.
    """
    unknown = set(overrides) - set(DEFAULT_PROFILE)
    if unknown:
        raise KeyError(f"unknown profile field(s): {sorted(unknown)}; valid: {sorted(DEFAULT_PROFILE)}")
    return {**DEFAULT_PROFILE, **overrides}


# A convenient Göteborg-archipelago start/end for the notebook.
GOTHENBURG_START = (57.684, 11.800)
GOTHENBURG_END = (57.700, 11.880)

# soft-cost kind -> RGB fill (deck.gl + Plotly share this palette).
SOFT_COLORS = {
    "shallow_but_legal": (255, 196, 0),
    "outside_fairway": (120, 144, 255),
    "restricted_soft": (200, 80, 200),
    "near_land_margin": (255, 120, 80),
    "poor_chart_quality": (160, 160, 160),
    "unknown_depth": (90, 90, 90),
    "traffic_density": (255, 80, 80),
}

_STATUS_NAMES = {2: "PLANNED", 3: "RECOMMENDED", 4: "ACKNOWLEDGED", 9: "ROUTE_ISSUES"}


# ---------------------------------------------------------------------------
# interactive start/end point picking (ipyleaflet)
# ---------------------------------------------------------------------------


class MapPicker:
    """Holds the start/end picked on an ipyleaflet map. Read ``.start`` / ``.end`` as (lat, lon)."""

    def __init__(self, start=None, end=None):
        self.start = tuple(start) if start else None
        self.end = tuple(end) if end else None
        self.map = None
        self.widget = None
        self._markers = {}

    @staticmethod
    def _fmt(p):
        return f"{p[0]:.5f}, {p[1]:.5f}" if p else "—"

    def points(self, default_start=None, default_end=None):
        """(start, end), falling back to the given defaults when a point hasn't been picked yet."""
        return (self.start or default_start, self.end or default_end)

    def _ipython_display_(self):
        from IPython.display import display

        display(self.widget)


def point_picker(*, start=None, end=None, center=None, zoom: int = 12):
    """An interactive map to click a START then an END point (markers are draggable to fine-tune).

    Display the returned ``MapPicker`` in a cell, click on the chart, then read ``picker.start`` /
    ``picker.end`` (lat, lon) — or ``picker.points(default_start, default_end)``. Seeded with the
    optional ``start`` / ``end``. Requires ipyleaflet (the ``notebook`` extra).
    """
    import ipywidgets as widgets
    from ipyleaflet import AwesomeIcon, Map, Marker, basemaps

    if center is None:
        s, e = start or GOTHENBURG_START, end or GOTHENBURG_END
        center = ((s[0] + e[0]) / 2, (s[1] + e[1]) / 2)

    picker = MapPicker(start=start, end=end)
    m = Map(center=center, zoom=zoom, scroll_wheel_zoom=True, basemap=basemaps.OpenStreetMap.Mapnik)
    picker.map = m
    readout = widgets.HTML()

    def refresh():
        readout.value = (
            f"<b>start</b>: {picker._fmt(picker.start)} &nbsp;&nbsp; "
            f"<b>end</b>: {picker._fmt(picker.end)}"
        )

    def place(which, latlon):
        latlon = (round(latlon[0], 6), round(latlon[1], 6))
        setattr(picker, which, latlon)
        marker = picker._markers.get(which)
        if marker is None:
            color = "green" if which == "start" else "red"
            marker = Marker(
                location=latlon, draggable=True, title=which.upper(),
                icon=AwesomeIcon(name="circle", marker_color=color),
            )

            def on_move(_change, w=which, mk=marker):
                setattr(picker, w, (round(mk.location[0], 6), round(mk.location[1], 6)))
                refresh()

            marker.observe(on_move, names="location")
            picker._markers[which] = marker
            m.add(marker)
        else:
            marker.location = latlon
        refresh()

    def on_click(**kwargs):
        if kwargs.get("type") != "click":
            return
        lat, lon = kwargs.get("coordinates")
        if picker.start is None:
            place("start", (lat, lon))
        elif picker.end is None:
            place("end", (lat, lon))
        else:  # both already set -> start a fresh pair from this click
            old = picker._markers.pop("end", None)
            if old is not None:
                m.remove(old)
            picker.end = None
            place("start", (lat, lon))

    m.on_interaction(on_click)
    if picker.start:
        place("start", picker.start)
    if picker.end:
        place("end", picker.end)
    refresh()

    picker.widget = widgets.VBox(
        [
            widgets.HTML("<b>Click START (green) then END (red) on the chart</b> — drag markers to adjust."),
            m,
            readout,
        ]
    )
    return picker


def default_conf(conf: dict | None = None) -> dict:
    """Chart-adapter config from the environment (DATABASE_URL / CHART_SERVER_URL) unless given."""
    if conf:
        return conf
    return {
        "database_url": os.environ.get("DATABASE_URL"),
        "chart_server_url": os.environ.get("CHART_SERVER_URL", "http://localhost:8080"),
    }


def run_pipeline(
    start,
    end,
    *,
    via=(),
    profile: dict | None = None,
    goal: str = "archipelago_conservative",
    n_alternatives: int = 3,
    constraints: dict | None = None,
    conf: dict | None = None,
    pad_m: float = 2000.0,
):
    """Run adapter -> navigability -> pathfinding -> serialize -> validate against the live chart.

    Returns a dict ``{bbox, chart, params, nav, planned, routes}`` so each stage can be inspected/
    plotted. Mirrors ``planner.run_plan`` but also exposes the intermediates. Closes the adapter.
    """
    from .chart_adapter import ChartAdapter
    from .model import serialize
    from .navigability import build_navigability, nav_params_from_profile
    from .pathfinding import plan as pathfind
    from .validation import validate

    profile = profile or DEFAULT_PROFILE
    via = list(via)
    constraints = constraints or {}
    bbox = corridor_bbox([start, *via, end], pad_m=pad_m)

    adapter = ChartAdapter(default_conf(conf))
    try:
        chart = adapter.fetch_corridor(bbox)
    finally:
        adapter.close()

    params = nav_params_from_profile(profile, goal, constraints)
    nav = build_navigability(chart, params)
    speed = profile.get("preferred_speed_kn")
    planned = pathfind(
        nav,
        start,
        end,
        via=via,
        optimization_goal=goal,
        n_alternatives=n_alternatives,
        preferred_speed_kn=float(speed) if speed else None,
    )
    routes = serialize.routes_from_alternatives(
        planned, profile=profile, optimization_goal=goal, evidence=nav.evidence
    )
    for route in routes:
        validate(route, nav)
    return {"bbox": bbox, "chart": chart, "params": params, "nav": nav, "planned": planned, "routes": routes}


# Weight profiles swept by default in run_benchmark (a representative spread, not every profile).
BENCHMARK_GOALS = ["shortest", "fastest", "safest", "prefer_fairways", "archipelago_conservative"]


def run_benchmark(
    reference,
    *,
    goals=None,
    profile: dict | None = None,
    n_alternatives: int = 3,
    constraints: dict | None = None,
    conf: dict | None = None,
    pad_m: float = 2000.0,
    ais_corridor=None,
):
    """Benchmark the planner against a reference route across weight profiles.

    Pass ``ais_corridor`` (a WGS84 geometry from ``reference.ais.corridor_wgs84``) together with
    ``constraints={"prefer_ais_corridors": True}`` to score the AIS-biased cost surface.

    ``reference`` may be an RTZ path/XML string, a parsed ``RtzRoute``, a keelson.Route, or a raw
    ``[(lat, lon)]`` list. Fetches the chart corridor **once**, then for each goal builds navigability,
    plans alternatives, validates them, and scores each against the reference (Fréchet / cross-track /
    length / validation delta). Returns ``{reference_coords, reference_route, rows, summary,
    results_by_goal, nav}``; feed ``rows`` to ``benchmark.runner.to_dataframe``.
    """
    from .benchmark.runner import benchmark_profiles, route_coords, summarize
    from .chart_adapter import ChartAdapter
    from .model import serialize
    from .navigability import build_navigability, nav_params_from_profile
    from .pathfinding import plan as pathfind
    from .validation import validate

    # Resolve the reference into comparison coords (+ a keelson.Route for the validation delta).
    rtz = None
    if isinstance(reference, str):
        from .reference.rtz import parse_rtz

        rtz = parse_rtz(reference)
    elif reference.__class__.__name__ == "RtzRoute":
        rtz = reference

    if rtz is not None:
        from .reference.rtz import rtz_to_planned, rtz_to_route

        ref_coords = rtz_to_planned(rtz).waypoints
        ref_route = rtz_to_route(rtz)
    else:
        ref_coords = route_coords(reference)
        ref_route = reference if hasattr(reference, "issues") else None

    goals = goals or BENCHMARK_GOALS
    profile = profile or DEFAULT_PROFILE
    constraints = constraints or {}
    start, end = ref_coords[0], ref_coords[-1]
    speed = profile.get("preferred_speed_kn")
    bbox = corridor_bbox([start, end], pad_m=pad_m)

    adapter = ChartAdapter(default_conf(conf))
    try:
        chart = adapter.fetch_corridor(bbox)
    finally:
        adapter.close()

    results_by_goal, navs = {}, {}
    for goal in goals:
        params = nav_params_from_profile(profile, goal, constraints)
        nav = build_navigability(chart, params, ais_corridor=ais_corridor)
        planned = pathfind(
            nav, start, end, optimization_goal=goal, n_alternatives=n_alternatives,
            preferred_speed_kn=float(speed) if speed else None,
        )
        routes = serialize.routes_from_alternatives(
            planned, profile=profile, optimization_goal=goal, evidence=nav.evidence
        )
        for route in routes:
            validate(route, nav)
        results_by_goal[goal] = routes
        navs[goal] = nav

    shared_nav = navs[goals[0]]
    if ref_route is not None:
        validate(ref_route, shared_nav)  # so the validation delta is meaningful
    rows = benchmark_profiles(
        ref_coords, results_by_goal, projection=shared_nav.projection, reference_route=ref_route
    )
    return {
        "reference_coords": ref_coords,
        "reference_route": ref_route,
        "rows": rows,
        "summary": summarize(rows),
        "results_by_goal": results_by_goal,
        "nav": shared_nav,
    }


# ---------------------------------------------------------------------------
# geometry helpers (metric <-> WGS84)
# ---------------------------------------------------------------------------


def route_lonlat(route) -> list:
    """A keelson.Route's waypoints as ``[[lon, lat], ...]`` (deck.gl / GeoJSON order)."""
    return [list(reversed(lat_lon(wp.position))) for wp in route.waypoints.waypoint]


def _soft_by_kind(nav) -> dict:
    from shapely.ops import unary_union

    grouped: dict = {}
    for soft in nav.soft_costs or []:
        grouped.setdefault(soft.kind, []).append(soft.geometry)
    return {kind: unary_union(geoms) for kind, geoms in grouped.items()}


def _wgs84_bounds(nav):
    geom = nav.navigable if nav.navigable and not nav.navigable.is_empty else nav.blocked
    return nav.projection.to_wgs84(geom).bounds  # (minlon, minlat, maxlon, maxlat)


def nav_geojson(nav) -> dict:
    """Project navigable / blocked / soft-cost geometry to WGS84 GeoJSON FeatureCollections."""
    from shapely.geometry import mapping

    proj = nav.projection

    def fc(items):
        feats = []
        for geom, props in items:
            if geom is None or geom.is_empty:
                continue
            feats.append({"type": "Feature", "geometry": mapping(proj.to_wgs84(geom)), "properties": props})
        return {"type": "FeatureCollection", "features": feats}

    soft = _soft_by_kind(nav)
    return {
        "navigable": fc([(nav.navigable, {"kind": "navigable"})]),
        "blocked": fc([(nav.blocked, {"kind": "blocked"})]),
        "soft": fc([(geom, {"kind": kind}) for kind, geom in soft.items()]),
    }


# ---------------------------------------------------------------------------
# deck.gl (pydeck) interactive map
# ---------------------------------------------------------------------------


def deck_map(nav, routes=(), *, picked=None, zoom: float = 12.0):
    """Build a pydeck.Deck of navigable/blocked/soft-cost layers + route alternatives over a basemap.

    Call ``.show()`` in Jupyter or ``.to_html("out.html")`` headless. RECOMMENDED routes are green.
    """
    import pydeck as pdk

    gj = nav_geojson(nav)
    layers = [
        pdk.Layer(
            "GeoJsonLayer", gj["navigable"], filled=True, get_fill_color=[80, 170, 255, 55],
            stroked=True, get_line_color=[40, 120, 220, 160], line_width_min_pixels=1, pickable=False,
        ),
        pdk.Layer(
            "GeoJsonLayer", gj["blocked"], filled=True, get_fill_color=[110, 110, 110, 90],
            stroked=False, pickable=False,
        ),
    ]
    by_kind: dict = {}
    for feat in gj["soft"]["features"]:
        by_kind.setdefault(feat["properties"]["kind"], []).append(feat)
    for kind, feats in by_kind.items():
        r, g, b = SOFT_COLORS.get(kind, (200, 200, 0))
        layers.append(
            pdk.Layer(
                "GeoJsonLayer", {"type": "FeatureCollection", "features": feats},
                filled=True, get_fill_color=[r, g, b, 70], stroked=False, pickable=True,
            )
        )

    paths = []
    for i, route in enumerate(routes):
        coords = route_lonlat(route)
        if len(coords) < 2:
            continue
        recommended = getattr(route, "status", 0) == 3
        paths.append({"path": coords, "name": f"alt {i + 1}", "color": [0, 200, 0] if recommended else [255, 140, 0]})
    if paths:
        layers.append(
            pdk.Layer("PathLayer", paths, get_path="path", get_color="color", width_min_pixels=3, get_width=4, pickable=True)
        )
        wps = [{"position": p, "name": d["name"]} for d in paths for p in d["path"]]
        layers.append(
            pdk.Layer("ScatterplotLayer", wps, get_position="position", get_fill_color=[0, 90, 200], get_radius=20, radius_min_pixels=3)
        )
    if picked:
        marks = [{"position": [p["lon"], p["lat"]], "name": label} for label, p in picked.items() if p]
        layers.append(
            pdk.Layer("ScatterplotLayer", marks, get_position="position", get_fill_color=[230, 50, 50], get_radius=40, radius_min_pixels=5)
        )

    w, s, e, n = _wgs84_bounds(nav)
    view = pdk.ViewState(longitude=(w + e) / 2, latitude=(s + n) / 2, zoom=zoom)
    return pdk.Deck(
        layers=layers, initial_view_state=view, map_provider="carto", map_style="light",
        tooltip={"text": "{name}{kind}"},
    )


# ---------------------------------------------------------------------------
# Plotly (analytical geometry + metrics)
# ---------------------------------------------------------------------------


def _rings(geom):
    """Exterior coordinate rings of a (Multi)Polygon, recursing into collections."""
    from shapely.geometry import MultiPolygon, Polygon

    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [list(geom.exterior.coords)]
    if isinstance(geom, MultiPolygon):
        return [list(g.exterior.coords) for g in geom.geoms]
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(_rings(g))
        return out
    return []


def _rgba(rgb, alpha: float) -> str:
    r, g, b = rgb
    return f"rgba({r},{g},{b},{alpha})"


def plotly_navigability(nav, routes=()):
    """A Plotly figure of the metric-CRS layers (navigable/blocked/soft + routes) — precise geometry."""
    import plotly.graph_objects as go

    fig = go.Figure()

    def add_poly(geom, name, rgb, alpha):
        first = True
        for ring in _rings(geom):
            xs = [c[0] for c in ring]
            ys = [c[1] for c in ring]
            fig.add_trace(
                go.Scatter(
                    x=xs, y=ys, fill="toself", mode="lines", line=dict(color=_rgba(rgb, 0.9), width=1),
                    fillcolor=_rgba(rgb, alpha), name=name, legendgroup=name, showlegend=first, hoverinfo="name",
                )
            )
            first = False

    add_poly(nav.navigable, "navigable", (40, 120, 220), 0.12)
    for kind, geom in _soft_by_kind(nav).items():
        add_poly(geom, f"soft:{kind}", SOFT_COLORS.get(kind, (200, 200, 0)), 0.4)
    add_poly(nav.blocked, "blocked", (110, 110, 110), 0.3)

    proj = nav.projection
    for i, route in enumerate(routes):
        pts = [proj.forward(*reversed(lat_lon(wp.position))) for wp in route.waypoints.waypoint]
        if len(pts) < 2:
            continue
        recommended = getattr(route, "status", 0) == 3
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in pts], y=[p[1] for p in pts], mode="lines+markers",
                line=dict(color="green" if recommended else "orange", width=3),
                name=f"alt {i + 1}{' ★' if recommended else ''}",
            )
        )

    fig.update_layout(
        title="Navigability (local metric CRS)", xaxis_title="x (m)", yaxis_title="y (m)",
        yaxis=dict(scaleanchor="x", scaleratio=1), height=680, legend=dict(itemsizing="constant"),
    )
    return fig


def planner_attrs(route) -> dict:
    """The route-planner Extensions attributes of a keelson.Route as a plain dict."""
    for entry in route.extensions.entries:
        if entry.manufacturer == "se.rise" and entry.name == "route-planner":
            return dict(entry.attributes)
    return {}


def metrics_rows(routes) -> list:
    """Per-route summary rows: (label, status, distance_nm, validation, #challenges, #issues)."""
    rows = []
    for i, route in enumerate(routes):
        attrs = planner_attrs(route)
        dist = attrs.get("distance_m")
        dist_nm = f"{float(dist) / 1852:.2f}" if dist else "—"
        rows.append(
            (
                f"alt {i + 1}",
                _STATUS_NAMES.get(getattr(route, "status", 0), str(getattr(route, "status", 0))),
                dist_nm,
                attrs.get("validation_status", "—"),
                len(route.challenges),
                len(route.issues),
            )
        )
    return rows


def plotly_metrics(routes):
    """A Plotly table of per-alternative distance / status / validation / warning + issue counts."""
    import plotly.graph_objects as go

    header = ["alternative", "status", "dist (nm)", "validation", "#warnings", "#issues"]
    rows = metrics_rows(routes)
    cols = list(zip(*rows)) if rows else [[] for _ in header]
    return go.Figure(
        go.Table(header=dict(values=header, fill_color="#1b3a5b", font=dict(color="white")), cells=dict(values=cols))
    )
