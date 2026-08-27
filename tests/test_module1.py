"""Module 1 -- lead-time-aware thresholds.

The behaviour under test is that urgency is a function of *time to act*, not of
current utilisation. A region can look comfortable and already be late.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

import module1
import ontology
from module1.threshold import (
    STATUS_APPROACHING,
    STATUS_BREACHED,
    STATUS_DUE,
    STATUS_OVERDUE,
    STATUS_STABLE,
    project_region,
)
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def onto():
    return ontology.build(WORKBOOK, "data/synthetic")


def _fake_onto(util_start, util_end, days=60, deployed=1000.0):
    """A single region whose curve we control exactly.

    It no longer takes a lead time. There is nothing about the region that makes
    it slower or faster to scale -- how long a decision takes is a policy, and it
    is passed to project_region by the tests that care.
    """
    start = date(2026, 1, 1)
    rows = []
    for i in range(days):
        pct = util_start + (util_end - util_start) * i / max(days - 1, 1)
        rows.append({
            "Date": (start + timedelta(days=i)).isoformat(),
            "Region": "testregion", "SKUClass": "Test-SKU",
            "TotalUnits": deployed, "UsedUnits": deployed * pct / 100,
            "UtilisationPct": pct,
        })
    return {
        "fact_usage_daily": pd.DataFrame(rows),
        # SKUClass and LeadTimeDays stay on the table because module 2 and the
        # propensity model still read them. module1 no longer does.
        "dim_region": pd.DataFrame([{"Region": "testregion", "SKUClass": "Test-SKU",
                                     "LeadTimeDays": 45}]),
    }


# --- the core rule --------------------------------------------------------


def test_the_act_by_date_is_the_crossing_minus_the_decision_window(onto):
    for region in onto["dim_region"]["Region"]:
        f = project_region(onto, region)
        if f.cross_date and f.act_by_date and f.status != STATUS_BREACHED:
            cross = date.fromisoformat(f.cross_date)
            act = date.fromisoformat(f.act_by_date)
            assert (cross - act).days == f.decision_window_days


def test_a_region_crossing_inside_the_decision_window_is_already_late():
    """70% and rising, and it crosses sooner than anyone can decide."""
    onto = _fake_onto(60, 70)
    f = project_region(onto, "testregion", threshold_pct=85,
                       decision_window_days=90)
    assert f.current_utilisation_pct < 71
    assert f.status == STATUS_OVERDUE
    assert f.days_until_action < 0


def test_the_same_curve_with_a_short_window_is_not_yet_due():
    """Identical usage. Only how long the organisation takes to decide changes
    the verdict -- which is the one thing that can change it, now that scaling
    itself is immediate everywhere."""
    onto = _fake_onto(60, 70)
    slow = project_region(onto, "testregion", threshold_pct=85,
                          decision_window_days=90)
    fast = project_region(onto, "testregion", threshold_pct=85,
                          decision_window_days=5)
    assert slow.days_to_threshold == pytest.approx(fast.days_to_threshold, abs=0.5)
    assert slow.status == STATUS_OVERDUE
    assert fast.status == STATUS_APPROACHING
    assert fast.days_until_action > slow.days_until_action


def test_the_decision_window_is_the_same_for_every_region(onto):
    """It was a per-region property when it described hardware. It is a policy
    now, and a policy that varied by region would be the old model wearing a
    new name."""
    windows = {project_region(onto, r).decision_window_days
               for r in onto["dim_region"]["Region"]}
    assert len(windows) == 1, f"regions disagree on the decision window: {windows}"


def test_no_flag_mentions_hardware_or_a_wait(onto):
    """Fabric scales an F SKU immediately. If any of this vocabulary comes back,
    so has a model that tells a Fabric customer something untrue."""
    import re

    banned = re.compile(r"\b(provision\w*|lead ?time|hardware|intel|amd|order)\b",
                        re.I)
    for region in onto["dim_region"]["Region"]:
        f = project_region(onto, region)
        hit = banned.search(f.reason)
        assert not hit, f"{region}: {hit.group(0)!r} in {f.reason!r}"


def test_lower_utilisation_can_outrank_higher(onto):
    """Urgency is time-to-act, so the ordering is not usage order.

    This used to prove it through lead time: something flagged had slower
    hardware than something calm, so it outranked it despite being emptier.
    Every region now has the same decision window, and that particular
    inversion is gone -- on this extract the two breached regions are also the
    two fullest, which is not a defect, it is just true here.

    The property survives one level in, where the trend does the work instead
    of the hardware: among the regions needing a decision, a less-full region
    ranks above a fuller one because it reaches its line sooner.
    """
    df = module1.project_all(onto)
    if len(df) < 2:
        pytest.skip("need at least two regions")

    util = df["current_utilisation_pct"].tolist()
    inversions = [(i, j) for i in range(len(util)) for j in range(i + 1, len(util))
                  if util[i] < util[j]]
    assert inversions, (
        "every region is ranked in descending utilisation order, so the "
        "ranking says nothing the utilisation column does not already say")

    i, j = inversions[0]
    assert (df["days_until_action"].iloc[i] or 0) <= (df["days_until_action"].iloc[j] or 0), (
        f"{df['region'].iloc[i]} outranks the fuller {df['region'].iloc[j]} but "
        f"is not closer to needing a decision")


# --- the states -----------------------------------------------------------


def test_already_over_the_line_is_breached():
    f = project_region(_fake_onto(80, 92), "testregion", threshold_pct=85)
    assert f.status == STATUS_BREACHED
    assert f.days_to_threshold == 0.0
    # It has to say the region is past its line and that this is actionable now.
    # The old wording was "N days late", which measured lateness against a
    # delivery that no longer has to happen.
    assert "past the" in f.reason and "immediate" in f.reason


def test_flat_usage_never_crosses():
    f = project_region(_fake_onto(50, 50), "testregion", threshold_pct=85)
    assert f.status == STATUS_STABLE
    assert f.cross_date is None


def test_falling_usage_never_crosses():
    f = project_region(_fake_onto(70, 55), "testregion", threshold_pct=85)
    assert f.status == STATUS_STABLE
    assert f.trend_pct_per_day < 0


def test_a_crossing_beyond_the_horizon_is_not_a_date_to_plan_against():
    """Barely-rising usage would project years out -- reported as stable."""
    f = project_region(_fake_onto(50.0, 50.4), "testregion", threshold_pct=85)
    assert f.status == STATUS_STABLE
    assert f.days_to_threshold is None or f.days_to_threshold > 365


def test_due_now_lands_on_the_order_date():
    onto = _fake_onto(60, 84)
    f = project_region(onto, "testregion", threshold_pct=85, grace_days=3)
    assert f.status in (STATUS_DUE, STATUS_OVERDUE, STATUS_BREACHED)


# --- ranking and queue ----------------------------------------------------


def test_project_all_covers_every_region_and_ranks_urgency_first(onto):
    df = module1.project_all(onto)
    assert set(df["region"]) == set(onto["dim_region"]["Region"])
    order = {STATUS_BREACHED: 0, STATUS_OVERDUE: 1, STATUS_DUE: 2,
             STATUS_APPROACHING: 3, STATUS_STABLE: 4}
    ranks = [order[s] for s in df["status"]]
    assert ranks == sorted(ranks), "actionable regions must come first"


def test_due_requests_is_the_queue_not_the_dashboard(onto):
    due = module1.due_requests(onto)
    everything = module1.project_all(onto)
    assert len(due) <= len(everything)
    assert set(due["status"]) <= {STATUS_BREACHED, STATUS_OVERDUE, STATUS_DUE}


def test_every_flag_explains_itself(onto):
    for f in (project_region(onto, r) for r in onto["dim_region"]["Region"]):
        assert len(f.reason) > 30
        if f.is_actionable():
            assert str(f.decision_window_days) in f.reason


def test_threshold_is_a_parameter_not_a_constant(onto):
    strict = module1.due_requests(onto, threshold_pct=60)
    lax = module1.due_requests(onto, threshold_pct=95)
    assert len(strict) >= len(lax)


def test_unknown_region_is_rejected(onto):
    with pytest.raises(KeyError):
        project_region(onto, "marsnorth1")


def test_a_flag_names_the_rule_that_actually_fired(onto):
    """The due_now branch fires on the review cycle, not the decision window.

    It said "inside the 7-day decision window" against a region crossing in 28
    days, because the sentence named the wrong one of the two numbers. Both are
    on screen, so a reader can check -- and did.
    """
    from module1.threshold import DEFAULT_REVIEW_DAYS

    for region in onto["dim_region"]["Region"]:
        f = project_region(onto, region)
        if f.status != STATUS_DUE or f.days_until_action is None:
            continue
        if f.days_until_action > f.decision_window_days:
            assert "decision window" not in f.reason, (
                f"{region}: needs deciding in {f.days_until_action} days, which is "
                f"outside its {f.decision_window_days}-day decision window, but the "
                f"reason claims it is inside it: {f.reason!r}")
        assert f.days_until_action <= DEFAULT_REVIEW_DAYS
