"""Pathfinding — water-graph build + A*/Dijkstra + via-point segmentation (P4 — stub).

Discretizes navigable water (grid or triangulation), runs A* with a great-circle heuristic
(Dijkstra fallback), segments by via-points, enforces hard constraints before cost optimization,
and produces N diverse alternatives. See docs/DESIGN.md §7 and docs/ROADMAP.md (P4). Not
implemented in the P1 skeleton.
"""
