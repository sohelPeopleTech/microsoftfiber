"""The Fabric capacity tables: do they reconcile, and are they Fabric's?

The point of this grain is that an admin can drill from a region to a building
to a single capacity without the numbers changing under them. So most of what is
asserted is arithmetic between levels rather than the content of any one table.

The rest guards the reframe. An earlier model described Azure infrastructure --
hardware classes, provisioning lead times, node failures. Fabric exposes none of
it, and there are tests here that fail if any of it comes back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.synthdata import fabric, fleet  # noqa: E402


@pytest.fixture(scope="module")
def entities():
    from dimensional.build import build
    return build(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                 ROOT / "data" / "synthetic", ROOT / "data" / "reference")


# --------------------------------------------------------------------------
# reconciliation between levels
# --------------------------------------------------------------------------


def test_capacity_units_sum_to_region_deployed_units(entities):
    """A region is its capacities, less at most one unbuyable remainder.

    Four units is an F2 and there is nothing smaller, so a region holding 2597
    units can allocate 2596 of them.
    """
    got = entities["dim_capacity"].groupby("Region")["DeployedUnits"].sum()
    want = entities["dim_region"].set_index("Region")["DeployedUnits"]
    for region in want.index:
        gap = float(want[region]) - float(got.get(region, 0))
        assert 0 <= gap < 4, f"{region}: {got.get(region, 0)} of {want[region]}"


def test_cu_available_is_the_sku_times_a_day(entities):
    """An F64 provides 64 CUs, so a day of it is 64 x 86,400 CU seconds.

    Real arithmetic, not a modelling choice, and everything downstream divides
    by it -- so if it drifts, every utilisation figure in the product is wrong
    by the same factor and nothing looks obviously broken.
    """
    cu = entities["fact_capacity_cu_daily"]
    sample = cu.sample(min(200, len(cu)), random_state=0)
    for r in sample.itertuples():
        assert r.CuSecondsAvailable == pytest.approx(r.CapacityUnits * 86_400, rel=1e-6)


def test_utilisation_is_consumed_over_available(entities):
    cu = entities["fact_capacity_cu_daily"]
    sample = cu.sample(min(200, len(cu)), random_state=1)
    for r in sample.itertuples():
        expected = r.CuSecondsConsumed / r.CuSecondsAvailable * 100
        assert r.UtilisationPct == pytest.approx(expected, abs=0.05)


def test_throttle_stage_matches_the_recorded_overage(entities):
    """Every row's stage has to follow from its own future-capacity minutes.

    If these ever disagree the screen shows one thing and the policy says
    another, which is the class of split this project keeps removing.
    """
    cu = entities["fact_capacity_cu_daily"]
    sample = cu.sample(min(500, len(cu)), random_state=2)
    for r in sample.itertuples():
        stage, _ = fabric.throttle_stage(r.FutureCapacityMinutes)
        assert r.ThrottleStage == stage, (
            f"{r.CapacityId} on {r.Date}: {r.FutureCapacityMinutes} min recorded "
            f"as {r.ThrottleStage}, policy says {stage}")


def test_bursting_is_allowed_and_recorded(entities):
    """Fabric lets operations use more compute than the SKU provides. A model
    that clamped at 100% would make smoothing invisible and throttling
    inexplicable."""
    cu = entities["fact_capacity_cu_daily"]
    assert (cu["UtilisationPct"] > 100).any(), "nothing ever bursts"
    over = cu[cu["UtilisationPct"] > 100]
    assert (over["FutureCapacityMinutes"] > 0).all(), (
        "bursting must produce overage, or smoothing is not being modelled")


def test_every_throttling_stage_actually_occurs(entities):
    """Including the worst. A product that describes background rejection but
    can never show it is asserting something nobody has checked."""
    seen = set(entities["fact_capacity_cu_daily"]["ThrottleStage"])
    for stage in ("none", "interactive_delay", "interactive_rejection",
                  "background_rejection"):
        assert stage in seen, f"{stage} never occurs anywhere in the fleet"


def test_throttling_events_match_the_throttled_days(entities):
    cu = entities["fact_capacity_cu_daily"]
    ev = entities["fact_throttling_event"]
    assert len(ev) == int((cu["ThrottleStage"] != "none").sum())


def test_interactive_delay_delays_rather_than_rejects(entities):
    """The first stage adds 20 seconds; it does not refuse anything. Counting
    rejections against it would overstate what users actually experienced."""
    ev = entities["fact_throttling_event"]
    delay = ev[ev["Stage"] == "interactive_delay"]
    assert len(delay)
    assert (delay["InteractiveRejected"] == 0).all()
    assert (delay["BackgroundRejected"] == 0).all()


def test_only_background_rejection_refuses_background_work(entities):
    ev = entities["fact_throttling_event"]
    not_bg = ev[ev["Stage"] != "background_rejection"]
    assert (not_bg["BackgroundRejected"] == 0).all(), (
        "background work refused before the 24-hour stage")


def test_datacentre_units_are_read_from_capacities_not_apportioned(entities):
    sites = entities["dim_datacentre"]
    for region, g in sites.groupby("Region"):
        assert g["DeployedUnits"].nunique() > 1, (
            f"{region}: all {len(g)} sites hold identical units")


# --------------------------------------------------------------------------
# the tables themselves
# --------------------------------------------------------------------------


def test_the_sku_ladder_matches_the_admission_module(entities):
    from admission import F_SKUS

    assert fabric.F_SKUS == F_SKUS
    assert fleet.F_SKUS == F_SKUS


def test_more_than_two_sku_sizes_are_in_use(entities):
    assert entities["dim_capacity"]["FabricSku"].nunique() >= 5


def test_free_viewer_flag_matches_the_f64_rule(entities):
    for c in entities["dim_capacity"].itertuples():
        assert bool(c.SupportsFreeViewers) == (c.CapacityUnits >= 64)


def test_every_workspace_belongs_to_a_real_capacity(entities):
    caps = set(entities["dim_capacity"]["CapacityId"])
    assert set(entities["dim_workspace"]["CapacityId"]) <= caps


def test_workspace_shares_add_up_on_each_capacity(entities):
    ws = entities["dim_workspace"]
    for cap, g in ws.groupby("CapacityId"):
        assert g["ShareOfCapacityPct"].sum() == pytest.approx(100.0, abs=0.6), cap


def test_some_capacities_are_dominated_by_one_workspace(entities):
    """Otherwise there is never anything to load balance and the
    recommendation is unreachable in practice."""
    ws = entities["dim_workspace"]
    multi = ws.groupby("CapacityId").filter(lambda g: len(g) > 1)
    dominant = multi.groupby("CapacityId")["ShareOfCapacityPct"].max()
    assert (dominant >= 55).any()


def test_every_generated_row_says_it_is_generated(entities):
    for name in ("dim_capacity", "fact_capacity_cu_daily", "dim_workspace",
                 "fact_throttling_event", "fact_partial_grant"):
        df = entities[name]
        assert "IsSynthetic" in df.columns, f"{name} has no IsSynthetic column"
        assert df["IsSynthetic"].all(), f"{name} has unmarked rows"
        assert df["Provenance"].str.len().gt(20).all(), f"{name} has empty provenance"


def test_the_real_tables_are_marked_real(entities):
    for name in ("dim_region_geography", "bridge_region_fabric_availability"):
        df = entities[name]
        assert not df["IsSynthetic"].any()
        assert df["Provenance"].str.startswith("REAL").all()


def test_every_region_has_real_coordinates(entities):
    geo = entities["dim_region_geography"].set_index("Region")
    for region in entities["dim_region"]["Region"]:
        assert region in geo.index
        assert -90 <= float(geo.loc[region, "Latitude"]) <= 90
        assert -180 <= float(geo.loc[region, "Longitude"]) <= 180


def test_every_datacentre_is_placed_near_its_region(entities):
    """The extract has no per-site location, so each capacity pool is its region's
    real point plus a small generated offset. Every site must land within a
    metro-sized radius of its region -- a bad offset that put a building an ocean
    away would read as a real location and mislead."""
    import math

    from dimensional.build import SITE_SPREAD_KM, attach_datacentre_coordinates

    geo = entities["dim_region_geography"].set_index("Region")
    sites = entities["dim_datacentre"]
    assert sites["Latitude"].notna().all() and sites["Longitude"].notna().all()

    for s in sites.itertuples():
        blat = float(geo.loc[s.Region, "Latitude"])
        blon = float(geo.loc[s.Region, "Longitude"])
        km = math.hypot(
            (s.Latitude - blat) * 111.0,
            (s.Longitude - blon) * 111.0 * math.cos(math.radians(blat)),
        )
        assert km <= SITE_SPREAD_KM + 1e-6, f"{s.DatacentreId} is {km:.0f} km from {s.Region}"

    # Deterministic: two builds put every site in the same place.
    again = attach_datacentre_coordinates(
        entities["dim_datacentre"][["DatacentreId", "Region"]].copy(),
        entities["dim_region_geography"],
    )
    assert again["Latitude"].tolist() == sites["Latitude"].tolist()


# --------------------------------------------------------------------------
# the reframe
# --------------------------------------------------------------------------


def test_the_azure_hardware_model_is_gone(entities):
    """Hardware classes, lead-time history and node incidents described Azure
    infrastructure Fabric does not expose. If these tables come back, so has a
    model that tells a Fabric customer things that are not true of Fabric."""
    for gone in ("dim_hardware", "dim_lead_time_history",
                 "fact_operational_incident", "fact_capacity_usage_daily"):
        assert gone not in entities.tables, f"{gone} is back"


def test_capacities_carry_no_hardware_attributes(entities):
    caps = entities["dim_capacity"]
    for column in ("SKUClass", "Vendor", "Model", "NodeCount", "Nodes"):
        assert column not in caps.columns, f"dim_capacity still carries {column}"


def test_partial_grants_are_partial(entities):
    pg = entities["fact_partial_grant"]
    assert len(pg)
    assert (pg["PartiallyGrantedUnits"] > 0).all()
    assert (pg["PartiallyGrantedUnits"] < pg["RequestedUnits"]).all()


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
