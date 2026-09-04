"""Idle capacity in a region that refused somebody else's request.

The engine exists to answer one question an executive asked: why did we turn a
customer away in a region where another customer was sitting on capacity nobody
was using. Most of what is asserted here is that the answer stays honest --
that it never proposes taking capacity from something that needs it, never
implies a transfer Fabric cannot perform, and never disagrees with the failure
counts every other screen prints.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT / "src"))

import api  # noqa: E402
from planning import IDLE_PCT, SUSTAINED_HIGH_PCT, F_SKUS  # noqa: E402
from planning import reclaim as reclaim_mod  # noqa: E402


@pytest.fixture(scope="module")
def recs():
    entities = api.get_entities()
    priced = api._ticket_rows(api.get_module5().priced, slice(None))
    return reclaim_mod.reclaim(entities, priced)


# --------------------------------------------------------------------------
# it must not take capacity from something that needs it
# --------------------------------------------------------------------------


def test_nothing_that_throttled_is_ever_reclaimed(recs):
    """A capacity quiet six days a week and overloaded on the seventh is sized
    for the seventh. Reclaiming from it would cause the outage it is meant to
    prevent."""
    health = api._capacity_health().set_index("CapacityId")
    for r in recs:
        row = health.loc[r.evidence["capacityId"]]
        assert row["ThrottledDays"] == 0, r.evidence["capacityId"]
        assert row["MeanUtilisationPct"] < IDLE_PCT


def test_the_whole_next_rung_down_still_fits(recs):
    """Capacity is not divisible. The only move is a whole rung, so the measured
    peak has to survive the step -- an F64 running 40 CU cannot give up 24."""
    for r in recs:
        e = r.evidence
        assert e["stepToUnits"] == F_SKUS[e["stepTo"]]
        assert e["stepToUnits"] < e["capacityUnits"]
        assert e["releasesUnits"] == e["capacityUnits"] - e["stepToUnits"]
        assert e["peakAfterPct"] < SUSTAINED_HIGH_PCT, (
            f"{e['capacityId']} would peak at {e['peakAfterPct']}% on "
            f"{e['stepTo']}, which is not spare capacity")


def test_a_capacity_using_more_than_the_rung_below_is_never_offered():
    """The case the executive asked about directly: F64 using 40 CU. F32 is the
    only step down and 32 < 40, so nothing is reclaimable however much of the
    64 looks unused."""
    options = reclaim_mod._idle_with_room  # noqa: SLF001 -- the unit under test
    entities = api.get_entities()
    for idle in options(entities, 30):
        used = idle["capacityUnits"] * idle["meanPct"] / 100.0
        assert used < idle["stepToUnits"], (
            f"{idle['capacityId']} uses {used:.0f} CU but is offered a step to "
            f"{idle['stepToUnits']} CU")


# --------------------------------------------------------------------------
# it must agree with the rest of the product
# --------------------------------------------------------------------------


def test_the_refusal_counts_match_every_other_screen(recs):
    """This module counts failures itself, which is how the capacity-policy
    simulator once reported 45 where everything else reported 30. Its first run
    reported six refusals in eastus2 against the Overview's five."""
    overview = {r["region"]: r for r in api.overview()["regions"]}
    for r in recs:
        e = r.evidence
        seen = overview[e["region"]]
        assert e["refusedRequests"] == seen["failed"], e["region"]
        assert e["exposureUnblocked"] == pytest.approx(seen["exposure"], abs=1.0)


def test_both_halves_are_required(recs):
    """Idle capacity alone is a scale_down and scale_down already says it; a
    refused request alone is a scale_up. This is only the overlap."""
    for r in recs:
        e = r.evidence
        assert e["releasesUnits"] > 0
        assert e["refusedRequests"] > 0
        assert e["shortfallUnits"] > 0


def test_it_is_worth_the_call(recs):
    """A reclaim covering two per cent of a shortfall is not worth an account
    conversation, and a list of those buries the one that is."""
    for r in recs:
        assert r.evidence["coversPct"] >= reclaim_mod.MIN_COVERAGE_PCT


