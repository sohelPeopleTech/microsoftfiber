"""The fleet below the region: capacities, the hardware under them, and how
each one actually behaves.

Everything the rest of this project knows stops at the region. A region has one
hardware class, one lead time, one utilisation curve, and ten data centres that
are the region's numbers divided by ten. That is enough to say *a region is
filling up* and not enough to say *what to buy*, which is the question a
capacity manager actually has.

These generators add the missing grain:

    hardware_models       what a unit physically is -- vendor, cores, memory
    capacity_inventory    Fabric capacities: F-SKU, class and units, per site
    capacity_usage_daily  how full each capacity ran, per day
    lead_time_history     what lead time *used to be*, so drift is visible
    operational_incidents node failures and throttling, per capacity
    partial_grants        requests met in part rather than granted or refused

All of it is synthetic and every row says so. Two rules keep that safe, and they
are the same two the region-level generators already follow.

**It reconciles with what exists.** Capacity units sum to the region's deployed
units; per-capacity daily usage sums to the region's recorded usage, exactly and
by construction rather than approximately. A drill-down that disagreed with the
screen above it would be worse than no drill-down at all -- this project has
spent several sessions removing exactly that class of contradiction.

**It is derived, not decorative.** Hardware classes vary between the sites of a
region because the UI already claims they do. Lead times drift upward because
procurement lead times did. Incidents concentrate on a class rather than
scattering, because the decision worth surfacing -- move this workload to
different hardware -- only exists if the data can distinguish good hardware from
bad.

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

#: What a "unit" physically is. Models are real, publicly listed servers; which
#: model sits in which data centre is invented, like everything else here.
HARDWARE_MODELS: dict[str, dict] = {
    "AMD-standard": {
        "Vendor": "Dell", "Model": "PowerEdge R6615",
        "Cpu": "AMD EPYC 9354P", "CoresPerNode": 32,
        "MemoryGB": 256, "StorageTB": 3.84, "PowerDrawW": 800,
    },
    "AMD-highmem": {
        "Vendor": "Dell", "Model": "PowerEdge R7625",
        "Cpu": "AMD EPYC 9554", "CoresPerNode": 64,
        "MemoryGB": 1024, "StorageTB": 7.68, "PowerDrawW": 1400,
    },
    "Intel-standard": {
        "Vendor": "HPE", "Model": "ProLiant DL360 Gen11",
        "Cpu": "Intel Xeon Gold 6438N", "CoresPerNode": 32,
        "MemoryGB": 256, "StorageTB": 3.84, "PowerDrawW": 850,
    },
    "Intel-highmem": {
        "Vendor": "HPE", "Model": "ProLiant DL380 Gen11",
        "Cpu": "Intel Xeon Platinum 8468", "CoresPerNode": 48,
        "MemoryGB": 1024, "StorageTB": 7.68, "PowerDrawW": 1600,
    },
    "GPU-class": {
        "Vendor": "Dell", "Model": "PowerEdge XE9680",
        "Cpu": "Intel Xeon Platinum 8470 + 8x H100", "CoresPerNode": 52,
        "MemoryGB": 2048, "StorageTB": 15.36, "PowerDrawW": 10200,
    },
}

#: Which classes a region will mix. A site running something unrelated to its
#: region's dominant class would be a data error, not variety, so the alternate
#: is always the same vendor family or the standard tier of it.
CLASS_NEIGHBOURS: dict[str, list[str]] = {
    "AMD-standard": ["AMD-standard", "AMD-highmem"],
    "AMD-highmem": ["AMD-highmem", "AMD-standard"],
    "Intel-standard": ["Intel-standard", "Intel-highmem"],
    "Intel-highmem": ["Intel-highmem", "Intel-standard"],
    "GPU-class": ["GPU-class", "AMD-highmem"],
}

#: Relative rate of operational incidents per class, against AMD-standard = 1.0.
#: Intel-highmem is the bad actor here on purpose: without one class visibly
#: worse than another, "move this workload" is not a decision anyone can take.
INCIDENT_RATE: dict[str, float] = {
    "AMD-standard": 0.85,
    "AMD-highmem": 0.70,
    "Intel-standard": 1.30,
    "Intel-highmem": 2.60,
    "GPU-class": 1.55,
}

INCIDENT_TYPES = [
    "Node unresponsive", "Capacity throttling", "Memory pressure",
    "Storage latency", "Network partition", "Unplanned reboot",
]

#: Sev1 is a customer-visible outage; Sev4 is a blip nobody noticed.
SEVERITIES = ["Sev1", "Sev2", "Sev3", "Sev4"]
SEVERITY_WEIGHTS = [0.08, 0.20, 0.42, 0.30]

#: Lead time as it stood at successive points. Present-day values match
#: `sku_reference.csv` exactly -- this table adds the past, it does not restate
#: the present, and changing today's number here would silently move every
#: order-by date in Module 1.
LEAD_TIME_HISTORY: list[tuple[str, str, str, int]] = [
    # (SKUClass, Supplier, EffectiveFrom, LeadTimeDays)
    ("AMD-standard", "Dell", "2025-03-01", 18),
    ("AMD-standard", "Dell", "2025-09-01", 20),
    ("AMD-standard", "Dell", "2026-01-05", 21),
    ("AMD-highmem", "Dell", "2025-03-01", 12),
    ("AMD-highmem", "Dell", "2025-09-01", 11),
    ("AMD-highmem", "Dell", "2026-01-05", 10),
    ("Intel-standard", "HPE", "2025-03-01", 21),
    ("Intel-standard", "HPE", "2025-09-01", 21),
    ("Intel-standard", "HPE", "2026-01-05", 21),
    # The one that moved. A class whose lead time more than doubled is the
    # case where waiting for the usual trigger is the wrong call.
    ("Intel-highmem", "HPE", "2025-03-01", 20),
    ("Intel-highmem", "HPE", "2025-08-15", 28),
    ("Intel-highmem", "HPE", "2026-01-05", 45),
    ("GPU-class", "Dell", "2025-03-01", 26),
    ("GPU-class", "Dell", "2025-09-01", 30),
    ("GPU-class", "Dell", "2026-01-05", 30),
]


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


def hardware_models() -> pd.DataFrame:
    """What one unit of each class physically is.

    The project has been calling its unit a "core" in the UI while the number
    behind it was an abstract capacity unit. This table is what makes the word
    answerable: a class now has a vendor, a CPU, a core count per node and a
    memory figure, so "move this workload from Intel to AMD" names two things
    that differ rather than two labels.
    """
    rows = []
    for cls, spec in HARDWARE_MODELS.items():
        rows.append({"SKUClass": cls, **spec,
                     "RelativeIncidentRate": INCIDENT_RATE[cls]})
    return _tag(pd.DataFrame(rows),
                "server models are real products; their assignment to a class is invented")


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

        neighbours = CLASS_NEIGHBOURS.get(dominant, [dominant])
        for i in range(1, datacentres_per_region + 1):
            site = f"{region}-dc{i:02d}"
            # Two sites in three run the region's dominant class.
            pick = _stable(site, "class") % 3
            cls = neighbours[0] if pick < 2 else neighbours[min(1, len(neighbours) - 1)]
            units_here = int(site_units[i - 1])
            for n, sku in enumerate(_skus_for_units(units_here, rng), start=1):
                rows.append({
                    "CapacityId": f"{site}-cap{n:02d}",
                    "DatacentreId": site,
                    "Region": region,
                    "FabricSku": sku,
                    "CapacityUnits": F_SKUS[sku],
                    "DeployedUnits": SKU_UNITS[sku],
                    "SKUClass": cls,
                    "Vendor": HARDWARE_MODELS[cls]["Vendor"],
                    "Model": HARDWARE_MODELS[cls]["Model"],
                    "NodeCount": max(1, SKU_UNITS[sku] // HARDWARE_MODELS[cls]["CoresPerNode"]),
                    "SupportsFreeViewers": F_SKUS[sku] >= F_SKUS[FREE_VIEWER_SKU],
                })
    return _tag(pd.DataFrame(rows),
                "region deployed units split unevenly across sites, then expressed "
                "on the real Fabric SKU ladder")


def capacity_usage_daily(capacities: pd.DataFrame,
                         region_usage: pd.DataFrame) -> pd.DataFrame:
    """Grain: one row per capacity per day.

    Built by distributing the region's *used units* for that day across its
    capacities, rather than by drawing a percentage per capacity and hoping the
    average lands near the region's. The difference matters: the first sums to
    the recorded region figure exactly, the second sums to something close to
    it, and a drill-down that is close to the screen above it is a defect.

    Capacities carry a stable pressure offset, so the same capacity is the busy
    one all the way through the window instead of jittering rank each day.
    """
    rng = _rng(12)
    caps = capacities.copy()
    # Stable per-capacity pressure: some capacities simply run hotter.
    caps["Pressure"] = [
        0.55 + (_stable(cid, "pressure") % 1000) / 1000.0 * 0.95
        for cid in caps["CapacityId"]
    ]

    usage = region_usage[["Date", "Region", "UsedUnits", "TotalUnits"]].copy()
    by_region = {rg: g for rg, g in caps.groupby("Region")}

    rows = []
    for rg, day_rows in usage.groupby("Region"):
        pool = by_region.get(rg)
        if pool is None or pool.empty:
            continue
        units = pool["DeployedUnits"].to_numpy(dtype=float)
        pressure = pool["Pressure"].to_numpy(dtype=float)
        ids = pool["CapacityId"].tolist()
        sites = pool["DatacentreId"].tolist()
        skus = pool["FabricSku"].tolist()
        classes = pool["SKUClass"].tolist()
        share = units * pressure

        for row in day_rows.itertuples():
            target_used = float(row.UsedUnits)
            wobble = 1.0 + rng.normal(0, 0.05, len(units))
            weights = np.clip(share * wobble, 1e-6, None)
            used = target_used * weights / weights.sum()

            # No capacity can be more than full. Spill the excess onto those
            # with room, repeatedly, so the region total is preserved.
            cap = units * 0.995
            for _ in range(6):
                over = used - cap
                spill = float(np.clip(over, 0, None).sum())
                if spill <= 1e-9:
                    break
                used = np.minimum(used, cap)
                room = cap - used
                if room.sum() <= 1e-9:
                    break
                used = used + spill * room / room.sum()
            used = np.minimum(used, cap)

            for i, cid in enumerate(ids):
                rows.append({
                    "Date": row.Date,
                    "CapacityId": cid,
                    "DatacentreId": sites[i],
                    "Region": rg,
                    "FabricSku": skus[i],
                    "SKUClass": classes[i],
                    "TotalUnits": round(float(units[i]), 1),
                    "UsedUnits": round(float(used[i]), 1),
                    "UtilisationPct": round(float(used[i]) / float(units[i]) * 100, 2),
                })
    return _tag(pd.DataFrame(rows),
                "region used-units distributed across its capacities by a stable "
                "per-capacity pressure; sums back to the region reading exactly")


# --------------------------------------------------------------------------
# procurement
# --------------------------------------------------------------------------


def lead_time_history() -> pd.DataFrame:
    """Lead time as it stood at successive dates, with a supplier.

    `sku_reference.csv` holds one number per class and no date, which makes the
    most useful procurement question unanswerable: not *how long is the lead
    time* but *is it getting worse*. A class whose lead time has more than
    doubled should pull its trigger earlier, and nothing in the data could say
    so until this table existed.
    """
    df = pd.DataFrame(LEAD_TIME_HISTORY,
                      columns=["SKUClass", "Supplier", "EffectiveFrom", "LeadTimeDays"])
    df = df.sort_values(["SKUClass", "EffectiveFrom"]).reset_index(drop=True)
    return _tag(df, "lead-time history invented; the current value of each class "
                    "matches sku_reference.csv and is not restated here")


# --------------------------------------------------------------------------
# operational health
# --------------------------------------------------------------------------


def operational_incidents(capacities: pd.DataFrame,
                          usage: pd.DataFrame) -> pd.DataFrame:
    """Grain: one row per operational incident on a capacity.

    Distinct from the ICM extract, which records capacity *requests* -- someone
    asking for more. This records the capacity misbehaving: nodes dropping,
    throttling, memory pressure. Nothing in the project had that, which is why
    a site could only ever be judged on how full it was.

    Rate is driven by hardware class and not by utilisation, deliberately. If
    incidents rose with fullness they would add nothing the utilisation figure
    does not already carry; keeping them independent is what allows the case
    that matters -- a capacity with room to spare that is nonetheless a bad
    place to run anything.
    """
    rng = _rng(13)
    days = sorted(usage["Date"].unique())
    if not days:
        return _tag(pd.DataFrame(columns=[
            "OperationalIncidentId", "CapacityId", "DatacentreId", "Region",
            "FabricSku", "SKUClass", "OpenedDate", "Severity", "IncidentType",
            "DowntimeMinutes", "ImpactedCustomers"]), "no usage window to place incidents in")

    rows = []
    seq = 0
    window = len(days)
    for cap in capacities.itertuples():
        rate = INCIDENT_RATE.get(cap.SKUClass, 1.0)
        # Incidents scale with the number of physical nodes, because a node is
        # what fails. This matters beyond realism: it makes incidents-per-node
        # a size-neutral measure of health, so a one-node F2 and an eight-node
        # F256 can be compared without the larger one looking worse purely for
        # being larger. An earlier draft scaled sub-linearly with capacity
        # units, which left every per-unit rate a function of SKU size and
        # buried the hardware signal underneath it.
        expected = rate * cap.NodeCount * (window / 150.0) * 2.0
        n = int(rng.poisson(max(expected, 0.05)))
        for _ in range(n):
            seq += 1
            day = days[int(rng.integers(0, window))]
            sev = str(rng.choice(SEVERITIES, p=SEVERITY_WEIGHTS))
            down = {"Sev1": (45, 400), "Sev2": (20, 180),
                    "Sev3": (5, 60), "Sev4": (1, 15)}[sev]
            rows.append({
                "OperationalIncidentId": f"OPS{seq:05d}",
                "CapacityId": cap.CapacityId,
                "DatacentreId": cap.DatacentreId,
                "Region": cap.Region,
                "FabricSku": cap.FabricSku,
                "SKUClass": cap.SKUClass,
                "OpenedDate": day,
                "Severity": sev,
                "IncidentType": str(rng.choice(INCIDENT_TYPES)),
                "DowntimeMinutes": int(rng.integers(*down)),
                "ImpactedCustomers": int(rng.integers(1, 9)),
            })
    df = pd.DataFrame(rows).sort_values("OpenedDate").reset_index(drop=True)
    return _tag(df, "incident rate driven by hardware class and capacity size, "
                    "independent of utilisation")


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
    """Every fleet-level table, anchored on the region tables that exist."""
    caps = capacity_inventory(dim_region)
    usage = capacity_usage_daily(caps, region_usage)
    return {
        "hardware_models": hardware_models(),
        "capacity_inventory": caps,
        "capacity_usage_daily": usage,
        "lead_time_history": lead_time_history(),
        "operational_incidents": operational_incidents(caps, usage),
        "partial_grants": partial_grants(tickets),
    }
