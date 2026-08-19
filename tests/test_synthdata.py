"""The synthetic world must agree with the real one.

Invented data that contradicts the tickets underneath it is worse than no data:
every downstream module would disagree with its own source. These tests assert
the couplings, not the values.
"""

from __future__ import annotations

import pandas as pd
import pytest

from module5 import ingest
from synthdata import generate
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def tickets():
    df = ingest.load_bronze(WORKBOOK)
    for c in ("CurrentLimitCapacity", "AdditionalLimitCapacity", "NewLimitCapacity"):
        df[c] = pd.to_numeric(df[c])
    return df


@pytest.fixture(scope="module")
def tables(tickets):
    return generate.generate_all(tickets)


def test_everything_is_tagged_as_synthetic(tables):
    """Nothing may be mistaken for a business source."""
    for name, df in tables.items():
        assert "IsSynthetic" in df.columns, name
        assert df["IsSynthetic"].all(), name
        assert df["Provenance"].str.startswith("SYNTHETIC").all(), name


def test_generation_is_deterministic(tickets):
    a = generate.generate_all(tickets)
    b = generate.generate_all(tickets)
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name]), name


def test_only_real_regions_appear(tickets, tables):
    real = set(tickets["Region"])
    for name in ("sku_by_region", "hardware_inventory", "capacity_usage",
                 "deal_events", "feature_matrix"):
        assert set(tables[name]["Region"]) <= real, name


def test_every_region_gets_hardware(tickets, tables):
    assert set(tables["sku_by_region"]["Region"]) == set(tickets["Region"])


def test_sku_classes_join_to_the_reference(tables):
    """The lead-time table was unjoinable before -- that was the whole problem."""
    assigned = set(tables["sku_by_region"]["SKUClass"])
    known = set(tables["sku_reference"]["SKUClass"])
    assert assigned <= known
    merged = tables["sku_by_region"].merge(tables["sku_reference"], on="SKUClass")
    assert len(merged) == len(tables["sku_by_region"])
    assert merged["LeadTimeDays"].notna().all()


def test_inventory_covers_the_largest_customer_in_each_region(tickets, tables):
    """A region cannot have deployed less than a customer already held there."""
    inv = tables["hardware_inventory"].set_index("Region")
    for region, largest in tickets.groupby("Region")["CurrentLimitCapacity"].max().items():
        assert inv.loc[region, "DeployedUnits"] >= largest


def test_usage_grows_more_where_demand_was_higher(tickets, tables):
    """The curve must explain the tickets, not contradict them."""
    usage = tables["capacity_usage"]
    demand = tickets.groupby("Region")["AdditionalLimitCapacity"].sum()
    growth = {}
    for region, grp in usage.groupby("Region"):
        grp = grp.sort_values("Date")
        growth[region] = grp["UtilisationPct"].tail(7).mean() - grp["UtilisationPct"].head(7).mean()
    busiest = demand.idxmax()
    quietest = demand.idxmin()
    assert growth[busiest] > growth[quietest], (
        f"{busiest} ({demand[busiest]}u) should grow faster than {quietest} ({demand[quietest]}u)"
    )


def test_usage_stays_inside_deployed_capacity(tables):
    usage = tables["capacity_usage"]
    assert (usage["UsedUnits"] <= usage["TotalUnits"]).all()
    assert usage["UtilisationPct"].between(0, 100).all()


def test_usage_covers_the_whole_observed_window(tickets, tables):
    dates = pd.to_datetime(
        pd.concat([tickets["DeniedDate"], tickets["ApprovedDate"]]).dropna(), utc=True
    )
    usage_dates = pd.to_datetime(tables["capacity_usage"]["Date"])
    assert usage_dates.min().date() <= dates.min().date()
    assert usage_dates.max().date() >= dates.max().date()


def test_linked_deal_events_precede_their_request(tickets, tables):
    """Module 4's premise: the deal closes, then the capacity request follows."""
    events = tables["deal_events"]
    linked = events[events["LinkedIncidentId"] != ""]
    assert len(linked) >= 10
    by_id = tickets.set_index(tickets["IncidentId"].astype(str))
    for e in linked.itertuples():
        t = by_id.loc[e.LinkedIncidentId]
        when = pd.to_datetime(t.DeniedDate if pd.notna(t.DeniedDate) else t.ApprovedDate, utc=True)
        assert pd.to_datetime(e.EventDate, utc=True) < when


def test_unlinked_events_exist_so_the_detector_must_discriminate(tables):
    events = tables["deal_events"]
    assert (events["LinkedIncidentId"] == "").sum() >= 4


def test_every_feature_has_a_status_in_every_region(tickets, tables):
    fm = tables["feature_matrix"]
    assert len(fm) == len(generate.FEATURES) * tickets["Region"].nunique()
    assert set(fm["Status"]) <= {"Live", "Preview", "Planned", "Unavailable"}


def test_ticket_status_agrees_with_the_dates(tickets, tables):
    """The gap that mattered most -- and it must not contradict the extract."""
    status = tables["ticket_status"].set_index("IncidentId")["TicketStatus"]
    approved = tickets[tickets["ApprovedDate"].notna()]["IncidentId"].astype(str)
    unapproved = tickets[tickets["ApprovedDate"].isna()]["IncidentId"].astype(str)

    assert (status.loc[approved] == "Fulfilled").all()
    assert set(status.loc[unapproved]) <= {"Rejected", "InProgress"}
    # Both outcomes must be represented, or the field tells us nothing new.
    assert (status.loc[unapproved] == "Rejected").any()
    assert (status.loc[unapproved] == "InProgress").any()


def test_status_splits_the_previously_ambiguous_group(tickets, tables):
    """12 tickets were counted as failures with no way to tell why."""
    status = tables["ticket_status"].set_index("IncidentId")["TicketStatus"]
    unapproved = tickets[tickets["ApprovedDate"].isna()]["IncidentId"].astype(str)
    counts = status.loc[unapproved].value_counts()
    assert counts.sum() == 12
    assert counts.get("Rejected", 0) + counts.get("InProgress", 0) == 12
