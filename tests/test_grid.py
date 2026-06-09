"""pathfinding.grid tests (P4) — rasterization, world<->cell, snap. Pure."""

from __future__ import annotations

from shapely.geometry import box

from route_planner.navigability import SoftCost
from route_planner.pathfinding.grid import CostGrid

W = {"distance": 1.0}


def test_mask_marks_navigable_and_outside():
    g = CostGrid(box(0, 0, 100, 100), [], W, cell_size=10.0)
    assert g.mask[g.world_to_cell(50, 50)]  # inside
    assert not g.mask[g.world_to_cell(-50, 50)]  # left of the box (clamped edge cell, off-water)


def test_world_cell_roundtrip_within_half_cell():
    g = CostGrid(box(0, 0, 100, 100), [], W, 10.0)
    x, y = g.cell_to_world(*g.world_to_cell(52, 37))
    assert abs(x - 52) <= 5.0
    assert abs(y - 37) <= 5.0


def test_snap_moves_outside_point_onto_navigable():
    g = CostGrid(box(0, 0, 100, 100), [], W, 10.0)
    cell = g.snap(-50, 50)
    assert cell is not None
    assert g.mask[cell]
    x, _y = g.cell_to_world(*cell)
    assert 0 <= x <= 100


def test_blocked_notch_removed_from_mask():
    nav = box(0, 0, 100, 100).difference(box(40, 0, 60, 60))  # notch from the bottom edge
    g = CostGrid(nav, [], W, 10.0)
    assert not g.mask[g.world_to_cell(50, 30)]  # inside the notch
    assert g.mask[g.world_to_cell(50, 80)]  # above it


def test_soft_cost_raises_cell_cost():
    soft = [SoftCost("shallow_but_legal", box(0, 0, 50, 100), 2.0)]
    weights = {"distance": 1.0, "shallow_but_legal": 3.0}
    g = CostGrid(box(0, 0, 100, 100), soft, weights, 10.0)
    inside = g.cell_cost[g.world_to_cell(25, 50)]
    outside = g.cell_cost[g.world_to_cell(75, 50)]
    assert inside > outside
    assert outside == 1.0  # just the distance weight where no soft-cost applies
