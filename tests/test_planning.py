"""The three recommendations: do they make the case they claim to?

Each engine exists because a utilisation figure cannot produce its answer. So
what is asserted here is mostly that the answer is not a utilisation ranking in
disguise -- that an early purchase is genuinely below its trigger, that a
hardware move is genuinely on a capacity with room, and that the licensing case
is genuinely about the SKU and not about how full it is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from planning import (  # noqa: E402
    CROWDED_PCT,
    FREE_VIEWER_CU,
    UNHEALTHY_MULTIPLE,
    adjusted_trigger,
    better_hardware,
    capacity_health,
    next_sku,
)
from planning import recommend  # noqa: E402


@pytest.fixture(scope="module")
def onto():
    from ontology.build import build
    return build(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                 ROOT / "data" / "synthetic", ROOT / "data" / "reference")


# --------------------------------------------------------------------------
# the trigger
# --------------------------------------------------------------------------


def test_the_order_window_is_the_whole_lead_time_not_just_the_drift():
    """An order goes in when time-to-trigger falls below the *whole* wait.

    The first draft subtracted only the days a lead time had drifted, which
    understated the window by however long the lead time already was and left
    the recommendation firing for almost nothing. At 0.2 points a day and a
    45-day wait the window is nine points, not the two that 25 days of drift
    would give.
    """
    raise_at, why = adjusted_trigger(85.0, lead_days=45, drift=None,
                                     growth_pct_per_day=0.2)
    assert raise_at == pytest.approx(85.0 - 45 * 0.2, abs=0.05)
    assert "45 days to arrive" in why


def test_a_flat_region_needs_no_early_order():
    """Nothing is approaching a trigger it is not moving toward."""
    raise_at, why = adjusted_trigger(85.0, lead_days=45, drift=None,
                                     growth_pct_per_day=0.0)
    assert raise_at == 85.0 and why == ""


def test_the_trigger_is_never_argued_below_half():
    """Past that "buy sooner" becomes "buy constantly", which is not advice."""
    raise_at, _ = adjusted_trigger(80.0, lead_days=900, growth_pct_per_day=0.5,
                                   drift=None)
    assert raise_at == 40.0


def test_drift_is_explained_only_when_it_qualifies():
    """A headline that claims the wait grew must be backed by the detail.

    An earlier version said "the wait has grown" for a class that had drifted
    fifteen per cent, below the threshold, so no drift sentence was generated
    and the claim stood on nothing.
    """
    small = {"was": 26.0, "now": 30.0, "changePct": 15.0, "changeDays": 4.0,
             "since": "2025-03-01", "supplier": "Dell"}
    big = {"was": 20.0, "now": 45.0, "changePct": 125.0, "changeDays": 25.0,
           "since": "2025-03-01", "supplier": "HPE"}
    _, quiet = adjusted_trigger(85.0, 30, small, 0.2)
    _, loud = adjusted_trigger(85.0, 45, big, 0.2)
    assert "used to be" not in quiet
    assert "used to be" in loud and "125" in loud


# --------------------------------------------------------------------------
# procurement
# --------------------------------------------------------------------------


def test_some_purchases_are_raised_below_their_trigger(onto):
    """The case review asked for, and the one a threshold cannot make."""
    early = [r for r in recommend.procurement(onto)
             if r.evidence["raisedEarly"]]
    assert early, "no purchase is raised before its trigger -- lead time is inert"
    for r in early:
        e = r.evidence
        assert e["utilisationPct"] < e["standardTriggerPct"], (
            f"{r.target} is flagged early at {e['utilisationPct']}% against a "
            f"{e['standardTriggerPct']}% trigger")


def test_at_least_one_early_raise_is_driven_by_drift(onto):
    early = [r for r in recommend.procurement(onto)
             if r.evidence["raisedEarly"] and r.evidence["leadTimeDrifted"]]
    assert early, "no early raise cites a lead time that actually moved"
    r = early[0]
    assert r.evidence["leadTimeWasDays"] < r.evidence["leadTimeDays"]
    assert "used to be" in r.detail


def test_the_headline_never_claims_drift_the_evidence_lacks(onto):
    for r in recommend.procurement(onto):
        if "wait has grown" in r.headline:
            assert r.evidence["leadTimeDrifted"], (
                f"{r.target}: headline claims drift, evidence says none")


def test_procurement_defaults_to_each_site_own_threshold(onto):
    """A flat trigger flagged two capacities in three.

    This fleet runs at eighty to ninety per cent, so 70% is not a trigger, it is
    a description of the estate. Forcing one number must still be possible, and
    must visibly differ from the per-site default.
    """
    per_site = recommend.procurement(onto)
    forced = recommend.procurement(onto, trigger_pct=70.0)
    assert len(forced) > len(per_site), (
        "forcing a 70% trigger did not widen the net, so the per-site default "
        "is probably not being used")


def test_all_recommendations_agrees_with_the_engines(onto):
    """Two entry points must not give two answers.

    `all_recommendations` once passed a flat trigger of its own, quietly
    overriding the per-site thresholds and inflating procurement by half.
    """
    combined = recommend.all_recommendations(onto)
    counts = {}
    for r in combined:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    assert counts.get("procurement", 0) == len(recommend.procurement(onto))
    assert counts.get("workload_change", 0) == len(recommend.workload_change(onto))
    assert counts.get("licensing", 0) == len(recommend.licensing(onto))


# --------------------------------------------------------------------------
# workload change
# --------------------------------------------------------------------------


def test_every_move_is_on_a_capacity_with_room(onto):
    """The whole point: capacity is not the problem on these.

    A crowded capacity that also has incidents is a buying case, and procurement
    already covers it. If a move ever appears above the crowding line the two
    recommendations are arguing with each other.
    """
    for r in recommend.workload_change(onto):
        assert r.evidence["utilisationPct"] < CROWDED_PCT, (
            f"{r.target} is {r.evidence['utilisationPct']}% full -- that is a "
            f"purchase, not a move")


def test_every_move_is_measurably_worse_than_the_fleet(onto):
    for r in recommend.workload_change(onto):
        assert r.evidence["rateVsFleet"] >= UNHEALTHY_MULTIPLE
        assert r.evidence["incidents"] >= 3, "moving on one or two incidents is noise"


def test_a_move_never_recommends_less_memory(onto):
    """Trading an incident problem for a capability problem is not a fix."""
    hw = onto["dim_hardware"].set_index("SKUClass")
    for r in recommend.workload_change(onto):
        now = hw.loc[r.evidence["skuClass"], "MemoryGB"]
        assert r.evidence["moveTo"]["memoryGB"] >= now, (
            f"{r.target}: moving from {now}GB to "
            f"{r.evidence['moveTo']['memoryGB']}GB")


def test_the_best_hardware_has_nowhere_better_to_go():
    """`better_hardware` returning None is an answer, not a gap."""
    import pandas as pd
    from src.synthdata.fleet import hardware_models

    hw = hardware_models()
    best = hw.sort_values("RelativeIncidentRate").iloc[0]["SKUClass"]
    assert better_hardware(best, hw) is None, (
        f"{best} is the most reliable class but something was recommended over it")


def test_incident_rate_is_shrunk_toward_the_fleet(onto):
    """Unshrunk, a one-node capacity with a bad month outranks an estate.

    The same empirical-Bayes device already applied to site failure rates. A
    small capacity's rate must sit closer to the fleet than its raw rate does.
    """
    h = capacity_health(onto["dim_capacity"], onto["fact_operational_incident"],
                        onto["fact_capacity_usage_daily"])
    small = h[(h["Nodes"] <= 1) & (h["Incidents"] >= 4)]
    assert len(small), "no small, busy capacity to check shrinkage against"
    for c in small.itertuples():
        assert abs(c.IncidentRate - c.FleetRate) < abs(c.RawRate - c.FleetRate), (
            f"{c.CapacityId}: shrinkage moved the rate away from the fleet")


# --------------------------------------------------------------------------
# licensing
# --------------------------------------------------------------------------


def test_licensing_only_flags_the_rung_below_the_cliff(onto):
    """Telling an F2 to become an F64 is arithmetic, not advice."""
    for r in recommend.licensing(onto):
        assert r.evidence["capacityUnits"] < FREE_VIEWER_CU
        assert next_sku(r.evidence["fabricSku"]) == "F64"


def test_licensing_cites_the_rule_and_its_source(onto):
    """A commercial recommendation that cannot be checked will not be believed."""
    recs = recommend.licensing(onto)
    assert recs
    for r in recs[:5]:
        assert "F64" in r.evidence["rule"]
        assert r.evidence["source"].startswith("https://learn.microsoft.com")


def test_licensing_is_not_a_utilisation_ranking(onto):
    """These capacities are flagged for their size, not their fullness.

    If the licensing list were sorted by how full things are it would be a
    worse copy of the procurement list.
    """
    caps = onto["dim_capacity"].set_index("CapacityId")
    flagged = {r.target for r in recommend.licensing(onto)}
    assert flagged
    assert {caps.loc[t, "FabricSku"] for t in flagged} == {"F32"}


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------


def test_every_recommendation_carries_its_evidence(onto):
    """Review rejected advice that asserted without showing its working."""
    for r in recommend.all_recommendations(onto):
        assert r["headline"] and r["detail"]
        assert len(r["detail"]) > 60, f"{r['target']}: detail is a stub"
        assert r["evidence"].get("region"), f"{r['target']}: no region"
        assert r["kind"] in ("procurement", "workload_change", "licensing")


def test_recommendations_are_ordered_by_urgency(onto):
    urg = [r["urgency"] for r in recommend.all_recommendations(onto)]
    assert urg == sorted(urg, reverse=True)
