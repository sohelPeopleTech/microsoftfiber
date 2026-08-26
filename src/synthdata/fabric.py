"""Fabric capacities as Fabric actually works them.

This replaces a model that described Azure infrastructure: hardware classes,
provisioning lead times, node failures, and purchase orders raised weeks ahead.
None of that exists in Fabric. A customer never sees a server, an F-SKU is
scaled in Azure and takes effect immediately, and a capacity does not fail --
it *throttles*.

What is modelled here is what a Fabric capacity admin actually sees:

    dim_workspace          workspaces assigned to a capacity
    fact_capacity_cu       CU seconds consumed per capacity per day
    fact_throttling        throttling events, by stage

The mechanics are Microsoft's, not invented:

**Capacity Units.** An F64 provides 64 CUs. A day of an F64 is therefore
64 x 86,400 = 5,529,600 CU seconds. Consumption is measured against that.

**Bursting and smoothing.** Operations may use more compute than the SKU
provides, and Fabric spreads the cost over future 30-second timepoints --
interactive over 5 to 64 minutes, background over 24 hours. So utilisation
above 100% is normal and is not by itself a fault.

**Overage protection and throttling.** Only when smoothed consumption eats
into future capacity does throttling begin, in the stages Microsoft publishes:

    <= 10 min of future capacity   overage protection, nothing happens
    10 - 60 min                    interactive delay, 20 seconds
    60 min - 24 h                  interactive rejection
    > 24 h                         background rejection, everything refused

https://learn.microsoft.com/en-us/fabric/enterprise/throttling

Everything generated here is tagged, as everywhere else in this project. The
SKU ladder, the CU arithmetic and the throttling thresholds are real; which
capacity consumed what is not.
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

#: Throttling thresholds, in minutes of *future* capacity consumed. Microsoft's
#: published policy, not a choice made here.
THROTTLE_STAGES = [
    (10, "none", "Overage protection — nothing is throttled"),
    (60, "interactive_delay", "Interactive jobs delayed 20 seconds at submission"),
    (24 * 60, "interactive_rejection", "Interactive jobs rejected; background still runs"),
    (float("inf"), "background_rejection", "All requests rejected, interactive and background"),
]

#: Scaling across this boundary can be slower, per the throttling guidance, so
#: a recommendation that crosses it should say so.
SLOW_SCALE_BOUNDARY = ("F256", "F512")

#: Workload mix a workspace can be dedicated to. Real Fabric workloads; which
#: workspace runs which is invented.
WORKLOADS = [
    "Power BI", "Data Engineering", "Data Warehouse", "Data Factory",
    "Real-Time Intelligence", "Data Science",
]

#: Workspaces are named after the sort of team that owns one.
TEAMS = [
    "Sales-BI", "Finance-Reporting", "Supply-Chain", "Marketing-Analytics",
    "Ops-Telemetry", "Customer-360", "Risk-Models", "Exec-Dashboards",
    "Product-Analytics", "Data-Platform", "Field-Ops", "Pricing",
]


def _rng(salt: int = 0) -> np.random.Generator:
    return np.random.default_rng(SEED + salt)


def _stable(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:12], 16)


def throttle_stage(future_minutes: float) -> tuple[str, str]:
    """Which throttling stage a capacity is in, from future capacity consumed.

    The single place the published thresholds are applied. Returns the stage and
    the sentence describing what a user experiences in it, so no screen has to
    restate the policy in its own words and get it subtly wrong.
    """
    for limit, name, effect in THROTTLE_STAGES:
        if future_minutes <= limit:
            return name, effect
    return THROTTLE_STAGES[-1][1], THROTTLE_STAGES[-1][2]


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
# workspaces
# --------------------------------------------------------------------------


def workspaces(capacities: pd.DataFrame) -> pd.DataFrame:
    """Grain: one workspace, assigned to one capacity.

    Fabric bills and sizes by capacity, and workspaces are what you move between
    them. Without this table "load balance across capacities" is advice with no
    object -- there is nothing to name as the thing to move.

    Share of consumption is deliberately uneven. A capacity where four
    workspaces each take a quarter has no load-balancing answer; one where a
    single workspace takes two thirds does.
    """
    rng = _rng(21)
    rows = []
    for cap in capacities.itertuples():
        # Bigger capacities host more workspaces.
        n = int(np.clip(1 + np.log2(max(cap.CapacityUnits, 2)) / 1.6, 1, 8))
        n = max(1, int(rng.integers(max(1, n - 1), n + 2)))
        # Dirichlet under 1 concentrates share on one or two workspaces, which
        # is the shape that makes a move worth recommending.
        share = rng.dirichlet(np.full(n, 0.7))
        for i in range(n):
            pick = _stable(cap.CapacityId, str(i))
            rows.append({
                "WorkspaceId": f"{cap.CapacityId}-ws{i + 1:02d}",
                "WorkspaceName": TEAMS[pick % len(TEAMS)],
                "CapacityId": cap.CapacityId,
                "Region": cap.Region,
                "PrimaryWorkload": WORKLOADS[(pick // 7) % len(WORKLOADS)],
                "ShareOfCapacityPct": round(float(share[i]) * 100, 1),
            })
    return _tag(pd.DataFrame(rows),
                "workspace assignment and consumption share invented; capacities "
                "and the SKU ladder are real")


# --------------------------------------------------------------------------
# consumption
# --------------------------------------------------------------------------


def capacity_cu_daily(capacities: pd.DataFrame,
                      region_usage: pd.DataFrame) -> pd.DataFrame:
    """Grain: one capacity, one day, in CU seconds.

    Anchored on the region series so the fleet still reconciles: the region's
    recorded utilisation for a day sets how hard its capacities were worked that
    day, and each capacity varies around it by a stable pressure of its own.

    Utilisation may exceed 100%. That is bursting, and in Fabric it is normal --
    the question is not whether a capacity went over but whether smoothing left
    it owing future capacity, which is what `FutureCapacityMinutes` carries.
    """
    rng = _rng(22)
    caps = capacities.copy()
    # Pressure spread deliberately kept mostly under 1. Sustaining more than
    # 100% of a SKU across a whole day is not a burst -- it is a capacity that
    # has borrowed hours of future compute, and an estate where a quarter of
    # capacities do that every day would be permanently throttled. A handful run
    # hot enough to reach the later stages, which is what makes those stages
    # demonstrable at all.
    def _pressure(cid: str) -> float:
        h = _stable(cid, "cu") % 1000
        # A small number of capacities are badly undersized for what runs on
        # them -- a heavy workspace parked on an F2. Without them the worst
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
            util = np.clip(centre * pressure * wobble, 0.02, 2.4)

            for i, cid in enumerate(ids):
                available = cu_seconds_per_day(cu[i])
                consumed = available * float(util[i])
                # Overage is what smoothing has to push into future timepoints.
                overage_seconds = max(consumed - available, 0.0)
                # Expressed as minutes of future capacity: the overage divided
                # by what this capacity earns per second.
                future_minutes = overage_seconds / max(cu[i], 1e-9) / 60.0
                stage, _ = throttle_stage(future_minutes)
                rows.append({
                    "Date": day.Date,
                    "CapacityId": cid,
                    "DatacentreId": sites[i],
                    "Region": region,
                    "FabricSku": skus[i],
                    "CapacityUnits": int(cu[i]),
                    "CuSecondsAvailable": round(available, 1),
                    "CuSecondsConsumed": round(consumed, 1),
                    "UtilisationPct": round(float(util[i]) * 100, 2),
                    "FutureCapacityMinutes": round(future_minutes, 2),
                    "ThrottleStage": stage,
                })
    return _tag(pd.DataFrame(rows),
                "CU consumption distributed from the region series; overage and "
                "throttle stage computed with Microsoft's published thresholds")


def throttling_events(cu_daily: pd.DataFrame) -> pd.DataFrame:
    """Grain: one throttling event -- a capacity, a day, a stage.

    Only days that actually reached a throttling stage. Rejected-operation
    counts scale with how far past the threshold the capacity went, because a
    capacity two hours into future consumption refuses more than one that is
    seventy minutes in.
    """
    rng = _rng(23)
    hit = cu_daily[cu_daily["ThrottleStage"] != "none"]
    rows = []
    for i, r in enumerate(hit.itertuples(), start=1):
        over = float(r.FutureCapacityMinutes)
        # Scaled on how long the stage lasted rather than on the raw overage.
        # A capacity fifteen hours into future consumption is not rejecting
        # forty times what one at ninety minutes rejects; both are refusing
        # whatever arrives, and what differs is how long it keeps doing it.
        span = min(over, 240.0)
        if r.ThrottleStage == "interactive_delay":
            interactive, background = 0, 0          # delayed, not rejected
        elif r.ThrottleStage == "interactive_rejection":
            interactive, background = int(rng.poisson(span * 0.12)), 0
        else:
            interactive = int(rng.poisson(span * 0.30))
            background = int(rng.poisson(span * 0.10))
        _, effect = throttle_stage(over)
        rows.append({
            "ThrottleEventId": f"THR{i:05d}",
            "Date": r.Date,
            "CapacityId": r.CapacityId,
            "DatacentreId": r.DatacentreId,
            "Region": r.Region,
            "FabricSku": r.FabricSku,
            "Stage": r.ThrottleStage,
            "FutureCapacityMinutes": round(over, 2),
            "InteractiveRejected": interactive,
            "BackgroundRejected": background,
            "Effect": effect,
        })
    cols = ["ThrottleEventId", "Date", "CapacityId", "DatacentreId", "Region",
            "FabricSku", "Stage", "FutureCapacityMinutes",
            "InteractiveRejected", "BackgroundRejected", "Effect"]
    df = pd.DataFrame(rows, columns=cols)
    return _tag(df, "throttling days derived from the CU series; rejected "
                    "operation counts invented in proportion to the overage")


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
