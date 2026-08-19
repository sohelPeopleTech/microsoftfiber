"""Outlier detection on daily utilisation, and what to do with what it finds.

Review asked for this specifically: IQR and box-plot to identify points sitting
outside the normal distribution, correlate them against known events, and then
**remove them before training the forecast** -- "these are anomalies, I need to
treat them in my regular data" -- so the model learns the underlying trend
rather than learning to expect another signed deal.

That last step is the whole point. Module 4 already detects monthly spikes and
attributes them to business events; this works on the daily utilisation series
that the forecast is fitted to, and its output feeds `forecast.forecast_region`
as the set of dates to drop.

    daily series -> seasonal detrend -> IQR fences -> outliers
                 -> match to events  -> explained / unexplained
                 -> exclusion list   -> forecast fits on what is left

WHAT IT REFUSES TO DO
    Explain everything. An outlier with no event inside the window is reported
    as unexplained, because a detector that finds a cause for every jump is
    pattern-matching and its answers cannot be trusted. Both kinds are excluded
    from training -- an unexplained spike is still not the trend -- but only the
    matched ones carry a stated cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: Tukey's fence. 1.5 x IQR is the standard box-plot whisker; 3.0 would only
#: catch the extreme outliers and would leave deal-driven jumps in the training
#: data, which is the thing this exists to prevent.
IQR_MULTIPLIER = 1.5

#: Weekly cycle, removed before fencing so an ordinary Monday is not an outlier.
SEASON = 7

#: How far before a spike an event may sit and still be considered its cause.
#: Wide enough for provisioning to follow a signature, narrow enough that the
#: match means something.
EVENT_WINDOW_DAYS = 21


@dataclass
class Outlier:
    region: str
    date: str
    value: float
    expected: float
    deviation: float
    direction: str
    #: Populated only when an event actually sits in the window.
    event_type: str | None = None
    event_date: str | None = None
    days_before: int | None = None
    explained: bool = False

    def to_dict(self) -> dict:
        return {
            "region": self.region, "date": self.date,
            "value": round(self.value, 2), "expected": round(self.expected, 2),
            "deviation": round(self.deviation, 2), "direction": self.direction,
            "eventType": self.event_type, "eventDate": self.event_date,
            "daysBefore": self.days_before, "explained": self.explained,
        }


@dataclass
class RegionAnomalies:
    region: str
    outliers: list = field(default_factory=list)
    lower_fence: float = 0.0
    upper_fence: float = 0.0
    q1: float = 0.0
    q3: float = 0.0
    median: float = 0.0
    n_points: int = 0

    @property
    def excluded_dates(self) -> set[str]:
        """Every outlier, explained or not. An unexplained spike is still not
        the trend, so it is kept out of the fit either way."""
        return {o.date for o in self.outliers}

    def to_dict(self) -> dict:
        return {
            "region": self.region,
            "outliers": [o.to_dict() for o in self.outliers],
            "boxplot": {
                "q1": round(self.q1, 2), "median": round(self.median, 2),
                "q3": round(self.q3, 2),
                "lowerFence": round(self.lower_fence, 2),
                "upperFence": round(self.upper_fence, 2),
                "iqr": round(self.q3 - self.q1, 2),
            },
            "nPoints": self.n_points,
            "nExplained": sum(1 for o in self.outliers if o.explained),
            "nUnexplained": sum(1 for o in self.outliers if not o.explained),
        }


#: Window for the rolling median that carries the trend. Wide enough to be
#: unmoved by a single day, narrow enough to follow real growth.
TREND_WINDOW = 21


def _residual(y: np.ndarray) -> np.ndarray:
    """Strip the trend and the weekly cycle, leaving noise to fence.

    Both removals are necessary and the trend one is easy to miss. Utilisation
    here climbs from roughly 56% to 75% over the window, so fencing a merely
    deseasonalised series measures the spread of the *trend* -- the fences come
    out at 28% to 103% and nothing is ever an outlier. Detrending first is what
    makes a spike visible against where the series was at the time.
    """
    if len(y) < 2 * SEASON:
        return y - np.median(y)

    # Centred rolling median: robust to the spikes we are trying to find, which
    # a rolling mean would absorb into the trend it is supposed to expose.
    trend = pd.Series(y).rolling(TREND_WINDOW, center=True, min_periods=1).median().to_numpy()
    detrended = y - trend

    effect = np.array([np.median(detrended[i::SEASON]) for i in range(SEASON)])
    effect = effect - effect.mean()
    return detrended - np.array([effect[i % SEASON] for i in range(len(y))])


def detect(usage: pd.DataFrame, region: str, events: pd.DataFrame | None = None,
           multiplier: float = IQR_MULTIPLIER) -> RegionAnomalies:
    """Box-plot outliers on one region's daily utilisation, matched to events."""
    series = (usage[usage["Region"] == region]
              .sort_values("Date").drop_duplicates("Date"))
    if series.empty:
        return RegionAnomalies(region=region)

    y = series["UtilisationPct"].to_numpy(dtype=float)
    dates = pd.to_datetime(series["Date"])
    residual = _residual(y)

    q1, q3 = np.percentile(residual, [25, 75])
    iqr = q3 - q1
    lower, upper = q1 - multiplier * iqr, q3 + multiplier * iqr

    here = pd.DataFrame()
    if events is not None and len(events) and "Region" in events.columns:
        here = events[events["Region"] == region].copy()
        if len(here):
            here["EventDate"] = pd.to_datetime(here["EventDate"])

    outliers = []
    for idx, (date, value, resid) in enumerate(zip(dates, y, residual, strict=True)):
        if lower <= resid <= upper:
            continue
        out = Outlier(
            region=region,
            date=date.date().isoformat(),
            value=float(value),
            expected=float(value - resid + np.median(residual)),
            deviation=float(resid - (upper if resid > upper else lower)),
            direction="above" if resid > upper else "below",
        )

        # Only an upward spike is worth attributing -- a dip is not a deal.
        if out.direction == "above" and len(here):
            window = here[(here["EventDate"] <= date)
                          & (here["EventDate"] >= date - pd.Timedelta(days=EVENT_WINDOW_DAYS))]
            if len(window):
                match = window.sort_values("EventDate").iloc[-1]
                out.event_type = str(match.get("EventType", "") or "")
                out.event_date = match["EventDate"].date().isoformat()
                out.days_before = int((date - match["EventDate"]).days)
                out.explained = True
        outliers.append(out)

    return RegionAnomalies(
        region=region, outliers=outliers,
        q1=float(q1), q3=float(q3), median=float(np.median(residual)),
        lower_fence=float(lower), upper_fence=float(upper),
        n_points=len(y),
    )


def detect_all(usage: pd.DataFrame, events: pd.DataFrame | None = None) -> dict:
    """region -> RegionAnomalies, for every region in the usage table."""
    return {r: detect(usage, r, events) for r in sorted(usage["Region"].unique())}


def exclusion_map(usage: pd.DataFrame, events: pd.DataFrame | None = None) -> dict:
    """region -> dates the forecast should not train on."""
    return {r: a.excluded_dates for r, a in detect_all(usage, events).items()}


__all__ = ["detect", "detect_all", "exclusion_map", "Outlier", "RegionAnomalies",
           "IQR_MULTIPLIER", "EVENT_WINDOW_DAYS", "SEASON", "TREND_WINDOW"]
