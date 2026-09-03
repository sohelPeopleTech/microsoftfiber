"""Assemble the entity model from the real extract plus the generated tables.

Each builder documents its **grain** -- what one row means -- because that is
the thing people get wrong when they join two facts together and double a total.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import attribution

from module5 import ingest

REAL = "ICM extract"
SYNTH = "generated"

#: entity -> (grain, where it came from)
ENTITIES = {
    "dim_region": ("one row per Azure region", "mixed"),
    "dim_datacentre": ("one row per datacentre", SYNTH),
    "dim_capacity_pool": ("one row per region capacity pool", SYNTH),
    "dim_subscription": ("one row per customer subscription", "mixed"),
    "dim_sku": ("one row per hardware class", SYNTH),
    "dim_feature": ("one row per product feature", SYNTH),
    "fact_capacity_request": ("one row per capacity ticket", "mixed"),
    "fact_usage_daily": ("one row per region per day", SYNTH),
    "fact_event": ("one row per business event", SYNTH),
    "bridge_feature_region": ("one row per feature per region", SYNTH),
    "fact_customer_demand_monthly": ("one row per subscription per month", "mixed"),
    # The fleet below the region. Until these existed the model stopped at a
    # region holding one hardware class and ten identical buildings, which is
    # enough to say a region is filling and not enough to say what to buy.
    "dim_capacity": ("one row per Fabric capacity", SYNTH),
    "dim_workspace": ("one row per workspace, assigned to a capacity", SYNTH),
    "fact_capacity_cu_daily": ("one row per capacity per day, in CU seconds", SYNTH),
    "fact_throttling_event": ("one row per throttling event", SYNTH),
    "fact_partial_grant": ("one row per partially-fulfilled request", SYNTH),
    # Real, and marked so. Everything else here is invented; these two are not.
    "dim_region_geography": ("one row per region, with coordinates", REAL),
    "bridge_region_fabric_availability": ("one row per region", REAL),
}


@dataclass
class DimensionalModel:
    tables: dict
    issues: list

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.tables[name]

    def __contains__(self, name: str) -> bool:
        return name in self.tables

    @property
    def is_valid(self) -> bool:
        return not self.issues


def _synth(synthetic_dir: str | Path, name: str) -> pd.DataFrame:
    # Large tables are written gzipped; pandas reads either transparently.
    path = Path(synthetic_dir) / f"{name}.csv"
    if not path.exists():
        gz = path.with_suffix(".csv.gz")
        if gz.exists():
            return pd.read_csv(gz)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- run synthdata.generate first, or the dimensional model "
            f"will be built with holes rather than failing loudly."
        )
    return pd.read_csv(path)


def _reference(reference_dir: str | Path, name: str) -> pd.DataFrame:
    """Real reference data, which lives apart from the generated tables.

    Separate loader rather than a flag on `_synth` so the two can never be
    confused at the call site: everything read through `_synth` is invented and
    everything read through here is not.
    """
    path = Path(reference_dir) / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- it is checked in; regenerate with "
            f"`python -m src.reference.refresh`."
        )
    return pd.read_csv(path)


# --------------------------------------------------------------------------
# dimensions
# --------------------------------------------------------------------------


def build_dim_sku(synthetic_dir) -> pd.DataFrame:
    """Grain: one hardware class. Carries the lead time Module 1 turns on."""
    sku = _synth(synthetic_dir, "sku_reference")
    return sku[["SKUClass", "RelativeCostPerUnit", "RelativePerformance",
                "LeadTimeDays", "IsSynthetic", "Provenance"]]


def build_dim_region(tickets, synthetic_dir) -> pd.DataFrame:
    """Grain: one Azure region.

    The region is the join point for five of the six modules, so everything a
    module needs about a region lives here: its hardware, its lead time, how
    much is deployed, and how much has been asked of it.
    """
    by_region = tickets.groupby("Region").agg(
        RequestCount=("IncidentId", "count"),
        RequestedUnits=("AdditionalLimitCapacity", "sum"),
        CustomerCount=("SubscriptionId", "nunique"),
        LargestCustomerLimit=("CurrentLimitCapacity", "max"),
    )

    sku = _synth(synthetic_dir, "sku_by_region")[["Region", "SKUClass"]]
    inv = _synth(synthetic_dir, "hardware_inventory")[["Region", "DeployedUnits"]]
    ref = _synth(synthetic_dir, "sku_reference")[["SKUClass", "LeadTimeDays"]]

    dim = (
        by_region.reset_index()
        .merge(sku, on="Region", how="left")
        .merge(inv, on="Region", how="left")
        .merge(ref, on="SKUClass", how="left")
    )
    # Region identity and request counts are real; hardware is generated.
    dim["Provenance"] = f"identity+demand: {REAL}; hardware+lead time: {SYNTH}"
    return dim.sort_values("RequestedUnits", ascending=False).reset_index(drop=True)


def build_fact_customer_demand(fact, dim_subscription) -> pd.DataFrame:
    """Grain: one subscription per month.

    Review asked for forecasting down to the customer and accepted that the
    history would have to be synthesised -- two or three tickets each cannot
    carry a forecast. Where the extract does have a month for a customer, that
    real figure replaces the generated one, so this is a floor under the real
    data rather than a replacement for it.
    """
    when = pd.to_datetime(fact["DeniedDate"].fillna(fact["ApprovedDate"]), errors="coerce")
    obs = fact.assign(Month=when.dt.tz_localize(None).dt.to_period("M"))
    obs = obs[obs["Month"].notna()]
    if obs.empty:
        return pd.DataFrame(columns=["SubscriptionId", "Month", "CoresRequested",
                                     "RequestCount", "IsDealMonth", "IsSynthetic"])

    last = obs["Month"].max()
    months = [str(last - i) for i in range(attribution.CUSTOMER_HISTORY_MONTHS - 1, -1, -1)]

    real = (obs.groupby(["SubscriptionId", "Month"])
            .agg(CoresRequested=("AdditionalLimitCapacity", "sum"),
                 RequestCount=("IncidentId", "count"))
            .reset_index())
    real["Month"] = real["Month"].astype(str)
    real_by = {(str(r.SubscriptionId), r.Month): r for r in real.itertuples()}

    # What this customer asks for in an ordinary month, from their own history
    # where they have one. A customer seen only during a spike would otherwise
    # get a generated baseline as large as the spike.
    typical = (obs.groupby("SubscriptionId")["AdditionalLimitCapacity"]
               .median().to_dict())

    rows = []
    for sub in sorted(dim_subscription["SubscriptionId"].astype(str)):
        base = float(typical.get(sub, 20.0) or 20.0)
        for gen in attribution.customer_monthly_demand(sub, months, base):
            hit = real_by.get((sub, gen["Month"]))
            if hit is not None:
                rows.append({
                    "SubscriptionId": sub, "Month": gen["Month"],
                    "CoresRequested": round(float(hit.CoresRequested), 1),
                    "RequestCount": int(hit.RequestCount),
                    "IsDealMonth": False, "IsSynthetic": False,
                })
            else:
                rows.append({"SubscriptionId": sub, **gen})

    out = pd.DataFrame(rows)
    out["Provenance"] = (
        f"MIXED - months present in the ICM extract are real ({REAL}); the "
        f"remainder are generated to give each customer enough history to "
        f"forecast. Kept out of fact_capacity_request so no reported figure moves."
    )
    return out


def build_dim_datacentre(dim_region, usage=None, capacities=None,
                         capacity_usage=None) -> pd.DataFrame:
    """Grain: one datacentre. Region -> datacentre -> ticket is the drill-down
    an engineer actually works in; the region total alone tells them which
    country to worry about, not which building.

    When the capacity tables are available this reads from them. Before they
    existed a site's units were the region's divided by ten and its utilisation
    was the region's rate reapplied, which made every site in a region identical
    on both counts -- the drill-down looked like ten buildings and behaved like
    one. `dim_capacity` gives each site its own capacities, its own hardware and
    its own daily readings, so those columns are now measured rather than
    apportioned. The old path is kept for callers that have no capacity tables.
    """
    from module2.conversion import datacentres_for

    dim = attribution.build_dim_datacentre(dim_region, datacentres_for)

    # Per-site safety line and how much of the site is already committed.
    # Review asked for both: "call out what cores they already have, how many
    # are still left, and the threshold for each data centre".
    dim["ThresholdPct"] = [attribution.site_threshold(d) for d in dim["DatacentreId"]]

    if capacities is not None and len(capacities):
        by_site = capacities.groupby("DatacentreId")
        units = by_site["DeployedUnits"].sum()
        # A site runs one hardware class in this model, but take the dominant
        # one rather than assuming it, so a mixed site would report honestly.
        dim["DeployedUnits"] = dim["DatacentreId"].map(units).fillna(0.0).round(1)
        dim["CapacityUnits"] = dim["DatacentreId"].map(
            by_site["CapacityUnits"].sum()).fillna(0).astype(int)
        dim["CapacityCount"] = dim["DatacentreId"].map(by_site.size()).fillna(0).astype(int)
        dim["Provenance"] = (
            "GENERATED - units and hardware read from the capacities in this "
            "site, not apportioned from its region."
        )

    if capacity_usage is not None and len(capacity_usage):
        latest_day = capacity_usage["Date"].max()
        latest = capacity_usage[capacity_usage["Date"] == latest_day]
        # CU seconds consumed against CU seconds available, applied to the
        # site's own units so the column keeps the meaning the rest of the
        # product gives it.
        by = latest.groupby("DatacentreId")[["CuSecondsConsumed", "CuSecondsAvailable"]].sum()
        # Not capped at 1.0. A Fabric capacity genuinely consumes past its
        # nameplate -- that is bursting, and smoothing is what absorbs it -- so a
        # rate above 1 is a reading, not an error. Clipping it reported
        # northcentralus-dc07 at exactly 100.0% while the single capacity in it
        # was running at 113% and peaking above 110%, which is precisely the
        # overload the page exists to surface.
        rate = by["CuSecondsConsumed"] / by["CuSecondsAvailable"].replace(0, pd.NA)
        dim["UsedUnits"] = (dim["DeployedUnits"]
                            * dim["DatacentreId"].map(rate).fillna(0.0)).round(1)
    else:
        # Utilisation is measured per region, so a site's used share is the
        # region rate applied to its own units. An apportionment, not a reading.
        rate = {}
        if usage is not None and len(usage):
            latest = usage.sort_values("Date").groupby("Region").tail(1)
            rate = dict(zip(latest["Region"], latest["UtilisationPct"] / 100.0, strict=True))
        dim["UsedUnits"] = [
            round(float(u) * rate.get(r, 0.0), 1)
            for u, r in zip(dim["DeployedUnits"], dim["Region"], strict=True)
        ]
    dim["FreeUnits"] = (dim["DeployedUnits"] - dim["UsedUnits"]).round(1)
    # Headroom before the site's own safety line, not before physical capacity.
    dim["HeadroomToThreshold"] = (
        dim["DeployedUnits"] * dim["ThresholdPct"] / 100.0 - dim["UsedUnits"]
    ).round(1)
    return dim


#: How far a data centre may sit from its region's published point. Azure gives
#: one coordinate per region -- itself an approximate central point -- and none
#: per building, so the sites are scattered within a metro-sized radius of it.
#: 75 km keeps every region's sites on the right landmass while still giving the
#: map something to separate them by.
SITE_SPREAD_KM = 75.0


def attach_datacentre_coordinates(dim: pd.DataFrame, geo: pd.DataFrame) -> pd.DataFrame:
    """Give every data centre a latitude and longitude.

    The extract has none: a data centre in this model is a generated name under
    a region. `dim_region_geography` carries a real coordinate per *region*
    (`az account list-locations`), but nothing per building, so each site is
    placed at its region's point plus a small deterministic offset -- a bearing
    and a distance from a hash of its id. Deterministic so the position is
    stable between runs and no two sites coincide; the distance is sqrt-weighted
    so the scatter fills a disc rather than bunching at the centre.

    Real region point, generated within-region offset. The Provenance column
    says exactly that.
    """
    point = geo.set_index("Region")[["Latitude", "Longitude"]]
    lats, lons = [], []
    for site in dim.itertuples():
        if site.Region not in point.index:
            lats.append(None)
            lons.append(None)
            continue
        blat = float(point.loc[site.Region, "Latitude"])
        blon = float(point.loc[site.Region, "Longitude"])
        h = hashlib.sha256(f"geo:{site.DatacentreId}".encode()).hexdigest()
        bearing = int(h[:8], 16) / 0xFFFFFFFF * 2 * math.pi
        dist_km = SITE_SPREAD_KM * math.sqrt(int(h[8:16], 16) / 0xFFFFFFFF)
        dlat = dist_km * math.cos(bearing) / 111.0
        dlon = dist_km * math.sin(bearing) / (111.0 * max(math.cos(math.radians(blat)), 0.2))
        lats.append(round(blat + dlat, 4))
        lons.append(round(blon + dlon, 4))
    dim = dim.copy()
    dim["Latitude"] = lats
    dim["Longitude"] = lons
    if "Provenance" in dim.columns:
        dim["Provenance"] = dim["Provenance"].astype(str) + (
            f" Coordinates are the region's real point plus a deterministic "
            f"offset of up to {int(SITE_SPREAD_KM)} km -- Azure publishes no "
            f"per-building location."
        )
    return dim


def build_dim_capacity_pool(dim_region) -> pd.DataFrame:
    """Grain: one Fabric capacity pool per region.

    The other modules reason in raw compute; a customer buys an F-SKU rated in
    Capacity Units. Both are needed so a denial can be discussed in the units
    the customer actually purchased.
    """
    from admission import build_dim_capacity_pool as _build

    return _build(dim_region)


def build_dim_subscription(gold) -> pd.DataFrame:
    """Grain: one subscription. Tier and ARR are the placeholder reference."""
    dim = (
        gold.groupby(["SubscriptionId", "TenantId", "SubscriptionTier"])
        .agg(
            ARR_USD=("ARR_USD", "max"),
            RequestCount=("IncidentId", "count"),
            RegionCount=("Region", "nunique"),
        )
        .reset_index()
    )
    dim["Provenance"] = f"ids+requests: {REAL}; tier+ARR: placeholder reference"
    # A readable name beside the id. Review: a subscription id sits next to an
    # incident id of the same shape, so a reader has to work out which is which
    # before they can read the row.
    names = attribution.assign_company_names(dim["SubscriptionId"])
    dim["CustomerName"] = [names[str(s)] for s in dim["SubscriptionId"]]
    return dim.sort_values("ARR_USD", ascending=False).reset_index(drop=True)


def build_dim_feature(synthetic_dir) -> pd.DataFrame:
    """Grain: one product feature."""
    fm = _synth(synthetic_dir, "feature_matrix")
    dim = fm.groupby("Feature").agg(RegionCount=("Region", "nunique")).reset_index()
    live = fm[fm["Status"] == "Live"].groupby("Feature").size().rename("LiveRegions")
    dim = dim.merge(live, on="Feature", how="left").fillna({"LiveRegions": 0})
    dim["LiveRegions"] = dim["LiveRegions"].astype(int)
    dim["IsSynthetic"] = True
    dim["Provenance"] = f"{SYNTH}"
    return dim


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def build_fact_capacity_request(gold, synthetic_dir) -> pd.DataFrame:
    """Grain: one capacity ticket. The spine of the whole model.

    Real in every column except TicketStatus, which ICM does not expose and
    which is the difference between "rejected" and "still being worked".
    """
    status = _synth(synthetic_dir, "ticket_status")[
        ["IncidentId", "TicketStatus", "ClosedDate"]
    ]
    fact = gold.copy()
    fact["IncidentId"] = fact["IncidentId"].astype(str)
    status["IncidentId"] = status["IncidentId"].astype(str)
    fact = fact.merge(status, on="IncidentId", how="left")
    fact["RequestedCapacity"] = fact["CurrentLimitCapacity"] + fact["AdditionalLimitCapacity"]
    fact["GrantedCapacity"] = fact["NewLimitCapacity"]

    # Which building, and why it was refused. ICM gives neither, so both are
    # derived -- deterministically, from the incident id. See attribution.py.
    from module2.conversion import datacentres_for

    fact["DatacentreId"] = attribution.assign_datacentre(fact, datacentres_for)
    # A request failed if it was denied and either never granted, or granted
    # late. Reason is attached to those and left blank everywhere else.
    denied = fact["DeniedDate"].notna()
    unfulfilled = fact["NewLimitCapacity"] < fact["RequestedCapacity"]
    late = fact["ApprovedDate"].notna() & denied
    # Reason is attached later, once per-site capacity exists -- it is derived
    # from that state rather than drawn from a distribution.
    fact.attrs["failed_mask"] = denied & (unfulfilled | late)
    fact["DenialReason"] = ""

    fact["Provenance"] = (
        f"ticket: {REAL}; TicketStatus, DatacentreId, DenialReason: {SYNTH}"
    )
    return fact


def build_fact_usage_daily(synthetic_dir) -> pd.DataFrame:
    """Grain: one region, one day. What Modules 1 and 3 forecast on."""
    usage = _synth(synthetic_dir, "capacity_usage")
    usage["Date"] = pd.to_datetime(usage["Date"]).dt.date.astype(str)
    return usage


def build_fact_event(synthetic_dir) -> pd.DataFrame:
    """Grain: one business event. What Module 4 matches spikes against."""
    ev = _synth(synthetic_dir, "deal_events")
    ev["LinkedIncidentId"] = ev["LinkedIncidentId"].fillna("").astype(str).str.replace(".0", "", regex=False)
    return ev


def build_bridge_feature_region(synthetic_dir) -> pd.DataFrame:
    """Grain: one feature in one region. Module 6, in a single table."""
    return _synth(synthetic_dir, "feature_matrix")


# --------------------------------------------------------------------------
# the fleet below the region
# --------------------------------------------------------------------------


def build_dim_capacity(synthetic_dir) -> pd.DataFrame:
    """Grain: one Fabric capacity -- an F-SKU sitting in one datacentre.

    The level a capacity manager actually buys at. `dim_capacity_pool` says a
    region is *equivalent to* an F2048; this says the region is forty-one
    separate capacities of eight different sizes, which is the difference
    between a summary and something you can act on.
    """
    return _synth(synthetic_dir, "capacity_inventory")


def build_dim_workspace(synthetic_dir) -> pd.DataFrame:
    """Grain: one workspace, assigned to one capacity.

    Fabric bills and sizes by capacity; workspaces are what you move between
    them. Without this, "load balance across capacities" names no object.
    """
    return _synth(synthetic_dir, "dim_workspace")


def build_fact_capacity_cu_daily(synthetic_dir) -> pd.DataFrame:
    """Grain: one capacity, one day, in CU seconds.

    The Fabric-native measure. An F64 provides 64 CUs, so a day of it is
    64 x 86,400 CU seconds, and consumption is measured against that.
    Utilisation over 100% is bursting and is not by itself a fault -- what
    matters is `FutureCapacityMinutes`, the smoothed overage that decides
    which throttling stage the capacity is in.
    """
    cu = _synth(synthetic_dir, "capacity_cu_daily")
    cu["Date"] = pd.to_datetime(cu["Date"]).dt.date.astype(str)
    return cu


def build_fact_throttling_event(synthetic_dir) -> pd.DataFrame:
    """Grain: one throttling event -- a capacity, a day, a stage.

    Replaces the node-failure incidents an earlier model carried. Fabric
    capacities do not fail; they delay interactive jobs, then reject them, then
    reject everything, on thresholds Microsoft publishes.
    """
    ev = _synth(synthetic_dir, "throttling_events")
    if len(ev):
        ev["Date"] = pd.to_datetime(ev["Date"]).dt.date.astype(str)
    return ev


def build_fact_partial_grant(synthetic_dir) -> pd.DataFrame:
    """Grain: one request that was met in part.

    A third outcome between granted and refused. The ICM extract contains none
    -- every row grants all or nothing -- so this table is the only place the
    state exists, and anything quoting it has to say so.
    """
    return _synth(synthetic_dir, "partial_grants")


def build_dim_region_geography(reference_dir) -> pd.DataFrame:
    """Grain: one region, with where it physically is. Real."""
    return _reference(reference_dir, "region_geography")


def build_bridge_region_fabric_availability(reference_dir) -> pd.DataFrame:
    """Grain: one region, with which Fabric workloads it supports. Real.

    Supersedes `bridge_feature_region` for any question about what Microsoft
    actually ships where. That table is a seeded random draw correlated with
    ticket volume; this one is Microsoft's published table. Both are kept --
    the generated one still drives the six-feature expansion check the earlier
    module was built around -- but only one of them should be quoted.
    """
    return _reference(reference_dir, "fabric_region_availability")


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build(
    ticket_source: str | Path = "data/Synthetic_ICM_Capacity_Data.xlsx",
    synthetic_dir: str | Path = "data/synthetic",
    reference_dir: str | Path = "data/reference",
) -> DimensionalModel:
    """Build every entity and check the joins hold."""
    gold, _ = ingest.load_gold(ticket_source)
    tickets = gold

    dim_region = build_dim_region(tickets, synthetic_dir)
    usage = build_fact_usage_daily(synthetic_dir)
    capacities = build_dim_capacity(synthetic_dir)
    cap_usage = build_fact_capacity_cu_daily(synthetic_dir)
    tables = {
        "dim_sku": build_dim_sku(synthetic_dir),
        "dim_region": dim_region,
        "dim_datacentre": build_dim_datacentre(dim_region, usage, capacities, cap_usage),
        "dim_capacity_pool": build_dim_capacity_pool(dim_region),
        "dim_subscription": build_dim_subscription(gold),
        "dim_feature": build_dim_feature(synthetic_dir),
        "fact_capacity_request": build_fact_capacity_request(gold, synthetic_dir),
        "fact_usage_daily": usage,
        "fact_event": build_fact_event(synthetic_dir),
        "bridge_feature_region": build_bridge_feature_region(synthetic_dir),
        "dim_capacity": capacities,
        "dim_workspace": build_dim_workspace(synthetic_dir),
        "fact_capacity_cu_daily": cap_usage,
        "fact_throttling_event": build_fact_throttling_event(synthetic_dir),
        "fact_partial_grant": build_fact_partial_grant(synthetic_dir),
        "dim_region_geography": build_dim_region_geography(reference_dir),
        "bridge_region_fabric_availability":
            build_bridge_region_fabric_availability(reference_dir),
    }
    # A region's safety threshold is the aggregate of the thresholds its own
    # facilities run at, weighted by how much capacity each holds. Review was
    # explicit that a threshold belongs to a region rather than being one number
    # imposed on all of them -- "this is a high utilised region, why should I keep
    # the same as a low utilisation region" -- and deriving it from the sites is
    # what keeps the two levels consistent: a region cannot claim a safety line
    # its buildings are not actually holding.
    #
    # Computed here rather than in build_dim_region because sites do not exist
    # until after regions are built, the same ordering that denial reasons need.
    _sites = tables["dim_datacentre"]
    _weighted = (
        _sites.assign(_w=_sites["DeployedUnits"] * _sites["ThresholdPct"])
        .groupby("Region")
        .apply(lambda g: g["_w"].sum() / g["DeployedUnits"].sum()
               if g["DeployedUnits"].sum() else float("nan"),
               include_groups=False)
    )
    tables["dim_region"]["ThresholdPct"] = (
        tables["dim_region"]["Region"].map(_weighted).round(1)
    )

    # Put each data centre on the map. Needs both tables, and dim_region_geography
    # is built above, so like the threshold roll-up this happens here rather than
    # inside the datacentre builder.
    tables["dim_datacentre"] = attach_datacentre_coordinates(
        tables["dim_datacentre"], tables["dim_region_geography"]
    )

    # Customer demand history. Generated, and kept in its own table rather than
    # merged into fact_capacity_request: every published figure -- exposure,
    # failure counts, cores pending -- is computed from that table, and padding
    # it with invented tickets would silently move numbers already reviewed.
    tables["fact_customer_demand_monthly"] = build_fact_customer_demand(
        tables["fact_capacity_request"], tables["dim_subscription"])

    # Denial reason, once the site capacity it depends on is available.
    fact, sites = tables["fact_capacity_request"], tables["dim_datacentre"]
    capacity = {
        str(r.DatacentreId): {"free": float(r.FreeUnits),
                              "headroom": float(r.HeadroomToThreshold)}
        for r in sites.itertuples()
    }
    fact["DenialReason"] = attribution.assign_denial_reason(
        fact, fact.attrs.get("failed_mask", fact["DeniedDate"].notna()), capacity)

    return DimensionalModel(tables=tables, issues=validate(tables))


def validate(tables: dict) -> list[str]:
    """Every foreign key must resolve. A silent drop is how a total stops adding up."""
    issues = []
    regions = set(tables["dim_region"]["Region"])
    subs = set(tables["dim_subscription"]["SubscriptionId"])
    skus = set(tables["dim_sku"]["SKUClass"])
    features = set(tables["dim_feature"]["Feature"])

    def check(table: str, column: str, allowed: set, label: str) -> None:
        if table not in tables or column not in tables[table]:
            return
        unknown = set(tables[table][column].dropna()) - allowed
        if unknown:
            issues.append(
                f"{table}.{column} has {len(unknown)} value(s) missing from {label}: "
                f"{sorted(unknown)[:3]}"
            )

    check("fact_capacity_request", "Region", regions, "dim_region")
    if "dim_datacentre" in tables:
        check("fact_capacity_request", "DatacentreId",
              set(tables["dim_datacentre"]["DatacentreId"]), "dim_datacentre")
    check("fact_capacity_request", "SubscriptionId", subs, "dim_subscription")
    check("fact_usage_daily", "Region", regions, "dim_region")
    check("fact_usage_daily", "SKUClass", skus, "dim_sku")
    check("fact_event", "Region", regions, "dim_region")
    check("bridge_feature_region", "Region", regions, "dim_region")
    check("bridge_feature_region", "Feature", features, "dim_feature")
    check("dim_region", "SKUClass", skus, "dim_sku")

    # A fact row with no measure is a modelling error, not data.
    if tables["fact_capacity_request"]["RequestedCapacity"].isna().any():
        issues.append("fact_capacity_request has rows with no RequestedCapacity")

    return issues


def sources(tables: dict) -> pd.DataFrame:
    """What is real and what is generated, per entity -- for the product to show."""
    rows = []
    for name, df in tables.items():
        grain, origin = ENTITIES.get(name, ("", "unknown"))
        synthetic = bool(df["IsSynthetic"].all()) if "IsSynthetic" in df else origin == SYNTH
        prov = df["Provenance"].iloc[0] if "Provenance" in df and len(df) else ""
        rows.append({
            "Entity": name,
            "Rows": len(df),
            "Grain": grain,
            "FullySynthetic": synthetic,
            "Provenance": prov,
        })
    return pd.DataFrame(rows)
