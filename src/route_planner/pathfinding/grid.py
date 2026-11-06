"""Cost grid — rasterize a metric navigable polygon + soft-costs for A* (pure, no I/O).

Cells are square (``cell_size`` metres). ``cell_cost[r, c]`` = ``w_distance + Σ region.weight ·
goal_weight[kind]`` inside the navigable polygon, ``inf`` outside (hard constraint). Built once per
plan; the pathfinder reads ``cell_cost`` / ``mask`` and walks 8-connected neighbours.
"""

from __future__ import annotations

import math

import numpy as np
import shapely

from . import cost as cost_mod

MAX_DIM = 600  # cap each grid axis; cell size grows for very large corridors

_INF = 1e20


def _dt1d(f: np.ndarray) -> np.ndarray:
    """1-D squared Euclidean distance transform (Felzenszwalb & Huttenlocher lower envelope)."""
    n = len(f)
    d = np.empty(n)
    v = np.zeros(n, dtype=np.int64)  # parabola vertices in the lower envelope
    z = np.empty(n + 1)  # intersection boundaries
    k = 0
    z[0], z[1] = -_INF, _INF
    for q in range(1, n):
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
        while s <= z[k]:
            k -= 1
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = _INF
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d


def _edt(foreground: np.ndarray) -> np.ndarray:
    """Euclidean distance (in cells) from every cell to the nearest True cell in ``foreground``.

    Exact, separable, numpy-only (no scipy). Cells with no foreground in their row+column get a huge
    distance (≈sqrt(_INF)), which the caller caps — so "no obstacle in view" means no penalty.
    """
    f = np.where(foreground, 0.0, _INF)
    f = np.apply_along_axis(_dt1d, 0, f)
    f = np.apply_along_axis(_dt1d, 1, f)
    return np.sqrt(f)


class CostGrid:
    """A metric, axis-aligned cost grid over the navigable water."""

    def __init__(
        self, navigable, soft_costs, weights: dict, cell_size: float, blocked=None, clear_target: float = 0.0
    ) -> None:
        self.cell_size = float(cell_size)
        self.weights = weights
        self.w_distance = float(weights.get("distance", 1.0))

        if navigable is None or navigable.is_empty:
            self.minx = self.miny = 0.0
            self.nx = self.ny = 1
            self.mask = np.zeros((1, 1), dtype=bool)
            self.cell_cost = np.full((1, 1), np.inf)
            self.base_cost = self.cell_cost.copy()
            return

        minx, miny, maxx, maxy = navigable.bounds
        self.minx = minx - self.cell_size
        self.miny = miny - self.cell_size
        self.nx = int(np.ceil((maxx - minx) / self.cell_size)) + 2
        self.ny = int(np.ceil((maxy - miny) / self.cell_size)) + 2

        cols = np.arange(self.nx)
        rows = np.arange(self.ny)
        xs = self.minx + (cols + 0.5) * self.cell_size
        ys = self.miny + (rows + 0.5) * self.cell_size
        gx, gy = np.meshgrid(xs, ys)  # (ny, nx)

        self.mask = shapely.contains_xy(navigable, gx, gy)
        soft = np.zeros((self.ny, self.nx))
        for sc in soft_costs or []:
            gw = weights.get(sc.kind, 0.0)
            if gw <= 0 or sc.geometry is None or sc.geometry.is_empty:
                continue
            soft += gw * float(sc.weight) * shapely.contains_xy(sc.geometry, gx, gy)

        # Clearance gradient: prefer cells far from real obstacles (land/shallow/hazard) so routes
        # bow to mid-channel / run channel centerlines instead of hugging the shore. Penalty ramps
        # from 1.0 at an obstacle to 0.0 at `clear_target` metres, flat beyond.
        w_clear = float(weights.get("clearance", 0.0))
        if w_clear > 0 and clear_target > 0 and blocked is not None and not blocked.is_empty:
            obstacles = shapely.contains_xy(blocked, gx, gy)
            if obstacles.any():
                clearance_m = _edt(obstacles) * self.cell_size
                soft += w_clear * np.clip((clear_target - clearance_m) / clear_target, 0.0, 1.0)

        self.cell_cost = np.where(self.mask, self.w_distance + soft, np.inf)
        self.base_cost = self.cell_cost.copy()  # penalty-free, for ranking alternatives

    def add_penalty(self, cells, radius: int = 2, amount: float | None = None) -> None:
        """Raise cost in a neighbourhood of ``cells`` (to push later alternatives elsewhere)."""
        if amount is None:
            amount = self.w_distance * self.cell_size * 5.0
        for row, col in cells:
            r0, r1 = max(0, row - radius), min(self.ny, row + radius + 1)
            c0, c1 = max(0, col - radius), min(self.nx, col + radius + 1)
            block = self.cell_cost[r0:r1, c0:c1]
            finite = np.isfinite(block)
            block[finite] += amount

    def path_base_cost(self, cells) -> float:
        """Penalty-free traversal cost of a cell path (for ranking alternatives)."""
        if len(cells) < 2:
            return 0.0
        total = 0.0
        for (r0, c0), (r1, c1) in zip(cells, cells[1:]):
            step = self.cell_size * math.hypot(r1 - r0, c1 - c0)
            total += step * 0.5 * (self.base_cost[r0, c0] + self.base_cost[r1, c1])
        return total

    @classmethod
    def from_navigability(cls, nav, optimization_goal: str | None = None) -> CostGrid:
        """Build a grid from a P3 NavigabilityResult; cell size scales to the route margin."""
        weights = cost_mod.weights_for(optimization_goal)
        navigable = nav.navigable
        margin = nav.params.route_margin_m if nav.params else 50.0
        cell = max(float(margin), 1.0)
        if navigable is not None and not navigable.is_empty:
            minx, miny, maxx, maxy = navigable.bounds
            extent = max(maxx - minx, maxy - miny)
            cell = max(cell, extent / MAX_DIM)
        clear_target = nav.params.clearance_target_m if nav.params else 0.0
        return cls(navigable, nav.soft_costs, weights, cell, blocked=nav.blocked, clear_target=clear_target)

    # --- world <-> cell -------------------------------------------------------

    def world_to_cell(self, x: float, y: float) -> tuple[int, int]:
        col = int(np.clip((x - self.minx) / self.cell_size, 0, self.nx - 1))
        row = int(np.clip((y - self.miny) / self.cell_size, 0, self.ny - 1))
        return row, col

    def cell_to_world(self, row: int, col: int) -> tuple[float, float]:
        return (self.minx + (col + 0.5) * self.cell_size, self.miny + (row + 0.5) * self.cell_size)

    def is_navigable(self, row: int, col: int) -> bool:
        return bool(self.mask[row, col])

    def snap(self, x: float, y: float) -> tuple[int, int] | None:
        """Nearest navigable cell to a world point (the cell itself if already navigable)."""
        row, col = self.world_to_cell(x, y)
        if self.mask[row, col]:
            return row, col
        nav_cells = np.argwhere(self.mask)
        if nav_cells.size == 0:
            return None
        cx = self.minx + (nav_cells[:, 1] + 0.5) * self.cell_size
        cy = self.miny + (nav_cells[:, 0] + 0.5) * self.cell_size
        idx = int(np.argmin((cx - x) ** 2 + (cy - y) ** 2))
        return int(nav_cells[idx, 0]), int(nav_cells[idx, 1])
