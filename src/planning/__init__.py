"""Capacity planning: what to do, and why now.

The rest of this project reports. It says how full a region is, which requests
failed, when a line will be crossed. Review's objection was that none of that is
a decision -- "this is reporting; where is capacity planning?" -- and the three
recommendations here are the answer, each one a case the utilisation number
alone cannot make.

    procurement      buy earlier than the trigger, because the wait got longer
    workload_change  move off this hardware, though it has room to spare
    licensing        step up to F64, because of who can read the reports

They are deliberately separate. A single blended "risk score" would let a
procurement case and a hardware case cancel each other out and report a calm
average, which is how a site running at 40% with a dozen outages looks fine.

Each recommendation carries the evidence that produced it, so the screen can
show the reasoning rather than a verdict. That is a house style here: the
denial-reason and remediation work went the same way after review rejected a
generic sentence that named no hardware, no units and no lead time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

#: Utilisation at which a purchase is conventionally raised. The trigger the
#: business already uses, and the number these recommendations argue with.
DEFAULT_TRIGGER_PCT = 70.0

#: A lead time this much longer than it used to be is treated as drift worth
#: acting on rather than noise. 25% of a three-week wait is five days.
LEAD_TIME_DRIFT_PCT = 25.0

#: Prior strength for shrinking incident rates, in nodes. Deliberately larger
#: than a small capacity's node count so a one-node capacity with three bad
#: weeks cannot outrank a fleet. The same device, and the same reasoning, as
#: the empirical-Bayes shrinkage already applied to site failure rates.
INCIDENT_PRIOR_NODES = 3.0

#: How much worse than the fleet a capacity has to run before moving the
#: workload is worth raising at all.
#:
#: Set against the measured spread rather than picked: across uncrowded
#: capacities carrying three or more incidents the shrunk rate has a mean of
#: 1.08 and a standard deviation of 0.27, so 1.4 is about 1.2 deviations out --
#: materially worse than the fleet rather than the top of the normal range.
#: 1.6 was the first draft and sat near two deviations, which surfaced a single
#: capacity in three hundred and made the recommendation effectively unreachable.
UNHEALTHY_MULTIPLE = 1.4

#: Above this, a capacity's trouble is at least arguably a symptom of being
#: full, and the recommendation is to buy rather than to move.
CROWDED_PCT = 80.0

#: The SKU at which Power BI content becomes readable on a Free licence.
#: Real and documented: below it every viewer needs Pro or PPU.
#: https://learn.microsoft.com/en-us/fabric/enterprise/licenses
FREE_VIEWER_SKU = "F64"
FREE_VIEWER_CU = 64

SKU_LADDER = ["F2", "F4", "F8", "F16", "F32", "F64", "F128",
              "F256", "F512", "F1024", "F2048"]


@dataclass
class Recommendation:
    """One thing to do, the case for it, and what it is worth.

    `urgency` orders a list for a reader; it is not a score anyone should quote.
    `evidence` is what the screen prints under the headline -- review rejected a
    recommendation that asserted without showing its working.
    """

    kind: str
    scope: str                      # "region" | "datacentre" | "capacity"
    target: str
    headline: str
    detail: str
    urgency: float
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "scope": self.scope, "target": self.target,
            "headline": self.headline, "detail": self.detail,
            "urgency": round(self.urgency, 1), "evidence": self.evidence,
        }


# --------------------------------------------------------------------------
# lead time
# --------------------------------------------------------------------------


def lead_time_drift(history: pd.DataFrame) -> dict[str, dict]:
    """How each hardware class's lead time has moved, per class.

    Returns the earliest and current figures and the change between them. A
    class whose wait has grown is one whose purchase trigger should move
    earlier: the trigger is a proxy for "there is still time", and how much time
    there is depends on the wait, which the trigger does not know about.
    """
    out: dict[str, dict] = {}
    if history is None or history.empty:
        return out
    for cls, g in history.sort_values("EffectiveFrom").groupby("SKUClass"):
        first, last = g.iloc[0], g.iloc[-1]
        was, now = float(first["LeadTimeDays"]), float(last["LeadTimeDays"])
        out[str(cls)] = {
            "was": was,
            "now": now,
            "since": str(first["EffectiveFrom"]),
            "asOf": str(last["EffectiveFrom"]),
            "supplier": str(last.get("Supplier", "")),
            "changePct": round((now - was) / was * 100, 1) if was else 0.0,
            "changeDays": round(now - was, 1),
        }
    return out


def adjusted_trigger(trigger_pct: float, lead_days: float, drift: dict | None,
                     growth_pct_per_day: float | None) -> tuple[float, str]:
    """The utilisation at which an order has to be raised, and why.

    An order must go in when the time left before the trigger falls below the
    time the hardware takes to arrive -- otherwise it lands after the capacity
    was needed. At a known growth rate that converts to a utilisation: the
    trigger less `lead_days * growth`. A class taking 45 days in a region
    climbing 0.21 points a day has to be ordered nine and a half points early.

    An earlier draft subtracted only the *extra* days a lead time had drifted,
    which understated the window by however long the lead time already was and
    left the recommendation firing for almost nothing. The drift is not the
    reason to order early; the lead time is. What the drift changes is how much
    earlier -- and that is the sentence worth putting on screen, because it is
    the one that overrides a planner's habit.

    Returns the raise-at utilisation and an explanation, empty when the region
    is not growing and the question does not arise.
    """
    if not growth_pct_per_day or growth_pct_per_day <= 0 or lead_days <= 0:
        return trigger_pct, ""

    window = float(lead_days) * float(growth_pct_per_day)
    # Never argue the trigger below half. Past that the recommendation stops
    # being "buy sooner" and becomes "buy constantly", which is not advice.
    raise_at = max(trigger_pct - window, trigger_pct / 2.0)

    why = (
        f"At {growth_pct_per_day:.2f} points a day this reaches its "
        f"{trigger_pct:.0f}% trigger in {window / growth_pct_per_day:.0f} days, "
        f"and the hardware takes {lead_days:.0f} days to arrive — so the order "
        f"has to be raised at {raise_at:.1f}%."
    )
    if drift and float(drift.get("changePct") or 0) >= LEAD_TIME_DRIFT_PCT:
        was_window = float(drift["was"]) * float(growth_pct_per_day)
        was_raise = max(trigger_pct - was_window, trigger_pct / 2.0)
        why += (
            f" That is {raise_at - was_raise:+.1f} points earlier than it used "
            f"to be: {drift['supplier']} lead time for this hardware has gone "
            f"from {drift['was']:.0f} to {drift['now']:.0f} days since "
            f"{drift['since']} ({drift['changePct']:+.0f}%), so waiting for the "
            f"{was_raise:.1f}% that used to be safe now lands the order "
            f"{drift['changeDays']:.0f} days late."
        )
    return round(raise_at, 1), why


# --------------------------------------------------------------------------
# capacity health
# --------------------------------------------------------------------------


def capacity_health(capacities: pd.DataFrame, incidents: pd.DataFrame,
                    usage: pd.DataFrame) -> pd.DataFrame:
    """Incidents per node per capacity, shrunk toward the fleet rate.

    Two corrections stand between the raw data and a usable number, and both
    were found by looking at what the ranking actually produced.

    Raw counts rank capacities by size: an eight-node F256 sees more trouble
    than a one-node F2 for the same reason a bigger building has more broken
    windows. Dividing by nodes removes that.

    Per-node rates on a one-node capacity are then wild -- three incidents in a
    bad month reads as a catastrophic rate -- so each capacity is shrunk toward
    the fleet rate in proportion to how much hardware stands behind its record.
    A capacity with one node keeps a quarter of its own rate; one with twelve
    keeps four fifths.
    """
    caps = capacities.copy()
    counts = incidents.groupby("CapacityId").size() if len(incidents) else pd.Series(dtype=int)
    sev = (incidents[incidents["Severity"].isin(["Sev1", "Sev2"])]
           .groupby("CapacityId").size() if len(incidents) else pd.Series(dtype=int))
    downtime = (incidents.groupby("CapacityId")["DowntimeMinutes"].sum()
                if len(incidents) else pd.Series(dtype=float))

    caps["Incidents"] = caps["CapacityId"].map(counts).fillna(0).astype(int)
    caps["SeriousIncidents"] = caps["CapacityId"].map(sev).fillna(0).astype(int)
    caps["DowntimeMinutes"] = caps["CapacityId"].map(downtime).fillna(0).astype(int)
    caps["Nodes"] = caps["NodeCount"].clip(lower=1).astype(float)

    total_nodes = float(caps["Nodes"].sum())
    fleet_rate = float(caps["Incidents"].sum()) / total_nodes if total_nodes else 0.0
    caps["FleetRate"] = round(fleet_rate, 3)
    caps["RawRate"] = (caps["Incidents"] / caps["Nodes"]).round(3)
    caps["IncidentRate"] = (
        (caps["Incidents"] + INCIDENT_PRIOR_NODES * fleet_rate)
        / (caps["Nodes"] + INCIDENT_PRIOR_NODES)
    ).round(3)
    caps["RateVsFleet"] = (caps["IncidentRate"] / fleet_rate).round(2) if fleet_rate else 1.0

    if usage is not None and len(usage):
        latest = usage[usage["Date"] == usage["Date"].max()]
        caps["UtilisationPct"] = caps["CapacityId"].map(
            latest.set_index("CapacityId")["UtilisationPct"]).fillna(0.0)
    else:
        caps["UtilisationPct"] = 0.0
    return caps


def better_hardware(current: str, hardware: pd.DataFrame) -> dict | None:
    """The class to move to: fewer incidents, and not a downgrade in memory.

    Recommending a move to hardware with half the memory would trade one
    problem for another, so a candidate has to be at least as large as what it
    replaces on that axis. Returns None when nothing available is better, which
    is a real answer -- the workload may simply be on the best hardware there is.
    """
    if hardware is None or hardware.empty or current not in set(hardware["SKUClass"]):
        return None
    hw = hardware.set_index("SKUClass")
    now = hw.loc[current]
    better = hw[(hw["RelativeIncidentRate"] < float(now["RelativeIncidentRate"]) * 0.85)
                & (hw["MemoryGB"] >= float(now["MemoryGB"]))]
    if better.empty:
        return None
    pick = better.sort_values("RelativeIncidentRate").iloc[0]
    return {
        "sku_class": str(pick.name),
        "vendor": str(pick["Vendor"]),
        "model": str(pick["Model"]),
        "cpu": str(pick["Cpu"]),
        "memoryGB": int(pick["MemoryGB"]),
        "expectedReductionPct": round(
            (1 - float(pick["RelativeIncidentRate"]) / float(now["RelativeIncidentRate"])) * 100),
    }


# --------------------------------------------------------------------------
# licensing
# --------------------------------------------------------------------------


def next_sku(sku: str) -> str | None:
    """The next rung up the Fabric ladder."""
    if sku not in SKU_LADDER:
        return None
    i = SKU_LADDER.index(sku)
    return SKU_LADDER[i + 1] if i + 1 < len(SKU_LADDER) else None
