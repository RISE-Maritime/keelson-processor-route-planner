# Chart Adapter

The `chart_adapter` is the **only** module that touches the chart server. It fetches and normalizes
S-57 features; it contains **no planning logic**. Verified against
`chart-server/query-bridge/http_app.py`, `query-bridge/sql.py`, `query-bridge/zenoh_handlers.py`,
`ingest/post_ingest.sql`, and `docker-compose.yml`.

---

## 1. Two access paths

| Path | When | Mechanism |
|------|------|-----------|
| **Direct PostGIS** (default for bulk) | Building navigable polygons over a route corridor — large feature pulls | `asyncpg` to `postgresql://charts:charts@postgis:5432/charts` (in-network) or `localhost:5433` (host). Read-only `charts` role. |
| **chart-server HTTP / Zenoh** (targeted) | Point/area checks, depth summary, clearance envelope, provenance | nginx `:8080/api/*` (HTTP) or the `chart/**` Zenoh queryable. |

**Invariant:** the adapter **never writes `public.*`** — ENC data must remain reproducible from
the source `.000` cells.

---

## 2. HTTP API (aiohttp, behind nginx `:8080`)

All return GeoJSON `FeatureCollection` unless noted. Source: `query-bridge/http_app.py`.

| Endpoint | Params | Returns | Planner use |
|---|---|---|---|
| `GET /api/identify` | `lat`, `lon`, `radius` (def 50 m) | features near a point + `distance_m` | spot identify on a waypoint |
| `GET /api/radius` | `lat`, `lon`, `meters` | features within radius | hazard sweep around a node |
| `GET /api/bbox` | `s`, `w`, `n`, `e` | features in box (cap 500) | corridor pre-scan |
| `GET /api/depth` | `lat`, `lon` | `{ depthArea, nearestSounding, dredgedArea, onLand }` | depth at a node |
| `GET /api/grounding-line` | `lat`, `lon`, `draft`, `ukc` | clearance-envelope FeatureCollection | safe-depth cross-check |
| `GET /api/layers` | — | `{ layers:[…], total }` | discover available layers/counts |
| `GET /api/catalog` | — | `{ cells:[…], layers, coverage_bbox, total_features, generated_at }` | **provenance** (chart cells + editions) |

> 500-feature cap per query (`sql.py`). For corridors that exceed it, page by sub-bbox or use the
> direct-PostGIS path.

---

## 3. Zenoh queryable (`chart/**`, Keelson-framed)

Source: `query-bridge/zenoh_handlers.py`. Replies are `keelson.enclose(json.dumps(payload))`
(decode with `keelson.uncover` then `json.loads`). This is the path Crowsnest already uses.

```
chart/identify/{lat}/{lon}                 # 50 m radius
chart/identify/{lat}/{lon}/{radius_m}
chart/query/bbox/{s}/{w}/{n}/{e}
chart/query/radius/{lat}/{lon}/{meters}
chart/layers
chart/coverage
```

The planner service can call these via its own Keelson session, but for bulk corridor work prefers
direct PostGIS (below).

---

## 4. PostGIS S-57 tables to read directly

All geometry is `wkb_geometry` in **EPSG:4326**, GIST-indexed. Rows are stamped with `chart_scale`
(source compilation scale) and `scamin`/`scamax`; `dsid_versions` tracks editions. Tables present
after ingest (`ingest/post_ingest.sql`):

| Internal layer | Table(s) | Depth / key columns |
|---|---|---|
| Land / coastline | `public.lndare`, `public.coalne` | containment test; `objnam` |
| Built-up area | `public.buaare` | `objnam` |
| Depth areas | `public.depare` | `drval1`, `drval2` (range, m) |
| Depth contours | `public.depcnt` | `valdco` (contour depth, m) |
| Soundings | `public.soundg` | `depth` (point depth, m) |
| Obstructions | `public.obstrn` | `valsou`, `catobs`, `watlev`, `objnam` |
| Wrecks | `public.wrecks` | `valsou`, `catwrk`, `watlev`, `objnam` |
| Underwater rocks | `public.uwtroc` | `valsou`, `watlev`, `objnam` |
| Dredged areas | `public.drgare` | `drval1`, `drval2`, `objnam` |
| Restricted areas | `public.resare` | `restrn` (S-57 restriction list), `catrea`, `objnam` |
| Caution areas | `public.ctnare` | `lnam` |
| Anchorages | `public.achare` | `objnam` |
| Fairways | `public.fairwy` | `objnam` |
| TSS lane / line | `public.tsslpt`, `public.tselne`, `public.tssbnd` | `lnam` |
| Aids to navigation | `public.boylat/boysaw/boycar/boyisd/boyspp`, `public.bcnlat/bcnsaw/bcnspp/bcnisd`, `public.lights` | `colour`, `objnam`, light sectors |
| Pilot boarding | `public.pilbop` | `objnam` |
| Chart coverage | `public.m_covr`, `public.dsid_versions` | `source_chart`, `dsnm`, `edtn`, `updn`, `cscl` |

**Edits overlay (Pillar 2).** Read `edits.effective_<layer>` views (or `public.<layer>` UNION
`edits.<layer>_overrides`/`_inserts`) so user edits are honored. The Martin function sources
(`chart_base`/`_nav`/`_aids`/`_routing`/`_borders`) already UNION-ALL public + edits — mirror that
read pattern.

---

## 5. No-go zone sources

A "no-go zone" reaching the planner can come from three places — all merged into the
navigability engine's hard-block set and onto `Route.hazard_zones[]`:

1. **Request payload** — zones the operator drew for this plan (Crowsnest `NoGoZonesPanel`).
2. **`edits.resare_inserts`** — restricted areas authored into the chart's edits schema (QGIS).
3. **`no_go_zone_geojson`** Keelson subject — live/published no-go overlays.

---

## 6. S-57 → internal normalization map

The adapter normalizes raw S-57 into the internal layers consumed by the navigability engine:

```
land/coastline      ← lndare, coalne
depth areas         ← depare (drval1/drval2)
depth contours      ← depcnt (valdco)
soundings           ← soundg (depth)
rocks               ← uwtroc (valsou, watlev)
wrecks              ← wrecks (valsou, watlev, catwrk)
obstructions        ← obstrn (valsou, watlev, catobs)
restricted areas    ← resare (restrn, catrea)
caution areas       ← ctnare
anchorages          ← achare
fairways            ← fairwy
recommended tracks  ← rectrc (if present)
traffic separation  ← tsslpt, tselne, tssbnd
bridges / clearance ← bridge / overhead features (if present; air-draught checks)
aids to navigation  ← boy*/bcn*/lights
harbour areas       ← hrbare/hrbfac (if present)
user no-go          ← request payload + edits.resare_inserts + no_go_zone_geojson
```

Each normalized feature retains `chart_scale` and a reference back to its source cell so the
validation engine can attach **chart evidence** and chart-quality penalties.

---

## 7. Provenance for the safety rule

`/api/catalog` (or `public.dsid_versions` directly) supplies the chart cells and their
edition/update numbers used for a plan. The adapter records, per plan: the set of contributing cells
(`dsnm`, `edtn`, `updn`, `cscl`), coverage gaps touched, and the worst chart-quality/compilation
scale encountered along the route. This feeds the `Route` provenance fields described in
[DESIGN.md §12](DESIGN.md#12-safety--provenance-rule).
