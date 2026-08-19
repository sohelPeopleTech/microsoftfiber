"""Module 2b -- the conversion planner.

The calculator says what a region would look like afterwards. These tests are
about what happens *during*, which is the part that decides whether the work can
be scheduled at all. The one that matters most is the first: a region at 98%
utilisation must come back CANNOT-CONVERT, because the cost saving is real and
the plan is still impossible.
"""

from __future__ import annotations

import pytest

import ontology
from module2 import plan_conversion
from module2.conversion import (
    DEFAULT_DATACENTRES,
    DEFAULT_SAFETY_MARGIN_PCT,
    datacentres_for,
)
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def onto():
    return ontology.build(WORKBOOK, "data/synthetic")


# --- the finding the module exists for ------------------------------------


def test_a_region_running_hot_cannot_convert(onto):
    """southcentralus: 806 of 826 units in use. 20 spare, a datacentre is 83."""
    plan = plan_conversion(onto, "southcentralus", "AMD-standard")
    assert plan.feasible is False
    assert plan.can_convert_a_whole_datacentre is False
    assert plan.max_offline_units == 0.0
    assert "cannot convert" in plan.summary


def test_the_blocker_says_which_numbers_caused_it(onto):
    """A no is only useful if it names the constraint."""
    plan = plan_conversion(onto, "southcentralus", "AMD-standard")
    assert "806" in plan.blocker and "826" in plan.blocker


def test_an_infeasible_plan_offers_what_would_change_it(onto):
    plan = plan_conversion(onto, "southcentralus", "AMD-standard")
    blocks = {o["blocks_on"] for o in plan.options}
    assert blocks == {"procurement", "demand", "time"}


def test_a_feasible_plan_offers_no_options(onto):
    """Nothing needs unblocking, so there is nothing to suggest."""
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    assert plan.feasible is True
    assert plan.options == []


# --- headroom arithmetic --------------------------------------------------


def test_headroom_is_deployed_minus_used(onto):
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    assert plan.headroom_units == pytest.approx(
        plan.deployed_units - plan.used_units, abs=0.11
    )


def test_the_safety_margin_is_held_back_from_what_can_go_offline(onto):
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    assert plan.safety_margin_units == pytest.approx(
        plan.deployed_units * DEFAULT_SAFETY_MARGIN_PCT / 100, abs=0.11
    )
    assert plan.max_offline_units == pytest.approx(
        plan.headroom_units - plan.safety_margin_units, abs=0.11
    )


def test_dropping_the_safety_margin_frees_capacity(onto):
    """Not a recommendation -- a check that the margin is what it claims to be."""
    tight = plan_conversion(onto, "westeurope", "AMD-standard")
    loose = plan_conversion(onto, "westeurope", "AMD-standard", safety_margin_pct=0)
    assert loose.max_offline_units > tight.max_offline_units


def test_a_region_with_zero_headroom_gets_no_tranche(onto):
    plan = plan_conversion(onto, "southcentralus", "AMD-standard")
    assert plan.tranche_size == 0.0
    assert plan.tranche_count == 0
    assert plan.tranches == []


# --- tranching ------------------------------------------------------------


def test_every_tranche_leaves_enough_capacity_for_current_load(onto):
    """The definition of feasible. If any step dips below load, it is a no."""
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    assert plan.tranches
    for step in plan.tranches:
        assert step["available_during"] >= step["required"]
        assert step["shortfall"] == 0.0
        assert step["safe"] is True


def test_tranches_add_up_to_the_units_being_converted(onto):
    plan = plan_conversion(onto, "westeurope", "AMD-standard", convert_datacentres=2)
    total = sum(t["units_out"] for t in plan.tranches)
    assert total == pytest.approx(plan.units_per_datacentre * 2, abs=0.11)


