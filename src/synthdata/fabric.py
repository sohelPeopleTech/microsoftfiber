"""Fabric capacities as Fabric actually works them.

This replaces a model that described Azure infrastructure: hardware classes,
provisioning lead times, node failures, and purchase orders raised weeks ahead.
None of that exists in Fabric. A customer never sees a server, an F-SKU is
scaled in Azure and takes effect immediately, and a capacity does not fail --
it *throttles*.

What is modelled here is what a Fabric capacity admin actually sees:

    dim_workspace          workloads assigned to a capacity
    fact_capacity_cu       CU seconds consumed per capacity per day
    fact_throttling        throttling events, by stage

The mechanics are Microsoft's, not invented:

**Capacity Units.** An F64 provides 64 CUs. A day of an F64 is therefore
64 x 86,400 = 5,529,600 CU seconds. Consumption is measured against that.

**A capacity never consumes more than it holds.** Consumption is bounded by
the CU seconds the SKU provides, so utilisation is always under 100% and the
CU a capacity used is always less than the CU it has. Every figure downstream
inherits that: `UtilisationPct` is `CuSecondsConsumed / CuSecondsAvailable`,
and a site or region roll-up is the same ratio over summed seconds.

This is a deliberate departure from Fabric's own bursting-and-smoothing
mechanic, where an operation may use more compute than the SKU provides and
Fabric spreads the cost over future 30-second timepoints -- which makes
utilisation above 100% normal and not by itself a fault. That model is
described at https://learn.microsoft.com/en-us/fabric/enterprise/throttling
and is *not* what is generated here.

**Throttling, on sustained headroom.** Because nothing borrows future
capacity, throttling cannot be cut on minutes of it. It is cut instead on how
close to its ceiling a capacity ran, and for how much of the day:

    below 90% of its CUs        headroom left, nothing is throttled
    90 - 95%                    interactive delay, 20 seconds
    95 - 98%                    interactive rejection, background still runs
    above 98%                   background rejection, everything refused

`MinutesOverLine` carries the severity that `FutureCapacityMinutes` used to:
minutes of the day spent above the 90% line, read straight off the
utilisation by the linear map in `minutes_over_line`. A capacity exactly on
the line spends none of the day over it; one pinned at its ceiling spends all
1,440 minutes there.

Everything generated here is tagged, as everywhere else in this project. The
SKU ladder and the CU arithmetic are real; which capacity consumed what is
not, and neither are the throttling thresholds -- see above, they are this
model's, not Microsoft's.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from . import SEED
from .generate import PROVENANCE, _tag

#: Fabric SKUs and their Capacity Units, mirroring `admission.F_SKUS`.
F_SKUS: dict[str, int] = {
    "F2": 2, "F4": 4, "F8": 8, "F16": 16, "F32": 32,
    "F64": 64, "F128": 128, "F256": 256, "F512": 512,
    "F1024": 1024, "F2048": 2048,
}

#: The SKU at which Power BI content becomes readable on a Free licence.
FREE_VIEWER_SKU = "F64"

#: Seconds in a day. One CU for a day is this many CU seconds.
SECONDS_PER_DAY = 86_400

#: A Fabric timepoint is 30 seconds; the next 24 hours hold 2,880 of them.
TIMEPOINT_SECONDS = 30

#: Minutes in a day. A capacity pinned at its ceiling spends all of them over
#: the throttling line; one sitting on the line spends none.
MINUTES_PER_DAY = 24 * 60

#: Where throttling begins, as a share of a capacity's CUs. A capacity is not
#: throttled here for being busy -- it is throttled for being busy with no
#: headroom left to absorb what arrives next.
THROTTLE_LINE_PCT = 90.0

#: Throttling thresholds, as utilisation of the capacity's own CUs. This model
#: does not let consumption exceed the SKU, so there is no borrowed future
#: capacity to cut the stages on and these are not Microsoft's published
#: numbers -- they are this model's, chosen so each stage names a distinct
#: amount of remaining headroom. The effects are real.
THROTTLE_STAGES = [
    (THROTTLE_LINE_PCT, "none", "Headroom left — nothing is throttled"),
    (95.0, "interactive_delay", "Interactive jobs delayed 20 seconds at submission"),
    (98.0, "interactive_rejection", "Interactive jobs rejected; background still runs"),
    (float("inf"), "background_rejection", "All requests rejected, interactive and background"),
]

#: How hard a capacity may be driven before the saturating map in
#: `capacity_cu_daily` takes over. Below it, demand and utilisation are the
#: same number; above it, demand is compressed into the headroom that is left.
SATURATION_KNEE = 0.80

#: Scaling across this boundary can be slower, per the throttling guidance, so
#: a recommendation that crosses it should say so.
SLOW_SCALE_BOUNDARY = ("F256", "F512")

#: The Fabric platform types a workload runs on -- `FabricWorkloadType` in the
#: table. Real Fabric workloads; which one a given workload runs is invented.
WORKLOADS = [
    "Power BI", "Data Engineering", "Data Warehouse", "Data Factory",
    "Real-Time Intelligence", "Data Science",
]

#: What a workload is called -- `WorkloadName` in the table. Named after the
#: team that owns it, which is how an admin refers to one.
#:
#: The two used to be `PrimaryWorkload` and `WorkspaceName`, and between them
#: they gave "workload" two meanings in the same row: Sales-BI is a workload,
#: and so is Power BI. The columns say which is which now.
TEAMS = [
    "Sales-BI", "Finance-Reporting", "Supply-Chain", "Marketing-Analytics",
    "Ops-Telemetry", "Customer-360", "Risk-Models", "Exec-Dashboards",
    "Product-Analytics", "Data-Platform", "Field-Ops", "Pricing",
]

#: How many workloads share a shared site's single capacity, inclusive. A
#: dedicated capacity always carries exactly one, at 100%.
SHARED_WORKLOAD_RANGE = (2, 5)


def _rng(salt: int = 0) -> np.random.Generator:
    return np.random.default_rng(SEED + salt)


def _stable(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:12], 16)


def throttle_stage(utilisation_pct: float) -> tuple[str, str]:
    """Which throttling stage a capacity is in, from how full it ran.

    The single place the thresholds are applied. Returns the stage and the
    sentence describing what a user experiences in it, so no screen has to
    restate the policy in its own words and get it subtly wrong.

    This took minutes of borrowed future capacity before. It cannot any more:
    consumption is bounded by the SKU, so nothing is ever borrowed and every
    capacity would sit in `none` forever. Headroom is what is left to cut on.
    """
    for limit, name, effect in THROTTLE_STAGES:
        if utilisation_pct <= limit:
            return name, effect
    return THROTTLE_STAGES[-1][1], THROTTLE_STAGES[-1][2]


def minutes_over_line(utilisation_pct: float) -> float:
    """How much of the day a capacity spent above the throttling line.

    Linear in utilisation between the line and the ceiling: exactly on the line
    is nought minutes, pinned at 100% is the whole 1,440. It is the severity
    measure the rejected-operation counts scale on, and the one figure a screen
    can quote to say *how badly* a capacity was throttled rather than merely
    that it was.

    Derived from `UtilisationPct` alone, so it cannot disagree with it -- which
    is the whole reason it replaced `FutureCapacityMinutes`, a column that
    measured a mechanic this model no longer has.
    """
    if utilisation_pct <= THROTTLE_LINE_PCT:
        return 0.0
    span = 100.0 - THROTTLE_LINE_PCT
    return MINUTES_PER_DAY * min((utilisation_pct - THROTTLE_LINE_PCT) / span, 1.0)


def cu_seconds_per_day(capacity_units: float) -> float:
    """A day of this capacity, in CU seconds. An F64 is 5,529,600."""
    return float(capacity_units) * SECONDS_PER_DAY


def next_sku(sku: str) -> str | None:
    ladder = list(F_SKUS)
    if sku not in ladder:
        return None
    i = ladder.index(sku)
    return ladder[i + 1] if i + 1 < len(ladder) else None


def previous_sku(sku: str) -> str | None:
    ladder = list(F_SKUS)
    if sku not in ladder:
        return None
    i = ladder.index(sku)
    return ladder[i - 1] if i > 0 else None


# --------------------------------------------------------------------------
# workloads
# --------------------------------------------------------------------------


def workspaces(capacities: pd.DataFrame) -> pd.DataFrame:
    """Grain: one workload, sitting on one capacity.

    Which shape the site is decides everything here:

    **Shared** -- the site's single capacity carries two to five workloads, and
    their shares sum to exactly 100%. The split is deliberately uneven. A
    capacity where four workloads each take a quarter has no load-balancing
    answer; one where a single workload takes two thirds does, and that is the
    case the rebalance recommendation exists for.

    **Dedicated** -- one workload per capacity, at 100% of it. There is nothing
    to rebalance: moving the workload does not relieve the capacity, it empties
    it. Recommendations that read a share have to check the site type first.

    Still written to `dim_workspace`: the table keeps its name, the two columns
    that were ambiguous do not. `WorkloadName` is the team-shaped name
    (Sales-BI, Pricing); `FabricWorkloadType` is the platform type it runs on
    (Power BI, Data Factory).
    """
    rng = _rng(21)
    rows = []
    for cap in capacities.itertuples():
        dedicated = getattr(cap, "SiteType", "") == "Dedicated"
        if dedicated:
            shares = [100.0]
        else:
            lo, hi = SHARED_WORKLOAD_RANGE
            n = lo + _stable(cap.CapacityId, "workloads") % (hi - lo + 1)
            # Dirichlet under 1 concentrates share on one or two workloads,
            # which is the shape that makes a move worth recommending.
            draw = rng.dirichlet(np.full(n, 0.7))
            shares = [round(float(x) * 100, 1) for x in draw]
            # Rounding to a decimal place leaves a few tenths unaccounted for.
            # They go on the largest share, so the column a reader can add up
            # adds up: a capacity whose workloads come to 99.8% invites the
            # question of what the missing fifth is doing.
            top = max(range(n), key=lambda i: shares[i])
            shares[top] = round(shares[top] + (100.0 - sum(shares)), 1)
        for i, share in enumerate(shares, start=1):
            pick = _stable(cap.CapacityId, str(i))
            rows.append({
                "WorkspaceId": f"{cap.CapacityId}-ws{i:02d}",
                "WorkloadName": TEAMS[pick % len(TEAMS)],
                "CapacityId": cap.CapacityId,
                "Region": cap.Region,
                "FabricWorkloadType": WORKLOADS[(pick // 7) % len(WORKLOADS)],
                "ShareOfCapacityPct": share,
            })
    return _tag(pd.DataFrame(rows),
                "workload assignment and consumption share invented; a shared "
                "site's capacity carries two to five workloads summing to 100%, "
                "a dedicated site's capacity carries exactly one at 100%; "
                "capacities and the SKU ladder are real")


# --------------------------------------------------------------------------
# consumption
# --------------------------------------------------------------------------


def capacity_cu_daily(capacities: pd.DataFrame,
                      region_usage: pd.DataFrame) -> pd.DataFrame:
    """Grain: one capacity, one day, in CU seconds.

    Anchored on the region series so the fleet still reconciles: the region's
    recorded utilisation for a day sets how hard its capacities were worked that
    day, and each capacity varies around it by a stable pressure of its own.

    Utilisation never reaches 100%. A capacity cannot consume more CU seconds
    than its SKU provides, so `CuSecondsConsumed < CuSecondsAvailable` on every
    row and `UtilisationPct` is the ratio of the two. Demand above the
    saturation knee is not discarded -- it is compressed into the headroom that
    remains, so a badly undersized capacity still ranks above a merely busy one
    and still reaches the worst stage. What it no longer does is claim to have
    used more than it holds.
    """
    rng = _rng(22)
    caps = capacities.copy()
    # Pressure is *demand*, not utilisation, and is deliberately kept mostly
    # under 1: an estate where a quarter of capacities ran out of headroom every
    # day would be permanently throttled. A handful are driven hard enough that
    # the saturating map pins them just under their ceiling, which is what makes
    # the later stages demonstrable at all.
    def _pressure(cid: str) -> float:
        h = _stable(cid, "cu") % 1000
        # A small number of capacities are badly undersized for what runs on
        # them -- a heavy workload parked on an F2. Without them the worst
        # throttling stage never occurs anywhere and the product would describe
        # a state it can never show, which is how a screen ends up asserting
        # something nobody has checked.
        if h >= 988:
            return 1.9 + (h - 988) / 12.0 * 0.6
        return 0.42 + h / 1000.0 * 0.78

    caps["Pressure"] = [_pressure(cid) for cid in caps["CapacityId"]]

    by_region = {r: g for r, g in caps.groupby("Region")}
    rows = []
    for region, days in region_usage.groupby("Region"):
        pool = by_region.get(region)
        if pool is None or pool.empty:
            continue
        cu = pool["CapacityUnits"].to_numpy(dtype=float)
        pressure = pool["Pressure"].to_numpy(dtype=float)
        ids = pool["CapacityId"].tolist()
        sites = pool["DatacentreId"].tolist()
        skus = pool["FabricSku"].tolist()

        for day in days.itertuples():
            # The region's utilisation that day, as a fraction, is the centre of
            # the distribution its capacities sit around.
            centre = float(day.UtilisationPct) / 100.0
            wobble = 1.0 + rng.normal(0, 0.09, len(cu))
            demand = np.clip(centre * pressure * wobble, 0.02, 2.4)
            # Demand becomes utilisation through a saturating map: identity up
            # to the knee, then an exponential approach to the ceiling that
            # never touches it. Continuous and with a continuous slope at the
            # knee, so there is no visible kink where the two halves meet, and
            # strictly increasing, so the hardest-driven capacity is still the
            # fullest one. A hard clip at 1.0 would have done neither -- it
            # would have stacked a quarter of the fleet on exactly 100.00% and
            # made every one of them look identically bad.
            head = 1.0 - SATURATION_KNEE
            util = np.where(demand <= SATURATION_KNEE, demand,
                            1.0 - head * np.exp(-(demand - SATURATION_KNEE) / head))

            for i, cid in enumerate(ids):
                available = cu_seconds_per_day(cu[i])
                # Each figure is computed from the one published beside it, not
                # from the float behind it: the percentage from the rounded
                # seconds, the minutes and the stage from the rounded
                # percentage. Otherwise a row can be published at 90.00% with a
                # stage worked out from an unrounded 89.997% -- true of the
                # number nobody can see and false of the one on screen.
                consumed = round(available * float(util[i]), 1)
                utilisation_pct = round(consumed / available * 100.0, 2)
                # Severity, read off the utilisation rather than off a borrowed
                # future this model does not have.
                over_minutes = minutes_over_line(utilisation_pct)
                stage, _ = throttle_stage(utilisation_pct)
                rows.append({
                    "Date": day.Date,
                    "CapacityId": cid,
                    "DatacentreId": sites[i],
                    "Region": region,
                    "FabricSku": skus[i],
                    "CapacityUnits": int(cu[i]),
                    "CuSecondsAvailable": round(available, 1),
                    "CuSecondsConsumed": consumed,
                    "UtilisationPct": utilisation_pct,
                    "MinutesOverLine": round(over_minutes, 2),
                    "ThrottleStage": stage,
                })
    return _tag(pd.DataFrame(rows),
                "one daily series per capacity in the Shared/Dedicated fleet -- CU "
                "consumption distributed from the region series, varied by a stable "
                "per-capacity pressure and bounded by the SKU so consumption never "
                "exceeds what the capacity holds; minutes over the line and throttle "
                "stage are read off that utilisation")


def throttling_events(cu_daily: pd.DataFrame) -> pd.DataFrame:
    """Grain: one throttling event -- a capacity, a day, a stage.

    Only days that actually reached a throttling stage. Rejected-operation
    counts scale with how much of the day the capacity spent over its line,
    because a capacity with no headroom for twenty hours refuses more than one
    that ran out of it for two.
    """
    rng = _rng(23)
    hit = cu_daily[cu_daily["ThrottleStage"] != "none"]
    rows = []
    for i, r in enumerate(hit.itertuples(), start=1):
        over = float(r.MinutesOverLine)
        # Capped rather than taken raw: a capacity over its line for twenty
        # hours is not rejecting ten times what one over for two hours rejects.
        # Both are refusing whatever arrives; what differs is how long they
        # keep doing it, and past a few hours that difference stops compounding.
        span = min(over, 240.0)
        if r.ThrottleStage == "interactive_delay":
            interactive, background = 0, 0          # delayed, not rejected
        elif r.ThrottleStage == "interactive_rejection":
            interactive, background = int(rng.poisson(span * 0.12)), 0
        else:
            interactive = int(rng.poisson(span * 0.30))
            background = int(rng.poisson(span * 0.10))
        _, effect = throttle_stage(float(r.UtilisationPct))
        rows.append({
            "ThrottleEventId": f"THR{i:05d}",
            "Date": r.Date,
            "CapacityId": r.CapacityId,
            "DatacentreId": r.DatacentreId,
            "Region": r.Region,
            "FabricSku": r.FabricSku,
            "Stage": r.ThrottleStage,
            "MinutesOverLine": round(over, 2),
            "InteractiveRejected": interactive,
            "BackgroundRejected": background,
            "Effect": effect,
        })
    cols = ["ThrottleEventId", "Date", "CapacityId", "DatacentreId", "Region",
            "FabricSku", "Stage", "MinutesOverLine",
            "InteractiveRejected", "BackgroundRejected", "Effect"]
    df = pd.DataFrame(rows, columns=cols)
    return _tag(df, "throttling days derived from the per-capacity CU series of "
                    "the Shared/Dedicated fleet; rejected operation counts invented "
                    "in proportion to the minutes spent over the throttling line")


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def generate_fabric(capacities: pd.DataFrame,
                    region_usage: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every Fabric-native table, from the capacities that already exist."""
    cu = capacity_cu_daily(capacities, region_usage)
    return {
        "dim_workspace": workspaces(capacities),
        "capacity_cu_daily": cu,
        "throttling_events": throttling_events(cu),
    }
