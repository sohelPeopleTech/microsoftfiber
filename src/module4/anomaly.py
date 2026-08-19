"""Detect the spike, then look for a reason -- and say so when there isn't one.

Detection is a robust z-score: median and MAD rather than mean and standard
deviation, because the mean of a series containing a spike is dragged by the
spike it is supposed to detect. With five monthly points per region that
matters -- one outlier in five moves a mean by 20%.

Matching is deliberately narrow. An event only explains a spike if it happened
in the same region, within a window before it. Anything else is reported as an
unexplained spike, which is a finding in its own right: demand moved and nobody
knows why.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

#: Robust z above which a period counts as a spike. 3.0 is conventional; with
#: five points per region a lower bar would flag ordinary variation.
DEFAULT_Z_THRESHOLD = 3.0

#: An event must precede the spike by no more than this. A deal signed three
#: months before a capacity request is not the reason for it -- and without a
#: bound, everything correlates with everything.
DEFAULT_MATCH_WINDOW_DAYS = 45

#: Series shorter than this cannot support a spike claim at all.
MIN_PERIODS = 4

#: A spike must also clear this many units above baseline. Monthly demand per
#: region is mostly zeros with occasional large requests, so medians sit near
#: zero and a z-score alone flags a fifth of all region-months -- every one
#: statistically true and none of them worth waking anyone for.
DEFAULT_MIN_DEVIATION_UNITS = 50.0

#: Reported z is capped. A near-flat series divided into a large jump yields
#: z in the hundreds, which reads as a broken calculation rather than a big
#: number -- and nothing downstream distinguishes 20 from 348.
Z_REPORTING_CAP = 20.0

#: MAD -> standard-deviation equivalent for a normal distribution.
MAD_SCALE = 1.4826


@dataclass
class Anomaly:
    region: str
    period: str
    value: float
    baseline: float
    deviation: float
    z_score: float
    pct_above_baseline: float | None
    matched: bool
    event_type: str = ""
    event_date: str = ""
    event_subscription: str = ""
    days_before_spike: int | None = None
    event_timing: str = ""
    match_strength: str = ""
    linked_incident: str = ""
    recommendation: str = ""
    requires_human_approval: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _robust_stats(values: np.ndarray) -> tuple[float, float]:
    """Median and a MAD-derived scale, both resistant to the spike itself."""
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    scale = mad * MAD_SCALE
    if scale == 0:
        # A flat series with one jump has MAD 0; fall back to the mean absolute
        # deviation so a genuine jump is still detectable rather than dividing
        # by zero and calling everything infinite.
        scale = float(np.mean(np.abs(values - median))) or 0.0
    return median, scale


def detect_anomalies(
    demand: pd.DataFrame,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    min_periods: int = MIN_PERIODS,
    min_deviation_units: float = DEFAULT_MIN_DEVIATION_UNITS,
) -> pd.DataFrame:
    """Flag periods where a region's demand jumped beyond its own normal range.

    Only upward spikes are reported. A collapse in demand is real but it is not
    a capacity risk, and mixing the two makes the output impossible to act on.
    """
    rows = []
    for region, grp in demand.groupby("Region"):
        grp = grp.sort_values("Period")
        values = grp["Value"].astype(float).to_numpy()
        if len(values) < min_periods:
            continue

        median, scale = _robust_stats(values)
        for period, value in zip(grp["Period"], values, strict=True):
            deviation = value - median
            z = (deviation / scale) if scale else 0.0
            # Both gates: unusual for this region *and* big enough to matter.
            if z >= z_threshold and deviation >= min_deviation_units:
                rows.append({
                    "Region": region,
                    "Period": str(period),
                    "Value": float(value),
                    "Baseline": round(median, 2),
                    "Deviation": round(float(deviation), 2),
                    "ZScore": round(min(float(z), Z_REPORTING_CAP), 2),
                    "ZCapped": bool(z > Z_REPORTING_CAP),
                    "PctAboveBaseline": (round(deviation / median * 100, 1)
                                         if median > 0 else None),
                })
    return pd.DataFrame(rows)


def _period_bounds(period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    p = pd.Period(period)
    return p.start_time, p.end_time


def match_events(
    anomalies: pd.DataFrame,
    events: pd.DataFrame,
    window_days: int = DEFAULT_MATCH_WINDOW_DAYS,
) -> list[Anomaly]:
    """Attach a business event to each spike, where one plausibly caused it.

    Plausible means: same region, and dated between `window_days` before the
    period started and the period's own end. The closest qualifying event wins,
    so a spike is explained by the most recent thing that could have caused it
    rather than the first one found.
    """
    if anomalies.empty:
        return []

    ev = events.copy()
    ev["EventDate"] = pd.to_datetime(ev["EventDate"], errors="coerce")
    ev = ev[ev["EventDate"].notna()]

    out = []
    for a in anomalies.itertuples():
        start, end = _period_bounds(a.Period)
        window_start = start - pd.Timedelta(days=window_days)

        candidates = ev[
            (ev["Region"] == a.Region)
            & (ev["EventDate"] >= window_start)
            & (ev["EventDate"] <= end)
        ].copy()

        anomaly = Anomaly(
            region=a.Region, period=a.Period, value=a.Value, baseline=a.Baseline,
            deviation=a.Deviation, z_score=a.ZScore,
            pct_above_baseline=a.PctAboveBaseline, matched=False,
        )

        if not candidates.empty:
            # Closest to the spike, not merely the first in the window.
            candidates["Distance"] = (start - candidates["EventDate"]).abs()
            # A deal that carries expected capacity outranks a campaign that
            # happens to sit closer to the spike.
            candidates["HasCapacity"] = (
                pd.to_numeric(candidates.get("ExpectedCapacityUnits"), errors="coerce")
                .fillna(0) > 0
            )
            best = candidates.sort_values(
                ["HasCapacity", "Distance"], ascending=[False, True]
            ).iloc[0]
            anomaly.matched = True
            anomaly.event_type = str(best["EventType"])
            anomaly.event_date = best["EventDate"].date().isoformat()
            anomaly.event_subscription = str(best.get("SubscriptionId", "") or "")
            anomaly.linked_incident = str(best.get("LinkedIncidentId", "") or "")
            offset = int((start - best["EventDate"]).days)
            anomaly.days_before_spike = offset
            n = abs(offset)
            unit = "day" if n == 1 else "days"
            anomaly.event_timing = (
                f"{n} {unit} before the period" if offset > 0
                else f"{n} {unit} into the period"
            )
            # An event with no expected capacity (a campaign, say) is not a
            # capacity-driving event -- matched, but not treated as a cause.
            anomaly.match_strength = (
                "strong" if float(best.get("ExpectedCapacityUnits") or 0) > 0 else "weak"
            )

        anomaly.recommendation = _recommend(anomaly)
        out.append(anomaly)

    return sorted(out, key=lambda x: (not x.matched, -x.z_score))


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _recommend(a: Anomaly) -> str:
    """What to do -- different advice for explained and unexplained spikes."""
    has_pct = a.pct_above_baseline is not None and not pd.isna(a.pct_above_baseline)
    lift = (
        f"{a.pct_above_baseline:,.0f}% above its {a.baseline:,.0f}-unit baseline"
        if has_pct
        else f"{a.deviation:,.0f} units above a near-zero baseline"
    )

    if a.matched and a.match_strength == "strong":
        return (
            f"{a.region} demand in {a.period} is {lift}, matching "
            f"{_article(a.event_type)} {a.event_type.lower()} {a.event_timing}. "
            f"Pre-provision capacity in "
            f"{a.region} rather than waiting for the next request to be denied."
        )
    if a.matched:
        return (
            f"{a.region} demand in {a.period} is {lift}. The only event on record "
            f"is {_article(a.event_type)} {a.event_type.lower()} {a.event_timing}, "
            f"which carries no "
            f"expected capacity -- treat the spike as unexplained until someone "
            f"confirms a cause."
        )
    return (
        f"{a.region} demand in {a.period} is {lift} with no matching business "
        f"event on record. Confirm whether this is organic growth or an event "
        f"we are not being told about before provisioning against it."
    )


def explain_anomalies(
    demand: pd.DataFrame,
    events: pd.DataFrame,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    window_days: int = DEFAULT_MATCH_WINDOW_DAYS,
) -> list[Anomaly]:
    """Detect and match in one call -- the module's whole job."""
    return match_events(
        detect_anomalies(demand, z_threshold=z_threshold),
        events,
        window_days=window_days,
    )
