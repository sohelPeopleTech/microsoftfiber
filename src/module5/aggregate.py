"""Step 4 -- group by region and rank by revenue exposure.

The classifier says *what happened* and the estimator says *what it cost*.
This is where those become a leaderboard someone can act on: one row per
region, ranked by exposure, carrying enough context that the recommendation
written on top of it can be specific rather than generic.

A region with zero flagged tickets still gets a row. "uksouth had 9 requests
and none of them failed" is an answer to "why is uksouth ranked lower?", and
dropping the row would make that question unanswerable.
"""

from __future__ import annotations

import pandas as pd

from .classifier import (
    DATA_QUALITY_ERROR,
    DENIED_THEN_APPROVED_LATE,
    DENIED_UNFULFILLED,
    NO_DENIAL,
    SAME_DAY_APPROVED,
)
from .config import Config
from .revenue import arr_affected

REGION_COLUMNS = [
    "Region",
    "Rank",
    "TicketsTotal",
    "TicketsFlagged",
    "DelayedCount",
    "UnfulfilledCount",
    "SameDayCount",
    "NoDenialCount",
    "DataQualityCount",
    "CustomersAffected",
    "ARRAffectedUSD",
    "RevenueExposureUSD",
    "ShareOfExposure",
    "MedianDelayDays",
    "MaxDelayDays",
    "BlockedUnits",
    "WorstIncidentId",
    "WorstIncidentExposureUSD",
]


def _worst_ticket(group: pd.DataFrame) -> tuple[str, float]:
    flagged = group[group["IsFlagged"]]
    if flagged.empty:
        return "", 0.0
    row = flagged.loc[flagged["RevenueExposureUSD"].idxmax()]
    return str(row["IncidentId"]), float(row["RevenueExposureUSD"])


