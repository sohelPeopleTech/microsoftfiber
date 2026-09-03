"""What moving a capacity along the F-SKU ladder would actually do.

This replaces module 2's migration calculator, which answered a question Fabric
cannot be asked. That model took a capacity pool offline, converted it from one
hardware class to another, costed the conversion and quoted a provisioning lead
time. A Fabric customer has none of those things: there is no server, no vendor,
nothing to take offline, and no wait.

The question that *is* asked, constantly, is the one this answers -- if I move
this capacity to that SKU, does it stop throttling, what does it cost me, and
what else changes. Three things change beyond the compute, and each has bitten
somebody:

    the F64 line     at F64 and above Power BI content is readable on a Free
                     licence. Scaling down across it can cost more in per-viewer
                     Pro licences than the smaller SKU saves.
    F256/F512        Microsoft notes scaling across this boundary can be slower.
    bursting         a capacity over 100% is not broken, it is bursting, and
                     smoothing may absorb it. Peak alone cannot condemn a SKU.

Everything here is arithmetic on measured consumption. Nothing is modelled, and
in particular nothing is costed in currency: F SKUs bill per Capacity Unit per
second, so a change in CU *is* the change in cost, and quoting a dollar figure
would mean inventing a rate card.

https://learn.microsoft.com/en-us/fabric/enterprise/scale-capacity
"""

from __future__ import annotations

import pandas as pd

from planning import (
    FREE_VIEWER_CU,
    F_SKUS,
    IDLE_PCT,
    SUSTAINED_HIGH_PCT,
    THROTTLED_DAYS_FOR_SCALE,
    crosses_slow_boundary,
)

#: What "comfortable" means, and it takes two readings rather than one.
#:
#: The mean has to sit under the same figure the recommendations use to call a
#: capacity out of headroom, or the calculator would propose a SKU the rest of
#: the product immediately flags.
#:
#: The peak has to stop bursting -- and 100%, not 85%. Bursting is not a fault
#: in Fabric; it is the feature smoothing exists to absorb, and only consumption
#: above the SKU's ceiling creates the overage that throttling is measured
#: against. An earlier version of this file held the peak to 85% too, and
#: recommended scaling an F64 running a 92% peak and no throttling for thirty
#: days -- a capacity the product's own engines correctly leave alone.
COMFORTABLE_MEAN_PCT = SUSTAINED_HIGH_PCT
COMFORTABLE_PEAK_PCT = 100.0


def _rescale(pct: float, from_cu: int, to_cu: int) -> float:
    """The same consumption expressed against a different ceiling.

    Utilisation is CU-seconds consumed over CU-seconds available, and only the
    denominator moves when a SKU changes. The work the capacity was asked to do
    is a property of the workspaces on it, not of the SKU underneath them.
    """
    if to_cu <= 0:
        return 0.0
    return round(pct * from_cu / to_cu, 1)


def scale_options(fabric_sku: str, capacity_units: int, mean_pct: float,
                  peak_pct: float) -> list[dict]:
    """Every rung of the ladder, with what the capacity would look like on it.

    Returned for all SKUs rather than only the plausible ones, because "why not
    F512" is a question a reviewer asks out loud and an answer of "it is not in
    the list" is not an answer.
    """
    options = []
    for sku, cu in F_SKUS.items():
        if sku == fabric_sku:
            continue
        peak_after = _rescale(peak_pct, capacity_units, cu)
        mean_after = _rescale(mean_pct, capacity_units, cu)
        options.append({
            "sku": sku,
            "capacityUnits": cu,
            "direction": "up" if cu > capacity_units else "down",
            "meanAfterPct": mean_after,
            "peakAfterPct": peak_after,
            # Headroom against the peak, not the mean. A capacity sized to its
            # average is a capacity that throttles at month end.
            "headroomPct": round(100.0 - peak_after, 1),
            "stillBursts": peak_after > COMFORTABLE_PEAK_PCT,
            "comfortable": (mean_after <= COMFORTABLE_MEAN_PCT
                            and peak_after <= COMFORTABLE_PEAK_PCT),
            # F SKUs bill per CU per second, so the CU change is the cost
            # change. No rate card is invented to say so.
            "cuDeltaPct": round((cu - capacity_units) / capacity_units * 100, 1),
            "freeViewers": cu >= FREE_VIEWER_CU,
            "gainsFreeViewers": capacity_units < FREE_VIEWER_CU <= cu,
            "losesFreeViewers": cu < FREE_VIEWER_CU <= capacity_units,
            "crossesSlowBoundary": crosses_slow_boundary(fabric_sku, sku),
            # There is no lead time to quote. Saying so explicitly is the point
            # of the whole exercise.
            "immediate": True,
        })
    options.sort(key=lambda o: o["capacityUnits"])
    return options


