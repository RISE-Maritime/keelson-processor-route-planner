"""pathfinding.cost tests (P4) — weight profiles. Pure."""

from __future__ import annotations

from route_planner.pathfinding import cost

GOALS = (
    "shortest", "fastest", "safest", "prefer_fairways",
    "avoid_traffic", "archipelago_conservative", "usv_trial_safe", "custom",
)


def test_every_goal_has_a_profile_with_distance():
    for goal in GOALS:
        weights = cost.weights_for(goal)
        assert "distance" in weights
        for kind in cost.KINDS:
            assert kind in weights


def test_safest_penalizes_shallow_and_land_more_than_shortest():
    shortest = cost.weights_for("shortest")
    safest = cost.weights_for("safest")
    assert safest["shallow_but_legal"] > shortest["shallow_but_legal"]
    assert safest["near_land_margin"] > shortest["near_land_margin"]
    assert safest["unknown_depth"] > shortest["unknown_depth"]


def test_prefer_fairways_penalizes_outside_fairway():
    assert cost.weights_for("prefer_fairways")["outside_fairway"] > cost.weights_for("shortest")["outside_fairway"]


def test_unknown_goal_falls_back_to_default():
    assert "distance" in cost.weights_for("nonsense-goal")
    assert "distance" in cost.weights_for(None)
