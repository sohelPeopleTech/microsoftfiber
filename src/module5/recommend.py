"""Step 5 -- one short, specific recommendation per top region.

Deliberately rule-based. The design doc asks for "what to change and why", and
a number a reviewer can check beats a fluent paragraph they can't: every
recommendation here carries the arithmetic that produced it, so the human
approving it in the application can disagree with the *policy* without having to
re-derive the *evidence*.

The optional LLM layer (narrative.py) only rewrites the prose around these
fields. It never invents a figure, and the module runs fully without it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from .classifier import DENIED_THEN_APPROVED_LATE, DENIED_UNFULFILLED
from .config import Config
from .formatting import money

# Failure modes drive which lever we recommend pulling.
MODE_UNFULFILLED = "unfulfilled_dominant"
MODE_DELAY = "delay_dominant"
MODE_MIXED = "mixed"

#: Share of a region's failed requests a proposed threshold should clear before
#: we bother recommending it. Below this, raising the ceiling is not the fix.
THRESHOLD_COVERAGE_TARGET = 0.8


@dataclass
class Recommendation:
    region: str
    rank: int
    failure_mode: str
    headline: str
    action: str
    rationale: str
    # The fields a reader scans for, kept separate so the card can label them
    # rather than burying them in a paragraph.
    problem: str = ""      # what went wrong, in counts
    cause: str = ""        # why it went wrong -- picks the lever
    impact: str = ""       # what it cost
    effect: str = ""       # what the action would have prevented
    owner: str = ""        # who is accountable for acting
    evidence: list = field(default_factory=list)
    revenue_exposure_usd: float = 0.0
    arr_affected_usd: float = 0.0
    tickets_flagged: int = 0
    customers_affected: int = 0
    suggested_threshold_units: float | None = None
    threshold_coverage: float | None = None
    requires_human_review: bool = True
    decision_note: str = ""   # set by the pipeline from the decisions log
    alert_state: str = "new"  # new / pending / escalate / reopened / resolved
    caveat: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def as_text(self) -> str:
        lines = [self.headline, "", f"Recommendation: {self.action}", "", self.rationale]
        if self.evidence:
            lines += ["", "Evidence:"] + [f"  - {e}" for e in self.evidence]
        if self.caveat:
            lines += ["", self.caveat]
        return "\n".join(lines)


def classify_failure_mode(row: pd.Series) -> str:
    delayed, unfulfilled = int(row["DelayedCount"]), int(row["UnfulfilledCount"])
    if unfulfilled and not delayed:
        return MODE_UNFULFILLED
    if delayed and not unfulfilled:
        return MODE_DELAY
    if unfulfilled >= 2 * max(delayed, 1):
        return MODE_UNFULFILLED
    if delayed >= 2 * max(unfulfilled, 1):
        return MODE_DELAY
    return MODE_MIXED


def suggest_threshold(
    priced: pd.DataFrame, region: str, coverage_target: float = THRESHOLD_COVERAGE_TARGET
) -> tuple[float | None, float | None]:
    """Smallest observed capacity ceiling that would have cleared most failures.

    Capacity values in this data sit on a discrete ladder (3, 4, 8, ... 512),
    so we walk the ladder of *requested* sizes among that region's failed
    tickets and return the first rung covering `coverage_target` of them --
    rather than inventing an off-ladder number nobody can provision.
    """
    flagged = priced[(priced["Region"] == region) & priced["IsFlagged"]]
    requested = flagged["RequestedCapacity"].dropna()
    requested = requested[requested > 0]
    if requested.empty:
        return None, None

    for rung in sorted(requested.unique()):
        coverage = float((requested <= rung).mean())
        if coverage >= coverage_target:
            return float(rung), round(coverage, 2)

    ceiling = float(requested.max())
    return ceiling, 1.0


def _evidence_lines(
    region_row: pd.Series, priced: pd.DataFrame, config: Config, limit: int = 3
) -> list[str]:
    """The two or three worst tickets, named, so the claim is checkable."""
    flagged = priced[(priced["Region"] == region_row["Region"]) & priced["IsFlagged"]]
    worst = flagged.nlargest(limit, "RevenueExposureUSD")

    lines = []
    for row in worst.itertuples(index=False):
        if row.Category == DENIED_UNFULFILLED:
            window = f"still unfulfilled after {row.DaysUnavailable:.0f} days"
        else:
            window = f"approved {row.DelayDays:.0f} days late"
        lines.append(
            f"Incident {row.IncidentId} ({row.SubscriptionTier} customer): "
            f"{row.AdditionalLimitCapacity:.0f} units requested, {window} "
            f"-- {money(row.RevenueExposureUSD)} exposure"
        )
    return lines


def recommend_for_region(
    region_row: pd.Series, priced: pd.DataFrame, config: Config
) -> Recommendation:
    region = str(region_row["Region"])
    mode = classify_failure_mode(region_row)
    exposure = float(region_row["RevenueExposureUSD"])
    arr = float(region_row["ARRAffectedUSD"])
    flagged_n = int(region_row["TicketsFlagged"])
    customers = int(region_row["CustomersAffected"])
    delayed = int(region_row["DelayedCount"])
    unfulfilled = int(region_row["UnfulfilledCount"])

    threshold, coverage = suggest_threshold(priced, region)

    headline = (
        f"{region} ranks #{int(region_row['Rank'])} by revenue exposure this period "
        f"-- {flagged_n} of {int(region_row['TicketsTotal'])} capacity requests were "
        f"delayed or unfulfilled, affecting {customers} customer(s) worth "
        f"{money(arr)} in ARR, with {money(exposure)} of that revenue at risk."
    )

    if mode == MODE_UNFULFILLED:
        action = (
            f"Raise the baseline capacity ceiling in {region} to "
            f"{threshold:.0f} units and route anything above it to a named owner "
            f"with a fulfilment SLA."
            if threshold
            else f"Give {region} denials a named owner and a fulfilment SLA."
        )
        rationale = (
            f"{unfulfilled} request(s) in {region} were denied and never fulfilled "
            f"-- these are not slow approvals, they are requests that fell out of "
            f"the process entirely, so a faster approval queue would not have "
            f"caught them. The exposure accrues for every day they stay open."
        )
    elif mode == MODE_DELAY:
        action = (
            f"Raise the auto-approval threshold in {region} to {threshold:.0f} units."
            if threshold
            else f"Shorten the approval path for {region} capacity requests."
        )
        rationale = (
            f"{delayed} request(s) were approved a median of "
            f"{region_row['MedianDelayDays']:.0f} days after denial "
            f"(worst: {region_row['MaxDelayDays']:.0f} days). Every one was "
            f"eventually granted, so the denial did not prevent the capacity -- "
            f"it only delayed it, and the customer carried the shortfall in the "
            f"meantime."
        )
    else:
        action = (
            f"Raise the baseline capacity ceiling in {region} to "
            f"{threshold:.0f} units and add a fulfilment check on denials that "
            f"stay open past {config.severity_medium_days:.0f} days."
            if threshold
            else f"Review {region} denial handling end to end."
        )
        rationale = (
            f"{region} fails in two ways at once: {delayed} delayed approval(s) "
            f"and {unfulfilled} request(s) never fulfilled. Threshold alone will "
            f"not fix the unfulfilled half, and an SLA alone will not stop the "
            f"denials being issued."
        )

    if threshold and coverage:
        rationale += (
            f" A {threshold:.0f}-unit ceiling would have cleared "
            f"{coverage:.0%} of the failed requests in {region} without review."
        )

    # --- the scannable fields -------------------------------------------
    bits = []
    if delayed:
        bits.append(
            f"{delayed} approved late (median "
            f"{region_row['MedianDelayDays']:.0f} days, worst "
            f"{region_row['MaxDelayDays']:.0f})"
        )
    if unfulfilled:
        bits.append(f"{unfulfilled} never fulfilled")
    problem = (
        f"{flagged_n} of {int(region_row['TicketsTotal'])} requests failed — "
        + ", ".join(bits)
    )

    cause = {
        MODE_DELAY: "Approval latency, not a capacity shortage — every request "
        "was eventually granted.",
        MODE_UNFULFILLED: "Requests fell out of the process entirely — nobody "
        "was waiting on an approval, so a faster queue would not have helped.",
        MODE_MIXED: "Two problems at once: some approvals are slow, and some "
        "requests are never actioned at all.",
    }[mode]

    impact = (
        f"{customers} customer(s) · {money(arr)} ARR affected · "
        f"{money(exposure)} at risk"
    )

    effect = (
        f"Would have cleared {coverage:.0%} of these without review."
        if threshold and coverage
        else "Removes the manual step that produced the delay."
    )

    caveat = ""
    if config.arr_reference_is_placeholder:
        caveat = (
            "Figures are illustrative: the subscription ARR reference is still "
            "placeholder data. Rankings hold; absolute dollars do not."
        )

    return Recommendation(
        region=region,
        rank=int(region_row["Rank"]),
        failure_mode=mode,
        headline=headline,
        action=action,
        rationale=rationale,
        problem=problem,
        cause=cause,
        impact=impact,
        effect=effect,
        owner=config.owner_for(region),
        evidence=_evidence_lines(region_row, priced, config),
        revenue_exposure_usd=round(exposure, 2),
        arr_affected_usd=round(arr, 2),
        tickets_flagged=flagged_n,
        customers_affected=customers,
        suggested_threshold_units=threshold,
        threshold_coverage=coverage,
        caveat=caveat,
    )


def recommend(
    regions: pd.DataFrame, priced: pd.DataFrame, config: Config | None = None
) -> list[Recommendation]:
    """Top-N regions by exposure. Regions with nothing wrong are skipped --
    there is no recommendation to make about a region that had no failures."""
    config = config or Config()
    candidates = regions[regions["RevenueExposureUSD"] > 0].head(config.top_n_regions)
    return [
        recommend_for_region(row, priced, config)
        for _, row in candidates.iterrows()
    ]


def why_ranked(region: str, regions: pd.DataFrame) -> str:
    """Answer the doc's example follow-up: "why is uksouth ranked lower?"

    Deterministic, so the chat answer and the report can never disagree.
    """
    match = regions[regions["Region"].str.lower() == region.lower()]
    if match.empty:
        known = ", ".join(sorted(regions["Region"]))
        return f"No capacity requests on record for '{region}'. Regions in this extract: {known}."

    row = match.iloc[0]
    top = regions.iloc[0]

    if row["TicketsFlagged"] == 0:
        return (
            f"{row['Region']} is ranked #{int(row['Rank'])} because none of its "
            f"{int(row['TicketsTotal'])} request(s) failed -- "
            f"{int(row['SameDayCount'])} were approved inside the delay cut-off and "
            f"{int(row['NoDenialCount'])} were never denied. Zero exposure."
        )

    gap = float(top["RevenueExposureUSD"]) - float(row["RevenueExposureUSD"])
    return (
        f"{row['Region']} is ranked #{int(row['Rank'])} with "
        f"{money(row['RevenueExposureUSD'])} exposure across "
        f"{int(row['TicketsFlagged'])} failed request(s) "
        f"({int(row['DelayedCount'])} delayed, {int(row['UnfulfilledCount'])} "
        f"unfulfilled, {int(row['CustomersAffected'])} customer(s), "
        f"{money(row['ARRAffectedUSD'])} ARR affected). That is {money(gap)} "
        f"below {top['Region']} at #1 -- exposure scales with the affected "
        f"customer's ARR, the share of capacity they were missing, and how long "
        f"they went without it, so a region can have more failures and still "
        f"rank lower if the customers are smaller or the gaps shorter."
    )
