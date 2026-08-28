"""Module 3 -- demand forecasting on deliberately thin data.

Most of these tests exist because 60 tickets across 11 regions and 5 months is
not enough signal for an unconstrained projection, and the failure mode is a
forecast that looks confident while being arithmetic.
"""

from __future__ import annotations

import pandas as pd
import pytest

import module3
import dimensional
from module3.forecast import moving_average, _slope
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def entities():
    return dimensional.build(WORKBOOK, "data/synthetic")


@pytest.fixture(scope="module")
def demand(entities):
    return module3.demand_by_period(entities, "M")


# --- the series -----------------------------------------------------------


def test_every_region_has_every_period(entities, demand):
    """Zero-filled: a month with no requests is a zero, not a missing row."""
    regions = entities["dim_region"]["Region"].nunique()
    periods = demand["Period"].nunique()
    assert len(demand) == regions * periods
    assert set(demand["Region"]) == set(entities["dim_region"]["Region"])


def test_totals_reconcile_with_the_tickets(entities, demand):
    fact = entities["fact_capacity_request"]
    assert demand["RequestCount"].sum() == len(fact)
    assert demand["RequestedUnits"].sum() == fact["AdditionalLimitCapacity"].sum()


def test_the_demand_signal_is_labelled_as_real(demand):
    assert set(demand["Source"]) == {"ICM extract"}


def test_usage_signal_is_labelled_as_generated(entities):
    usage = module3.usage_by_period(entities, "M")
    assert set(usage["Source"]) == {"generated"}
    assert usage["UtilisationPct"].between(0, 100).all()


# --- the maths ------------------------------------------------------------


def test_moving_average_is_trailing():
    assert moving_average([1, 2, 3, 4], window=2) == [1.0, 1.5, 2.5, 3.5]


def test_short_series_average_what_they_have():
    assert moving_average([5], window=3) == [5.0]


def test_slope_detects_direction():
    assert _slope([1, 2, 3, 4]) == pytest.approx(1.0)
    assert _slope([4, 3, 2, 1]) == pytest.approx(-1.0)
    assert _slope([2, 2, 2]) == pytest.approx(0.0)
    assert _slope([7]) == 0.0


# --- the constraints that stop it lying -----------------------------------


def test_sparse_regions_get_no_trend(demand):
    """Fewer than four periods with demand is not a direction, it is noise."""
    f = module3.forecast_demand(demand)
    for row in f[~f["Confident"]].itertuples():
        assert row.TrendApplied is False
        assert row.SlopePerPeriod == 0.0


def test_the_trend_is_damped_not_linear(demand):
    """Undamped, westeurope tripled over three periods. Damped, it flattens."""
    f = module3.forecast_demand(demand)
    we = f[(f["Region"] == "westeurope")].sort_values("Horizon")
    if we.empty or not we.iloc[0]["TrendApplied"]:
        pytest.skip("westeurope carries no trend in this extract")
    steps = we["Forecast"].tolist()
    growth = [steps[i + 1] - steps[i] for i in range(len(steps) - 1)]
    assert growth[0] > growth[-1], "each step must add less than the one before"


def test_no_forecast_exceeds_twice_the_observed_peak(demand):
    f = module3.forecast_demand(demand)
    peaks = demand.groupby("Region")["Value"].max()
    for row in f.itertuples():
        assert row.Forecast <= peaks[row.Region] * 2 + 1e-6, row.Region


def test_forecasts_are_never_negative(demand):
    f = module3.forecast_demand(demand)
    assert (f["Forecast"] >= 0).all()


def test_horizon_and_periods_are_contiguous(demand):
    f = module3.forecast_demand(demand, horizon=3)
    last_observed = pd.Period(sorted(demand["Period"])[-1])
    for region, grp in f.groupby("Region"):
        got = sorted(pd.Period(p) for p in grp["Period"])
        assert got == [last_observed + i for i in (1, 2, 3)]


# --- the ranking ----------------------------------------------------------


def test_growth_ranking_covers_every_region(entities, demand):
    g = module3.growth_ranking(demand)
    assert set(g["Region"]) == set(entities["dim_region"]["Region"])
    assert g["Rank"].tolist() == list(range(1, len(g) + 1))


def test_a_region_starting_from_zero_is_not_infinite_growth(demand):
    """Reported as new demand, which is honest and also more useful."""
    g = module3.growth_ranking(demand)
    new = g[g["Basis"].str.startswith("new demand")]
    assert new["GrowthPct"].isna().all()


def test_ranking_uses_absolute_change_not_percentage(demand):
    """1 -> 3 units is 200% and irrelevant; 200 -> 400 is 100% and matters."""
    g = module3.growth_ranking(demand)
    changes = g["AbsoluteChange"].tolist()
    assert changes == sorted(changes, reverse=True)


def test_confidence_is_reported_per_region(demand):
    g = module3.growth_ranking(demand)
    assert g["Confident"].dtype == bool
    assert (g["PeriodsWithDemand"] <= demand["Period"].nunique()).all()


def test_weekly_grain_also_works(entities):
    weekly = module3.demand_by_period(entities, "W")
    assert weekly["Period"].nunique() > 15
    assert module3.forecast_demand(weekly)["Forecast"].ge(0).all()
