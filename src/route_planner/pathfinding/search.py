"""Grid search — A* (Dijkstra fallback) over a CostGrid (pure, no I/O).

8-connected; an edge between adjacent navigable cells costs ``segment_metres ·
½(cell_cost[a]+cell_cost[b])``. The A* heuristic is ``w_distance · straight-line metres to goal`` —
admissible because every cell costs at least ``w_distance``, so A* returns an optimal least-cost
path. Cells with infinite cost (outside the navigable polygon) are never entered.
"""

from __future__ import annotations

import heapq
import math

_SQRT2 = math.sqrt(2.0)

# (d_row, d_col, step_factor) — orthogonal steps cost 1 cell, diagonals √2.
_NEIGHBORS = [
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, _SQRT2), (-1, 1, _SQRT2), (1, -1, _SQRT2), (1, 1, _SQRT2),
]


def _reconstruct(came_from, current):
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def search(grid, start, goal, use_heuristic: bool = True):
    """Least-cost cell path ``start`` → ``goal`` (each an (row, col)); None if unreachable.

    ``use_heuristic=False`` makes it a plain Dijkstra (the fallback when no admissible heuristic
    applies — here the heuristic is always admissible, so A* is the default).
    """
    cost = grid.cell_cost
    ny, nx = grid.ny, grid.nx
    if not (grid.mask[start] and grid.mask[goal]):
        return None

    goal_x, goal_y = grid.cell_to_world(*goal)
    w_distance = grid.w_distance
    cell_size = grid.cell_size

    def heuristic(row, col):
        if not use_heuristic:
            return 0.0
        x, y = grid.cell_to_world(row, col)
        return w_distance * math.hypot(x - goal_x, y - goal_y)

    open_heap = [(heuristic(*start), 0.0, start)]
    g_score = {start: 0.0}
    came_from = {}
    closed = set()

    while open_heap:
        _f, g, current = heapq.heappop(open_heap)
        if current == goal:
            return _reconstruct(came_from, current)
        if current in closed:
            continue
        closed.add(current)
        row, col = current
        for d_row, d_col, factor in _NEIGHBORS:
            nr, nc = row + d_row, col + d_col
            if nr < 0 or nr >= ny or nc < 0 or nc >= nx:
                continue
            nb = (nr, nc)
            nb_cost = cost[nr, nc]
            if not math.isfinite(nb_cost) or nb in closed:
                continue
            step = cell_size * factor
            tentative = g + step * 0.5 * (cost[row, col] + nb_cost)
            if tentative < g_score.get(nb, math.inf):
                g_score[nb] = tentative
                came_from[nb] = current
                heapq.heappush(open_heap, (tentative + heuristic(nr, nc), tentative, nb))
    return None
