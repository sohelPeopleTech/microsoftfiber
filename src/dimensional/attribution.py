"""Two attributes ICM does not give us: which datacentre, and why it was denied.

Both are assumptions. They are generated here rather than hand-placed in a CSV
so that the rule is readable, testable, and identical on every run -- the
reporting pack was rebuilt once already because figures had been typed rather
than generated, and inventing two new columns by hand would be the same mistake
with more surface area.

**Deterministic, not random.** Every value is derived from a hash of the
incident id, so three consecutive runs are byte-identical and a figure quoted in
a review is still true tomorrow. `random` is deliberately not imported.

WHAT THIS IS FOR
    A denial is only actionable once you know why. "westeurope has 4 failures"
    tells an engineer nothing; "3 of them hit the capacity ceiling and 1 was a
    hardware failure" tells them which of the two fixes to reach for -- and they
    are different fixes with different owners.

WHAT IT IS NOT
    Evidence about real denials. The distribution below is a plausible shape
    agreed in review, not a measurement. When ICM exposes a reason column,
    delete `assign_denial_reason` and read the column instead; nothing else has
    to change, because everything downstream reads `DenialReason`.
"""

from __future__ import annotations

import hashlib

import pandas as pd

#: Reasons a capacity request gets refused, and who can do something about it.
#: `module` names the part of the platform that already produces a fix, or None
#: where no automated recommendation is honest.
REASONS: dict[str, dict] = {
    "Insufficient capacity": {
        "weight": 34,
        "detail": "The region did not have enough free units to grant the request.",
        "module": "module2",
        "action": "Scale the capacities that are short, or raise the ceiling if headroom exists.",
    },
    "Threshold reached": {
        "weight": 24,
        "detail": "Granting it would have pushed the region past its safety line.",
        "module": "module1",
        "action": "Review the safety threshold, or scale now -- an F SKU applies immediately.",
    },
    # Was "Hardware failure", describing units offline in a building. A Fabric
    # customer never sees the building or the units; what they experience when
    # the platform itself is at fault is a service incident on the capacity.
    "Platform incident": {
        "weight": 14,
        "detail": "The capacity was degraded by a platform-side incident when the "
                  "request landed.",
        "module": "module2",
        "action": "Confirm the incident is closed, then re-run the request.",
    },
    "Awaiting maintenance window": {
        "weight": 10,
        "detail": "The work needed a window that had not yet opened.",
        "module": None,
        "action": "Confirm the scheduled window and set the customer's expectation to it.",
    },
    "Quota policy": {
        "weight": 9,
        "detail": "The subscription's own limit blocked it, not the region's capacity.",
        "module": None,
        "action": "A quota increase, not a capacity order. Route to the account team.",
    },
    "Network unreachable": {
        "weight": 7,
        "detail": "The target datacentre was not reachable while the request was open.",
        "module": None,
        "action": "Needs investigation with the network owner before anything is promised.",
    },
    #: The residual. Kept deliberately small and never dressed up: a request we
    #: cannot explain is the one case where the honest recommendation is to go
    #: and ask a human.
    #:
    #: Sized at 5% rather than the 1-2% floated in review for one reason: on 45
    #: denials, 2% rounds to nothing, and a category that never appears cannot
    #: be reviewed. 5% puts two or three tickets in it, which is enough to show
    #: the human-review path exists and small enough to stay honest about how
    #: rare it should be.
    "Reason not recorded": {
        "weight": 5,
        "detail": "The ticket closed without a cause. We do not know why this failed.",
        "module": None,
        "action": "Human review: talk to the engineer who worked it, or to the customer.",
    },
}

UNKNOWN_REASON = "Reason not recorded"


def _bucket(key: str, salt: str, modulo: int) -> int:
    """A stable integer for a key. Same key, same answer, forever."""
    digest = hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) % modulo


def datacentres_for_region(region: str, count: int) -> list[str]:
    """`westeurope` -> ['westeurope-dc01', ...]. Names, not just a count.

    Module 2 already assumes a number of datacentres per region; this gives them
    identities so a ticket can be attributed to one and an engineer can be told
    which building to look at.
    """
    return [f"{region}-dc{i:02d}" for i in range(1, count + 1)]


