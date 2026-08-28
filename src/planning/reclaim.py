"""Idle capacity in a region that refused somebody else's request.

THE QUESTION THIS ANSWERS
    A region turned down Customer B while Customer A sat on capacity nobody was
    using. Microsoft carried the revenue loss, and possibly the account, for a
    shortage that was not real. Which regions is that true of, and which account
    do we call?

    Every other engine in this module looks at one capacity and asks what to do
    about it. This one is the only one that looks at two parties at once: the
    account holding capacity, and the accounts that were refused beside it.

WHAT IT CANNOT DO, AND WHY THE WORDING MATTERS
    A Fabric capacity belongs to a tenant. There is no mechanism to hand
    Customer A's capacity to Customer B, and any screen implying otherwise would
    be describing a product that does not exist. What is real is narrower and
    still useful: A scales down what they are not using, the Capacity Units
    return to the region, and B's next request can be granted from them.

    So this recommends a conversation, not a transfer. Microsoft cannot scale a
    customer's capacity either -- the customer owns that resource -- which is
    why the output names an account and an owner rather than an action.

WHY SO FEW OF THESE EXIST
    Capacity is not divisible and the ladder doubles. There is no F24 to hand
    back out of an F64: the only move is a whole rung, and the rung below F64 is
    F32. A capacity running 40 CU of its 64 cannot give up 24 -- dropping to F32
    would take eight CU away from what it is actually using. Slack is only
    recoverable when the entire next rung down still fits, which is true of a
    handful of capacities in a fleet, not most of them.

    That is a real constraint and the number should not be talked up. Four
    capacities in this estate qualify. The value is not the volume, it is that
    the platform can name the region, the account and the amount at all.

WHO READS THIS
    Microsoft executives, not customers. The figures are Microsoft's revenue
    exposure and Microsoft's regional efficiency, not the customer's bill, and
    the recommendation is ranked by what it unblocks rather than by CU freed.
    Sixty-four Capacity Units is an engineering number; the money it releases
    and the accounts it serves are the ones that decide whether the call is
    worth making.
"""

from __future__ import annotations

import pandas as pd

from planning import (
    FREE_VIEWER_CU,
    FREE_VIEWER_SKU,
    F_SKUS,
    IDLE_DAYS,
    IDLE_PCT,
    SUSTAINED_HIGH_PCT,
    Recommendation,
    previous_sku,
)
from planning import recommend

#: How much of the refused demand a reclaim has to cover before it is worth
#: anybody's time. Below this the conversation costs more than it returns, and
#: a list of trivial matches buries the two that matter.
MIN_COVERAGE_PCT = 10.0


def _idle_with_room(entities, window_days: int) -> list[dict]:
    """Capacities one whole rung too big, and what stepping down would release.

    Deliberately the same conditions `scale_down` uses, read from the same
    constants. A capacity that is quiet six days a week and overloaded on the
    seventh is sized for the seventh, and reclaiming from it would be taking
    capacity away from something that needs it -- so anything that throttled in
    the window is refused however idle its average looks.
    """
    health = recommend._health(entities, window_days)
    out = []
    for c in health.itertuples():
        if c.ThrottledDays > 0 or c.MeanUtilisationPct >= IDLE_PCT:
            continue
        if c.DaysObserved < IDLE_DAYS:
            continue
        step = previous_sku(c.FabricSku)
        if not step:
            continue
        after = c.PeakUtilisationPct * c.CapacityUnits / F_SKUS[step]
        if after >= SUSTAINED_HIGH_PCT:
            continue
        out.append({
            "capacityId": str(c.CapacityId),
            "datacentre": str(c.DatacentreId),
            "region": str(c.Region),
            "sku": str(c.FabricSku),
            "capacityUnits": int(c.CapacityUnits),
            "stepTo": step,
            "stepToUnits": F_SKUS[step],
            "releases": int(c.CapacityUnits) - F_SKUS[step],
            "meanPct": round(float(c.MeanUtilisationPct), 1),
            "peakPct": round(float(c.PeakUtilisationPct), 1),
            "peakAfterPct": round(float(after), 1),
            "windowDays": int(c.WindowDays),
            "losesFreeViewers": F_SKUS[step] < FREE_VIEWER_CU <= int(c.CapacityUnits),
        })
    return out


def _unmet_demand(entities, priced_rows) -> dict[str, dict]:
    """What each region refused: how much, to whom, and what it cost.

    Counted on the same failed-request definition the rest of the product uses.
    An earlier module in this project carried its own and reported 45 where
    every other screen reported 30.
    """
    fact = entities["fact_capacity_request"]
    # Flagged rows only. Keying on every priced ticket counted the ones handled
    # inside SLA too, and reported six refusals in eastus2 where every other
    # screen reports five -- the exact defect this module's own docstring warns
    # about, produced on its first run.
    priced = {str(r["incidentId"]): r for r in priced_rows if r.get("isFlagged")}
    failed = fact[fact["IncidentId"].astype(str).isin(priced)]

    out: dict[str, dict] = {}
    for region, group in failed.groupby("Region"):
        short = (pd.to_numeric(group["RequestedCapacity"], errors="coerce").fillna(0)
                 - pd.to_numeric(group["GrantedCapacity"], errors="coerce").fillna(0))
        short = short.clip(lower=0)
        rows = [priced[i] for i in group["IncidentId"].astype(str) if i in priced]
        out[str(region)] = {
            "refused": int(len(group)),
            "shortfallUnits": int(short.sum()),
            "accounts": int(group["SubscriptionId"].nunique()),
            "exposure": round(sum(float(r.get("exposure", 0)) for r in rows), 2),
            "worstWaitDays": max((float(r.get("openDays", 0) or 0) for r in rows),
                                 default=0.0),
        }
    return out


