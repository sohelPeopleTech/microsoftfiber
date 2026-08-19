"""Group, smooth, project, rank.

Two demand signals are available and they are not interchangeable:

  **Requested units** -- from the real tickets. What customers asked for. Sparse
  (60 requests across 11 regions and 5 months), so a single large request moves
  a region's line. Real, but thin.

  **Utilisation** -- from the usage curve. Dense and smooth, but generated. Good
  for shape, not evidence.

Both are exposed, labelled, and never silently mixed -- a forecast built on
generated data must not be presented with the authority of one built on
tickets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Periods with fewer than this many observations produce a forecast that is
#: arithmetic rather than evidence. Flagged, not hidden.
MIN_PERIODS_FOR_CONFIDENCE = 4

DEFAULT_WINDOW = 3
DEFAULT_HORIZON = 3


def _period_key(dates: pd.Series, period: str) -> pd.Series:
    ts = pd.to_datetime(dates, utc=True, errors="coerce").dt.tz_convert(None)
    return ts.dt.to_period(period).astype(str)


def demand_by_period(
    onto,
    period: str = "M",
    measure: str = "RequestedUnits",
) -> pd.DataFrame:
    """Requests per region per period, zero-filled across the full calendar.

    Zero-filling matters: a region with no requests in March genuinely had zero
    demand, and leaving the row out would make a moving average skip it and
    overstate the level.
    """
    fact = onto["fact_capacity_request"].copy()
    # A request is "raised" when it was denied, or approved if never denied.
    fact["When"] = fact["DeniedDate"].fillna(fact["ApprovedDate"])
    fact["Period"] = _period_key(fact["When"], period)
    fact = fact[fact["Period"].notna() & (fact["Period"] != "NaT")]

    grouped = (
        fact.groupby(["Region", "Period"])
        .agg(
            RequestCount=("IncidentId", "count"),
            RequestedUnits=("AdditionalLimitCapacity", "sum"),
            DeniedCount=("DeniedDate", lambda s: int(s.notna().sum())),
        )
        .reset_index()
    )

    regions = sorted(onto["dim_region"]["Region"])
    periods = sorted(grouped["Period"].unique())
    full = pd.MultiIndex.from_product([regions, periods], names=["Region", "Period"])
    out = (
        grouped.set_index(["Region", "Period"])
        .reindex(full, fill_value=0)
        .reset_index()
        .sort_values(["Region", "Period"])
    )
    out["Measure"] = measure
    out["Value"] = out[measure]
    out["Source"] = "ICM extract"
    return out.reset_index(drop=True)


def usage_by_period(onto, period: str = "M") -> pd.DataFrame:
    """Mean utilisation per region per period -- the dense, generated signal."""
    usage = onto["fact_usage_daily"].copy()
    usage["Period"] = _period_key(usage["Date"], period)
    out = (
        usage.groupby(["Region", "Period"])
        .agg(
            UtilisationPct=("UtilisationPct", "mean"),
            UsedUnits=("UsedUnits", "mean"),
            TotalUnits=("TotalUnits", "max"),
        )
        .reset_index()
        .sort_values(["Region", "Period"])
    )
    out["UtilisationPct"] = out["UtilisationPct"].round(2)
    out["Source"] = "generated"
    return out.reset_index(drop=True)


def moving_average(values: list[float], window: int = DEFAULT_WINDOW) -> list[float]:
    """Trailing mean. Short series average what they have rather than returning
    nothing -- an empty forecast for a young region is less useful than a
    coarse one, provided the coarseness is declared."""
    out = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        out.append(float(np.mean(values[start : i + 1])))
    return out


def _slope(values: list[float]) -> float:
    """Units per period, by least squares. Zero for a series too short to fit."""
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, np.asarray(values, dtype=float), 1)[0])


#: Trend damping. An undamped slope compounds -- three periods of +318/month
#: turns 215 into 1,169, which is extrapolation wearing a forecast's clothes.
#: Each step further out contributes phi^step of the trend, so the projection
#: flattens instead of running away. Standard damped-trend behaviour.
TREND_DAMPING = 0.6

#: Hard ceiling as a multiple of the largest period ever observed. A region
#: that has never exceeded 552 units in a month is not about to need 1,169.
FORECAST_CEILING_MULTIPLE = 2.0


def forecast_demand(
    demand: pd.DataFrame,
    window: int = DEFAULT_WINDOW,
    horizon: int = DEFAULT_HORIZON,
    damping: float = TREND_DAMPING,
) -> pd.DataFrame:
    """Project each region forward from its moving average plus a damped trend.

    Three deliberate constraints, because five sparse periods cannot support an
    unconstrained projection:

    1. **Trend is damped** -- its contribution decays by `damping` per step.
    2. **Sparse regions get no trend at all.** Fewer than
       MIN_PERIODS_FOR_CONFIDENCE observations is not a direction, it is noise;
       those regions forecast flat at their moving average.
    3. **Everything is clipped** to [0, 2x the largest period observed].

    The output says which of these applied, so a reader can see when they are
    looking at a projection versus a held level.
    """
    rows = []
    for region, grp in demand.groupby("Region"):
        grp = grp.sort_values("Period")
        values = grp["Value"].astype(float).tolist()
        periods = grp["Period"].tolist()
        if not values:
            continue

        ma = moving_average(values, window)
        level = ma[-1]
        observed = int(sum(1 for v in values if v > 0))
        confident = observed >= MIN_PERIODS_FOR_CONFIDENCE

        raw_slope = _slope(values[-max(window, 2):]) if len(values) >= 2 else 0.0
        slope = raw_slope if confident else 0.0
        ceiling = max(values) * FORECAST_CEILING_MULTIPLE if max(values) > 0 else 0.0

        last = pd.Period(periods[-1])
        for step in range(1, horizon + 1):
            # Damped: sum of phi^1..phi^step, not step * slope.
            damped = sum(damping**i for i in range(1, step + 1))
            projected = level + slope * damped
            clipped = min(max(0.0, projected), ceiling) if ceiling else 0.0
            rows.append({
                "Region": region,
                "Period": str(last + step),
                "Forecast": round(clipped, 2),
                "Level": round(level, 2),
                "SlopePerPeriod": round(slope, 3),
                "RawSlope": round(raw_slope, 3),
                "TrendApplied": bool(slope),
                "CappedAt": round(ceiling, 2),
                "WasCapped": bool(ceiling and projected > ceiling),
                "BasedOnPeriods": len(values),
                "PeriodsWithDemand": observed,
                "Confident": confident,
                "Horizon": step,
            })
    return pd.DataFrame(rows)


def growth_ranking(demand: pd.DataFrame, window: int = DEFAULT_WINDOW) -> pd.DataFrame:
    """Which regions are outpacing the others.

    Growth compares the trailing window against the equivalent leading window,
    so one exceptional month cannot crown a region. A region starting from zero
    has an undefined percentage -- reported as new demand rather than as
    infinite growth, which is the honest reading and also the useful one.
    """
    rows = []
    for region, grp in demand.groupby("Region"):
        grp = grp.sort_values("Period")
        values = grp["Value"].astype(float).tolist()
        if not values:
            continue

        k = min(window, max(1, len(values) // 2))
        first = float(np.mean(values[:k]))
        last = float(np.mean(values[-k:]))
        observed = int(sum(1 for v in values if v > 0))

        if first > 0:
            growth = (last - first) / first * 100.0
            basis = "percent change"
        elif last > 0:
            growth = float("inf")
            basis = "new demand -- no baseline to compare against"
        else:
            growth = 0.0
            basis = "no demand in the window"

        rows.append({
            "Region": region,
            "EarlyMean": round(first, 2),
            "RecentMean": round(last, 2),
            "GrowthPct": None if growth == float("inf") else round(growth, 1),
            "AbsoluteChange": round(last - first, 2),
            "SlopePerPeriod": round(_slope(values), 3),
            "PeriodsWithDemand": observed,
            "Confident": observed >= MIN_PERIODS_FOR_CONFIDENCE,
            "Basis": basis,
            "Total": round(float(np.sum(values)), 2),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Rank on absolute change so a region going 1 -> 3 does not outrank one
    # going 200 -> 400 on percentage alone.
    out = out.sort_values(["AbsoluteChange", "Total"], ascending=False).reset_index(drop=True)
    out.insert(0, "Rank", range(1, len(out) + 1))
    return out
