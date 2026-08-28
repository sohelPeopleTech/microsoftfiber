"""Module 4 -- spike detection, and the discipline of not explaining everything."""

from __future__ import annotations

import pandas as pd
import pytest

import module3
import module4
import dimensional
from module4.anomaly import Z_REPORTING_CAP, detect_anomalies, match_events
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def entities():
    return dimensional.build(WORKBOOK, "data/synthetic")


@pytest.fixture(scope="module")
def demand(entities):
    return module3.demand_by_period(entities, "M")


@pytest.fixture(scope="module")
def found(entities, demand):
    return module4.explain_anomalies(demand, entities["fact_event"])


def _series(region, values, start="2025-09"):
    periods = pd.period_range(start, periods=len(values), freq="M").astype(str)
    return pd.DataFrame({"Region": region, "Period": periods, "Value": values})


# --- detection ------------------------------------------------------------


def test_a_clear_spike_is_detected():
    d = _series("r", [10, 12, 11, 400, 13])
    out = detect_anomalies(d)
    assert len(out) == 1
    assert out.iloc[0]["Period"] == "2025-12"


def test_a_flat_series_produces_nothing():
    assert detect_anomalies(_series("r", [100, 100, 100, 100, 100])).empty


def test_ordinary_variation_is_not_a_spike():
    assert detect_anomalies(_series("r", [100, 110, 95, 105, 98])).empty


def test_a_drop_is_never_reported():
    """Demand collapsing is real, but it is not a capacity risk."""
    assert detect_anomalies(_series("r", [500, 500, 500, 5, 500])).empty


def test_small_absolute_jumps_are_ignored_however_unusual():
    """1 -> 6 units is statistically wild and operationally meaningless."""
    assert detect_anomalies(_series("r", [1, 1, 1, 6, 1])).empty


def test_the_floor_is_a_parameter(demand):
    loose = detect_anomalies(demand, min_deviation_units=0)
    strict = detect_anomalies(demand, min_deviation_units=200)
    assert len(loose) > len(strict)


def test_a_short_series_makes_no_claim():
    assert detect_anomalies(_series("r", [1, 500, 1])).empty


def test_z_is_capped_so_it_reads_as_a_number(demand):
    out = detect_anomalies(demand)
    assert (out["ZScore"] <= Z_REPORTING_CAP).all()
    assert out["ZCapped"].any(), "this extract should contain a near-flat series"


def test_zero_baseline_reports_units_not_a_percentage(found):
    for a in found:
        if a.baseline == 0:
            assert a.pct_above_baseline is None or pd.isna(a.pct_above_baseline)
            assert "near-zero baseline" in a.recommendation
            assert "nan" not in a.recommendation.lower()


# --- matching -------------------------------------------------------------


def test_an_event_in_the_window_explains_the_spike():
    spikes = detect_anomalies(_series("r", [10, 12, 11, 400, 13]))
    events = pd.DataFrame([{"EventDate": "2025-11-25", "Region": "r",
                            "EventType": "Deal closed", "ExpectedCapacityUnits": 300,
                            "SubscriptionId": "s1", "LinkedIncidentId": "1"}])
    out = match_events(spikes, events)
    assert out[0].matched and out[0].match_strength == "strong"


def test_an_event_in_another_region_does_not_explain_it():
    spikes = detect_anomalies(_series("r", [10, 12, 11, 400, 13]))
    events = pd.DataFrame([{"EventDate": "2025-11-25", "Region": "elsewhere",
                            "EventType": "Deal closed", "ExpectedCapacityUnits": 300}])
    assert match_events(spikes, events)[0].matched is False


def test_an_event_long_before_the_spike_does_not_explain_it():
    """Without a bound, everything correlates with everything."""
    spikes = detect_anomalies(_series("r", [10, 12, 11, 400, 13]))
    events = pd.DataFrame([{"EventDate": "2025-06-01", "Region": "r",
                            "EventType": "Deal closed", "ExpectedCapacityUnits": 300}])
    assert match_events(spikes, events)[0].matched is False


def test_an_event_with_no_capacity_is_a_weak_match_not_a_cause():
    """A marketing campaign is not why 200 units were requested."""
    spikes = detect_anomalies(_series("r", [10, 12, 11, 400, 13]))
    events = pd.DataFrame([{"EventDate": "2025-12-02", "Region": "r",
                            "EventType": "Marketing campaign",
                            "ExpectedCapacityUnits": 0}])
    out = match_events(spikes, events)[0]
    assert out.matched and out.match_strength == "weak"
    assert "unexplained until someone confirms" in out.recommendation


def test_a_real_deal_outranks_a_closer_campaign():
    spikes = detect_anomalies(_series("r", [10, 12, 11, 400, 13]))
    events = pd.DataFrame([
        {"EventDate": "2025-12-05", "Region": "r", "EventType": "Marketing campaign",
         "ExpectedCapacityUnits": 0},
        {"EventDate": "2025-11-20", "Region": "r", "EventType": "Deal closed",
         "ExpectedCapacityUnits": 300},
    ])
    out = match_events(spikes, events)[0]
    assert out.event_type == "Deal closed"
    assert out.match_strength == "strong"


def test_unexplained_spikes_are_reported_as_findings(found):
    """Demand moved and nobody knows why is itself worth saying."""
    unexplained = [a for a in found if a.match_strength != "strong"]
    assert unexplained
    for a in unexplained:
        assert "no matching business event" in a.recommendation or "unexplained" in a.recommendation


def test_the_detector_discriminates(found):
    """Six of the eighteen events are noise -- matching all of them is failure."""
    strong = [a for a in found if a.match_strength == "strong"]
    assert strong, "must find some real causes"
    assert len(strong) < len(found), "must not explain everything"


def test_timing_reads_correctly(found):
    for a in found:
        if a.matched:
            assert "-" not in a.event_timing
            assert "1 days" not in a.event_timing


def test_explained_spikes_come_first(found):
    matched = [a.matched for a in found]
    assert matched == sorted(matched, reverse=True)


def test_nothing_is_auto_executed(found):
    assert all(a.requires_human_approval for a in found)