def reclaim(entities, priced_rows, window_days: int = IDLE_DAYS) -> list[Recommendation]:
    """Regions holding idle capacity while refusing requests.

    Both halves have to be true. Idle capacity in a region that refused nobody
    is a cost story and `scale_down` already tells it; a refused request in a
    region with nothing spare is a scale-up and `scale_up` already tells that.
    This is only the overlap, which is the case neither of them can see.
    """
    demand = _unmet_demand(entities, priced_rows)
    caps = entities["dim_capacity"]
    holders = (caps.set_index("CapacityId")["SubscriptionId"].astype(str).to_dict()
               if "SubscriptionId" in caps.columns else {})

    names = {}
    if "dim_subscription" in getattr(entities, "tables", {}):
        sub = entities["dim_subscription"]
        if "CustomerName" in sub.columns:
            names = dict(zip(sub["SubscriptionId"].astype(str),
                             sub["CustomerName"].astype(str)))

    out: list[Recommendation] = []
    for idle in _idle_with_room(entities, window_days):
        need = demand.get(idle["region"])
        if not need or not need["shortfallUnits"]:
            continue

        releases = idle["releases"]
        shortfall = need["shortfallUnits"]
        covers_pct = min(100.0, releases / shortfall * 100.0)
        if covers_pct < MIN_COVERAGE_PCT:
            continue

        holder = holders.get(idle["capacityId"], "")
        account = names.get(holder, holder[:8] if holder else "an unidentified account")

        licence_note = ""
        if idle["losesFreeViewers"]:
            licence_note = (
                f" Note before the call: {idle['stepTo']} is below "
                f"{FREE_VIEWER_SKU}, so every user viewing Power BI content on "
                f"this capacity would need a Pro or PPU licence. That can cost "
                f"the account more than the capacity saves, and it is their "
                f"decision to weigh.")

        out.append(Recommendation(
            kind="reclaim", scope="region", target=idle["region"],
            headline=(
                f"{idle['region']} refused {need['refused']} request(s) while "
                f"{releases} CU sat idle — {account} holds it"),
            detail=(
                f"{account} holds {idle['capacityId']}, an {idle['sku']} that has "
                f"averaged {idle['meanPct']:.0f}% of its {idle['capacityUnits']} "
                f"CUs for {idle['windowDays']} days and never throttled. Stepping "
                f"it to {idle['stepTo']} would return {releases} CU to "
                f"{idle['region']} and still leave its measured peak at about "
                f"{idle['peakAfterPct']:.0f}%. That region refused "
                f"{need['refused']} request(s) from {need['accounts']} account(s), "
                f"short by {shortfall} CU and carrying "
                f"${need['exposure']:,.0f} of revenue exposure; the returned "
                f"capacity covers {covers_pct:.0f}% of what was asked for and not "
                f"granted. Fabric capacity belongs to a tenant and cannot be "
                f"moved between them, so this is a conversation with the account "
                f"holding it, not a change anyone can make for them."
                + licence_note),
            # Ranked by the exposure it could unblock, scaled by how much of the
            # shortfall it actually covers. CU released is the engineering
            # figure; this is the one that decides whether the call is worth
            # making, and the reader is an executive.
            urgency=round(need["exposure"] / 1000.0 * (covers_pct / 100.0), 1),
            evidence={
                "region": idle["region"],
                "capacityId": idle["capacityId"],
                "datacentre": idle["datacentre"],
                "heldBy": holder,
                "heldByName": account,
                "fabricSku": idle["sku"],
                "capacityUnits": idle["capacityUnits"],
                "meanUtilisationPct": idle["meanPct"],
                "peakUtilisationPct": idle["peakPct"],
                "windowDays": idle["windowDays"],
                "stepTo": idle["stepTo"],
                "stepToUnits": idle["stepToUnits"],
                "releasesUnits": releases,
                "peakAfterPct": idle["peakAfterPct"],
                "losesFreeViewers": idle["losesFreeViewers"],
                "refusedRequests": need["refused"],
                "refusedAccounts": need["accounts"],
                "shortfallUnits": shortfall,
                "coversPct": round(covers_pct, 1),
                "exposureUnblocked": need["exposure"],
                "worstWaitDays": round(need["worstWaitDays"], 1),
                # Said on every row because the screen is read by people who did
                # not build it, and a transfer is the obvious wrong reading.
                "isConversationNotAction": True,
                "owner": f"Capacity Operations — {idle['region']}",
            },
        ))

    out.sort(key=lambda r: -r.urgency)
    return out
