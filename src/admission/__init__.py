"""Fabric capacity pools, and who is allowed to consume them.

Two things review asked for, and they belong together because one is the
inventory and the other is the policy that rations it.

CAPACITY POOLS
    Fabric is not sold in cores. It is sold as an F-SKU -- F2, F8, F64 up to
    F2048 -- rated in Capacity Units. A region's raw compute is what the other
    modules reason about; a pool is what a customer actually buys. Both are
    modelled here so a request can be expressed either way.

ADMISSION POLICY
    "One customer requests 100 cores, so they fulfil that request and not the
    rest. Based on the tier they are providing the cores." The complaint is
    first-come-first-served with a tier bias, and the absence of any reserve:
    nothing is held back, so an Enterprise request arriving on Tuesday finds a
    region emptied by Free-tier requests on Monday.

    This models the alternative. Each tier gets a share it is guaranteed, and
    higher tiers may borrow downward from unused lower-tier reserve but not the
    other way. A request is then admitted, queued or denied by a rule anyone can
    read, and the reason is recorded rather than inferred afterwards.

WHAT IT IS NOT
    A description of how Azure actually allocates capacity. It is a policy
    simulator: it says what *would* have happened under a stated reserve, so the
    reserve can be argued about with numbers instead of opinions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# --------------------------------------------------------------------------
# capacity pools
# --------------------------------------------------------------------------

#: Fabric F-SKUs and their Capacity Units. CU is the billing and throttling
#: unit; the ladder doubles, so F64 is 32x an F2.
F_SKUS: dict[str, int] = {
    "F2": 2, "F4": 4, "F8": 8, "F16": 16, "F32": 32,
    "F64": 64, "F128": 128, "F256": 256, "F512": 512,
    "F1024": 1024, "F2048": 2048,
}

#: How many raw compute units back one Capacity Unit. An assumption, and a
#: load-bearing one -- every pool figure scales with it -- so it is a named
#: constant rather than a number buried in a calculation.
UNITS_PER_CU = 0.5


def smallest_sku_for(capacity_units: float) -> str:
    """The cheapest F-SKU that covers a requirement. You cannot buy half a SKU."""
    for name, cu in F_SKUS.items():
        if cu >= capacity_units:
            return name
    return "F2048"


def build_dim_capacity_pool(dim_region: pd.DataFrame) -> pd.DataFrame:
    """Grain: one capacity pool per region.

    Expresses each region's deployed compute as the Fabric SKU ladder, so a
    denial can be discussed in the units a customer actually buys.
    """
    rows = []
    for r in dim_region.itertuples():
        units = float(getattr(r, "DeployedUnits", 0) or 0)
        cu = units * UNITS_PER_CU
        rows.append({
            "Region": r.Region,
            "DeployedUnits": round(units, 1),
            "CapacityUnits": round(cu, 1),
            "EquivalentSKU": smallest_sku_for(cu),
            "SKUClass": getattr(r, "SKUClass", None),
        })
    pool = pd.DataFrame(rows)
    pool["Provenance"] = (
        "GENERATED - Fabric SKU ladder is real; the units-per-CU conversion and "
        f"the mapping of raw compute to a pool are assumed (UNITS_PER_CU={UNITS_PER_CU})."
    )
    return pool


# --------------------------------------------------------------------------
# tier reservation
# --------------------------------------------------------------------------

#: Share of a region held for each tier. Sums to 1.0. A starting position, not
#: a measured optimum -- it lives here so it can be argued with, and the
#: simulator below reports what changing it would have done.
DEFAULT_RESERVE = {
    "Enterprise": 0.45,
    "Premium": 0.25,
    "Standard": 0.20,
    "Free": 0.10,
}

#: Tier order, best first. Borrowing is allowed downward only.
TIER_RANK = ["Enterprise", "Premium", "Standard", "Free"]


@dataclass
class Decision:
    incident_id: str
    tier: str
    requested: float
    admitted: bool
    used_own_reserve: float = 0.0
    borrowed_from: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "incidentId": self.incident_id, "tier": self.tier,
            "requested": round(self.requested, 1), "admitted": self.admitted,
            "usedOwnReserve": round(self.used_own_reserve, 1),
            "borrowedFrom": self.borrowed_from, "reason": self.reason,
        }


@dataclass
class SimulationResult:
    region: str
    capacity: float
    reserve: dict
    decisions: list = field(default_factory=list)
    admitted: int = 0
    denied: int = 0
    #: What actually happened in the extract, for comparison.
    actual_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "region": self.region,
            "capacity": round(self.capacity, 1),
            "reserve": {k: round(v, 1) for k, v in self.reserve.items()},
            "decisions": [d.to_dict() for d in self.decisions],
            "admitted": self.admitted,
            "denied": self.denied,
            "actualFailures": self.actual_failures,
            "wouldHavePrevented": max(0, self.actual_failures - self.denied),
        }


def simulate_region(requests: pd.DataFrame, capacity: float,
                    reserve: dict | None = None) -> SimulationResult:
    """Replay a region's requests under a tier reserve, in arrival order.

    Arrival order matters and is the point: the current complaint is that
    whoever asks first wins. Replaying chronologically is what shows whether a
    reserve would have changed the outcome.
    """
    reserve = reserve or DEFAULT_RESERVE
    region = str(requests["Region"].iloc[0]) if len(requests) else ""

    pools = {tier: capacity * share for tier, share in reserve.items()}
    result = SimulationResult(region=region, capacity=capacity, reserve=dict(pools))

    ordered = requests.sort_values("DeniedDate", na_position="last")
    for row in ordered.itertuples():
        tier = str(getattr(row, "SubscriptionTier", "") or "Free")
        want = float(getattr(row, "AdditionalLimitCapacity", 0) or 0)
        decision = Decision(incident_id=str(row.IncidentId), tier=tier, requested=want,
                            admitted=False)

        own = pools.get(tier, 0.0)
        take = min(own, want)
        pools[tier] = own - take
        decision.used_own_reserve = take
        outstanding = want - take

        # Borrow downward only. An Enterprise request may use unspent Free-tier
        # reserve; a Free-tier request may never touch Enterprise's.
        if outstanding > 0 and tier in TIER_RANK:
            for lower in TIER_RANK[TIER_RANK.index(tier) + 1:]:
                if outstanding <= 0:
                    break
                available = pools.get(lower, 0.0)
                borrowed = min(available, outstanding)
                if borrowed > 0:
                    pools[lower] = available - borrowed
                    outstanding -= borrowed
                    decision.borrowed_from.append(
                        {"tier": lower, "units": round(borrowed, 1)})

        if outstanding <= 0:
            decision.admitted = True
            decision.reason = (
                "Met from own reserve."
                if not decision.borrowed_from
                else "Met from own reserve plus unused lower-tier reserve."
            )
            result.admitted += 1
        else:
            decision.reason = (
                f"{outstanding:.0f} of {want:.0f} units short. "
                f"{tier} reserve exhausted and no lower tier had spare."
            )
            result.denied += 1
        result.decisions.append(decision)

    return result


def simulate_all(onto, reserve: dict | None = None,
                 failed_ids: frozenset | set | None = None) -> dict:
    """Every region, under one reserve policy.

    `failed_ids` is the platform's one definition of a failed request. Pass it.

    Without it this falls back to counting "short, or denied and later approved",
    which also sweeps in requests denied and then approved *inside* their SLA --
    the category review said must not be counted anywhere. That fallback made
    this simulator report 45 failures while every other screen reported 30, and
    a reviewer comparing two tabs would have found two answers to one question.
    """
    fact = onto["fact_capacity_request"]
    regions = onto["dim_region"].set_index("Region")

    out = {}
    for region, grp in fact.groupby("Region"):
        capacity = float(regions.loc[region, "DeployedUnits"]) if region in regions.index else 0.0
        result = simulate_region(grp, capacity, reserve)
        if failed_ids is None:
            result.actual_failures = int(
                ((grp["NewLimitCapacity"] < grp["RequestedCapacity"])
                 | (grp["DeniedDate"].notna() & grp["ApprovedDate"].notna())).sum()
            )
        else:
            result.actual_failures = int(
                grp["IncidentId"].astype(str).isin(set(failed_ids)).sum()
            )
        out[region] = result
    return out


def validate_reserve(reserve: dict) -> dict:
    """Reject a policy that does not add up rather than renormalising it."""
    missing = set(DEFAULT_RESERVE) - set(reserve)
    unknown = set(reserve) - set(DEFAULT_RESERVE)
    if missing or unknown:
        raise ValueError(
            f"reserve must name exactly {sorted(DEFAULT_RESERVE)}; "
            f"missing={sorted(missing)} unknown={sorted(unknown)}")
    total = sum(float(v) for v in reserve.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"reserve shares must sum to 1.0, got {total:.4f}")
    if any(float(v) < 0 for v in reserve.values()):
        raise ValueError("reserve shares cannot be negative")
    return {k: float(reserve[k]) for k in DEFAULT_RESERVE}


__all__ = ["F_SKUS", "UNITS_PER_CU", "smallest_sku_for", "build_dim_capacity_pool",
           "DEFAULT_RESERVE", "TIER_RANK", "simulate_region", "simulate_all",
           "validate_reserve", "Decision", "SimulationResult"]
