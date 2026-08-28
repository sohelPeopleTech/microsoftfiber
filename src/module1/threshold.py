"""Forecast the crossing, subtract the decision window, flag what is due.

Everything here is arithmetic on a fitted trend. There is no model to trust and
no threshold buried in code -- the safety line, the horizon, the trend window
and the decision window are all parameters, so a reviewer can move them and
watch the answer move.

WHAT THE DECISION WINDOW REPLACED
    This module used to subtract a hardware provisioning lead time, read per
    region from dim_region.LeadTimeDays, and say things like "Intel-highmem
    takes 45 days to provision -- the request needed raising 30 days ago."

    Fabric has no provisioning. An F SKU is scaled in Azure and takes effect
    immediately, so the wait that model was built around does not exist, and a
    region did not become more urgent than another because of the hardware
    underneath it.

    Something real remains, though, and setting the wait to zero would have
    thrown it away: somebody still has to notice, decide and approve. That is
    an organisational latency, not a hardware one, which is why it is now a
    single policy figure rather than a per-region property. The default is
    grounded in this estate's own record -- the ICM extract's denied-then-
    approved requests took a median of 6.3 days to turn around.

    The visible consequence is that regions no longer rank by whose hardware is
    slowest. They rank by who crosses first, which in Fabric is the truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

#: Flag before usage crosses this share of deployed capacity. The design doc
#: suggests 85%; it is a parameter because the right number is a policy choice
#: about how much headroom the business wants to carry.
DEFAULT_THRESHOLD_PCT = 85.0

#: Days of history the trend is fitted on. Long enough to survive a weekend
#: dip, short enough to notice a change in direction.
DEFAULT_TREND_DAYS = 45

#: Beyond this, "when will we cross" stops being a forecast and starts being a
#: guess. Regions projected further out are reported as not-approaching rather
#: than given a date nobody should plan against.
MAX_PROJECTION_DAYS = 365

#: Days before the crossing by which a decision has to be made.
#:
#: Not a provisioning wait -- there is none. This is how long it takes an
#: organisation to notice a region is filling, agree to scale it, and act. The
#: default is the median turnaround on this estate's own denied-then-approved
#: capacity requests, which is the same approval path.
#:
#: A policy figure, so it is uniform. In Fabric no region can be scaled faster
#: than another, and pretending otherwise was the defect.
DEFAULT_DECISION_WINDOW_DAYS = 7

#: How far ahead a capacity review looks. A region whose decision falls inside
#: this is on this cycle's agenda; one beyond it is not yet.
#:
#: This is what the amber band on the fleet map means, and it has to be stated
#: somewhere. Under the old model amber was produced accidentally, by hardware
#: lead times of 30 to 45 days exceeding the days left before a crossing -- so
#: which regions appeared amber depended on what machines happened to be in
#: them. `grace_days` existed to express this properly and defaulted to zero,
#: which is why nothing was ever "due now": the state was unreachable and the
#: colour came from the lead time instead.
DEFAULT_REVIEW_DAYS = 30

STATUS_OVERDUE = "overdue"          # already past the act-by date
STATUS_DUE = "due_now"              # decision falls inside this review cycle
STATUS_APPROACHING = "approaching"  # will be due, but not yet
STATUS_STABLE = "stable"            # flat or falling -- no crossing in range
STATUS_BREACHED = "breached"        # already over the threshold


@dataclass
class ThresholdFlag:
    region: str
    decision_window_days: int
    current_utilisation_pct: float
    threshold_pct: float
    trend_pct_per_day: float
    days_to_threshold: float | None
    cross_date: str | None
    act_by_date: str | None
    days_until_action: float | None
    status: str
    reason: str
    deployed_units: float
    used_units: float
    units_short_at_cross: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def is_actionable(self) -> bool:
        return self.status in (STATUS_OVERDUE, STATUS_DUE, STATUS_BREACHED)


def _fit_trend(series: pd.DataFrame, trend_days: int) -> tuple[float, float]:
    """Percentage points per day, and today's level, from the recent window.

    Least squares on the window rather than last-minus-first: one noisy day at
    either end should not set the direction for a provisioning decision.
    """
    recent = series.tail(trend_days)
    y = recent["UtilisationPct"].to_numpy(dtype=float)
    if len(y) < 2:
        return 0.0, float(y[-1]) if len(y) else 0.0
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    # Level from the fit, not the last raw point -- same reason as above.
    return float(slope), float(slope * (len(y) - 1) + intercept)


def project_region(
    onto,
    region: str,
    as_of: date | None = None,
    threshold_pct: float | None = None,
    trend_days: int = DEFAULT_TREND_DAYS,
    grace_days: int = DEFAULT_REVIEW_DAYS,
    crossing_for=None,
    decision_window_days: int = DEFAULT_DECISION_WINDOW_DAYS,
) -> ThresholdFlag:
    """When does this region cross its own safety threshold?

    `threshold_pct` is an override. Left as None -- which is the normal case --
    the region's own threshold is used, because a safety line is a property of a
    region rather than one figure imposed on all of them: review put it as "this
    is a high utilised region, why should I keep the same threshold as a low
    utilisation region". Passing a value forces every region to that line, which
    is what the what-if control on the Regions tab does.


    `crossing_for(region, threshold_pct)` optionally supplies the crossing date
    from a better forecast. Without it this fits a least-squares trend over the
    recent window, which is honest but is a second opinion -- and two forecasts
    in one product disagreed by ten days on canadaeast, one on the Regions tab
    and one on the Forecast tab.

    Review asked for the predictive trigger to *be* the forecast ("growth rate
    plus lead time logic, that flags a request as due today"), so the app passes
    the backtested winner in and this module derives the order-by date from it.
    The local fit remains the fallback, so module 1 still stands alone.
    """
    usage = onto["fact_usage_daily"]
    series = usage[usage["Region"] == region].sort_values("Date")
    if series.empty:
        raise KeyError(f"no usage history for region {region!r}")

    dim = onto["dim_region"].set_index("Region")
    if region not in dim.index:
        raise KeyError(f"unknown region {region!r}")
    meta = dim.loc[region]
    # dim_region still carries LeadTimeDays and SKUClass; module 2 and the
    # propensity model read them. This module deliberately does not: they
    # describe hardware, and nothing here is about hardware any more.
    window = int(decision_window_days)

    # The region's own line unless one was forced on it. Falls back to the
    # policy default only if the ontology has no threshold for this region,
    # which would mean the table was built by an older path.
    if threshold_pct is None:
        own = meta.get("ThresholdPct")
        threshold_pct = (float(own) if own is not None and pd.notna(own)
                         else DEFAULT_THRESHOLD_PCT)
    threshold_pct = float(threshold_pct)

    as_of = as_of or date.fromisoformat(str(series["Date"].iloc[-1]))
    slope, level = _fit_trend(series, trend_days)
    deployed = float(series["TotalUnits"].iloc[-1])
    used = float(series["UsedUnits"].iloc[-1])

    def flag(status, reason, days=None, cross=None, order=None, until=None, short=None):
        return ThresholdFlag(
            region=region, decision_window_days=window,
            current_utilisation_pct=round(level, 2), threshold_pct=threshold_pct,
            trend_pct_per_day=round(slope, 4),
            days_to_threshold=days, cross_date=cross, act_by_date=order,
            days_until_action=until, status=status, reason=reason,
            deployed_units=round(deployed, 1), used_units=round(used, 1),
            units_short_at_cross=short,
        )

    # Already over the line -- no forecast needed. The decision window is now a
    # measure of how long ago this should have been acted on.
    if level >= threshold_pct:
        order = as_of - timedelta(days=window)
        return flag(
            STATUS_BREACHED,
            f"already at {level:.1f}%, past the {threshold_pct:.0f}% threshold; "
            f"the decision was due {window} days ago. Scaling is immediate.",
            days=0.0,
            cross=as_of.isoformat(),
            order=order.isoformat(),
            until=-float(window),
            short=0.0,
        )

    if slope <= 0:
        return flag(
            STATUS_STABLE,
            f"utilisation is flat or falling ({slope:+.3f} pts/day) -- no crossing "
            f"projected.",
        )

    days_to_cross = (threshold_pct - level) / slope

    # Prefer the supplied forecast. It was chosen by backtest against six
    # held-out folds; this module's own fit never was.
    #
    # Three outcomes, and the middle one is the subtle case: `False` means the
    # forecast ran and concluded there is no crossing, which is an answer, not
    # an absence. Treating it as "unavailable" and falling back to the local fit
    # is how eastus ended up with an order-by date from the weaker model while
    # the better one said it never crosses at all.
    if crossing_for is not None:
        supplied = crossing_for(region, threshold_pct)
        if supplied:
            days_to_cross = (date.fromisoformat(str(supplied)[:10]) - as_of).days
        elif supplied is False:
            return flag(
                STATUS_STABLE,
                f"growing at {slope:+.3f} pts/day, but the forecast does not "
                f"project a crossing of {threshold_pct:.0f}% within "
                f"{MAX_PROJECTION_DAYS} days.",
            )

    if days_to_cross > MAX_PROJECTION_DAYS:
        return flag(
            STATUS_STABLE,
            f"growing at {slope:+.3f} pts/day, which reaches "
            f"{threshold_pct:.0f}% in {days_to_cross:.0f} days -- beyond the "
            f"{MAX_PROJECTION_DAYS}-day projection limit.",
            days=round(days_to_cross, 1),
        )

    cross = as_of + timedelta(days=float(days_to_cross))
    order = cross - timedelta(days=window)
    until = (order - as_of).days
    short = deployed * (threshold_pct - level) / 100.0

    if until < 0:
        status = STATUS_OVERDUE
        reason = (
            f"the region's utilisation is projected to reach "
            f"{threshold_pct:.0f}% on {cross.isoformat()} ({days_to_cross:.0f} "
            f"days), inside the {window}-day decision window -- this needed "
            f"deciding {abs(until)} days ago."
        )
    elif until <= grace_days:
        status = STATUS_DUE
        # Names the review cycle, because that is the branch that fired.
        # It named the decision window instead, which put "inside the 7-day
        # decision window" against a region crossing in 28 days.
        reason = (
            f"the region's utilisation is projected to reach "
            f"{threshold_pct:.0f}% on {cross.isoformat()} ({days_to_cross:.0f} "
            f"days); allowing {window} days to decide, settle it by "
            f"{order.isoformat()}, {until} days from now."
        )
    else:
        status = STATUS_APPROACHING
        reason = (
            f"the region's utilisation is projected to reach "
            f"{threshold_pct:.0f}% on {cross.isoformat()}; allowing {window} "
            f"days to decide, settle it by {order.isoformat()} -- {until} days "
            f"away."
        )

    return flag(
        status, reason,
        days=round(float(days_to_cross), 1),
        cross=cross.isoformat(),
        order=order.isoformat(),
        until=float(until),
        short=round(short, 1),
    )


def project_all(onto, **kwargs) -> pd.DataFrame:
    """Every region, ordered by urgency rather than by utilisation.

    Sorting by days-until-action rather than by current usage still matters --
    a region at 71% and climbing steeply outranks one flat at 94% -- but it no
    longer produces the inversion this docstring used to celebrate, where a
    region outranked a fuller one because its hardware took longer to arrive.
    Every region now has the same decision window, because in Fabric no region
    can be scaled faster than another.

    The urgency that hardware lead time was standing in for has not gone away;
    it moved to where it is actually measured. A capacity that is throttling is
    refusing work today regardless of when its region's average crosses, and
    that is on the fleet map, in the recommendations, and in the risk score.
    """
    rows = [
        project_region(onto, region, **kwargs).to_dict()
        for region in sorted(onto["dim_region"]["Region"])
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    priority = {
        STATUS_BREACHED: 0, STATUS_OVERDUE: 1, STATUS_DUE: 2,
        STATUS_APPROACHING: 3, STATUS_STABLE: 4,
    }
    df["_p"] = df["status"].map(priority)
    df = df.sort_values(
        ["_p", "days_until_action", "current_utilisation_pct"],
        ascending=[True, True, False],
        na_position="last",
    ).drop(columns="_p")
    return df.reset_index(drop=True)


def due_requests(onto, **kwargs) -> pd.DataFrame:
    """Only what needs a decision now -- the approval queue, not the dashboard."""
    everything = project_all(onto, **kwargs)
    if everything.empty:
        return everything
    return everything[
        everything["status"].isin([STATUS_BREACHED, STATUS_OVERDUE, STATUS_DUE])
    ].reset_index(drop=True)
