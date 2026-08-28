"""The shared entity model -- one definition of each thing, joins that hold."""

from __future__ import annotations

import pandas as pd
import pytest

import dimensional
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def entities():
    return dimensional.build(WORKBOOK, "data/synthetic")


def test_every_entity_is_present(entities):
    assert set(entities.tables) == set(dimensional.ENTITIES)


def test_referential_integrity_holds(entities):
    """A join that silently drops rows is how a total stops reconciling."""
    assert entities.is_valid, entities.issues


def test_a_broken_key_is_caught_not_ignored(entities):
    """The validator must actually fail when something is wrong."""
    broken = dict(entities.tables)
    fact = broken["fact_capacity_request"].copy()
    fact.loc[fact.index[0], "Region"] = "marsnorth1"
    broken["fact_capacity_request"] = fact
    issues = dimensional.validate(broken)
    assert any("dim_region" in i for i in issues)


def test_dimensions_have_one_row_per_key(entities):
    assert entities["dim_region"]["Region"].is_unique
    assert entities["dim_subscription"]["SubscriptionId"].is_unique
    assert entities["dim_sku"]["SKUClass"].is_unique
    assert entities["dim_feature"]["Feature"].is_unique


def test_the_spine_still_matches_the_extract(entities):
    """The dimensional model must not quietly change the real data."""
    fact = entities["fact_capacity_request"]
    assert len(fact) == 60
    assert fact["IncidentId"].is_unique
    # Requested = Current + Additional, the relationship verified against ICM.
    assert (fact["RequestedCapacity"]
            == fact["CurrentLimitCapacity"] + fact["AdditionalLimitCapacity"]).all()


def test_every_region_carries_its_hardware_and_lead_time(entities):
    dim = entities["dim_region"]
    assert dim["SKUClass"].notna().all()
    assert dim["LeadTimeDays"].notna().all()
    assert (dim["LeadTimeDays"] > 0).all()


def test_lead_time_is_now_reachable_from_a_ticket(entities):
    """The join that did not exist before: ticket -> region -> SKU -> lead time."""
    joined = entities["fact_capacity_request"].merge(
        entities["dim_region"][["Region", "SKUClass", "LeadTimeDays"]], on="Region", how="left"
    )
    assert len(joined) == len(entities["fact_capacity_request"])
    assert joined["LeadTimeDays"].notna().all()


def test_usage_covers_every_region_every_day(entities):
    usage = entities["fact_usage_daily"]
    per_region = usage.groupby("Region")["Date"].nunique()
    assert per_region.nunique() == 1, "regions must share the same calendar"
    assert set(usage["Region"]) == set(entities["dim_region"]["Region"])


def test_provenance_is_stated_for_every_entity(entities):
    """Nothing may be quoted without knowing where it came from."""
    src = dimensional.sources(entities.tables)
    assert len(src) == len(dimensional.ENTITIES)
    assert src["Provenance"].str.len().gt(0).all()
    # The spine is real data with one generated column -- it must not be
    # advertised as fully synthetic, nor as fully real.
    spine = src[src["Entity"] == "fact_capacity_request"].iloc[0]
    assert spine["FullySynthetic"] is False or not spine["FullySynthetic"]
    assert "ICM extract" in spine["Provenance"] and "generated" in spine["Provenance"]


def test_missing_synthetic_input_fails_loudly(tmp_path):
    """A hole in the model must not look like an empty result."""
    with pytest.raises(FileNotFoundError, match="synthdata.generate"):
        dimensional.build(WORKBOOK, tmp_path)
