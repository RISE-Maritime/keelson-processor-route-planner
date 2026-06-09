"""navigability.rules tests (P3) — NavParams + S-57 classification, pure."""

from __future__ import annotations

from shapely.geometry import Point

from route_planner.chart_adapter.layers import ChartFeature
from route_planner.navigability import rules

PROFILE = {"draught_m": 0.25, "min_under_keel_clearance_m": 0.5, "route_margin_m": 15.0}


def test_safety_depth_is_draught_plus_ukc():
    p = rules.nav_params_from_profile(PROFILE)
    assert p.safety_depth_m == 0.75
    assert p.route_margin_m == 15.0
    assert p.comfortable_depth_m > p.safety_depth_m


def test_conservative_mode_blocks_unknown_and_prefers_fairways():
    p = rules.nav_params_from_profile(PROFILE, optimization_goal="archipelago_conservative")
    assert p.block_unknown_depth is True
    assert p.prefer_fairways is True


def test_allow_unknown_depth_overrides_mode():
    p = rules.nav_params_from_profile(
        PROFILE, optimization_goal="safest", constraints={"allow_unknown_depth": True}
    )
    assert p.block_unknown_depth is False


def test_parse_restrn_variants():
    assert rules.parse_restrn("7") == {7}
    assert rules.parse_restrn("1,7") == {1, 7}
    assert rules.parse_restrn("7;8 14") == {7, 8, 14}
    assert rules.parse_restrn(7) == {7}
    assert rules.parse_restrn(None) == set()


def _rock(**attrs):
    return ChartFeature("rocks", Point(0, 0), attrs)


def test_is_unsafe_hazard():
    assert rules.is_unsafe_hazard(_rock(watlev=5), 0.75, False) is True  # awash
    assert rules.is_unsafe_hazard(_rock(valsou=0.3), 0.75, False) is True  # shallower than safety
    assert rules.is_unsafe_hazard(_rock(valsou=20.0, watlev=3), 0.75, False) is False  # deep & submerged
    assert rules.is_unsafe_hazard(_rock(), 0.75, conservative=True) is True  # unknown -> block when safe
    assert rules.is_unsafe_hazard(_rock(), 0.75, conservative=False) is False