def build_dim_datacentre(dim_region: pd.DataFrame, count_for) -> pd.DataFrame:
    """Grain: one datacentre. Capacity is split evenly across a region's sites.

    Even splitting is an assumption and a visible one -- real sites differ in
    size. It is stated rather than smoothed over because every per-datacentre
    figure scales with it.
    """
    rows = []
    for r in dim_region.itertuples():
        names = datacentres_for_region(r.Region, count_for(r.Region))
        deployed = float(getattr(r, "DeployedUnits", 0) or 0)
        share = deployed / len(names) if names else 0.0
        for name in names:
            rows.append({
                "DatacentreId": name,
                "Region": r.Region,
                "SKUClass": getattr(r, "SKUClass", None),
                "DeployedUnits": round(share, 1),
                "LeadTimeDays": getattr(r, "LeadTimeDays", None),
            })
    dim = pd.DataFrame(rows)
    dim["Provenance"] = (
        "GENERATED - the source data gives a region total only. Sites, their "
        "names and an even capacity split are all assumed."
    )
    return dim


def assign_datacentre(fact: pd.DataFrame, count_for) -> pd.Series:
    """Attribute each ticket to one datacentre inside its own region."""
    out = []
    for row in fact.itertuples():
        names = datacentres_for_region(row.Region, count_for(row.Region))
        out.append(names[_bucket(str(row.IncidentId), "dc", len(names))] if names else None)
    return pd.Series(out, index=fact.index)


#: Causes that are operational rather than capacity-driven. Any request can hit
#: one of these regardless of how much room the site has, so they are the
#: fallback once the capacity-based causes have been ruled out.
OPERATIONAL = [
    "Platform incident",
    "Awaiting maintenance window",
    "Quota policy",
    "Network unreachable",
    "Reason not recorded",
]


def assign_denial_reason(fact: pd.DataFrame, failed_mask: pd.Series,
                         site_capacity: dict | None = None) -> pd.Series:
    """A reason for every failed request, consistent with the site's own state.

    **This is derived, not drawn.** An earlier version picked a reason from a
    weighted distribution using a hash of the incident id, which produced a
    plausible-looking spread and complete nonsense on the page: canadacentral
    sat at 75% utilisation with 921 cores free while its requests were denied
    for "insufficient capacity", and the recommendation engine then computed
    threshold arithmetic for sites nowhere near their threshold.

    A reason has to be a fact about the request, so it is decided in the order
    an approver would actually hit the constraints:

      1. the site physically could not cover the ask   -> Insufficient capacity
      2. granting it would cross the safety line       -> Threshold reached
      3. neither -- the room existed                   -> an operational cause

    `site_capacity` maps DatacentreId -> {free, headroom}. Without it only the
    operational causes are available, because nothing else can be justified.
    """
    site_capacity = site_capacity or {}
    out = pd.Series([""] * len(fact), index=fact.index, dtype=object)

    for idx, row in zip(fact.index, fact.itertuples(), strict=True):
        if not bool(failed_mask.loc[idx]):
            continue

        wanted = float(getattr(row, "AdditionalLimitCapacity", 0) or 0)
        state = site_capacity.get(str(getattr(row, "DatacentreId", "")), {})
        free = state.get("free")
        headroom = state.get("headroom")

        if free is not None and wanted > free:
            out.loc[idx] = "Insufficient capacity"
        elif headroom is not None and wanted > headroom:
            out.loc[idx] = "Threshold reached"
        else:
            # The capacity was there, so the cause is operational. Which one is
            # still a synthetic choice -- ICM records none of them -- but it is
            # now only chosen where a capacity explanation has been ruled out.
            out.loc[idx] = OPERATIONAL[_bucket(str(row.IncidentId), "op", len(OPERATIONAL))]
    return out


def reason_summary() -> pd.DataFrame:
    """The reason reference, for the methodology page and the assistant."""
    return pd.DataFrame([
        {"Reason": name, "Detail": meta["detail"], "Action": meta["action"],
         "HandledBy": meta["module"] or "human review",
         "SharePct": round(meta["weight"] / sum(m["weight"] for m in REASONS.values()) * 100, 1)}
        for name, meta in REASONS.items()
    ])

# --------------------------------------------------------------------------
# customer identity
# --------------------------------------------------------------------------

