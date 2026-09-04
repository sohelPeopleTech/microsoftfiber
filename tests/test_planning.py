"""The four Fabric recommendations: do they make the case they claim to?

Each exists because the utilisation figure cannot produce its answer on its own.
So most of what is asserted here is that the answer is not a utilisation ranking
in disguise -- that a scale-up is genuinely throttling or genuinely out of
headroom, that a scale-down is genuinely idle and never something that
throttled, and that the licensing case is about the SKU rather than the load.

The throttling thresholds are Microsoft's, not this project's, so they are
asserted against the published policy rather than against whatever the code
currently does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from planning import (  # noqa: E402
    DOMINANT_WORKLOAD_PCT,
    SITE_TYPE_SHARED,
    FREE_VIEWER_CU,
    IDLE_PCT,
    STAGE_RANK,
    SUSTAINED_HIGH_PCT,
    THROTTLED_DAYS_FOR_SCALE,
    capacity_health,
    crosses_slow_boundary,
    next_sku,
    previous_sku,
    throttle_stage,
)
from planning import recommend  # noqa: E402


@pytest.fixture(scope="module")
def entities():
    from dimensional.build import build
    return build(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                 ROOT / "data" / "synthetic", ROOT / "data" / "reference")


# --------------------------------------------------------------------------
# the published policy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("minutes,expected", [
    (0, "none"), (10, "none"),
    (10.5, "interactive_delay"), (60, "interactive_delay"),
    (61, "interactive_rejection"), (24 * 60, "interactive_rejection"),
    (24 * 60 + 1, "background_rejection"), (99_999, "background_rejection"),
])
def test_throttling_stages_match_microsofts_thresholds(minutes, expected):
    """Ten minutes free, then delay, then interactive rejection, then all of it.

    https://learn.microsoft.com/en-us/fabric/enterprise/throttling
    These boundaries are policy, not a choice this project gets to make, so they
    are pinned at the exact minute rather than approximately.
    """
    stage, effect = throttle_stage(minutes)
    assert stage == expected, f"{minutes} min should be {expected}, got {stage}"
    assert effect, "every stage has to say what a user actually experiences"


def test_the_stages_are_ordered_worst_last():
    assert (STAGE_RANK["none"] < STAGE_RANK["interactive_delay"]
            < STAGE_RANK["interactive_rejection"] < STAGE_RANK["background_rejection"])


def test_the_sku_ladder_steps_both_ways():
    assert next_sku("F64") == "F128"
    assert previous_sku("F64") == "F32"
    assert next_sku("F2048") is None, "nothing above the top of the ladder"
    assert previous_sku("F2") is None, "nothing below the bottom"


def test_the_slow_scaling_boundary_is_flagged():
    """Microsoft notes scaling across F256/F512 can be slower, which is worth
    saying on a recommendation that does it."""
    assert crosses_slow_boundary("F256", "F512")
    assert not crosses_slow_boundary("F64", "F128")
    assert not crosses_slow_boundary("F512", "F1024")


# --------------------------------------------------------------------------
# scale up
# --------------------------------------------------------------------------


def test_scale_up_covers_throttling_and_no_headroom(entities):
    """Two different cases, and the copy has to tell them apart.

    A capacity at interactive rejection is refusing users now. One at ninety per
    cent that has never throttled is not hurting anyone yet but has nothing left
    to absorb a surge -- and Fabric's overage protection is only ten minutes.
    """
    recs = recommend.scale_up(entities)
    assert recs, "nothing to scale in an estate that throttles somewhere"
    throttling = [r for r in recs if r.evidence["isThrottling"]]
    airless = [r for r in recs if not r.evidence["isThrottling"]]
    assert throttling, "no throttling capacity surfaced"
    for r in throttling:
        assert r.evidence["throttledDays"] >= THROTTLED_DAYS_FOR_SCALE
        assert r.evidence["worstStage"] != "none"
    for r in airless:
        assert r.evidence["meanUtilisationPct"] >= SUSTAINED_HIGH_PCT


def test_scale_up_never_promises_an_order_date(entities):
    """Scaling an F SKU is immediate. An earlier model raised purchase orders
    against provisioning lead times, which Fabric does not have -- if an order
    date ever reappears here, the Azure model has crept back in."""
    for r in recommend.scale_up(entities):
        assert r.evidence.get("immediate") is True
        assert "orderByDate" not in r.evidence
        assert "leadTime" not in str(r.evidence)
        assert "order" not in r.headline.lower()


def test_scale_up_always_names_a_real_next_sku(entities):
    from planning import F_SKUS

    for r in recommend.scale_up(entities):
        e = r.evidence
        assert e["scaleTo"] in F_SKUS
        assert e["scaleToUnits"] == F_SKUS[e["scaleTo"]]
        assert e["scaleToUnits"] > e["capacityUnits"]


# --------------------------------------------------------------------------
# load balance
# --------------------------------------------------------------------------


def test_a_move_needs_a_dominant_workload_and_somewhere_to_go(entities):
    for r in recommend.load_balance(entities):
        e = r.evidence
        assert e["workloadSharePct"] >= DOMINANT_WORKLOAD_PCT
        assert e["workloadsOnCapacity"] > 1, (
            "moving the only workload empties the capacity -- that is "
            "consolidation, not load balancing")
        assert e["moveTo"] and e["moveTo"] != r.target


def test_a_move_is_only_ever_offered_on_a_shared_site(entities):
    """A dedicated site holds a capacity per workload, each at 100% of it.

    Share alone would fire on every one of them and recommend moving a workload
    off the capacity that exists to run it -- which does not rebalance anything,
    it empties one capacity into another. Half the fleet is dedicated, so this
    is the difference between a rebalance list and a list of every capacity in
    the estate.
    """
    sites = entities["dim_datacentre"].set_index("DatacentreId")["SiteType"]
    for r in recommend.load_balance(entities):
        assert r.evidence["siteType"] == SITE_TYPE_SHARED, r.target
        assert sites[r.evidence["datacentre"]] == SITE_TYPE_SHARED, r.target


def test_a_move_never_targets_a_throttling_capacity(entities):
    """Rebalancing onto something already refusing operations moves the problem."""
    health = recommend._health(entities)
    throttling = set(health[health["ThrottledDays"] > 0]["CapacityId"])
    for r in recommend.load_balance(entities):
        assert r.evidence["moveTo"] not in throttling, (
            f"{r.target} would move onto {r.evidence['moveTo']}, which throttles")


def test_a_move_stays_inside_the_region(entities):
    caps = entities["dim_capacity"].set_index("CapacityId")
    for r in recommend.load_balance(entities):
        assert caps.loc[r.evidence["moveTo"], "Region"] == r.evidence["region"]


# --------------------------------------------------------------------------
# scale down
# --------------------------------------------------------------------------


def test_scale_down_never_touches_anything_that_throttled(entities):
    """A capacity quiet six days a week and overloaded on the seventh is sized
    for the seventh. Averages alone would recommend shrinking it."""
    for r in recommend.scale_down(entities):
        assert r.evidence["throttledDays"] == 0
        assert r.evidence["meanUtilisationPct"] < IDLE_PCT


def test_scale_down_leaves_room_for_the_observed_peak(entities):
    """Halving a capacity whose peak would then exceed its ceiling trades a
    standing cost for a throttling incident."""
    for r in recommend.scale_down(entities):
        assert r.evidence["peakAfterScaleDownPct"] < SUSTAINED_HIGH_PCT


def test_scale_down_warns_when_it_would_cross_below_f64(entities):
    """Saving on compute while forcing every viewer onto a Pro licence can cost
    more than it saves, so the recommendation has to say so."""
    for r in recommend.scale_down(entities):
        if r.evidence["losesFreeViewers"]:
            assert "Pro" in r.detail or "PPU" in r.detail


# --------------------------------------------------------------------------
# licensing
# --------------------------------------------------------------------------


def test_licensing_only_flags_the_rung_below_the_cliff(entities):
    for r in recommend.licensing(entities):
        assert r.evidence["capacityUnits"] < FREE_VIEWER_CU
        assert next_sku(r.evidence["fabricSku"]) == "F64"


def test_licensing_cites_the_rule_and_its_source(entities):
    recs = recommend.licensing(entities)
    assert recs
    for r in recs[:5]:
        assert "F64" in r.evidence["rule"]
        assert r.evidence["source"].startswith("https://learn.microsoft.com")


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------


def test_capacity_health_reads_a_window_not_a_day(entities):
    """A single throttled day is not evidence: capacities are self-healing and
    burndown clears a surge."""
    h = capacity_health(entities["dim_capacity"], entities["fact_capacity_cu_daily"],
                        entities["fact_throttling_event"], window_days=30)
    assert (h["WindowDays"] > 1).all()
    assert (h["ThrottledDays"] <= h["WindowDays"]).all()


def test_bursting_is_not_treated_as_a_fault(entities):
    """Utilisation over 100% is normal in Fabric -- it is what smoothing exists
    for. If every bursting capacity were flagged, the list would be noise."""
    h = recommend._health(entities)
    bursting = h[h["PeakUtilisationPct"] > 100]
    assert len(bursting), "expected some capacities to burst"
    quiet = bursting[bursting["ThrottledDays"] == 0]
    assert len(quiet), "every bursting capacity is flagged -- bursting is not a fault"
    flagged = {r.target for r in recommend.scale_up(entities)}
    assert not (set(quiet[quiet["MeanUtilisationPct"] < SUSTAINED_HIGH_PCT]["CapacityId"])
                & flagged)


def test_every_recommendation_carries_its_evidence(entities):
    for r in recommend.all_recommendations(entities):
        assert r["headline"] and r["detail"]
        assert len(r["detail"]) > 60, f"{r['target']}: detail is a stub"
        assert r["evidence"].get("region")
        assert r["kind"] in ("scale_up", "load_balance", "scale_down", "licensing")


def test_recommendations_are_ordered_by_urgency(entities):
    urg = [r["urgency"] for r in recommend.all_recommendations(entities)]
    assert urg == sorted(urg, reverse=True)


def test_no_azure_hardware_vocabulary_survives(entities):
    """Fabric exposes no hardware, no vendors and no lead times. If any of those
    words reappear in what a user reads, the old model has crept back."""
    import re

    # Whole words only. A substring check flagged "Real-Time Intelligence" for
    # containing "intel", which is a Fabric workload and exactly the vocabulary
    # this is meant to protect.
    banned = ["intel", "amd", "vendor", "lead time", "provisioning",
              "node", "nodes", "poweredge", "proliant", "gpu"]
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in banned) + r")\b")
    for r in recommend.all_recommendations(entities):
        text = f"{r['headline']} {r['detail']}".lower()
        hit = pattern.search(text)
        assert not hit, (
            f"{r['target']} uses the Azure vocabulary {hit.group(0)!r}: "
            f"...{text[max(0, hit.start() - 50):hit.end() + 50]}...")


def test_no_recommendation_smuggles_markup_into_its_text(entities):
    """`detail` and `headline` are rendered by three separate templates and all
    three escape them, so a <b> placed here for emphasis arrives on screen as
    the literal characters in front of the words it was meant to emphasise.

    It shipped that way: "This capacity reached <b>background rejection</b>."
    Emphasis belongs to the renderer, which knows whether it is drawing HTML, a
    plain-text card or a prompt for the assistant.
    """
    import re

    tag = re.compile(r"</?[a-zA-Z][^>]*>")
    for r in recommend.all_recommendations(entities):
        for field in ("headline", "detail"):
            hit = tag.search(r[field])
            assert not hit, (
                f"{r['target']} {field} contains markup {hit.group(0)!r}, which "
                f"every renderer escapes: ...{r[field][max(0, hit.start()-40):hit.end()+40]}...")