def test_converting_more_datacentres_takes_more_passes(onto):
    """Headroom caps how much is offline at once, so more work means more passes,
    not bigger ones."""
    one = plan_conversion(onto, "westeurope", "AMD-standard", convert_datacentres=1)
    three = plan_conversion(onto, "westeurope", "AMD-standard", convert_datacentres=3)
    assert one.tranche_count == 1, "one datacentre fits inside the headroom"
    assert three.tranche_count > one.tranche_count
    assert three.tranche_size <= three.max_offline_units + 1e-9


def test_a_tranche_never_exceeds_what_can_safely_go_offline(onto):
    for n in (1, 2, 5, 10):
        plan = plan_conversion(onto, "westeurope", "AMD-standard", convert_datacentres=n)
        assert plan.tranche_size <= plan.max_offline_units + 1e-9


# --- what the conversion buys ---------------------------------------------


def test_moving_to_less_capable_hardware_loses_capacity_and_saves_cost(onto):
    """GPU-class -> AMD-standard. Cheaper per unit, less work per unit."""
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    assert plan.capacity_delta < 0
    assert plan.cost_delta_pct < 0


def test_holding_capacity_flat_can_need_more_racks_than_came_out(onto):
    """The constraint a cost comparison never shows."""
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    assert plan.units_to_hold_capacity_flat > plan.units_per_datacentre
    assert plan.footprint_multiple > 1.0
    assert plan.fits_in_footprint is False
    assert "rack space" in plan.summary


def test_converting_to_the_same_class_changes_nothing(onto):
    same = str(onto["dim_region"].set_index("Region").loc["westeurope", "SKUClass"])
    plan = plan_conversion(onto, "westeurope", same)
    assert plan.capacity_delta == 0.0
    assert plan.cost_delta_pct == 0.0
    assert plan.footprint_multiple == 1.0
    assert plan.fits_in_footprint is True


def test_the_lead_time_is_the_targets_not_the_sources(onto):
    """Replacement hardware has to arrive before the old comes out."""
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    target = onto["dim_sku"].set_index("SKUClass").loc["AMD-standard", "LeadTimeDays"]
    assert plan.lead_time_days == int(target)


# --- the datacentre-count assumption --------------------------------------


def test_the_datacentre_split_is_stated_not_hidden(onto):
    """The whole answer scales with it, so it travels with the answer."""
    plan = plan_conversion(onto, "westeurope", "AMD-standard")
    assert plan.datacentres == DEFAULT_DATACENTRES
    assert plan.units_per_datacentre == pytest.approx(
        plan.deployed_units / plan.datacentres, abs=0.11
    )


def test_fewer_larger_datacentres_are_harder_to_convert(onto):
    """The same region, split four ways instead of ten."""
    many = plan_conversion(onto, "westeurope", "AMD-standard", datacentres=10)
    few = plan_conversion(onto, "westeurope", "AMD-standard", datacentres=4)
    assert few.units_per_datacentre > many.units_per_datacentre
    assert many.can_convert_a_whole_datacentre is True
    assert few.can_convert_a_whole_datacentre is False


def test_regions_without_real_inventory_fall_back_to_the_default():
    assert datacentres_for("a-region-with-no-inventory") == DEFAULT_DATACENTRES


# --- rejections -----------------------------------------------------------


def test_unknown_region_names_what_is_available(onto):
    with pytest.raises(KeyError, match="Known:"):
        plan_conversion(onto, "marsnorth1", "AMD-standard")


def test_cannot_convert_more_datacentres_than_the_region_has(onto):
    with pytest.raises(ValueError, match="between 1 and 10"):
        plan_conversion(onto, "westeurope", "AMD-standard", convert_datacentres=11)
    with pytest.raises(ValueError, match="between 1 and 10"):
        plan_conversion(onto, "westeurope", "AMD-standard", convert_datacentres=0)


def test_a_region_has_at_least_one_datacentre(onto):
    with pytest.raises(ValueError, match="at least one"):
        plan_conversion(onto, "westeurope", "AMD-standard", datacentres=0)
