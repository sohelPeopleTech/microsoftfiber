"""The Fabric capacities below the region.

Everything the rest of this project knows stops at the region, which is enough
to say a region is filling up and not enough to say what to do about it. This
adds the level a Fabric admin actually works at: the capacity, identified by its
F-SKU and its Capacity Units.

    capacity_inventory  Fabric capacities per site, on the real SKU ladder
    partial_grants      requests met in part rather than granted or refused

An earlier version of this module also generated hardware models, provisioning
lead times and node-failure incidents. All three described Azure infrastructure
that Fabric does not expose: a customer never sees a server, an F-SKU is scaled
in Azure and takes effect immediately, and a capacity does not fail, it
throttles. They are gone, and `synthdata.fabric` models what replaces them --
CU consumption, smoothing overage and the published throttling stages.

Every site is either Shared -- one large capacity carrying several workloads --
or Dedicated -- a capacity per workload, each at 100% of its own. Never both.
What a site holds follows from which of the two it is, rather than from a unit
budget handed down from its region.

That inverts a reconciliation this module used to perform. Capacities were sized
to add up to the region's deployed units; now the capacities are the fact and the
region's deployed units are their sum (`generate.hardware_inventory` takes them
from here). The drill-down still agrees with the screen above it -- exactly,
rather than to within an unbuyable remainder -- because both are now reading the
same rows.

Seeded, like the rest, so two runs agree.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from . import SEED
from .generate import PROVENANCE, _tag

#: Fabric SKU ladder and Capacity Units, mirroring `admission.F_SKUS`. Imported
#: rather than redefined would be circular at build time, so it is asserted
#: equal in the tests instead.
F_SKUS: dict[str, int] = {
    "F2": 2, "F4": 4, "F8": 8, "F16": 16, "F32": 32,
    "F64": 64, "F128": 128, "F256": 256, "F512": 512,
    "F1024": 1024, "F2048": 2048,
}
UNITS_PER_CU = 0.5

#: Raw compute units backing one capacity of each SKU.
SKU_UNITS: dict[str, int] = {name: int(cu / UNITS_PER_CU) for name, cu in F_SKUS.items()}

#: The SKU at which Power BI content becomes viewable on a Free licence. Real,
#: documented, and the reason an F32 sitting next to an F64 is a commercial
#: question and not only a capacity one.
#: https://learn.microsoft.com/en-us/fabric/enterprise/licenses
FREE_VIEWER_SKU = "F64"

def _rng(salt: int = 0) -> np.random.Generator:
    return np.random.default_rng(SEED + salt)


def _stable(*parts: str) -> int:
    """A deterministic integer from strings, for per-entity choices.

    Seeded from the identity rather than from draw order, so adding a region
    does not reshuffle the hardware of every region that came before it.
    """
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:12], 16)


# --------------------------------------------------------------------------
# what a site is
# --------------------------------------------------------------------------

#: Every site is one of exactly two shapes, and never both.
#:
#: **Shared** -- one capacity, on a large rung, with several workloads sitting
#: on it and sharing its CU. This is the estate a rebalance is about: a workload
#: on a shared capacity can be moved off it and the capacity carries on.
#:
#: **Dedicated** -- a capacity per workload, each workload at 100% of its own.
#: There is nothing to rebalance here: the workload *is* the capacity, so the
#: only lever is the rung it sits on.
#:
#: The two shapes are the reason a lot of advice has to be conditional. "Move
#: the dominant workload off this capacity" is sound on a shared site and
#: meaningless on a dedicated one, and until the model could tell them apart
#: the product could not say which it was looking at.
SITE_TYPE_SHARED = "Shared"
SITE_TYPE_DEDICATED = "Dedicated"
SITE_TYPES = (SITE_TYPE_SHARED, SITE_TYPE_DEDICATED)

#: The rung a shared site's single capacity sits on. Assigned directly rather
#: than summed from what the site used to hold: a shared capacity is bought big
#: enough for everything that lands on it, which is what puts it up here.
SHARED_SKUS = ("F128", "F256")

#: The rungs a dedicated site's capacities sit on. Lower, because each one
#: carries a single workload rather than a building's worth of them.
DEDICATED_SKUS = ("F2", "F4", "F8", "F16", "F32", "F64")

#: How many capacities a dedicated site holds, inclusive.
DEDICATED_CAPACITY_RANGE = (2, 5)


def assign_site_types(datacentre_ids) -> dict[str, str]:
    """Half the sites shared, half dedicated -- exactly, not on average.

    Taking the parity of a hash would be the obvious way to do this and is
    50/50 only in expectation: across ten sites it lands on seven-three often
    enough to matter, and a region that came out all-shared would quietly
    switch off half the product. Ranking the ids by a stable digest and cutting
    the list in half gives the same answer for the same ids, whatever order
    they arrive in, and gives it exactly.

    An odd number of sites leaves the extra one dedicated.
    """
    ids = sorted({str(d) for d in datacentre_ids})
    ranked = sorted(ids, key=lambda d: (_stable(d, "sitetype"), d))
    shared = set(ranked[:len(ranked) // 2])
    return {d: (SITE_TYPE_SHARED if d in shared else SITE_TYPE_DEDICATED)
            for d in ids}


def skus_for_site(datacentre_id: str, site_type: str) -> list[str]:
    """The capacities in one site, as SKU rungs.

    Deterministic from the id: the same building always holds the same
    capacities, so a rebuild does not reshuffle the estate under saved links.
    """
    if site_type == SITE_TYPE_SHARED:
        return [SHARED_SKUS[_stable(datacentre_id, "sharedsku") % len(SHARED_SKUS)]]
    lo, hi = DEDICATED_CAPACITY_RANGE
    n = lo + _stable(datacentre_id, "capcount") % (hi - lo + 1)
    return [DEDICATED_SKUS[_stable(datacentre_id, "sku", str(i)) % len(DEDICATED_SKUS)]
            for i in range(1, n + 1)]


# --------------------------------------------------------------------------
# capacities
# --------------------------------------------------------------------------


def capacity_inventory(dim_region: pd.DataFrame,
                       datacentres_per_region: int = 10) -> pd.DataFrame:
    """Grain: one row per Fabric capacity.

    Every site is Shared or Dedicated, half the fleet each, decided by
    `assign_site_types` from the site's own id. A shared site holds exactly one
    capacity on a large rung; a dedicated site holds two to five, each of which
    will carry a single workload.

    **This used to work the other way round.** A region's deployed units were
    split unevenly across its sites and each site's budget was then decomposed
    into buyable SKUs largest-first, so a site's shape was an accident of the
    arithmetic: twelve capacities in one building, one in the next, and no
    reason for either that anybody could state. The rung is now assigned
    directly -- nothing is summed from what a site used to hold -- and the
    region's deployed units are the sum of what its sites turn out to hold.

    `dim_region` is read for its region names only. It is still the parameter
    because the caller has it and because a region that exists with no sites
    would be a hole in the drill-down.
    """
    rows = []
    for r in dim_region.itertuples():
        region = r.Region
        sites = [f"{region}-dc{i:02d}"
                 for i in range(1, datacentres_per_region + 1)]
        types = assign_site_types(sites)
        for site in sites:
            kind = types[site]
            for n, sku in enumerate(skus_for_site(site, kind), start=1):
                rows.append({
                    "CapacityId": f"{site}-cap{n:02d}",
                    "DatacentreId": site,
                    "Region": region,
                    "SiteType": kind,
                    "FabricSku": sku,
                    "CapacityUnits": F_SKUS[sku],
                    "DeployedUnits": SKU_UNITS[sku],
                    "SupportsFreeViewers": F_SKUS[sku] >= F_SKUS[FREE_VIEWER_SKU],
                })
    return _tag(pd.DataFrame(rows),
                "every site is Shared (one large capacity, several workloads "
                "sharing its CU) or Dedicated (a capacity per workload, each at "
                "100% of its own), split half and half from the site id; SKU "
                "rungs assigned directly on the real Fabric ladder")


def assign_capacity_owners(capacities: pd.DataFrame,
                           tickets: pd.DataFrame) -> pd.DataFrame:
    """Give every capacity the account that holds it.

    Nothing in the estate recorded this, and without it the platform can say
    "this capacity is idle" but never "this account is sitting on it" -- which
    is the whole of the reclaim question. In production the link is real and
    already exists: a Fabric capacity is an Azure resource in a customer's
    subscription.

    Here it is generated, like the capacities themselves, but not invented from
    nothing. The subscriptions are the real ones from the ICM extract, and a
    capacity is assigned to an account weighted by how much capacity that
    account has actually asked for in that region -- so the accounts holding the
    most in a region are the ones that requested the most there, which is the
    relationship the real link would have. An account that has never requested
    anything in a region can never be shown holding capacity there.

    Regions with no requests at all fall back to the largest accounts by ARR,
    because a region has to belong to somebody and leaving it blank would put a
    hole in the one column the reclaim recommendation reads.
    """
    if capacities.empty or tickets.empty:
        return capacities.assign(SubscriptionId="", TenantId="")

    rng = _rng(29)
    # The raw extract carries AdditionalLimitCapacity -- how much extra the
    # account asked for. RequestedCapacity is a derived name that appears later
    # in the dimensional model, so it cannot be read here.
    asked_col = next((c for c in ("AdditionalLimitCapacity", "RequestedCapacity")
                      if c in tickets.columns), None)
    needed = {"SubscriptionId", "TenantId", "Region"}
    if asked_col is None or not needed <= set(tickets.columns):
        return capacities.assign(SubscriptionId="", TenantId="")

    t = tickets[["SubscriptionId", "TenantId", "Region", asked_col]].copy()
    t["asked"] = pd.to_numeric(t[asked_col], errors="coerce").fillna(0.0)

    # Every account that has asked for capacity anywhere, largest first. This is
    # the fallback pool, and it is ordered so the fallback is deterministic.
    everywhere = (t.groupby(["SubscriptionId", "TenantId"], as_index=False)
                   .agg(asked=("asked", "sum")).sort_values("asked", ascending=False))
    everywhere = everywhere[everywhere["asked"] > 0]

    owners = {}
    for region, group in t.groupby("Region"):
        demand = (group.groupby(["SubscriptionId", "TenantId"], as_index=False)
                       .agg(asked=("asked", "sum")))
        demand = demand[demand["asked"] > 0]
        owners[str(region)] = demand if not demand.empty else everywhere

    out, assigned = capacities.copy(), []
    for row in out.itertuples():
        pool = owners.get(str(row.Region))
        if pool is None or pool.empty:
            pool = everywhere
        weights = pool["asked"].to_numpy(dtype=float)
        weights = weights / weights.sum()
        pick = int(rng.choice(len(pool), p=weights))
        assigned.append((str(pool["SubscriptionId"].iloc[pick]),
                         str(pool["TenantId"].iloc[pick])))

    out["SubscriptionId"] = [a for a, _ in assigned]
    out["TenantId"] = [b for _, b in assigned]
    out["Provenance"] = out["Provenance"] + (
        "; holder assigned from the real subscriptions in the extract, weighted "
        "by capacity each account requested in that region")
    return out


# --------------------------------------------------------------------------
# procurement
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# operational health
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# partial fulfilment
# --------------------------------------------------------------------------


def partial_grants(tickets: pd.DataFrame) -> pd.DataFrame:
    """Requests met in part: neither granted nor refused.

    The ICM extract has no partial fulfilment in it -- every one of its rows
    either grants the whole ask or none of it. The schema can express a partial
    grant, and reviewers describe them happening, so the state exists in the
    business and not in the export.

    This table supplies that third state for a subset of the requests that were
    recorded as refused. It is an overlay, not a correction: the extract still
    says what it says, and anything reading this table has to say where the
    number came from.
    """
    rng = _rng(14)
    df = tickets.copy()
    df["Requested"] = df["CurrentLimitCapacity"] + df["AdditionalLimitCapacity"]
    refused = df[df["NewLimitCapacity"] < df["Requested"]]

    rows = []
    for t in refused.itertuples():
        # Two in five of the refusals were actually part-filled.
        if _stable(str(t.IncidentId), "partial") % 5 >= 2:
            continue
        asked = int(t.AdditionalLimitCapacity)
        if asked < 4:
            continue
        granted = int(round(asked * rng.uniform(0.25, 0.75)))
        rows.append({
            "IncidentId": str(t.IncidentId),
            "Region": t.Region,
            "RequestedUnits": asked,
            "PartiallyGrantedUnits": granted,
            "ShortfallUnits": asked - granted,
            "GrantedPct": round(granted / asked * 100, 1),
        })
    return _tag(pd.DataFrame(rows),
                "partial fulfilment invented for a subset of the refused requests; "
                "the ICM extract records none")


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def generate_fleet(tickets: pd.DataFrame, dim_region: pd.DataFrame,
                   region_usage: pd.DataFrame,
                   capacities: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """The capacities themselves, plus everything Fabric-native above them.

    Hardware models, lead-time history and node incidents used to be generated
    here. They described Azure infrastructure and Fabric has none of it: a
    customer never sees a server, an F-SKU scales immediately, and a capacity
    does not fail, it throttles. `synthdata.fabric` replaces all three.
    """
    from . import fabric

    # `capacities` is passed in by `generate_all`, which needs them before this
    # point: the region's deployed units are their sum. Built here when a caller
    # has none, so this stays runnable on its own.
    if capacities is None:
        capacities = capacity_inventory(dim_region)
    caps = assign_capacity_owners(capacities, tickets)
    return {
        "capacity_inventory": caps,
        "partial_grants": partial_grants(tickets),
        **fabric.generate_fabric(caps, region_usage),
    }