def by_region(priced: pd.DataFrame, config: Config | None = None) -> pd.DataFrame:
    """One ranked row per region. Rank 1 = highest revenue exposure."""
    config = config or Config()
    rows = []

    for region, group in priced.groupby("Region", sort=False):
        flagged = group[group["IsFlagged"]]
        delays = flagged.loc[
            flagged["Category"] == DENIED_THEN_APPROVED_LATE, "DelayDays"
        ].dropna()

        worst_id, worst_exposure = _worst_ticket(group)
        counts = group["Category"].value_counts()

        rows.append(
            {
                "Region": region,
                "TicketsTotal": int(len(group)),
                "TicketsFlagged": int(len(flagged)),
                "DelayedCount": int(counts.get(DENIED_THEN_APPROVED_LATE, 0)),
                "UnfulfilledCount": int(counts.get(DENIED_UNFULFILLED, 0)),
                "SameDayCount": int(counts.get(SAME_DAY_APPROVED, 0)),
                "NoDenialCount": int(counts.get(NO_DENIAL, 0)),
                "DataQualityCount": int(counts.get(DATA_QUALITY_ERROR, 0)),
                "CustomersAffected": int(flagged["SubscriptionId"].nunique()),
                # Dedup per subscription: one customer with three bad tickets
                # is one customer's ARR at risk, not three.
                "ARRAffectedUSD": round(arr_affected(group), 2),
                "RevenueExposureUSD": round(
                    float(flagged["RevenueExposureUSD"].sum()), 2
                ),
                "MedianDelayDays": round(float(delays.median()), 1)
                if len(delays)
                else 0.0,
                "MaxDelayDays": round(float(delays.max()), 1) if len(delays) else 0.0,
                "BlockedUnits": float(flagged["BlockedUnits"].sum()),
                "WorstIncidentId": worst_id,
                "WorstIncidentExposureUSD": round(worst_exposure, 2),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=REGION_COLUMNS)

    # Exposure first; ties broken by flagged volume then name, so the ordering
    # is stable across runs rather than dependent on groupby order.
    df = df.sort_values(
        ["RevenueExposureUSD", "TicketsFlagged", "Region"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    df["Rank"] = range(1, len(df) + 1)

    total = df["RevenueExposureUSD"].sum()
    df["ShareOfExposure"] = (df["RevenueExposureUSD"] / total) if total else 0.0

    return df[REGION_COLUMNS]


def by_subscription(priced: pd.DataFrame) -> pd.DataFrame:
    """Per-customer view -- who to talk to, once a region is chosen."""
    flagged = priced[priced["IsFlagged"]]
    if flagged.empty:
        return pd.DataFrame(
            columns=[
                "SubscriptionId",
                "SubscriptionTier",
                "ARR_USD",
                "Regions",
                "TicketsFlagged",
                "UnfulfilledCount",
                "RevenueExposureUSD",
            ]
        )

    out = (
        flagged.groupby(["SubscriptionId", "SubscriptionTier"], sort=False)
        .agg(
            ARR_USD=("ARR_USD", "max"),
            Regions=("Region", lambda s: ", ".join(sorted(set(s)))),
            TicketsFlagged=("IncidentId", "count"),
            UnfulfilledCount=(
                "Category",
                lambda s: int((s == DENIED_UNFULFILLED).sum()),
            ),
            RevenueExposureUSD=("RevenueExposureUSD", "sum"),
        )
        .reset_index()
    )
    return out.sort_values("RevenueExposureUSD", ascending=False).reset_index(drop=True)


def portfolio_summary(priced: pd.DataFrame, config: Config | None = None) -> dict:
    """Totals for the headline sentence."""
    config = config or Config()
    flagged = priced[priced["IsFlagged"]]
    delays = flagged.loc[
        flagged["Category"] == DENIED_THEN_APPROVED_LATE, "DelayDays"
    ].dropna()

    total_tickets = int(len(priced))
    return {
        "as_of": str(priced["AsOf"].iloc[0].date()) if len(priced) else None,
        "tickets_total": total_tickets,
        "tickets_flagged": int(len(flagged)),
        "flagged_rate": round(len(flagged) / total_tickets, 4) if total_tickets else 0.0,
        "delayed_count": int((priced["Category"] == DENIED_THEN_APPROVED_LATE).sum()),
        "unfulfilled_count": int((priced["Category"] == DENIED_UNFULFILLED).sum()),
        "same_day_count": int((priced["Category"] == SAME_DAY_APPROVED).sum()),
        "no_denial_count": int((priced["Category"] == NO_DENIAL).sum()),
        "data_quality_count": int((priced["Category"] == DATA_QUALITY_ERROR).sum()),
        "customers_affected": int(flagged["SubscriptionId"].nunique()),
        "regions_total": int(priced["Region"].nunique()),
        "regions_affected": int(flagged["Region"].nunique()),
        "arr_affected_usd": round(arr_affected(priced), 2),
        "revenue_exposure_usd": round(float(flagged["RevenueExposureUSD"].sum()), 2),
        "median_delay_days": round(float(delays.median()), 1) if len(delays) else 0.0,
        "max_delay_days": round(float(delays.max()), 1) if len(delays) else 0.0,
        "currency": config.currency,
        "arr_reference_is_placeholder": config.arr_reference_is_placeholder,
    }


def exposure_trend(priced: pd.DataFrame, config: Config | None = None) -> pd.DataFrame:
    """Exposure by denial period -- is this getting better or worse?

    Bucketed on DeniedDate because that is when the failure started, which is
    the question a reviewer actually asks ("is our denial handling degrading?").
    """
    config = config or Config()
    flagged = priced[priced["IsFlagged"] & priced["DeniedDate"].notna()].copy()
    if flagged.empty:
        return pd.DataFrame(columns=["Period", "TicketsFlagged", "RevenueExposureUSD"])

    # Drop the tz before bucketing: periods are calendar months, and pandas
    # warns rather than silently discarding the offset.
    flagged["Period"] = flagged["DeniedDate"].dt.tz_convert(None).dt.to_period(
        config.trend_period
    )
    out = (
        flagged.groupby("Period")
        .agg(
            TicketsFlagged=("IncidentId", "count"),
            RevenueExposureUSD=("RevenueExposureUSD", "sum"),
        )
        .reset_index()
        .sort_values("Period")
    )
    out["Period"] = out["Period"].astype(str)
    out["RevenueExposureUSD"] = out["RevenueExposureUSD"].round(2)
    return out.reset_index(drop=True)
