"""Generate the entities the ICM extract does not contain.

Each generator takes the real ticket data as its anchor so the synthetic world
agrees with the real one. Where a choice is arbitrary it is made deterministic
from a seed, never from the clock.

    sku_by_region        which hardware each region runs      -> modules 1, 2
    capacity_usage       usage per region per day             -> modules 1, 3
    hardware_inventory   deployed units per region per SKU    -> module 2
    sku_reference        cost + performance per SKU class     -> module 2
    deal_events          customer-success / deal closures     -> module 4
    feature_matrix       feature x region availability        -> module 6
    ticket_status        open / rejected / fulfilled per ticket -> module 5
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import SEED

PROVENANCE = "SYNTHETIC - generated, not a business source"

# The five classes the workbook's lead-time table already names. Reusing them
# keeps the generated assignment joinable to the sheet that exists.
SKU_CLASSES = ["AMD-standard", "AMD-highmem", "Intel-standard", "Intel-highmem", "GPU-class"]

# Fabric features worth asking "is it live in my region?" about.
FEATURES = [
    "Copilot in Fabric",
    "Real-Time Intelligence",
    "Mirrored Databases",
    "OneLake Shortcuts",
    "Data Activator",
    "Fabric SQL Database",
]


def _rng(salt: int = 0) -> np.random.Generator:
    return np.random.default_rng(SEED + salt)


def _tag(df: pd.DataFrame, note: str) -> pd.DataFrame:
    df = df.copy()
    df["IsSynthetic"] = True
    df["Provenance"] = f"{PROVENANCE}: {note}"
    return df


# --------------------------------------------------------------------------
# hardware
# --------------------------------------------------------------------------


def sku_by_region(tickets: pd.DataFrame) -> pd.DataFrame:
    """Assign each real region a hardware class.

    Deterministic from the region name, so the same region always lands on the
    same SKU no matter what order the data arrives in -- and so the assignment
    survives a re-run without silently re-shuffling every downstream number.

    Regions carrying the largest requests get the denser classes, which is the
    direction reality tends to run in and makes the lead-time story coherent:
    the biggest asks sit on the longest lead times.
    """
    demand = (
        tickets.groupby("Region")["AdditionalLimitCapacity"].sum().sort_values(ascending=False)
    )
    rows = []
    for rank, (region, units) in enumerate(demand.items()):
        # Top demand -> highmem/GPU (long lead times), tail -> standard.
        if rank < 2:
            sku = "Intel-highmem"
        elif rank < 4:
            sku = "GPU-class"
        elif rank < 7:
            sku = "AMD-highmem"
        else:
            sku = SKU_CLASSES[rank % 2]  # AMD-standard / AMD-highmem alternating
        rows.append({"Region": region, "SKUClass": sku, "TotalRequestedUnits": int(units)})
    return _tag(pd.DataFrame(rows), "hardware class inferred from observed request volume")


def sku_reference() -> pd.DataFrame:
    """Cost and relative performance per class.

    Indexed so AMD-standard = 1.0 on both axes; everything else is expressed
    against it. Relative figures are honest about what they are -- an absolute
    dollar-per-unit would look like a price and get quoted as one.
    """
    rows = [
        ("AMD-standard", 1.00, 1.00, 21),
        ("AMD-highmem", 1.45, 1.30, 10),
        ("Intel-standard", 1.10, 0.95, 21),
        ("Intel-highmem", 1.70, 1.40, 45),
        ("GPU-class", 3.20, 2.60, 30),
    ]
    df = pd.DataFrame(rows, columns=["SKUClass", "RelativeCostPerUnit",
                                     "RelativePerformance", "LeadTimeDays"])
    return _tag(df, "relative cost/performance index, AMD-standard = 1.0")


def hardware_inventory(tickets: pd.DataFrame, skus: pd.DataFrame) -> pd.DataFrame:
    """Units deployed per region.

    Anchored on the largest CurrentLimitCapacity actually seen in each region:
    whatever a customer already had, the region must at least have had that
    much, plus headroom.
    """
    rng = _rng(1)
    largest = tickets.groupby("Region")["CurrentLimitCapacity"].max()
    rows = []
    for region, sku in skus.set_index("Region")["SKUClass"].items():
        floor = int(largest.get(region, 0))
        deployed = int(max(floor * 4, 512) * rng.uniform(1.0, 1.8))
        rows.append({"Region": region, "SKUClass": sku, "DeployedUnits": deployed,
                     "LargestObservedCustomerLimit": floor})
    return _tag(pd.DataFrame(rows), "deployed units scaled from the largest observed customer limit")


# --------------------------------------------------------------------------
# usage over time  (the input modules 1 and 3 actually need)
# --------------------------------------------------------------------------


def capacity_usage(tickets: pd.DataFrame, inventory: pd.DataFrame) -> pd.DataFrame:
    """Daily used-vs-total capacity per region across the observed window.

    Built so the curve explains the tickets rather than contradicting them: a
    region trends upward in proportion to the capacity its customers asked for,
    and a request lands on the days around its denial. Without that coupling a
    forecast would point one way while the tickets point the other, and nobody
    would trust either.
    """
    rng = _rng(2)
    dates = pd.to_datetime(
        pd.concat([tickets["DeniedDate"], tickets["ApprovedDate"]]).dropna(), utc=True
    )
    start, end = dates.min().normalize(), dates.max().normalize()
    days = pd.date_range(start, end, freq="D", tz="UTC")

    demand = tickets.groupby("Region")["AdditionalLimitCapacity"].sum()
    total_demand = max(demand.sum(), 1)

    rows = []
    for _, inv in inventory.iterrows():
        region = inv["Region"]
        total = float(inv["DeployedUnits"])
        # Growth over the window, proportional to that region's share of demand.
        share = float(demand.get(region, 0)) / total_demand
        start_util = rng.uniform(0.45, 0.62)
        end_util = min(0.97, start_util + 0.10 + share * 1.6)

        noise = rng.normal(0, 0.012, len(days))
        # A gentle weekly rhythm -- weekends dip -- so the curve looks like
        # infrastructure rather than a straight line.
        weekly = np.array([-0.02 if d.weekday() >= 5 else 0.005 for d in days])
        ramp = np.linspace(start_util, end_util, len(days))
        util = np.clip(ramp + noise + weekly, 0.05, 0.995)

        for day, u in zip(days, util, strict=True):
            rows.append({
                "Date": day.date().isoformat(),
                "Region": region,
                "SKUClass": inv["SKUClass"],
                "TotalUnits": round(total, 1),
                "UsedUnits": round(total * u, 1),
                "UtilisationPct": round(u * 100, 2),
            })
    return _tag(pd.DataFrame(rows), "utilisation curve grown in proportion to observed request volume")


# --------------------------------------------------------------------------
# events and features
# --------------------------------------------------------------------------


def deal_events(tickets: pd.DataFrame) -> pd.DataFrame:
    """Customer-success / deal-closure events, with region and date.

    Deliberately placed a few days *before* the biggest capacity requests in a
    region -- that is the causal story Module 4 exists to detect ("demand spiked
    because a deal closed"). Events with no matching spike are included too, so
    the detector has to discriminate rather than match everything.
    """
    rng = _rng(3)
    df = tickets.copy()
    df["When"] = pd.to_datetime(df["DeniedDate"].fillna(df["ApprovedDate"]), utc=True)
    biggest = df.sort_values("AdditionalLimitCapacity", ascending=False).head(12)

    kinds = ["Deal closed", "Expansion signed", "Pilot converted", "Renewal upsized"]
    rows = []
    for i, t in enumerate(biggest.itertuples()):
        lead = int(rng.integers(2, 10))
        rows.append({
            "EventDate": (t.When - pd.Timedelta(days=lead)).date().isoformat(),
            "Region": t.Region,
            "SubscriptionId": t.SubscriptionId,
            "EventType": kinds[i % len(kinds)],
            "ExpectedCapacityUnits": int(t.AdditionalLimitCapacity),
            "LinkedIncidentId": str(t.IncidentId),
        })

    # Unlinked events -- noise the detector must not fire on.
    regions = sorted(tickets["Region"].unique())
    window = pd.to_datetime(df["When"]).dropna()
    for i in range(6):
        day = window.min() + pd.Timedelta(days=int(rng.integers(0, (window.max()-window.min()).days)))
        rows.append({
            "EventDate": day.date().isoformat(),
            "Region": regions[int(rng.integers(0, len(regions)))],
            "SubscriptionId": "",
            "EventType": "Marketing campaign",
            "ExpectedCapacityUnits": 0,
            "LinkedIncidentId": "",
        })
    out = pd.DataFrame(rows).sort_values("EventDate").reset_index(drop=True)
    return _tag(out, "deal events seeded ahead of the largest observed requests, plus unlinked noise")


def feature_matrix(tickets: pd.DataFrame) -> pd.DataFrame:
    """Feature x region availability: live, preview, planned or unavailable.

    Weighted so the busiest regions have the most features live, which is how
    rollouts actually run and makes "is it live here?" a question with a
    non-uniform answer.
    """
    rng = _rng(4)
    order = (
        tickets.groupby("Region")["IncidentId"].count().sort_values(ascending=False).index.tolist()
    )
    states = ["Live", "Preview", "Planned", "Unavailable"]
    rows = []
    for rank, region in enumerate(order):
        maturity = 1.0 - rank / max(len(order) - 1, 1)   # 1.0 busiest -> 0.0 quietest
        for feature in FEATURES:
            p = rng.uniform(0, 1) * 0.55 + maturity * 0.45
            state = states[0] if p > 0.62 else states[1] if p > 0.45 else states[2] if p > 0.28 else states[3]
            rows.append({"Feature": feature, "Region": region, "Status": state})
    return _tag(pd.DataFrame(rows), "rollout weighted by regional request volume")


def ticket_status(tickets: pd.DataFrame) -> pd.DataFrame:
    """The field ICM does not give us: what happened to the ticket.

    This is the single most consequential gap in the real data -- without it,
    "denied and never approved" cannot be split into *rejected* and *still being
    worked*, and the headline exposure is overstated. Generated consistently
    with the dates: anything approved is Fulfilled; anything still open is split
    between Rejected and InProgress, weighted by how long it has been open.
    """
    rng = _rng(5)
    rows = []
    as_of = pd.to_datetime(
        pd.concat([tickets["DeniedDate"], tickets["ApprovedDate"]]).dropna(), utc=True
    ).max()
    for t in tickets.itertuples():
        approved = pd.notna(t.ApprovedDate) and str(t.ApprovedDate).strip() != ""
        if approved:
            status, closed = "Fulfilled", str(pd.to_datetime(t.ApprovedDate, utc=True).date())
        else:
            open_days = (as_of - pd.to_datetime(t.DeniedDate, utc=True)).days
            # The longer it has sat, the likelier it was actually rejected
            # rather than still being worked.
            p_rejected = min(0.85, 0.25 + open_days / 200)
            rejected = rng.uniform(0, 1) < p_rejected
            status, closed = ("Rejected", str(as_of.date())) if rejected else ("InProgress", "")
        rows.append({"IncidentId": str(t.IncidentId), "TicketStatus": status,
                     "ClosedDate": closed})
    return _tag(pd.DataFrame(rows), "status inferred from dates and how long a denial stayed open")


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def generate_all(tickets: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every synthetic table, built from the real ticket extract.

    Two tiers. The region tables come first and are what the original six
    modules consume. The fleet tables underneath them -- capacities, the
    hardware in them, how each one ran -- are generated from the region tables
    rather than alongside, so the finer grain always adds up to the coarser one
    instead of merely resembling it.
    """
    # Imported here rather than at module scope: fleet.py takes `_tag` and
    # `PROVENANCE` from this module, so a top-level import would be circular.
    from . import fleet

    skus = sku_by_region(tickets)
    inventory = hardware_inventory(tickets, skus)
    usage = capacity_usage(tickets, inventory)

    tables = {
        "sku_by_region": skus,
        "sku_reference": sku_reference(),
        "hardware_inventory": inventory,
        "capacity_usage": usage,
        "deal_events": deal_events(tickets),
        "feature_matrix": feature_matrix(tickets),
        "ticket_status": ticket_status(tickets),
    }
    tables.update(fleet.generate_fleet(tickets, inventory, usage))
    return tables


#: Row count above which a table is written gzipped. The per-capacity usage
#: table is fifty thousand rows and every one of them carries the same
#: hundred-character provenance string, which is six megabytes of identical
#: text. Compressing is preferable to dropping the provenance column: the
#: convention that every generated row says it is generated is worth more than
#: the disk, and pandas reads .csv.gz without anything else having to know.
GZIP_ABOVE_ROWS = 20_000


def table_path(out_dir: Path, name: str, rows: int) -> Path:
    return Path(out_dir) / (f"{name}.csv.gz" if rows > GZIP_ABOVE_ROWS else f"{name}.csv")


def write_all(tables: dict[str, pd.DataFrame], out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, df in tables.items():
        path = table_path(out_dir, name, len(df))
        # mtime=0 so a rebuild of unchanged data is byte-identical; gzip
        # otherwise stamps the clock into the header and every regeneration
        # would look like a change.
        df.to_csv(path, index=False,
                  **({"compression": {"method": "gzip", "mtime": 0}}
                     if path.suffix == ".gz" else {}))
        written.append(path)
    return written
