"""Opt-in planner latency report — NOT a CI gate. Set ``RUN_PERF=1`` to run (mirrors the repo's
``DATABASE_URL`` live-test convention); plain ``pytest`` skips it. Prints elapsed vs target and only
fails on a gross (5x) regression, since absolute latency is environment-dependent.
"""

from __future__ import annotations

import os

import pytest
from shapely.geometry import box

from route_planner.benchmark import perf
from route_planner.chart_adapter.layers import DEPTH_AREAS, ChartFeature, NormalizedChart
from route_planner.navigability import build_navigability, nav_params_from_profile

pytestmark = pytest.mark.perf

CORRIDOR = (11.80, 57.70, 11.90, 57.76)
PROFILE = {"draught_m": 0.25, "min_under_keel_clearance_m": 0.5, "route_margin_m": 15.0}


def _open_nav():
    chart = NormalizedChart()
    chart.add(ChartFeature(DEPTH_AREAS, box(*CORRIDOR), {"drval1": 10, "drval2": 20}))
    chart.provenance.coverage_bbox = CORRIDOR
    return build_navigability(chart, nav_params_from_profile(PROFILE))


@pytest.mark.skipif(not os.environ.get("RUN_PERF"), reason="perf report is opt-in; set RUN_PERF=1")
def test_plan_latency_report(capsys):
    nav = _open_nav()
    reports = [
        perf.time_plan(
            nav, (57.705, 11.81), (57.755, 11.89), scenario="single", target_s=perf.WARM_S
        ),
        perf.time_plan(
            nav,
            (57.705, 11.81),
            (57.755, 11.89),
            scenario="3-alt+2-via",
            target_s=perf.THREE_ALT_TWO_VIA_S,
            via=[(57.71, 11.84), (57.74, 11.86)],
            n_alternatives=3,
        ),
    ]
    with capsys.disabled():
        for r in reports:
            print(
                f"  [{'ok' if r.within else 'OVER'}] {r.scenario}: {r.elapsed_s:.3f}s / {r.target_s:.1f}s"
            )
    # Env-dependent, so don't gate on the target itself — only catch a gross regression.
    assert all(r.elapsed_s < r.target_s * 5 for r in reports)
