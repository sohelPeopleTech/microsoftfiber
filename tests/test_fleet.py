"""The fleet tables: do they reconcile, and do they say what they claim?

The point of this grain is that a capacity manager can drill from a region to a
building to a single F-SKU without the numbers changing under them. That only
holds if the finer tables sum to the coarser ones, so most of what is asserted
here is arithmetic between levels rather than the content of any one table.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.synthdata import fleet  # noqa: E402


@pytest.fixture(scope="module")
def onto():
    from ontology.build import build
    return build(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                 ROOT / "data" / "synthetic", ROOT / "data" / "reference")


# --------------------------------------------------------------------------
# reconciliation between levels
# --------------------------------------------------------------------------


def test_capacity_units_sum_to_region_deployed_units(onto):
    """A region is its capacities, less at most one unbuyable remainder.

    Four units is an F2 and there is nothing smaller, so a region holding 2597
    units can allocate 2596 of them. More drift than that means the split lost
    capacity somewhere.
    """
    got = onto["dim_capacity"].groupby("Region")["DeployedUnits"].sum()
    want = onto["dim_region"].set_index("Region")["DeployedUnits"]
    for region in want.index:
        gap = float(want[region]) - float(got.get(region, 0))
        assert 0 <= gap < fleet.SKU_UNITS["F2"], (
            f"{region}: capacities account for {got.get(region, 0)} of "
            f"{want[region]} units, a gap of {gap}")


def test_capacity_usage_sums_to_the_region_reading(onto):
    """The drill-down cannot disagree with the screen above it.

    Per-capacity usage is built by distributing the region's used units, so this
    is exact by construction and not an approximation. The tolerance is for
    rounding each capacity to one decimal, nothing more.
    """
    caps = (onto["fact_capacity_usage_daily"]
            .groupby(["Region", "Date"])["UsedUnits"].sum())
    region = onto["fact_usage_daily"].set_index(["Region", "Date"])["UsedUnits"]
    joined = pd.DataFrame({"caps": caps, "region": region}).dropna()
    assert len(joined) > 1000, "expected a reading per region per day"
    worst = (joined["caps"] - joined["region"]).abs().max()
    assert worst < 2.0, f"capacity usage drifts from the region by {worst:.2f} units"


def test_no_capacity_is_more_than_full(onto):
    use = onto["fact_capacity_usage_daily"]
    over = use[use["UsedUnits"] > use["TotalUnits"] + 0.05]
    assert over.empty, f"{len(over)} readings exceed the capacity's own size"


def test_datacentre_units_are_read_from_capacities_not_apportioned(onto):
    """Sites within a region must stop being ten identical rows.

    The old model split a region's units evenly and copied its hardware down, so
    every site in a region matched every other. If that ever comes back the
    drill-down is decorative.
    """
    sites = onto["dim_datacentre"]
    for region, g in sites.groupby("Region"):
        assert g["DeployedUnits"].nunique() > 1, (
            f"{region}: all {len(g)} sites hold identical units")


def test_hardware_class_varies_within_a_region(onto):
    """The UI has always claimed a region mixes hardware. Now the data agrees."""
    caps = onto["dim_capacity"]
    mixed = sum(1 for _, g in caps.groupby("Region") if g["SKUClass"].nunique() > 1)
    assert mixed == caps["Region"].nunique(), (
        f"only {mixed} regions run more than one hardware class")


# --------------------------------------------------------------------------
# the tables themselves
# --------------------------------------------------------------------------


def test_the_sku_ladder_matches_the_admission_module(onto):
    """Two definitions of the Fabric ladder is one too many.

    `fleet` cannot import `admission` at generation time without a cycle, so the
    duplicate is asserted equal here instead of being prevented structurally.
    """
    from admission import F_SKUS, UNITS_PER_CU

    assert fleet.F_SKUS == F_SKUS
    assert fleet.UNITS_PER_CU == UNITS_PER_CU


def test_more_than_two_sku_sizes_are_in_use(onto):
    """The old model derived one SKU label per region and only ever produced
    F512 and F2048, which is not a fleet anyone buys."""
    mix = onto["dim_capacity"]["FabricSku"].nunique()
    assert mix >= 5, f"only {mix} distinct F-SKUs across the whole fleet"


def test_free_viewer_flag_matches_the_f64_rule(onto):
    """F64 is where a Free licence can read Power BI. Real, documented, and the
    reason an F32 next to an F64 is a commercial question."""
    caps = onto["dim_capacity"]
    for c in caps.itertuples():
        assert bool(c.SupportsFreeViewers) == (c.CapacityUnits >= 64), (
            f"{c.CapacityId}: {c.FabricSku} flagged {c.SupportsFreeViewers}")


def test_every_generated_row_says_it_is_generated(onto):
    """The rule the whole project rests on: nothing invented is unmarked."""
    for name in ("dim_capacity", "fact_capacity_usage_daily", "dim_hardware",
                 "dim_lead_time_history", "fact_operational_incident",
                 "fact_partial_grant"):
        df = onto[name]
        assert "IsSynthetic" in df.columns, f"{name} has no IsSynthetic column"
        assert df["IsSynthetic"].all(), f"{name} has unmarked rows"
        assert df["Provenance"].str.len().gt(20).all(), f"{name} has empty provenance"


def test_the_real_tables_are_marked_real(onto):
    """The two that are not invented must not be tarred with the same brush."""
    for name in ("dim_region_geography", "bridge_region_fabric_availability"):
        df = onto[name]
        assert not df["IsSynthetic"].any(), f"{name} is marked synthetic"
        assert df["Provenance"].str.startswith("REAL").all()


def test_every_region_has_real_coordinates(onto):
    geo = onto["dim_region_geography"].set_index("Region")
    for region in onto["dim_region"]["Region"]:
        assert region in geo.index, f"{region} has no coordinates"
        assert -90 <= float(geo.loc[region, "Latitude"]) <= 90
        assert -180 <= float(geo.loc[region, "Longitude"]) <= 180


def test_lead_time_history_agrees_with_todays_lead_time(onto):
    """The history adds the past; it must not restate the present.

    Every order-by date in Module 1 turns on `dim_sku.LeadTimeDays`. If the
    newest row of the history disagreed with it, two screens would give two
    answers to when a purchase is due.
    """
    current = onto["dim_sku"].set_index("SKUClass")["LeadTimeDays"].to_dict()
    latest = (onto["dim_lead_time_history"].sort_values("EffectiveFrom")
              .groupby("SKUClass").tail(1).set_index("SKUClass"))
    for cls, days in current.items():
        assert cls in latest.index, f"{cls} has no lead-time history"
        assert float(latest.loc[cls, "LeadTimeDays"]) == float(days), (
            f"{cls}: history ends at {latest.loc[cls, 'LeadTimeDays']}d but "
            f"dim_sku says {days}d")


def test_at_least_one_class_has_a_lead_time_that_materially_moved(onto):
    """Without drift somewhere, "buy earlier than the trigger" is unreachable."""
    from planning import LEAD_TIME_DRIFT_PCT, lead_time_drift

    drift = lead_time_drift(onto["dim_lead_time_history"])
    moved = [c for c, d in drift.items() if d["changePct"] >= LEAD_TIME_DRIFT_PCT]
    assert moved, f"no hardware class drifted: {drift}"


def test_incidents_are_not_a_restatement_of_utilisation(onto):
    """Operational incidents have to carry information utilisation does not.

    If incident counts simply tracked fullness they would add nothing, and the
    whole workload-change recommendation would be a second utilisation ranking
    wearing a different hat.
    """
    from planning import capacity_health

    h = capacity_health(onto["dim_capacity"], onto["fact_operational_incident"],
                        onto["fact_capacity_usage_daily"])
    corr = h["UtilisationPct"].corr(h["IncidentRate"])
    assert abs(corr) < 0.45, (
        f"incident rate correlates {corr:.2f} with utilisation -- it is not "
        f"telling you anything new")


def test_partial_grants_are_partial(onto):
    pg = onto["fact_partial_grant"]
    assert len(pg), "no partial grants generated"
    assert (pg["PartiallyGrantedUnits"] > 0).all()
    assert (pg["PartiallyGrantedUnits"] < pg["RequestedUnits"]).all(), (
        "a partial grant that gave everything is not partial")
    assert (pg["ShortfallUnits"]
            == pg["RequestedUnits"] - pg["PartiallyGrantedUnits"]).all()


def test_generation_is_deterministic():
    """Two runs must agree, or a figure quoted on Monday moves by Friday."""
    tickets = pd.read_excel(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                            sheet_name="ICM_Data")
    inv = pd.read_csv(ROOT / "data" / "synthetic" / "hardware_inventory.csv")
    usage = pd.read_csv(ROOT / "data" / "synthetic" / "capacity_usage.csv")
    a = fleet.generate_fleet(tickets, inv, usage)
    b = fleet.generate_fleet(tickets, inv, usage)
    for name in a:
        assert a[name].to_csv(index=False) == b[name].to_csv(index=False), name
