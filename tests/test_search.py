"""pathfinding.search tests (P4) — A* / Dijkstra over a CostGrid. Pure."""

from __future__ import annotations

from shapely.geometry import box

from route_planner.pathfinding.grid import CostGrid
from route_planner.pathfinding.search import search

W = {"distance": 1.0}


def test_astar_crosses_open_grid():
    g = CostGrid(box(0, 0, 100, 100), [], W, 10.0)
    start, goal = g.snap(5, 5), g.snap(95, 95)
    path = search(g, start, goal)
    assert path is not None
    assert path[0] == start
    assert path[-1] == goal


def test_astar_routes_around_barrier():
    nav = box(0, 0, 100, 100).difference(box(45, 0, 55, 80))  # wall from the bottom, gap above y=80
    g = CostGrid(nav, [], W, 5.0)
    path = search(g, g.snap(20, 40), g.snap(80, 40))
    assert path is not None
    ys = [g.cell_to_world(r, c)[1] for r, c in path]
    assert max(ys) > 80  # had to detour over the wall


def test_no_path_when_walled_off():
    nav = box(0, 0, 100, 40).union(box(0, 60, 100, 100))  # two disconnected strips
    g = CostGrid(nav, [], W, 5.0)
    assert search(g, g.snap(50, 20), g.snap(50, 80)) is None


def test_dijkstra_also_finds_a_path():
    g = CostGrid(box(0, 0, 100, 100), [], W, 10.0)
    path = search(g, g.snap(5, 5), g.snap(95, 5), use_heuristic=False)
    assert path is not None
