"""Ranking behaviour -- step 4."""

from __future__ import annotations

import pandas as pd
import pytest

from module5 import aggregate
from module5.classifier import classify
from module5.config import Config
from module5.revenue import estimate_impact
from tests.conftest import make_ticket


def test_ranked_by_exposure_descending(regions):
    exposure = regions["RevenueExposureUSD"].tolist()
    assert exposure == sorted(exposure, reverse=True)
    assert regions["Rank"].tolist() == list(range(1, len(regions) + 1))


def test_every_region_gets_a_row_even_with_no_failures(priced, regions):
    assert set(regions["Region"]) == set(priced["Region"])


def test_counts_reconcile_with_the_ticket_frame(priced, regions):
    assert regions["TicketsTotal"].sum() == len(priced)
    assert regions["TicketsFlagged"].sum() == int(priced["IsFlagged"].sum())
    assert regions["RevenueExposureUSD"].sum() == pytest.approx(
        priced["RevenueExposureUSD"].sum(), abs=1.0
    )


def test_share_of_exposure_sums_to_one(regions):
    assert regions["ShareOfExposure"].sum() == pytest.approx(1.0)


def test_region_arr_is_deduplicated_per_customer():
    """Same customer, two failed tickets in one region => ARR counted once."""
    two = pd.concat(
        [make_ticket(IncidentId="1"), make_ticket(IncidentId="2")], ignore_index=True
    )
    ranked = aggregate.by_region(estimate_impact(classify(two)))
    assert ranked.loc[0, "TicketsFlagged"] == 2
    assert ranked.loc[0, "CustomersAffected"] == 1
    assert ranked.loc[0, "ARRAffectedUSD"] == pytest.approx(365_000.0)


def test_ranking_is_stable_across_runs(priced, config):
    first = aggregate.by_region(priced, config)["Region"].tolist()
    shuffled = priced.sample(frac=1.0, random_state=7)
    second = aggregate.by_region(shuffled, config)["Region"].tolist()
    assert first == second


def test_portfolio_summary_totals(priced, config, regions):
    s = aggregate.portfolio_summary(priced, config)
    assert s["tickets_total"] == len(priced)
    assert s["tickets_flagged"] == int(priced["IsFlagged"].sum())
    assert s["delayed_count"] + s["unfulfilled_count"] == s["tickets_flagged"]
    assert s["revenue_exposure_usd"] == pytest.approx(
        regions["RevenueExposureUSD"].sum(), abs=1.0
    )
    assert s["arr_reference_is_placeholder"] is True


def test_customer_view_ranked_and_flagged_only(priced):
    customers = aggregate.by_subscription(priced)
    exposure = customers["RevenueExposureUSD"].tolist()
    assert exposure == sorted(exposure, reverse=True)
    assert customers["SubscriptionId"].is_unique


def test_trend_buckets_by_denial_month(priced, config):
    trend = aggregate.exposure_trend(priced, config)
    assert not trend.empty
    assert trend["Period"].tolist() == sorted(trend["Period"])
    assert trend["TicketsFlagged"].sum() == int(
        (priced["IsFlagged"] & priced["DeniedDate"].notna()).sum()
    )


def test_empty_input_does_not_crash():
    empty = estimate_impact(classify(make_ticket())).iloc[0:0]
    assert aggregate.by_region(empty).empty
    assert aggregate.by_subscription(empty).empty
    assert aggregate.exposure_trend(empty).empty
