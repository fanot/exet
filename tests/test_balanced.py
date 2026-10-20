"""Regression cases for the compromise criterion, independent of route geometry."""
import math

import pytest

from planner.geo import parse_geojson
from planner.optimizer import Planner
from planner.report import mission_report
from scripts.qa_acceptance import parameters, small_scenario


def calibrated(candidates, **params):
    optimizer = Planner(parse_geojson(small_scenario()),
                        parameters().model_copy(update={"objective": "balanced", **params}))
    optimizer.search_log = [
        dict(makespan=t, flight=f, n_sorties=1, bad_m=0,
             score=optimizer._mission_score(t, f, 1))
        for t, f in candidates
    ]
    optimizer._calibrate_balance()
    return optimizer


@pytest.mark.parametrize("middle", [0.25, 0.5, 0.8], ids=["convex", "linear", "concave"])
@pytest.mark.parametrize("offsets,spans", [
    ((1000, 10), (20, 200)),
    ((10, 1000), (200, 20)),
])
def test_balance_selects_compromise_across_front_shapes_and_scales(middle, offsets, spans):
    front = [(0, 1), (middle, middle), (1, 0)]
    candidates = [(offsets[0] + t * spans[0], offsets[1] + f * spans[1]) for t, f in front]
    optimizer = calibrated(candidates)
    best = min(optimizer.search_log, key=lambda r: r["score"])
    assert (best["makespan"], best["flight"]) == candidates[1]
    # Both extremes have the same regret, regardless of offsets and units.
    assert optimizer.search_log[0]["score"] == pytest.approx(0.51 * offsets[0])
    assert optimizer.search_log[2]["score"] == pytest.approx(optimizer.search_log[0]["score"])


def test_balance_sum_breaks_equal_worst_regret_in_favor_of_dominating_plan():
    optimizer = calibrated([(10, 110), (110, 10), (70, 40), (70, 30)])
    assert optimizer.search_log[3]["score"] < optimizer.search_log[2]["score"]
    assert min(optimizer.search_log, key=lambda r: r["score"]) is optimizer.search_log[3]


@pytest.mark.parametrize("candidates", [
    [(10, 30), (10, 20)],
    [(30, 10), (20, 10)],
    [(10, 10), (10, 10)],
    [(10, 10)],
])
def test_constant_objectives_and_single_candidate_have_finite_scores(candidates):
    optimizer = calibrated(candidates)
    assert all(math.isfinite(r["score"]) for r in optimizer.search_log)
    assert optimizer.search_log[-1]["score"] == 0
    t, f = candidates[-1]
    assert optimizer._mission_score(t + 1, f + 1, 1) > optimizer._mission_score(t, f, 1)


def test_later_candidates_use_fixed_ranges_without_clipping():
    optimizer = calibrated([(10, 30), (30, 10)])
    reference = optimizer.balance_reference.copy()
    assert optimizer._mission_score(5, 15, 1) < optimizer._mission_score(10, 15, 1)
    assert optimizer._mission_score(40, 40, 1) > optimizer._mission_score(30, 30, 1)
    assert optimizer.balance_reference == reference


def test_calibration_excludes_invalid_candidates_and_preserves_all_penalties():
    optimizer = calibrated([(3600, 7200), (7200, 3600)], deadline_h=1.5, takeoff_weight_h=0.1)
    invalid = [
        dict(makespan=360, flight=36000, n_sorties=3, bad_m=20, extra=2000),
        dict(makespan=36000, flight=360, n_sorties=2, bad_m=0,
             scheduling_error="Outside scheduling horizon", extra=1e15),
    ]
    for row in invalid:
        row["score"] = optimizer._mission_score(row["makespan"], row["flight"], row["n_sorties"]) + row["extra"]
        optimizer.search_log.append(row)
    optimizer._calibrate_balance()
    ref = optimizer.balance_reference
    assert (ref["time_s"], ref["time_max_s"], ref["flight_s"], ref["flight_max_s"]) == (3600, 7200, 3600, 7200)
    assert ref["source"] == "feasible_grid"
    assert optimizer.search_log[0]["score"] == pytest.approx(1836 + 360)
    assert optimizer.search_log[1]["score"] == pytest.approx(1836 + 360 + 1800 * 50)
    for row in invalid:
        assert row["score"] == pytest.approx(
            optimizer._mission_score(row["makespan"], row["flight"], row["n_sorties"]) + row["extra"])
    scores = [r["score"] for r in optimizer.search_log]
    optimizer._calibrate_balance()
    assert [r["score"] for r in optimizer.search_log] == pytest.approx(scores)


def test_partial_grid_fallback_and_no_valid_candidates():
    optimizer = calibrated([(10, 30), (30, 10)])
    for row in optimizer.search_log:
        row["bad_m"] = 1
        row["score"] += 100
    optimizer._calibrate_balance()
    assert optimizer.balance_reference["source"] == "partial_grid"
    assert [r["score"] for r in optimizer.search_log] == pytest.approx([105.1, 105.1])
    optimizer.balance_reference = None
    for row in optimizer.search_log:
        row["scheduling_error"] = "No schedule"
    optimizer._calibrate_balance()
    assert optimizer.balance_reference is None


def test_wear_penalty_can_change_balanced_choice():
    optimizer = calibrated([(3600, 7200), (5400, 5400), (7200, 3600)], takeoff_weight_h=0.1)
    assert optimizer._mission_score(5400, 5400, 1) < optimizer._mission_score(3600, 7200, 1)
    assert optimizer._mission_score(5400, 5400, 5) > optimizer._mission_score(3600, 7200, 1)


@pytest.mark.parametrize("objective,expected", [("time", 7203.6), ("flight", 93967.2)])
def test_other_objectives_keep_their_scoring(objective, expected):
    optimizer = calibrated([(3600, 7200), (7200, 3600)],
                           objective=objective, deadline_h=1.5, takeoff_weight_h=0.1)
    assert optimizer._mission_score(7200, 3600, 1) == pytest.approx(expected)


def test_report_explains_new_balance_and_keeps_legacy_plans_readable():
    optimizer = calibrated([(3600, 7200), (7200, 3600)])
    saved = {"id": "balanced", "params": {"objective": "balanced"},
             "summary": {"balance_reference": optimizer.balance_reference}}
    text = " ".join(mission_report(saved)["decisions"])
    assert "Чебышёва" in text and "Диапазоны" in text and "износ" in text
    saved["summary"]["balance_reference"] = {"time_s": 3600, "flight_s": 7200}
    legacy = " ".join(mission_report(saved)["decisions"])
    assert "Опорные оценки" in legacy and "Чебышёва" not in legacy