# --------------------------------------------------------------------------
# it must not describe a product that does not exist
# --------------------------------------------------------------------------


def test_no_recommendation_implies_a_transfer_between_customers(recs):
    """A Fabric capacity belongs to its tenant. Any wording suggesting it can be
    handed to another customer describes something that cannot be done."""
    import re

    banned = re.compile(r"\b(transfer|hand over|reassign to|give .{0,12}capacity to|"
                        r"move .{0,20}capacity to another)\b", re.I)
    for r in recs:
        text = f"{r.headline} {r.detail}"
        hit = banned.search(text)
        assert not hit, f"{r.target}: implies a transfer — {hit.group(0)!r}"
        assert "conversation" in text.lower(), (
            f"{r.target} does not say this is a conversation rather than an action")
        assert r.evidence["isConversationNotAction"] is True


def test_every_recommendation_names_the_account_and_an_owner(recs):
    """The output is a call for somebody to make. Without both it is trivia."""
    for r in recs:
        e = r.evidence
        assert e["heldByName"], f"{r.target} does not name who holds the capacity"
        assert e["owner"], f"{r.target} does not name who makes the call"


def test_the_f64_licensing_cliff_is_stated_when_it_applies(recs):
    """Stepping below F64 puts every Power BI viewer on a Pro or PPU licence,
    which routinely costs the account more than the capacity saves."""
    for r in recs:
        if r.evidence["losesFreeViewers"]:
            assert "Pro or PPU" in r.detail, r.target


def test_it_is_ranked_by_what_it_unblocks_not_by_capacity_units(recs):
    """CU released is an engineering figure. The reader is an executive deciding
    whether a call is worth making, and that turns on the money."""
    if len(recs) < 2:
        pytest.skip("need two to compare an ordering")
    urgencies = [r.urgency for r in recs]
    assert urgencies == sorted(urgencies, reverse=True)


# --------------------------------------------------------------------------
# the ownership link it depends on
# --------------------------------------------------------------------------


def test_every_capacity_has_a_holder():
    caps = api.get_entities()["dim_capacity"]
    assert "SubscriptionId" in caps.columns
    assert (caps["SubscriptionId"].astype(str).str.len() > 0).all()


def test_holders_are_real_subscriptions_from_the_extract():
    """The allocation is generated; the accounts are not. A capacity held by an
    account that does not exist would be an invention on top of an invention."""
    entities = api.get_entities()
    real = set(entities["dim_subscription"]["SubscriptionId"].astype(str))
    held = set(entities["dim_capacity"]["SubscriptionId"].astype(str))
    assert held <= real, sorted(held - real)[:5]


def test_an_account_only_holds_capacity_where_it_has_asked_for_some():
    """Weighting by a region's own request history is what makes the generated
    allocation resemble the real link. An account holding capacity in a region
    it has never touched would not."""
    entities = api.get_entities()
    fact = entities["fact_capacity_request"]
    asked = {(str(s), str(r)) for s, r in
             zip(fact["SubscriptionId"], fact["Region"])}
    caps = entities["dim_capacity"]
    regions_with_requests = set(fact["Region"].astype(str))
    for c in caps.itertuples():
        if str(c.Region) not in regions_with_requests:
            continue
        assert (str(c.SubscriptionId), str(c.Region)) in asked, (
            f"{c.CapacityId} is held by an account that has never requested "
            f"capacity in {c.Region}")


def test_adding_holders_moved_no_existing_figure():
    """The owner column was added to a table every capacity screen reads. If it
    changed a total, every reviewed number in the product moved with it."""
    caps = api.get_entities()["dim_capacity"]
    # 265 since every site became Shared (one capacity) or Dedicated (two to
    # five); it was 317 when a site's capacities were a decomposition of a unit
    # budget. The number is here as a canary -- if the owner column ever adds or
    # drops a row, every capacity figure in the product moves with it.
    assert len(caps) == 265
    assert int(caps["CapacityUnits"].sum()) == 15148