def recommended_option(options: list[dict], capacity_units: int, *,
                       needs_more: bool, may_shrink: bool) -> dict | None:
    """The SKU to move to, or None because nothing needs to move.

    The trigger is deliberately not this module's own. `scale_up` and
    `scale_down` already decide when a capacity is in trouble, and a calculator
    that answered differently would be a second opinion nobody asked for. So
    the caller passes those engines' conditions in, and this only chooses the
    rung once they have said something has to happen.

    An earlier version chose a rung whenever a larger one scored better, which
    is always true -- it recommended upgrading 272 of 317 capacities, including
    an F64 running at 42%.

    Smallest rung up rather than safest: the ladder doubles, so "one more to be
    sure" is not a small hedge.
    """
    if needs_more:
        up = [o for o in options
              if o["capacityUnits"] > capacity_units and o["comfortable"]]
        return min(up, key=lambda o: o["capacityUnits"]) if up else None
    if may_shrink:
        down = [o for o in options
                if o["capacityUnits"] < capacity_units
                and o["comfortable"] and not o["losesFreeViewers"]]
        return max(down, key=lambda o: o["capacityUnits"]) if down else None
    return None


def capacity_scale_view(capacity: pd.Series, health: pd.Series) -> dict:
    """One capacity: where it is now, and every rung it could move to."""
    sku = str(capacity["FabricSku"])
    cu = int(capacity["CapacityUnits"])
    mean_pct = float(health.get("MeanUtilisationPct", 0.0) or 0.0)
    peak_pct = float(health.get("PeakUtilisationPct", 0.0) or 0.0)

    throttled = int(health.get("ThrottledDays", 0) or 0)
    # The same two conditions scale_up and scale_down use, read from the same
    # constants, so the calculator and the recommendation list cannot drift.
    needs_more = (throttled >= THROTTLED_DAYS_FOR_SCALE
                  or mean_pct >= SUSTAINED_HIGH_PCT)
    may_shrink = throttled == 0 and mean_pct < IDLE_PCT
    options = scale_options(sku, cu, mean_pct, peak_pct)
    best = recommended_option(options, cu, needs_more=needs_more,
                              may_shrink=may_shrink)
    return {
        "capacityId": str(capacity["CapacityId"]),
        "datacentre": str(capacity["DatacentreId"]),
        "region": str(capacity["Region"]),
        "current": {
            "sku": sku,
            "capacityUnits": cu,
            "meanPct": round(mean_pct, 1),
            "peakPct": round(peak_pct, 1),
            "throttledDays": throttled,
            "windowDays": int(health.get("WindowDays", 0) or 0),
            "worstStage": str(health.get("WorstStage", "none")),
            "freeViewers": cu >= FREE_VIEWER_CU,
            "interactiveRejected": int(health.get("InteractiveRejected", 0) or 0),
            "backgroundRejected": int(health.get("BackgroundRejected", 0) or 0),
        },
        "options": options,
        "recommended": best["sku"] if best else None,
        "needsMore": needs_more,
        "mayShrink": may_shrink,
        # Said once here rather than repeated on every row of the table.
        "comfortableNow": (mean_pct <= COMFORTABLE_MEAN_PCT
                           and peak_pct <= COMFORTABLE_PEAK_PCT),
        "why": (
            f"A target is comfortable when the mean sits under "
            f"{COMFORTABLE_MEAN_PCT:.0f}% and the peak stops bursting past "
            f"100%. This capacity averages {round(mean_pct, 1)}% and peaks at "
            f"{round(peak_pct, 1)}%. Bursting is not a fault on its own -- "
            f"smoothing absorbs it -- but only consumption above the ceiling "
            f"creates the overage that throttling is measured against."),
    }
