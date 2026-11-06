# Prior Art — papers & open source

Reference material for this processor's core problem: **constrained voyage-route generation over an
S-57/ENC-derived navigability surface, scored and validated**, emitted as a standard `keelson.Route`.
This is a well-studied problem in the maritime literature, so most of our design choices have prior
art to lean on. Each entry is annotated with *why it matters to us* and any scope caveat per
[DESIGN.md §1](DESIGN.md#1-purpose--scope). Companion docs:
[DESIGN.md](DESIGN.md), [ROADMAP.md](ROADMAP.md), [KEELSON-INTEGRATION.md](KEELSON-INTEGRATION.md).

---

## Most relevant — maps 1:1 to our engines

### Pathfinding over an ENC-derived grid — our [navigability/](../src/route_planner/navigability/) + [pathfinding/](../src/route_planner/pathfinding/)

- **Static Maritime Environment Representation of ENC in Global Path Planning** — Šakan, Žuškin,
  Valčić, Pavletić, JMSE 2022 ([open-access](https://upcommons.upc.edu/handle/2117/369108)). Survey
  of how ENC generalization + hydrographic uncertainty shape the *static environment model* a planner
  runs on. *Why:* the headline ENC-representation read; validates our **geometry-first** navigability
  engine — representation quality matters as much as the search algorithm.
- **Research and Implementation of Global Path Planning for USV Based on Electronic Chart** —
  [arXiv:1802.03221](https://arxiv.org/pdf/1802.03221). Improved `A*` directly on an **S-57** chart
  with a sailing-safety weight, a "pilot quantity" term, and path-curve smoothing.
  *Why:* closest single analogue to our cost-surface + `A*` slice (P3/P4).
- **A route planning method for small ships in coastal areas based on quadtree** —
  [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S2092678225000056). Builds a
  **quadtree chart from an S-57 cell** rather than a uniform raster.
  *Why:* fallback if our regular grid gets too coarse/expensive in tight archipelago passages.
- **Ship path planning based on improved multi-scale `A*` of collision-risk function** —
  [Sci. Reports](https://www.nature.com/articles/s41598-024-80712-8). Multi-scale raster +
  line-of-sight smoothing + a soft cost term. *Why:* template for our soft-cost surface.
  *Caveat:* its collision-risk angle is partly out of scope per DESIGN §1.

### Soft-cost / multi-objective scoring & validation — our [pathfinding/cost.py](../src/route_planner/pathfinding/cost.py) + [validation/](../src/route_planner/validation/)

- **Ship weather routing: a taxonomy and survey** — Ocean Engineering 2020
  ([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0029801820306879)).
  The canonical method map: isochrone, DP, Dijkstra, `A*`, EA/GA/ACO/PSO.
  *Why:* best single orientation read for choosing/justifying our approach.
- **State-of-the-art optimization algorithms in weather routing** — Ocean Engineering 2025
  ([ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0029801825009114)).
  Updated taxonomy + decision-support framing. *Why:* useful for P11 (AIS/weather cost layers).
- **A Ship Route Planning Method under the Sailing Time Constraint** — JMSE 2023, 11(6), 1242
  ([MDPI](https://www.mdpi.com/2077-1312/11/6/1242)). *Why:* its hard-constraint-then-optimize
  structure mirrors our "hard constraints before cost optimization" rule.

## Open-source projects to read / possibly reuse

- **eurostat/searoute** ([repo](https://github.com/eurostat/searoute)) + **genthalili/searoute-py**
  ([repo](https://github.com/genthalili/searoute-py), PyPI `searoute`) — gridded global sea network +
  Dijkstra over NetworkX, GeoJSON out, multi-route + passages. *Why:* good reference for graph
  construction and GeoJSON output shape. *Caveat:* the authors state it is built for *visualization*,
  **not** for routing real ships — exactly the boundary our
  [DESIGN §12 safety/provenance rule](DESIGN.md#12-safety--provenance-rule) exists to enforce.
- **vss2sn/path_planning** ([repo](https://github.com/vss2sn/path_planning)) — clean C++ reference
  implementations of `A*`/Dijkstra/`D*`/RRT on a grid. *Why:* sanity-check our `A*` heuristic/tie-breaking.
- **OpenCPN / OpenPlotter ecosystem** — open-source ECDIS-class chart plotters that consume S-57.
  *Why:* reference for ENC parsing and chart-feature semantics feeding `chart_adapter/`.
- **ISO8211 / S-57 parsing libs** (e.g. GDAL's S-57 driver) — for normalizing `.000` cells.
  *Why:* aligns with our "never write `public.*`, stay reproducible from source cells" rule.
- **scikit-image (`medial_axis` / `route_through_array`) + NetworkX (`astar_path` custom weight)** —
  ([skimage](https://scikit-image.org/), [networkx](https://networkx.org/)). Channel-centerline
  skeletons, raster cost-array routing, graph `A*` with edge-hiding weights. *Why:* the building blocks
  **if** we ever add hybrid graph + medial-axis fairway seeding for narrow passages. *Caveat:* a
  future divergence from our grid `A*` + clearance-gradient engine — not planned; evaluate first in
  [notebooks/](../notebooks/).
- **52°North WeatherRoutingTool** ([repo](https://github.com/52North/WeatherRoutingTool)) — modular
  Dijkstra/GA/isofuel weather routing with reusable land-crossing / seamark / depth / wave-height
  constraints. *Why:* reference architecture for **P11** AIS/weather cost layers.
- **VISIR-2 — ship weather routing in Python** — Mannarini et al., GMD 2024
  ([paper](https://gmd.copernicus.org/articles/17/4355/2024/), [code](https://zenodo.org/records/10960842)).
  Graph-search router with coast-intersection screening + dynamic edge weights. *Why:* **P11**
  weather-routing template with a clean dynamic-cost design.
- **pgRouting** ([pgrouting.org](https://pgrouting.org/)) — PostGIS-native `A*`/Dijkstra/Yen over a
  routing graph. *Why:* candidate for **P11** once routes/graphs persist in PostGIS (cf. ROADMAP P11
  `route_legs`/`route_metrics`). *Caveat:* dynamic per-vessel weights blunt its preprocessing wins.

## Standards / format lineage — validates our `keelson.Route` choice

- **RTZ → IEC 61174 ed.4 → S-421 (IEC 63173-1) Route Plan on S-100** —
  [IHO S-100WG paper](https://iho.int/mtg_docs/com_wg/S-100WG/S-100WG3/S-100WG3-9.3.2_Information_about_S-421_Route_Plan_Exchange.pdf).
  *Why:* confirms `keelson.Route` (RTZ-1.2 / S-421 superset) is the right canonical artifact, and
  that "route exchange + validation status + provenance" is a recognized standardized workflow.
- **RTZ & S-421 editor** ([rtzeditor.com](https://rtzeditor.com/)) — a working
  import/edit/**validate**/export tool. *Why:* reference for what "route validation" means to the
  wider ecosystem vs. our chart-aware `validate_route` RPC.
- **STM (Sea Traffic Management)** — the project that originated RTZ route exchange. *Why:*
  background on ship-shore route sharing.

## Worth a skim — adjacent, partly out of scope

- **Improved `A*` on historical AIS data for multi-objective weather routes** (distance / cost /
  emissions). *Why:* pattern for P11 AIS-derived cost layers.
- **Shipping map: grid generation of a global maritime network from AIS** —
  [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0968090X25000191).
  *Why:* AIS-derived navigability priors (P11).
- **Real-Time Navigation for ASVs in Ice-Covered Waters** —
  [arXiv:2302.11601](https://arxiv.org/pdf/2302.11601). Out of scope (real-time), but a clean
  cost-surface formulation.
- **Grounding-aware RRT\*** — Enevoldsen & Galeazzi, IFAC 2021
  ([arXiv:2109.06967](https://arxiv.org/abs/2109.06967)). KDE over historical **AIS** → a
  "where mariners actually sail" prior encoded as a cost that penalizes shallow-water deviations;
  navigable area from ENC depth contours. *Why:* the **P11** lesson is its *cost design* — AIS belongs
  in the soft cost, not as a hard constraint. *Caveat:* `RRT*` itself is out of scope; our engine stays
  deterministic grid `A*`.
- **A Methodology to extract Geo-Referenced Standard Routes from AIS Data** — Corvino et al.
  ([arXiv:2503.22734](https://arxiv.org/abs/2503.22734)). Segments + clusters raw AIS into standard
  routes (<5% outliers over a 6-year corpus). *Why:* **P11** route to derive fairway/ferry corridors +
  traffic-density priors where ENC fairway objects are sparse.
- **EMODnet Human Activities — vessel-density / route-density rasters**
  ([portal](https://emodnet.ec.europa.eu/en/human-activities)). Public monthly shipping-density
  grids. *Why:* a ready **P11** data source for the AIS-density cost layer without standing up our own
  AIS archive.
