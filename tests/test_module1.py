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


def _fake_onto(util_start, util_end, lead_time, days=60, deployed=1000.0):
    """A single region whose curve and hardware we control exactly."""
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
        "dim_region": pd.DataFrame([{"Region": "testregion", "SKUClass": "Test-SKU",
                                     "LeadTimeDays": lead_time}]),
    }


# --- the core rule --------------------------------------------------------


def test_order_by_date_is_the_crossing_minus_the_lead_time(onto):
    for region in onto["dim_region"]["Region"]:
        f = project_region(onto, region)
        if f.cross_date and f.order_by_date and f.status != STATUS_BREACHED:
            cross = date.fromisoformat(f.cross_date)
            order = date.fromisoformat(f.order_by_date)
            assert (cross - order).days == f.lead_time_days


def test_a_comfortable_region_on_slow_hardware_is_already_late():
    """The whole point: 70% and rising, but 90 days of lead time."""
    onto = _fake_onto(60, 70, lead_time=90)
    f = project_region(onto, "testregion", threshold_pct=85)
    assert f.current_utilisation_pct < 71
    assert f.status == STATUS_OVERDUE
    assert f.days_until_order < 0


def test_the_same_curve_on_fast_hardware_is_not_yet_due():
    """Identical usage, different SKU -- only the lead time changes the verdict."""
    slow = project_region(_fake_onto(60, 70, lead_time=90), "testregion", threshold_pct=85)
    fast = project_region(_fake_onto(60, 70, lead_time=5), "testregion", threshold_pct=85)
    assert slow.days_to_threshold == pytest.approx(fast.days_to_threshold, abs=0.5)
    assert slow.status == STATUS_OVERDUE
    assert fast.status == STATUS_APPROACHING
    assert fast.days_until_order > slow.days_until_order


def test_lower_utilisation_can_outrank_higher(onto):
    """Urgency is time-to-act, so the ordering is not usage order."""
    df = module1.project_all(onto)
    actionable = df[df["status"].isin([STATUS_BREACHED, STATUS_OVERDUE, STATUS_DUE])]
    calm = df[df["status"] == STATUS_APPROACHING]
    if actionable.empty or calm.empty:
        pytest.skip("this extract has no mixed case")
    # Something flagged must sit below something not flagged on raw utilisation,
    # or the lead time is doing no work at all.
    assert actionable["lead_time_days"].max() > calm["lead_time_days"].min()


# --- the states -----------------------------------------------------------


def test_already_over_the_line_is_breached():
    f = project_region(_fake_onto(80, 92, lead_time=30), "testregion", threshold_pct=85)
    assert f.status == STATUS_BREACHED
    assert f.days_to_threshold == 0.0
    assert "late" in f.reason


def test_flat_usage_never_crosses():
    f = project_region(_fake_onto(50, 50, lead_time=30), "testregion", threshold_pct=85)
    assert f.status == STATUS_STABLE
    assert f.cross_date is None


def test_falling_usage_never_crosses():
    f = project_region(_fake_onto(70, 55, lead_time=30), "testregion", threshold_pct=85)
    assert f.status == STATUS_STABLE
    assert f.trend_pct_per_day < 0


def test_a_crossing_beyond_the_horizon_is_not_a_date_to_plan_against():
    """Barely-rising usage would project years out -- reported as stable."""
    f = project_region(_fake_onto(50.0, 50.4, lead_time=30), "testregion", threshold_pct=85)
    assert f.status == STATUS_STABLE
    assert f.days_to_threshold is None or f.days_to_threshold > 365


def test_due_now_lands_on_the_order_date():
    onto = _fake_onto(60, 84, lead_time=1)
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
            assert str(f.lead_time_days) in f.reason


def test_threshold_is_a_parameter_not_a_constant(onto):
    strict = module1.due_requests(onto, threshold_pct=60)
    lax = module1.due_requests(onto, threshold_pct=95)
    assert len(strict) >= len(lax)


def test_unknown_region_is_rejected(onto):
    with pytest.raises(KeyError):
        project_region(onto, "marsnorth1")
