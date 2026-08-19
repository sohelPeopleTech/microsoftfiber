"""The estimator's arithmetic, checked against hand-computed answers.

These numbers are the ones that end up in a message to finance, so each case
is constructed so the expected value can be worked out on paper.
"""

from __future__ import annotations

import pandas as pd
import pytest

from module5.classifier import DENIED_UNFULFILLED, classify
from module5.config import Config
from module5.revenue import arr_affected, estimate_impact, resolve_as_of, size_shortfall
from tests.conftest import make_ticket


def price(df: pd.DataFrame, config: Config | None = None) -> pd.Series:
    config = config or Config()
    return estimate_impact(classify(df, config), config).iloc[0]


def test_late_approval_exposure_is_arr_x_share_x_time():
    # 100 of 200 units blocked (50%) for 10 days on $365,000 ARR
    #   => 365000 * 0.5 * 10/365 = 5000.00
    row = price(make_ticket())
    assert row["CapacityShare"] == pytest.approx(0.5)
    assert row["DaysUnavailable"] == pytest.approx(10.0)
    assert row["RevenueExposureUSD"] == pytest.approx(5000.0)


def test_same_day_approval_carries_no_exposure():
    row = price(
        make_ticket(ApprovedDate=pd.Timestamp("2025-01-02T00:00:00Z"))  # 24h
    )
    assert not row["IsFlagged"]
    assert row["RevenueExposureUSD"] == 0.0
    assert row["ARRAffectedUSD"] == 0.0


def test_unfulfilled_accrues_from_denial_to_as_of():
    # Denied 2025-01-01, as_of 2025-07-01 => 181 days
    #   => 365000 * 0.5 * 181/365 = 90500.00
    config = Config(as_of="2025-07-01")
    row = price(make_ticket(ApprovedDate=None, NewLimitCapacity=100.0), config)
    assert row["Category"] == DENIED_UNFULFILLED
    assert row["DaysUnavailable"] == pytest.approx(181.0)
    assert row["RevenueExposureUSD"] == pytest.approx(90_500.0)


def test_unfulfilled_is_capped_so_one_old_ticket_cannot_dominate():
    config = Config(as_of="2030-01-01", unfulfilled_cap_days=90.0)
    row = price(make_ticket(ApprovedDate=None, NewLimitCapacity=100.0), config)
    assert row["DaysUnavailable"] == pytest.approx(90.0)


def test_zero_capacity_request_does_not_divide_by_zero():
    row = price(
        make_ticket(
            CurrentLimitCapacity=0.0, AdditionalLimitCapacity=0.0, NewLimitCapacity=0.0
        )
    )
    assert row["CapacityShare"] == 0.0
    assert row["RevenueExposureUSD"] == 0.0


def test_unmet_units_only_for_never_granted_capacity():
    granted = size_shortfall(classify(make_ticket())).iloc[0]
    assert granted["UnmetUnits"] == 0.0  # eventually granted, just late

    never = size_shortfall(
        classify(make_ticket(ApprovedDate=None, NewLimitCapacity=100.0))
    ).iloc[0]
    assert never["UnmetUnits"] == 100.0


def test_arr_affected_deduplicates_customers():
    """One customer with three bad tickets is one customer's ARR at risk."""
    three = pd.concat(
        [make_ticket(IncidentId=str(i)) for i in (1, 2, 3)], ignore_index=True
    )
    priced = estimate_impact(classify(three))
    assert priced["ARRAffectedUSD"].sum() == pytest.approx(1_095_000.0)  # naive triple
    assert arr_affected(priced) == pytest.approx(365_000.0)  # deduplicated


def test_unflagged_tickets_are_kept_not_dropped(priced, classified):
    assert len(priced) == len(classified)
    assert (priced.loc[~priced["IsFlagged"], "RevenueExposureUSD"] == 0).all()


def test_as_of_defaults_to_latest_date_in_the_data(classified):
    as_of = resolve_as_of(classified, Config())
    assert as_of == pd.Timestamp("2026-01-28T15:35:55Z")


def test_as_of_override_is_honoured(classified):
    assert resolve_as_of(classified, Config(as_of="2026-03-01")) == pd.Timestamp(
        "2026-03-01", tz="UTC"
    )


def test_free_tier_customer_contributes_zero_dollars():
    """$0 ARR must not silently become a ranking driver."""
    row = price(make_ticket(ARR_USD=0.0))
    assert row["IsFlagged"]
    assert row["RevenueExposureUSD"] == 0.0
