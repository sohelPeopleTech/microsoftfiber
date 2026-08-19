"""Revenue-Impact Estimator -- agent tool #2.

Turns a technical shortfall into a business figure. Two numbers come out of
this, and they answer different questions -- keeping them apart is the whole
point, because conflating them is how a credible estimate turns into a number
nobody trusts at the demo.

  ARR_affected      Total ARR of the customers who hit a failure. Headline,
                    blast-radius framing -- "4 requests affected $1.25M in
                    customer revenue". Deduplicated per subscription, so one
                    customer with three bad tickets is counted once.

  Revenue exposure  Risk-adjusted estimate of the revenue actually at stake,
                    not the customer's whole book:

                      exposure = ARR x capacity_share x days_unavailable / 365

                    capacity_share     = requested delta / total requested, i.e.
                                         how much of what they needed was missing
                    days_unavailable   = the delay for a late approval;
                                         denial -> as_of for an unfulfilled one,
                                         capped by config.unfulfilled_cap_days

Both are illustrative while the ARR reference is a placeholder (Section 11,
Open Items). The provenance flag rides along on the output so no downstream
message can quietly drop the caveat.
"""

from __future__ import annotations

import pandas as pd

from .classifier import DENIED_THEN_APPROVED_LATE, DENIED_UNFULFILLED
from .config import Config


def resolve_as_of(classified: pd.DataFrame, config: Config) -> pd.Timestamp:
    """Evaluation date: config override, else the latest date in the data.

    Defaulting to the data (not `today`) keeps repeated runs over a fixed
    extract byte-identical, which matters for the ground-truth tests.
    """
    if config.as_of:
        return pd.Timestamp(config.as_of, tz="UTC")
    latest = max(
        classified["DeniedDate"].max(skipna=True),
        classified["ApprovedDate"].max(skipna=True),
    )
    if pd.isna(latest):
        raise ValueError("No usable dates in ticket data; set config.as_of explicitly.")
    return pd.Timestamp(latest)


def size_shortfall(classified: pd.DataFrame) -> pd.DataFrame:
    """Requested vs. granted capacity, per ticket. No revenue involved yet."""
    df = classified.copy()
    df["RequestedCapacity"] = df["CurrentLimitCapacity"] + df["AdditionalLimitCapacity"]
    df["GrantedCapacity"] = df["NewLimitCapacity"]

    # Units still missing at as_of: > 0 only for never-fulfilled tickets.
    df["UnmetUnits"] = (df["RequestedCapacity"] - df["GrantedCapacity"]).clip(lower=0)

    # Units the customer went without during the failure window. For a late
    # approval that is the delta they eventually got; for an unfulfilled
    # ticket it is the delta they never got.
    df["BlockedUnits"] = df["AdditionalLimitCapacity"].where(df["IsFlagged"], 0.0)

    # Share of the requested footprint that was missing. Guard the degenerate
    # request-of-zero case rather than dividing by it.
    df["CapacityShare"] = 0.0
    usable = df["RequestedCapacity"] > 0
    df.loc[usable, "CapacityShare"] = (
        df.loc[usable, "BlockedUnits"] / df.loc[usable, "RequestedCapacity"]
    ).clip(0.0, 1.0)
    return df


def estimate_impact(classified: pd.DataFrame, config: Config | None = None) -> pd.DataFrame:
    """Per-ticket revenue exposure. Unflagged tickets get 0.0, not dropped."""
    config = config or Config()
    as_of = resolve_as_of(classified, config)
    df = size_shortfall(classified)

    days_open = (as_of - df["DeniedDate"]).dt.total_seconds() / 86400.0

    days_unavailable = pd.Series(0.0, index=df.index)
    late = df["Category"] == DENIED_THEN_APPROVED_LATE
    days_unavailable[late] = df.loc[late, "DelayDays"]

    unfulfilled = df["Category"] == DENIED_UNFULFILLED
    days_unavailable[unfulfilled] = (
        days_open[unfulfilled].clip(lower=0.0, upper=config.unfulfilled_cap_days)
    )

    df["DaysUnavailable"] = days_unavailable.fillna(0.0)
    df["RevenueExposureUSD"] = (
        df["ARR_USD"]
        * df["CapacityShare"]
        * (df["DaysUnavailable"] / config.annualisation_days)
    ).round(2)

    # ARR of the affected customer, carried per ticket. Deduplicate by
    # SubscriptionId before summing -- see arr_affected().
    df["ARRAffectedUSD"] = df["ARR_USD"].where(df["IsFlagged"], 0.0)
    df["AsOf"] = as_of
    return df


def arr_affected(priced: pd.DataFrame) -> float:
    """Total ARR of distinct subscriptions with at least one flagged ticket."""
    flagged = priced[priced["IsFlagged"]]
    if flagged.empty:
        return 0.0
    return float(flagged.drop_duplicates("SubscriptionId")["ARR_USD"].sum())


def explain_ticket(row: pd.Series, config: Config) -> str:
    """One-line, auditable arithmetic for a single ticket.

    Backs the "why is this number what it is?" follow-up in the application.
    """
    if not row["IsFlagged"]:
        return (
            f"{row['IncidentId']} ({row['Region']}): {row['Category']} -- "
            f"not a service failure, excluded from impact."
        )
    return (
        f"{row['IncidentId']} ({row['Region']}, {row['SubscriptionTier']}): "
        f"{row['Category']}, {row['BlockedUnits']:.0f} of "
        f"{row['RequestedCapacity']:.0f} units blocked "
        f"({row['CapacityShare']:.0%}) for {row['DaysUnavailable']:.1f} days. "
        f"Exposure = ${row['ARR_USD']:,.0f} ARR x {row['CapacityShare']:.0%} x "
        f"{row['DaysUnavailable']:.1f}/{config.annualisation_days:.0f} days = "
        f"${row['RevenueExposureUSD']:,.0f}."
    )