#: Microsoft's own long-standing sample-company names. Chosen deliberately:
#: they are recognisably fictional, so nothing here can be mistaken for a real
#: organisation, while still reading as a customer rather than as an id.
#:
#: Review feedback was blunt about why this matters -- a subscription id like
#: "925aa064" sits next to an incident id of the same shape, so a reader has to
#: work out which is which before they can read the row at all.
COMPANY_NAMES = [
    "Contoso Manufacturing", "Northwind Traders", "Fabrikam Health",
    "Adventure Works", "Tailspin Toys", "Wide World Importers",
    "Proseware Systems", "Litware Analytics", "Woodgrove Bank",
    "Lamna Healthcare", "Relecloud Media", "Trey Research",
    "Alpine Ski House", "Blue Yonder Airlines", "Coho Vineyard",
    "Fourth Coffee", "Graphic Design Institute", "Humongous Insurance",
    "Margie's Travel", "Nod Publishers", "Southridge Video",
    "The Phone Company", "VanArsdel Logistics",
]


def company_name(subscription_id: str, taken: set[str] | None = None) -> str:
    """A stable, unique display name for a subscription.

    Deterministic from the id, so the same customer is called the same thing on
    every run and in every screenshot. Collides gracefully: if the first choice
    is taken, walk the list rather than appending a number, so no customer ends
    up as "Contoso Manufacturing 2".
    """
    taken = taken or set()
    start = _bucket(str(subscription_id), "name", len(COMPANY_NAMES))
    for offset in range(len(COMPANY_NAMES)):
        candidate = COMPANY_NAMES[(start + offset) % len(COMPANY_NAMES)]
        if candidate not in taken:
            return candidate
    return f"Subscription {str(subscription_id)[:8]}"


def assign_company_names(subscription_ids) -> dict:
    """id -> name, assigned in a stable order so the mapping never shifts."""
    names, taken = {}, set()
    for sub in sorted({str(s) for s in subscription_ids}):
        name = company_name(sub, taken)
        names[sub] = name
        taken.add(name)
    return names


# --------------------------------------------------------------------------
# per-site capacity
# --------------------------------------------------------------------------

#: Safety threshold per site. The region-wide 85% is a policy default; real
#: sites differ, so this varies them slightly and deterministically. Stated as
#: an assumption because every "headroom remaining" figure moves with it.
SITE_THRESHOLD_CHOICES = (80.0, 85.0, 90.0)


def site_threshold(datacentre_id: str) -> float:
    return SITE_THRESHOLD_CHOICES[_bucket(datacentre_id, "thr", len(SITE_THRESHOLD_CHOICES))]


# --------------------------------------------------------------------------
# customer demand history
# --------------------------------------------------------------------------

#: How far back a customer's generated demand history runs. Fifteen tickets per
#: customer would not support a forecast; eighteen monthly points will.
CUSTOMER_HISTORY_MONTHS = 18

#: A deal-sized month is this many times the customer's ordinary ask. Taken from
#: the observed split in the real extract, where event-linked requests average
#: 338 cores against 34 for everything else.
DEAL_MULTIPLIER = 9.0

#: Roughly one month in eight carries a deal. Chosen so a customer's history has
#: one or two, which is what makes a spike a spike rather than the pattern.
DEAL_ODDS = 8


def customer_monthly_demand(subscription_id: str, months: list[str],
                            ordinary_cores: float) -> list[dict]:
    """A month-by-month request history for one customer.

    **This is generated, and it is generated for a reason that must not be lost.**
    The extract holds 60 tickets across 23 subscriptions -- two or three each,
    which cannot carry a forecast. Review asked for customer-level forecasting
    anyway and said to synthesise what was missing. So this produces the shape a
    customer's demand actually has: a steady ordinary ask, with the occasional
    month where something was signed.

    It is deliberately kept out of `fact_capacity_request`. Every figure the
    platform reports -- exposure, failures, cores pending -- is computed from
    that table, and quietly padding it with invented tickets would move numbers
    that have already been reviewed and published. Real months are overlaid on
    top of this by the caller, so where the extract has an answer the extract
    wins.
    """
    base = max(4.0, float(ordinary_cores))
    out = []
    for i, month in enumerate(months):
        key = f"{subscription_id}:{month}"
        # 60% to 160% of the ordinary ask, so the baseline breathes without
        # wandering; a flat line would make any forecast look better than it is.
        swing = 0.6 + (_bucket(key, "cdem", 101) / 100.0)
        cores = base * swing
        deal = _bucket(key, "cdeal", DEAL_ODDS) == 0
        if deal:
            cores *= DEAL_MULTIPLIER * (0.7 + _bucket(key, "cmag", 61) / 100.0)
        out.append({
            "Month": month,
            "CoresRequested": round(cores, 1),
            "RequestCount": 1 + _bucket(key, "cnt", 3),
            "IsDealMonth": bool(deal),
            "IsSynthetic": True,
        })
    return out
