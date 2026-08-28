"""Module 2 -- conversion arithmetic, checked against hand-computed answers."""

from __future__ import annotations

import pytest

import dimensional
from module2 import (
    Conversion,
    convert_like_for_like,
    convert_same_footprint,
    migrate_region,
)
from module2.calculator import SKU, conversion_ratio, sku_from_dim
from tests.conftest import WORKBOOK

BASE = SKU("AMD-standard", relative_performance=1.0, relative_cost=1.0, lead_time_days=21)
FAST = SKU("Intel-highmem", relative_performance=1.4, relative_cost=1.7, lead_time_days=45)


@pytest.fixture(scope="module")
def entities():
    return dimensional.build(WORKBOOK, "data/synthetic")


# --- the ratio ------------------------------------------------------------


def test_ratio_is_not_one_to_one():
    """The assumption the module exists to prevent."""
    assert conversion_ratio(BASE, FAST) == pytest.approx(1 / 1.4)
    assert conversion_ratio(FAST, BASE) == pytest.approx(1.4)


def test_ratio_of_a_class_to_itself_is_one():
    assert conversion_ratio(BASE, BASE) == 1.0


def test_a_sku_must_have_positive_performance():
    with pytest.raises(ValueError, match="relative_performance"):
        SKU("broken", relative_performance=0, relative_cost=1)


# --- like for like --------------------------------------------------------


def test_like_for_like_needs_fewer_denser_units():
    """200 AMD-standard of work needs 200/1.4 = 142.86 Intel-highmem."""
    c = convert_like_for_like(BASE, FAST, 200)
    assert c.to_units == pytest.approx(142.8571, abs=1e-3)
    assert c.capacity_delta == 0.0, "work is held constant by definition"


def test_like_for_like_cost_can_rise_even_as_units_fall():
    """Fewer units x higher unit cost -- the trap a 1:1 view misses.

    200 x 1.0 = 200 before;  142.857 x 1.7 = 242.86 after.
    """
    c = convert_like_for_like(BASE, FAST, 200)
    assert c.cost_before == pytest.approx(200.0)
    assert c.cost_after == pytest.approx(242.857, abs=1e-2)
    assert c.cost_delta > 0
    assert c.cost_delta_pct == pytest.approx(21.43, abs=0.05)


def test_moving_to_cheaper_slower_hardware_saves_money_but_needs_more_units():
    c = convert_like_for_like(FAST, BASE, 100)
    assert c.to_units == pytest.approx(140.0)
    assert c.cost_before == pytest.approx(170.0)
    assert c.cost_after == pytest.approx(140.0)
    assert c.cost_delta < 0


# --- same footprint -------------------------------------------------------


def test_same_footprint_keeps_units_and_moves_capacity():
    c = convert_same_footprint(BASE, FAST, 500)
    assert c.from_units == c.to_units == 500
    assert c.capacity_before == pytest.approx(500.0)
    assert c.capacity_after == pytest.approx(700.0)
    assert c.capacity_delta == pytest.approx(200.0)


def test_same_footprint_reports_headroom_against_a_requirement():
    c = convert_same_footprint(BASE, FAST, 500, required_work_units=650)
    assert c.headroom_units == pytest.approx(50.0)
    assert c.covers_requirement is True

    short = convert_same_footprint(BASE, FAST, 400, required_work_units=650)
    assert short.headroom_units == pytest.approx(-90.0)
    assert short.covers_requirement is False


def test_lead_time_travels_with_the_target():
    """The number that makes a migration a schedule decision, not just a cost one."""
    assert convert_same_footprint(BASE, FAST, 10).lead_time_days == 45
    assert convert_same_footprint(FAST, BASE, 10).lead_time_days == 21


def test_negative_units_are_rejected():
    for fn in (convert_like_for_like, convert_same_footprint):
        with pytest.raises(ValueError, match="negative"):
            fn(BASE, FAST, -5)


def test_zero_units_is_valid_and_costs_nothing():
    c = convert_same_footprint(BASE, FAST, 0)
    assert c.capacity_after == 0 and c.cost_delta == 0 and c.cost_delta_pct == 0


def test_summary_reads_like_a_sentence():
    text = convert_same_footprint(BASE, FAST, 500).summary()
    assert "AMD-standard" in text and "Intel-highmem" in text and "45-day" in text


# --- against the dimensional model -------------------------------------------------


def test_sku_can_be_read_from_the_ontology(entities):
    sku = sku_from_dim(entities["dim_sku"], "GPU-class")
    assert sku.relative_performance == 2.6
    assert sku.lead_time_days == 30


def test_unknown_sku_names_what_is_available(entities):
    with pytest.raises(KeyError, match="Known:"):
        sku_from_dim(entities["dim_sku"], "Quantum-class")


def test_migrating_a_real_region_uses_its_real_deployment(entities):
    result = migrate_region(entities, "westeurope", "Intel-highmem")
    region = entities["dim_region"].set_index("Region").loc["westeurope"]
    assert result["deployed_units"] == pytest.approx(float(region["DeployedUnits"]))
    assert result["from_sku"] == region["SKUClass"]
    assert result["used_units_now"] > 0
    assert isinstance(result["conversion"], dict)
    assert result["lead_time_days"] == 45


def test_migrating_to_denser_hardware_covers_current_load(entities):
    """A region running hot should be covered by a denser class."""
    result = migrate_region(entities, "southcentralus", "GPU-class")
    assert result["conversion"]["capacity_delta"] > 0
    assert result["conversion"]["covers_requirement"] is True


def test_unknown_region_names_what_is_available(entities):
    with pytest.raises(KeyError, match="Known:"):
        migrate_region(entities, "marsnorth1", "GPU-class")
