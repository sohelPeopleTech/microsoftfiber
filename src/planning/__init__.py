"""Fabric capacity planning: what to do, and why now.

The rest of this project reports. It says how full a region is, which requests
failed, when a line will be crossed. Review's objection was that none of that is
a decision -- "this is reporting; where is capacity planning?" -- and the four
recommendations here are the answer.

An earlier version of this module recommended raising purchase orders weeks
ahead against hardware provisioning lead times, and moving workloads between
Intel and AMD. Neither exists in Fabric. A customer never sees a server, an
F-SKU is scaled in Azure and takes effect immediately, and a capacity does not
fail -- it throttles. The remedies Microsoft actually names are the ones here:

    scale_up      the capacity is throttling; move it up the SKU ladder
    load_balance  one workload dominates on a shared site; move it to a quieter capacity
    scale_down    consistently idle; a smaller SKU costs less per second
    licensing     step to F64, where Power BI reads on a free licence

They stay separate rather than blending into a score: a throttling capacity and
an idle one are opposite problems, and an average of them describes neither.

Each carries the evidence that produced it, so the screen can show the reasoning
rather than a verdict.

https://learn.microsoft.com/en-us/fabric/enterprise/throttling
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from module1.threshold import DEFAULT_THRESHOLD_PCT

# Imported as `synthdata.fabric`, not `src.synthdata.fabric`. The app puts
# ROOT/src on sys.path and nothing else, so the `src.` prefix resolves only
# where ROOT also happens to be on the path -- which is true of the tests and
# of a dev server started from the repo root, and false in the container. That
# difference shipped a 500 on every page the planning module touches.
from synthdata.fleet import (  # noqa: F401  -- re-exported for callers
    SITE_TYPE_DEDICATED,
    SITE_TYPE_SHARED,
    SITE_TYPES,
)
from synthdata.fabric import (  # noqa: F401  -- re-exported for callers
    FREE_VIEWER_SKU,
    F_SKUS,
    SLOW_SCALE_BOUNDARY,
    THROTTLE_STAGES,
    cu_seconds_per_day,
    next_sku,
    previous_sku,
    throttle_stage,
)

#: Capacity Units at which Power BI content becomes readable on a Free licence.
FREE_VIEWER_CU = F_SKUS[FREE_VIEWER_SKU]

#: How many days in the window a capacity has to spend throttling before it is
#: a sizing problem rather than a bad afternoon. Fabric capacities are
#: self-healing and burndown clears a one-off surge, so a single throttled day
#: is not evidence of anything.
THROTTLED_DAYS_FOR_SCALE = 3

#: Sustained utilisation above this, even without throttling, means the next
#: surge has nowhere to go. Below 100% there is no overage at all, so this sits
#: under it deliberately: the recommendation is about headroom, not about being
#: already broken. It is the estate's capacity threshold, not a second figure -- a
#: capacity that has crossed the line the map colours it red for is exactly the
#: one this should be recommending action on.
SUSTAINED_HIGH_PCT = DEFAULT_THRESHOLD_PCT

#: A workload taking more than this share of a capacity is worth moving on its
#: own, because moving it materially changes the picture.
#:
#: Only ever applied to a shared site. Every capacity on a dedicated site holds
#: one workload at 100%, so this would be true of all of them and would mean
#: nothing about any of them.
DOMINANT_WORKLOAD_PCT = 55.0

#: Utilisation below which a capacity is paying for compute nobody uses. F SKUs
#: bill per second, so an over-provisioned capacity is a standing cost rather
#: than a harmless margin.
IDLE_PCT = 30.0

#: Days of evidence before calling a capacity idle. Longer than the throttling
#: threshold: scaling down a capacity that is quiet for a fortnight and busy at
#: quarter-end is a worse mistake than leaving it.
IDLE_DAYS = 30


@dataclass
class Recommendation:
    """One thing to do, the case for it, and what it is worth.

    `urgency` orders a list for a reader; it is not a score anyone should quote.
    `evidence` is what the screen prints under the headline -- review rejected a
    recommendation that asserted without showing its working.
    """

    kind: str
    scope: str
    target: str
    headline: str
    #: Plain text, never markup. Three separate templates render this and all
    #: three escape it, so a <b> put here for emphasis reached the screen as the
    #: literal characters in front of the words it was meant to emphasise.
    #: Emphasis is the renderer's job; this field is the sentence.
    detail: str
    urgency: float
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "scope": self.scope, "target": self.target,
            "headline": self.headline, "detail": self.detail,
            "urgency": round(self.urgency, 1), "evidence": self.evidence,
        }


#: What a reader should call each stage, and how bad it is. The ordering is the
#: policy's own, so nothing else has to decide which of two stages is worse.
STAGE_LABEL = {
    "none": "not throttling",
    "interactive_delay": "interactive delay",
    "interactive_rejection": "interactive rejection",
    "background_rejection": "background rejection",
}
STAGE_RANK = {"none": 0, "interactive_delay": 1,
              "interactive_rejection": 2, "background_rejection": 3}


def capacity_health(capacities: pd.DataFrame, cu_daily: pd.DataFrame,
                    throttling: pd.DataFrame, window_days: int = 30) -> pd.DataFrame:
    """How each capacity has behaved over the recent window.

    Everything the recommendations turn on, computed once: how hard it ran, how
    often it throttled, the worst stage it reached, and how many operations it
    actually refused. A single day is never enough to size on -- capacities are
    self-healing and burndown clears a surge -- so the window matters more than
    the latest reading.
    """
    if cu_daily.empty:
        return capacities.assign(ThrottledDays=0, WorstStage="none")

    recent_days = sorted(cu_daily["Date"].unique())[-window_days:]
    recent = cu_daily[cu_daily["Date"].isin(recent_days)]
    grouped = recent.groupby("CapacityId")

    stats = pd.DataFrame({
        "MeanUtilisationPct": grouped["UtilisationPct"].mean().round(1),
        "PeakUtilisationPct": grouped["UtilisationPct"].max().round(1),
        "DaysObserved": grouped.size(),
        "ThrottledDays": grouped["ThrottleStage"].agg(lambda s: int((s != "none").sum())),
        "WorstStage": grouped["ThrottleStage"].agg(
            lambda s: max(s, key=lambda x: STAGE_RANK.get(x, 0))),
        "PeakMinutesOverLine": grouped["MinutesOverLine"].max().round(1),
    })

    if len(throttling):
        recent_ev = throttling[throttling["Date"].isin(recent_days)]
        by_cap = recent_ev.groupby("CapacityId")
        stats["InteractiveRejected"] = by_cap["InteractiveRejected"].sum()
        stats["BackgroundRejected"] = by_cap["BackgroundRejected"].sum()
    for col in ("InteractiveRejected", "BackgroundRejected"):
        if col not in stats.columns:
            stats[col] = 0
        stats[col] = stats[col].fillna(0).astype(int)

    out = capacities.merge(stats, left_on="CapacityId", right_index=True, how="left")
    out["ThrottledDays"] = out["ThrottledDays"].fillna(0).astype(int)
    out["WorstStage"] = out["WorstStage"].fillna("none")
    for col in ("MeanUtilisationPct", "PeakUtilisationPct", "PeakMinutesOverLine"):
        out[col] = out[col].fillna(0.0)
    out["WindowDays"] = len(recent_days)
    return out


def crosses_slow_boundary(from_sku: str, to_sku: str) -> bool:
    """Whether a scale crosses the F256/F512 line.

    Microsoft's guidance notes that scaling between SKUs on opposite sides of
    that boundary can be slower, which is worth saying on a recommendation that
    does it rather than letting someone find out during an incident.
    """
    ladder = list(F_SKUS)
    if from_sku not in ladder or to_sku not in ladder:
        return False
    lo, hi = sorted((ladder.index(from_sku), ladder.index(to_sku)))
    return lo < ladder.index(SLOW_SCALE_BOUNDARY[1]) <= hi
