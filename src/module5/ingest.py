"""Bronze -> Silver -> Gold ingestion for the ICM capacity ticket extract.

Mirrors the medallion layout in Section 9 of the solution document:

  Bronze  raw rows exactly as exported, nothing coerced
  Silver  typed, trimmed, de-duplicated, with data-quality findings recorded
  Gold    Silver joined to the Subscription reference (Incident -> Subscription
          -> Region dimensional model), ready for the classifier and estimator

Locally the "lakehouse" is just parquet/CSV on disk. In Fabric these three
functions become the three notebook stages against Lakehouse tables; the
signatures do not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

TICKET_COLUMNS = [
    "IncidentId",
    "SubscriptionId",
    "TenantId",
    "Region",
    "CurrentLimitCapacity",
    "AdditionalLimitCapacity",
    "NewLimitCapacity",
    "DeniedDate",
    "ApprovedDate",
]

DATE_COLUMNS = ["DeniedDate", "ApprovedDate"]
CAPACITY_COLUMNS = [
    "CurrentLimitCapacity",
    "AdditionalLimitCapacity",
    "NewLimitCapacity",
]


@dataclass
class DataQualityReport:
    """Anything Silver had to drop, fix, or flag. Printed with every run."""

    rows_in: int = 0
    rows_out: int = 0
    duplicate_incident_ids: list = field(default_factory=list)
    missing_region: list = field(default_factory=list)
    missing_both_dates: list = field(default_factory=list)
    approved_before_denied: list = field(default_factory=list)
    negative_capacity: list = field(default_factory=list)
    unmatched_subscriptions: list = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not any(
            [
                self.duplicate_incident_ids,
                self.missing_region,
                self.missing_both_dates,
                self.approved_before_denied,
                self.negative_capacity,
                self.unmatched_subscriptions,
            ]
        )

    def to_dict(self) -> dict:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "is_clean": self.is_clean,
            "duplicate_incident_ids": self.duplicate_incident_ids,
            "missing_region": self.missing_region,
            "missing_both_dates": self.missing_both_dates,
            "approved_before_denied": self.approved_before_denied,
            "negative_capacity": self.negative_capacity,
            "unmatched_subscriptions": self.unmatched_subscriptions,
        }

    def summary_lines(self) -> list[str]:
        if self.is_clean:
            return [f"Data quality: clean ({self.rows_out} tickets)."]
        lines = [f"Data quality: {self.rows_out}/{self.rows_in} tickets usable."]
        checks = [
            ("duplicate IncidentIds (kept first)", self.duplicate_incident_ids),
            ("missing Region", self.missing_region),
            ("no DeniedDate and no ApprovedDate", self.missing_both_dates),
            ("ApprovedDate before DeniedDate", self.approved_before_denied),
            ("negative capacity value", self.negative_capacity),
            ("SubscriptionId absent from ARR reference", self.unmatched_subscriptions),
        ]
        for label, ids in checks:
            if ids:
                shown = ", ".join(str(i) for i in ids[:5])
                more = f" (+{len(ids) - 5} more)" if len(ids) > 5 else ""
                lines.append(f"  - {len(ids)} x {label}: {shown}{more}")
        return lines


# --------------------------------------------------------------------------
# Bronze
# --------------------------------------------------------------------------


def load_bronze(source: str | Path, sheet: str = "ICM_Data") -> pd.DataFrame:
    """Read the raw extract as-is. .xlsx (multi-sheet workbook) or .csv."""
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"Ticket extract not found: {source}")
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        df = pd.read_excel(source, sheet_name=sheet, dtype=str)
    else:
        df = pd.read_csv(source, dtype=str)

    missing = [c for c in TICKET_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"{source.name} is missing required column(s): {missing}. "
            f"Expected the ICM schema: {TICKET_COLUMNS}"
        )
    return df[TICKET_COLUMNS].copy()


def load_subscription_reference(source: str | Path) -> pd.DataFrame:
    """Subscription tier / ARR reference.

    Placeholder data today (Section 11, Open Items). Swapping in the real
    reference means pointing this at a different table -- nothing downstream
    changes as long as the SubscriptionId / SubscriptionTier / ARR_USD columns
    keep their names.
    """
    source = Path(source)
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        df = pd.read_excel(source, sheet_name="Subscription_Reference")
    else:
        df = pd.read_csv(source)

    required = {"SubscriptionId", "SubscriptionTier", "ARR_USD"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Subscription reference missing column(s): {sorted(missing)}")

    df = df[["SubscriptionId", "SubscriptionTier", "ARR_USD"]].copy()
    # The workbook carries a trailing free-text provenance note; drop non-rows.
    df = df[df["SubscriptionId"].notna()]
    df["SubscriptionId"] = df["SubscriptionId"].astype(str).str.strip()
    df = df[df["SubscriptionId"].str.len() == 36]
    df["ARR_USD"] = pd.to_numeric(df["ARR_USD"], errors="coerce").fillna(0.0)
    return df.drop_duplicates("SubscriptionId").reset_index(drop=True)


def load_expected_classifications(source: str | Path) -> pd.DataFrame:
    """Ground-truth labels used to test the classifier before trusting it."""
    source = Path(source)
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        df = pd.read_excel(source, sheet_name="Expected_Classifications")
    else:
        df = pd.read_csv(source)
    df["IncidentId"] = df["IncidentId"].astype(str).str.strip()
    return df


# --------------------------------------------------------------------------
# Silver
# --------------------------------------------------------------------------


def to_silver(bronze: pd.DataFrame) -> tuple[pd.DataFrame, DataQualityReport]:
    """Type and clean. Returns the clean frame plus what had to be dropped."""
    dq = DataQualityReport(rows_in=len(bronze))
    df = bronze.copy()

    for col in ("IncidentId", "SubscriptionId", "TenantId", "Region"):
        df[col] = df[col].astype(str).str.strip().replace({"nan": "", "None": ""})

    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    for col in CAPACITY_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    dup_mask = df.duplicated("IncidentId", keep="first")
    dq.duplicate_incident_ids = df.loc[dup_mask, "IncidentId"].tolist()
    df = df[~dup_mask]

    no_region = df["Region"] == ""
    dq.missing_region = df.loc[no_region, "IncidentId"].tolist()
    df = df[~no_region]

    no_dates = df["DeniedDate"].isna() & df["ApprovedDate"].isna()
    dq.missing_both_dates = df.loc[no_dates, "IncidentId"].tolist()
    df = df[~no_dates]

    # Recorded, not dropped: the classifier gives these their own category so a
    # reviewer sees them rather than having them silently vanish.
    inverted = (
        df["DeniedDate"].notna()
        & df["ApprovedDate"].notna()
        & (df["ApprovedDate"] < df["DeniedDate"])
    )
    dq.approved_before_denied = df.loc[inverted, "IncidentId"].tolist()

    negative = (df[CAPACITY_COLUMNS] < 0).any(axis=1)
    dq.negative_capacity = df.loc[negative, "IncidentId"].tolist()

    df[CAPACITY_COLUMNS] = df[CAPACITY_COLUMNS].fillna(0)
    dq.rows_out = len(df)
    return df.reset_index(drop=True), dq


# --------------------------------------------------------------------------
# Gold
# --------------------------------------------------------------------------


def to_gold(
    silver: pd.DataFrame,
    subscriptions: pd.DataFrame,
    dq: DataQualityReport | None = None,
) -> pd.DataFrame:
    """Join Silver tickets to subscription context (the Gold dimensional model)."""
    gold = silver.merge(subscriptions, on="SubscriptionId", how="left")

    unmatched = gold.loc[gold["SubscriptionTier"].isna(), "SubscriptionId"]
    if dq is not None:
        dq.unmatched_subscriptions = sorted(unmatched.unique().tolist())

    # An unmatched subscription contributes 0 exposure rather than crashing the
    # run -- it still appears in the ticket counts, and in the DQ report.
    gold["SubscriptionTier"] = gold["SubscriptionTier"].fillna("Unknown")
    gold["ARR_USD"] = pd.to_numeric(gold["ARR_USD"], errors="coerce").fillna(0.0)
    return gold


def load_gold(
    ticket_source: str | Path,
    subscription_source: str | Path | None = None,
) -> tuple[pd.DataFrame, DataQualityReport]:
    """Convenience: run the whole medallion in one call."""
    if subscription_source is None:
        subscription_source = ticket_source
    bronze = load_bronze(ticket_source)
    silver, dq = to_silver(bronze)
    subs = load_subscription_reference(subscription_source)
    return to_gold(silver, subs, dq), dq
