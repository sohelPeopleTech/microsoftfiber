"""The three recommendations, built from the fleet tables.

Each answers a question the utilisation number cannot:

    procurement      it is not at the trigger yet -- but the wait got longer
    workload_change  it has room -- but it keeps falling over
    licensing        it is fine -- but only Pro licences can read it

Every one carries its evidence. A recommendation that says "raise a purchase"
without naming the hardware, the supplier, the units and the date is the generic
sentence review already rejected once.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from . import (
    CROWDED_PCT,
    DEFAULT_TRIGGER_PCT,
    LEAD_TIME_DRIFT_PCT,
    FREE_VIEWER_CU,
    FREE_VIEWER_SKU,
    UNHEALTHY_MULTIPLE,
    Recommendation,
    adjusted_trigger,
    better_hardware,
    capacity_health,
    lead_time_drift,
    next_sku,
)


def _growth_per_day(usage: pd.DataFrame, region: str, days: int = 45) -> float:
    """Points of utilisation gained per day, over the recent window."""
    s = usage[usage["Region"] == region].sort_values("Date").tail(days)
    if len(s) < 2:
        return 0.0
    span = len(s) - 1
    return float((s["UtilisationPct"].iloc[-1] - s["UtilisationPct"].iloc[0]) / span)


# --------------------------------------------------------------------------


def procurement(onto, trigger_pct: float | None = None) -> list[Recommendation]:
    """Capacities to buy for, including ones below their usual trigger.

    The case review asked for: a capacity whose hardware now takes three months
    instead of one should be bought for today, because by the time it reaches
    the usual trigger the order will already be late. The trigger is not wrong,
    it is just blind to the wait -- so the wait is priced into it.

    The trigger defaults to the *site's own safety threshold* rather than a flat
    figure. A fixed 70% was the first draft and it flagged two capacities in
    three: this fleet runs at eighty to ninety per cent, so a 70% line is not a
    trigger, it is a description of the estate. Each site already carries the
    threshold its own facility holds, and using it is both more accurate and
    consistent with every other screen. A caller can still force one number
    across the fleet to see what changes.
    """
    caps = onto["dim_capacity"]
    usage = onto["fact_capacity_usage_daily"]
    region_usage = onto["fact_usage_daily"]
    drift = lead_time_drift(onto["dim_lead_time_history"])
    sku_lead = onto["dim_sku"].set_index("SKUClass")["LeadTimeDays"].to_dict()
    site_trigger = (onto["dim_datacentre"].set_index("DatacentreId")["ThresholdPct"]
                    .to_dict())

    latest_day = usage["Date"].max()
    latest = usage[usage["Date"] == latest_day].set_index("CapacityId")
    as_of = date.fromisoformat(str(latest_day))

    out: list[Recommendation] = []
    growth_cache: dict[str, float] = {}
    for cap in caps.itertuples():
        if cap.CapacityId not in latest.index:
            continue
        util = float(latest.loc[cap.CapacityId, "UtilisationPct"])
        region = cap.Region
        if region not in growth_cache:
            growth_cache[region] = _growth_per_day(region_usage, region)
        growth = growth_cache[region]

        base = float(trigger_pct if trigger_pct is not None
                     else site_trigger.get(cap.DatacentreId, DEFAULT_TRIGGER_PCT))
        lead = float(sku_lead.get(cap.SKUClass, 0) or 0)
        d = drift.get(cap.SKUClass)
        eff_trigger, why = adjusted_trigger(base, lead, d, growth)
        if util < eff_trigger:
            continue

        # Days until it reaches its trigger, less the wait: negative means the
        # order should already have gone in.
        days_to_trigger = max(base - util, 0.0) / growth if growth > 0 else 0.0
        order_by = as_of + timedelta(days=days_to_trigger - lead)
        days_until = (order_by - as_of).days

        early = util < base
        # Only claim the wait has grown when it actually has. Every early raise
        # is early because of the lead time; only some are early because that
        # lead time moved, and saying so indiscriminately would put a sentence
        # on screen the evidence underneath it does not support.
        drifted = bool(d and float(d.get("changePct") or 0) >= LEAD_TIME_DRIFT_PCT)
        out.append(Recommendation(
            kind="procurement",
            scope="capacity",
            target=cap.CapacityId,
            headline=(
                f"Raise a purchase for {cap.CapacityId} now — {util:.0f}% used, "
                f"below its {base:.0f}% trigger"
                + (", and the wait has grown" if drifted
                   else f", but {cap.SKUClass} takes {lead:.0f} days to arrive")
                if early else
                f"Purchase for {cap.CapacityId} is "
                f"{'overdue' if days_until < 0 else 'due'} — {util:.0f}% used"
            ),
            detail=(why or
                    f"{cap.SKUClass} takes {lead:.0f} days to provision, so the "
                    f"order has to be raised {lead:.0f} days before the capacity "
                    f"is needed."),
            urgency=round(100.0 - days_until, 1),
            evidence={
                "region": region, "datacentre": cap.DatacentreId,
                "fabricSku": cap.FabricSku, "skuClass": cap.SKUClass,
                "utilisationPct": round(util, 1),
                "standardTriggerPct": round(base, 1),
                "adjustedTriggerPct": eff_trigger,
                "raisedEarly": early,
                "leadTimeDrifted": drifted,
                "leadTimeDays": lead,
                "leadTimeWasDays": (d or {}).get("was"),
                "leadTimeChangePct": (d or {}).get("changePct"),
                "supplier": (d or {}).get("supplier"),
                "growthPctPerDay": round(growth, 3),
                "orderByDate": order_by.isoformat(),
                "daysUntilOrder": days_until,
                "unitsToAdd": int(cap.DeployedUnits),
            },
        ))
    out.sort(key=lambda r: -r.urgency)
    return out


# --------------------------------------------------------------------------


def workload_change(onto) -> list[Recommendation]:
    """Capacities worth moving off their hardware despite having room.

    Review's point, and the one the product could not previously make: enough
    capacity is not the same as good capacity. A capacity at 40% that keeps
    dropping nodes is costing customers outages while every screen calls it
    healthy, because every screen is looking at how full it is.

    Capacities that are both unhealthy *and* crowded are excluded. Above 80% the
    trouble is at least arguably a symptom of load, and the answer there is to
    buy more rather than to move -- procurement already covers it.
    """
    health = capacity_health(onto["dim_capacity"],
                             onto["fact_operational_incident"],
                             onto["fact_capacity_usage_daily"])
    hardware = onto["dim_hardware"]

    out: list[Recommendation] = []
    for c in health.itertuples():
        if c.RateVsFleet < UNHEALTHY_MULTIPLE or c.Incidents < 3:
            continue
        if c.UtilisationPct >= CROWDED_PCT:
            continue
        move_to = better_hardware(c.SKUClass, hardware)
        if not move_to:
            continue

        hours = c.DowntimeMinutes / 60.0
        out.append(Recommendation(
            kind="workload_change",
            scope="capacity",
            target=c.CapacityId,
            headline=(
                f"Move {c.CapacityId} off {c.SKUClass} — {c.UtilisationPct:.0f}% "
                f"used, but {c.Incidents} incidents in the window"
            ),
            detail=(
                f"This capacity has room: it is {c.UtilisationPct:.0f}% used "
                f"against a fleet that averages {c.FleetRate:.1f} incidents per "
                f"node, and it is running {c.RateVsFleet:.1f} times that. "
                f"{c.SeriousIncidents} were Sev1 or Sev2 and it lost "
                f"{hours:.0f} hours. Capacity is not the problem here, so adding "
                f"more of the same hardware would not fix it. "
                f"{move_to['vendor']} {move_to['model']} ({move_to['sku_class']}, "
                f"{move_to['cpu']}, {move_to['memoryGB']}GB) runs about "
                f"{move_to['expectedReductionPct']}% fewer incidents at the same "
                f"memory."
            ),
            urgency=round(c.RateVsFleet * 10 + c.SeriousIncidents * 4, 1),
            evidence={
                "region": c.Region, "datacentre": c.DatacentreId,
                "fabricSku": c.FabricSku, "skuClass": c.SKUClass,
                "nodes": int(c.Nodes),
                "utilisationPct": round(c.UtilisationPct, 1),
                "incidents": int(c.Incidents),
                "seriousIncidents": int(c.SeriousIncidents),
                "downtimeHours": round(hours, 1),
                "incidentsPerNode": c.IncidentRate,
                "rawIncidentsPerNode": c.RawRate,
                "fleetIncidentsPerNode": c.FleetRate,
                "rateVsFleet": c.RateVsFleet,
                "moveTo": move_to,
            },
        ))
    out.sort(key=lambda r: -r.urgency)
    return out


# --------------------------------------------------------------------------


def licensing(onto) -> list[Recommendation]:
    """Capacities one rung below the licence that pays for itself.

    F64 is where Power BI content becomes readable on a Free licence; below it
    every viewer needs Pro or PPU. A tenant with a hundred readers on an F32 is
    buying a hundred Pro licences to avoid one SKU step, which is a commercial
    decision that no amount of utilisation monitoring will surface.
    """
    caps = onto["dim_capacity"]
    requests = onto["fact_capacity_request"]
    viewers = (requests.groupby("Region")["SubscriptionId"].nunique().to_dict()
               if "SubscriptionId" in requests.columns else {})

    out: list[Recommendation] = []
    for cap in caps.itertuples():
        step = next_sku(cap.FabricSku)
        # Only the capacities one step below the cliff. Recommending an F2 jump
        # to F64 is arithmetic, not advice.
        if cap.CapacityUnits >= FREE_VIEWER_CU or step != FREE_VIEWER_SKU:
            continue
        customers = int(viewers.get(cap.Region, 0))
        out.append(Recommendation(
            kind="licensing",
            scope="capacity",
            target=cap.CapacityId,
            headline=(
                f"{cap.CapacityId} is one step below {FREE_VIEWER_SKU} — every "
                f"Power BI viewer here needs a paid licence"
            ),
            detail=(
                f"This is an {cap.FabricSku}. On any capacity below "
                f"{FREE_VIEWER_SKU}, each user viewing Power BI content needs "
                f"Pro or Premium Per User; at {FREE_VIEWER_SKU} or larger a Free "
                f"licence and a viewer role are enough. Stepping up doubles the "
                f"capacity units from {cap.CapacityUnits} to {FREE_VIEWER_CU} and "
                f"removes the per-viewer licence entirely. "
                + (f"{customers} subscriptions have raised capacity requests in "
                   f"{cap.Region}, so the viewer population is not nil."
                   if customers else
                   "No requests are recorded in this region, so size the viewer "
                   "population before acting.")
            ),
            urgency=round(30.0 + customers, 1),
            evidence={
                "region": cap.Region, "datacentre": cap.DatacentreId,
                "fabricSku": cap.FabricSku,
                "capacityUnits": int(cap.CapacityUnits),
                "stepTo": FREE_VIEWER_SKU,
                "stepToUnits": FREE_VIEWER_CU,
                "skuClass": cap.SKUClass,
                "subscriptionsInRegion": customers,
                "rule": (
                    "Fabric licensing: F64 or larger lets Free-licensed users "
                    "view Power BI content with a viewer role; below F64 each "
                    "viewer needs Pro, PPU or a trial."),
                "source": "https://learn.microsoft.com/en-us/fabric/enterprise/licenses",
            },
        ))
    out.sort(key=lambda r: -r.urgency)
    return out


# --------------------------------------------------------------------------


def all_recommendations(onto, trigger_pct: float | None = None) -> list[dict]:
    """Every recommendation, most urgent first, kinds interleaved.

    Not grouped by kind: a planner wants the most pressing thing, and which of
    the three engines produced it is a detail of how this was computed.
    """
    recs = (procurement(onto, trigger_pct)
            + workload_change(onto)
            + licensing(onto))
    recs.sort(key=lambda r: -r.urgency)
    return [r.to_dict() for r in recs]
