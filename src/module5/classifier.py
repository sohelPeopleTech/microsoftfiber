"""Denial Classifier -- agent tool #1.

Pure date-diff logic on DeniedDate / ApprovedDate. No external reference data,
no model, no network: this is the piece the design doc says to build and test
first, before trusting anything downstream.

Every ticket lands in exactly one category:

  no_denial                  approved with no denial on record (healthy)
  same_day_approved          denied, cleared inside the meaningful-delay cutoff
  denied_then_approved_late  denied, cleared after the cutoff  <- FAILURE
  denied_unfulfilled         denied, never approved            <- FAILURE
  data_quality_error         dates present but inverted; never counted, always
                             surfaced, so bad rows are visible not invisible

Only the two FAILURE categories carry revenue impact.
"""

from __future__ import annotations

import pandas as pd

from .config import Config

NO_DENIAL = "no_denial"
SAME_DAY_APPROVED = "same_day_approved"
DENIED_THEN_APPROVED_LATE = "denied_then_approved_late"
DENIED_UNFULFILLED = "denied_unfulfilled"
DATA_QUALITY_ERROR = "data_quality_error"

CATEGORIES = (
    NO_DENIAL,
    SAME_DAY_APPROVED,
    DENIED_THEN_APPROVED_LATE,
    DENIED_UNFULFILLED,
    DATA_QUALITY_ERROR,
)

#: The categories that count as a capacity-service failure.
FLAGGED_CATEGORIES = frozenset({DENIED_THEN_APPROVED_LATE, DENIED_UNFULFILLED})

CATEGORY_LABELS = {
    NO_DENIAL: "No denial on record",
    SAME_DAY_APPROVED: "Approved within the delay cut-off",
    DENIED_THEN_APPROVED_LATE: "Denied, then approved late",
    DENIED_UNFULFILLED: "Denied, never fulfilled",
    DATA_QUALITY_ERROR: "Data quality error",
}

SEVERITY_NONE = "none"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

SEVERITY_ORDER = {
    SEVERITY_NONE: 0,
    SEVERITY_LOW: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_HIGH: 3,
    SEVERITY_CRITICAL: 4,
}


def classify_ticket(
    denied_date: pd.Timestamp | None,
    approved_date: pd.Timestamp | None,
    cutoff_hours: float,
) -> tuple[str, float | None]:
    """Classify one ticket.

    Returns (category, delay_hours). delay_hours is None where no denial-to-
    approval gap exists (no_denial, denied_unfulfilled).

    >>> classify_ticket(pd.Timestamp("2025-09-03"), pd.Timestamp("2025-09-09"), 48.0)
    ('denied_then_approved_late', 144.0)
    >>> classify_ticket(pd.Timestamp("2025-09-03"), None, 48.0)
    ('denied_unfulfilled', None)
    """
    has_denial = denied_date is not None and not pd.isna(denied_date)
    has_approval = approved_date is not None and not pd.isna(approved_date)

    if not has_denial:
        # No denial recorded. An approval alone is healthy; neither date should
        # already have been dropped in Silver, but stay defensive.
        return (NO_DENIAL if has_approval else DATA_QUALITY_ERROR), None

    if not has_approval:
        return DENIED_UNFULFILLED, None

    delay_hours = (approved_date - denied_date).total_seconds() / 3600.0
    if delay_hours < 0:
        return DATA_QUALITY_ERROR, delay_hours

    # <= cutoff is normal turnaround; strictly greater is a real delay.
    if delay_hours <= cutoff_hours:
        return SAME_DAY_APPROVED, delay_hours
    return DENIED_THEN_APPROVED_LATE, delay_hours


def severity_for(category: str, delay_days: float | None, config: Config) -> str:
    """Rank how bad a flagged ticket is. Unfulfilled is always the worst case."""
    if category == DENIED_UNFULFILLED:
        return SEVERITY_CRITICAL
    if category != DENIED_THEN_APPROVED_LATE or delay_days is None:
        return SEVERITY_NONE
    if delay_days >= config.severity_high_days:
        return SEVERITY_HIGH
    if delay_days >= config.severity_medium_days:
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


def classify(gold: pd.DataFrame, config: Config | None = None) -> pd.DataFrame:
    """Classify a Gold ticket frame. Adds Category / DelayHours / DelayDays /
    IsFlagged / Severity and returns a new frame."""
    config = config or Config()
    df = gold.copy()

    # The allowance depends on what the customer is owed, so it is resolved per
    # ticket from its tier rather than applied globally.
    tiers = (
        df["SubscriptionTier"]
        if "SubscriptionTier" in df.columns
        else pd.Series([None] * len(df), index=df.index)
    )
    results = [
        classify_ticket(row.DeniedDate, row.ApprovedDate, config.cutoff_for(tier))
        for row, tier in zip(df.itertuples(index=False), tiers, strict=True)
    ]
    df["Category"] = [r[0] for r in results]
    df["DelayHours"] = [r[1] for r in results]
    df["DelayDays"] = df["DelayHours"] / 24.0
    df["IsFlagged"] = df["Category"].isin(FLAGGED_CATEGORIES)
    df["Severity"] = [
        severity_for(c, d, config)
        for c, d in zip(df["Category"], df["DelayDays"], strict=True)
    ]
    return df


def category_counts(classified: pd.DataFrame) -> dict[str, int]:
    """Counts for every category, including the ones that scored zero."""
    counts = classified["Category"].value_counts().to_dict()
    return {c: int(counts.get(c, 0)) for c in CATEGORIES}


def evaluate_against_labels(
    classified: pd.DataFrame,
    expected: pd.DataFrame,
    label_column: str = "ExpectedCategory",
) -> dict:
    """Score the classifier against the pre-labelled sample.

    Step 2 of the build plan: this must pass before the classifier is pointed
    at real data. Returns accuracy, per-category counts and every mismatch.
    """
    left = classified[["IncidentId", "Category", "DelayHours"]].copy()
    left["IncidentId"] = left["IncidentId"].astype(str).str.strip()
    right = expected[["IncidentId", label_column]].copy()
    right["IncidentId"] = right["IncidentId"].astype(str).str.strip()

    merged = left.merge(right, on="IncidentId", how="outer", indicator=True)

    unlabelled = merged.loc[merged["_merge"] == "left_only", "IncidentId"].tolist()
    unmatched_labels = merged.loc[merged["_merge"] == "right_only", "IncidentId"].tolist()
    scored = merged[merged["_merge"] == "both"].copy()
    scored["correct"] = scored["Category"] == scored[label_column]

    mismatches = [
        {
            "IncidentId": r.IncidentId,
            "expected": getattr(r, label_column),
            "predicted": r.Category,
            "delay_hours": None if pd.isna(r.DelayHours) else round(r.DelayHours, 1),
        }
        for r in scored[~scored["correct"]].itertuples(index=False)
    ]

    per_category = {}
    for cat, grp in scored.groupby(label_column):
        per_category[str(cat)] = {
            "n": int(len(grp)),
            "correct": int(grp["correct"].sum()),
        }

    return {
        "n_scored": int(len(scored)),
        "n_correct": int(scored["correct"].sum()),
        "accuracy": float(scored["correct"].mean()) if len(scored) else 0.0,
        "passed": bool(len(scored)) and bool(scored["correct"].all()),
        "per_category": per_category,
        "mismatches": mismatches,
        "tickets_without_label": unlabelled,
        "labels_without_ticket": unmatched_labels,
    }
