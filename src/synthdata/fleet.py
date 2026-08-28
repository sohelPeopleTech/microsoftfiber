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

Capacity units still sum to each region's deployed units, less a remainder of
under one F2, because a drill-down that disagreed with the screen above it would
be worse than no drill-down at all.

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
# hardware
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# capacities
# --------------------------------------------------------------------------


def _skus_for_units(units: int, rng: np.random.Generator) -> list[str]:
    """Express a unit budget as a list of buyable Fabric capacities.

    Largest-first, because a site holding six hundred units as one F256 and
    change is a likelier shape than the same units as fifty F2s. The remainder
    falls down the ladder until it is spent, which is also why small SKUs exist
    in the output at all.
    """
    ladder = sorted(SKU_UNITS.items(), key=lambda kv: -kv[1])
    out: list[str] = []
    left = int(units)
    for name, size in ladder:
        # Leave the largest SKU occasionally unused so two sites of the same
        # size do not always decompose identically.
        while left >= size and len(out) < 12:
            if size >= 512 and rng.random() < 0.25:
                break
            out.append(name)
            left -= size
    if not out and units > 0:
        out.append("F2")
    return out


def capacity_inventory(dim_region: pd.DataFrame,
                       datacentres_per_region: int = 10) -> pd.DataFrame:
    """Grain: one row per Fabric capacity.

    A region's deployed units are spread over its sites unevenly -- a real
    region has a flagship site and some small ones -- and each site's units are
    then expressed as capacities on the Fabric SKU ladder.

    Units per region sum to `DeployedUnits` less a remainder of under four,
    which is the arithmetic rather than a slip: four units is an F2 and there is
    nothing smaller to buy, so a region holding, say, 2597 units can allocate
    2596 of them and the last one is spare. Real fleets carry that remainder
    too. What must not happen is the total drifting by more than one capacity's
    worth, and the tests hold it to that.

    Hardware class varies between the sites of a region. The UI already tells
    readers that "a region holds ten data centres that may run Intel, AMD or
    GPU-class"; until now the data made that sentence false.
    """
    rng = _rng(11)
    rows = []
    for r in dim_region.itertuples():
        region = r.Region
        total_units = int(float(getattr(r, "DeployedUnits", 0) or 0))
        dominant = getattr(r, "SKUClass", None) or "AMD-standard"
        if total_units <= 0:
            continue

        # Uneven split across sites: a flagship, a middle, and a tail. Split in
        # units of four, because four units is one F2 and there is no smaller
        # capacity to buy -- splitting finer would leave every site holding a
        # remainder it cannot express as a capacity.
        blocks = total_units // SKU_UNITS["F2"]
        if blocks < datacentres_per_region:
            continue
        weights = rng.dirichlet(np.full(datacentres_per_region, 2.4))
        site_blocks = np.maximum((weights * blocks).astype(int), 1)
        site_blocks[int(np.argmax(site_blocks))] += blocks - int(site_blocks.sum())
        site_units = site_blocks * SKU_UNITS["F2"]

        for i in range(1, datacentres_per_region + 1):
            site = f"{region}-dc{i:02d}"
            units_here = int(site_units[i - 1])
            for n, sku in enumerate(_skus_for_units(units_here, rng), start=1):
                rows.append({
                    "CapacityId": f"{site}-cap{n:02d}",
                    "DatacentreId": site,
                    "Region": region,
                    "FabricSku": sku,
                    "CapacityUnits": F_SKUS[sku],
                    "DeployedUnits": SKU_UNITS[sku],
                    "SupportsFreeViewers": F_SKUS[sku] >= F_SKUS[FREE_VIEWER_SKU],
                })
    return _tag(pd.DataFrame(rows),
                "region deployed units split unevenly across sites, then expressed "
                "on the real Fabric SKU ladder")


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
    # in the ontology, so it cannot be read here.
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
                   region_usage: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The capacities themselves, plus everything Fabric-native above them.

    Hardware models, lead-time history and node incidents used to be generated
    here. They described Azure infrastructure and Fabric has none of it: a
    customer never sees a server, an F-SKU scales immediately, and a capacity
    does not fail, it throttles. `synthdata.fabric` replaces all three.
    """
    from . import fabric

    caps = assign_capacity_owners(capacity_inventory(dim_region), tickets)
    return {
        "capacity_inventory": caps,
        "partial_grants": partial_grants(tickets),
        **fabric.generate_fabric(caps, region_usage),
    }
