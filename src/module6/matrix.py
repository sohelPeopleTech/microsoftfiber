"""The matrix, and the gate the other modules should pass through.

Statuses are ordered, not merely labelled -- Live beats Preview beats Planned
beats Unavailable -- because "is it good enough" is the real question and it
needs a comparison, not a string equality check.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

LIVE = "Live"
PREVIEW = "Preview"
PLANNED = "Planned"
UNAVAILABLE = "Unavailable"

#: Higher is better. Used for "at least Preview" style questions.
RANK = {UNAVAILABLE: 0, PLANNED: 1, PREVIEW: 2, LIVE: 3}

#: What counts as usable by default. Preview is deliberately excluded: telling
#: someone a feature is available when it carries no production commitment is
#: how a capacity recommendation becomes an outage.
DEFAULT_MINIMUM = LIVE


@dataclass
class Availability:
    feature: str
    region: str
    status: str
    available: bool
    minimum_required: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def availability_matrix(entities) -> pd.DataFrame:
    """Feature x region, wide -- the shape a person reads."""
    bridge = entities["bridge_feature_region"]
    wide = bridge.pivot(index="Feature", columns="Region", values="Status")
    return wide.sort_index()


def is_available(
    entities,
    feature: str,
    region: str,
    minimum: str = DEFAULT_MINIMUM,
) -> Availability:
    """Direct answer for one feature in one region.

    An unknown feature or region raises rather than returning False -- silently
    answering "no" to a typo is how someone concludes a feature is missing when
    it is only misspelled.
    """
    bridge = entities["bridge_feature_region"]
    known_features = set(bridge["Feature"])
    known_regions = set(bridge["Region"])

    if feature not in known_features:
        raise KeyError(f"unknown feature {feature!r}. Known: {', '.join(sorted(known_features))}")
    if region not in known_regions:
        raise KeyError(f"unknown region {region!r}. Known: {', '.join(sorted(known_regions))}")
    if minimum not in RANK:
        raise ValueError(f"minimum must be one of {', '.join(RANK)}")

    row = bridge[(bridge["Feature"] == feature) & (bridge["Region"] == region)].iloc[0]
    status = str(row["Status"])
    ok = RANK[status] >= RANK[minimum]

    if ok:
        note = f"{feature} is {status.lower()} in {region}."
    elif status == PREVIEW:
        note = (
            f"{feature} is in preview in {region} -- usable for evaluation, but it "
            f"carries no production commitment."
        )
    elif status == PLANNED:
        note = f"{feature} is planned for {region} but not yet available."
    else:
        elsewhere = bridge[(bridge["Feature"] == feature) & (bridge["Status"] == LIVE)]
        where = ", ".join(sorted(elsewhere["Region"])[:3])
        note = (
            f"{feature} is not available in {region}."
            + (f" It is live in {where}." if where else " It is not live anywhere yet.")
        )

    return Availability(
        feature=feature, region=region, status=status, available=ok,
        minimum_required=minimum, note=note,
    )


def check_expansion(
    entities,
    region: str,
    features: list[str] | None = None,
    minimum: str = DEFAULT_MINIMUM,
) -> dict:
    """The gate for a capacity recommendation.

    Before Module 1, 4 or 5 says "put capacity in this region", this says what
    that region cannot yet do. A recommendation that ignores it can be
    technically correct and still wrong.
    """
    bridge = entities["bridge_feature_region"]
    features = features or sorted(bridge["Feature"].unique())
    results = [is_available(entities, f, region, minimum) for f in features]

    missing = [r for r in results if not r.available]
    return {
        "region": region,
        "minimum_required": minimum,
        "features_checked": len(results),
        "features_available": len(results) - len(missing),
        "blocked_features": [r.feature for r in missing],
        "clear": not missing,
        "detail": [r.to_dict() for r in results],
        "summary": (
            f"{region} supports all {len(results)} features at {minimum} or better."
            if not missing
            else f"{region} is missing {len(missing)} of {len(results)} features at "
                 f"{minimum} or better: {', '.join(r.feature for r in missing)}."
        ),
    }


def region_summary(entities, minimum: str = DEFAULT_MINIMUM) -> pd.DataFrame:
    """How complete each region is -- the ranking an expansion decision needs."""
    bridge = entities["bridge_feature_region"].copy()
    bridge["Rank"] = bridge["Status"].map(RANK)
    threshold = RANK[minimum]

    out = (
        bridge.groupby("Region")
        .agg(
            FeaturesTotal=("Feature", "count"),
            FeaturesAvailable=("Rank", lambda s: int((s >= threshold).sum()),),
            Live=("Status", lambda s: int((s == LIVE).sum())),
            Preview=("Status", lambda s: int((s == PREVIEW).sum())),
            Planned=("Status", lambda s: int((s == PLANNED).sum())),
            Unavailable=("Status", lambda s: int((s == UNAVAILABLE).sum())),
        )
        .reset_index()
    )
    out["CoveragePct"] = (out["FeaturesAvailable"] / out["FeaturesTotal"] * 100).round(1)
    return out.sort_values(["CoveragePct", "Region"], ascending=[False, True]).reset_index(drop=True)


def feature_summary(entities) -> pd.DataFrame:
    """How far each feature has rolled out -- the view a product owner wants."""
    bridge = entities["bridge_feature_region"].copy()
    out = (
        bridge.groupby("Feature")
        .agg(
            Regions=("Region", "count"),
            Live=("Status", lambda s: int((s == LIVE).sum())),
            Preview=("Status", lambda s: int((s == PREVIEW).sum())),
            Planned=("Status", lambda s: int((s == PLANNED).sum())),
            Unavailable=("Status", lambda s: int((s == UNAVAILABLE).sum())),
        )
        .reset_index()
    )
    out["LivePct"] = (out["Live"] / out["Regions"] * 100).round(1)
    return out.sort_values("LivePct", ascending=False).reset_index(drop=True)
