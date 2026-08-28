"""The four recommendations, built from the Fabric capacity tables.

Each answers a question the utilisation figure cannot on its own:

    scale_up      it is throttling -- users are being delayed or refused
    load_balance  one workspace is the whole problem; move that, not the SKU
    scale_down    it is idle, and an F SKU bills per second whether used or not
    licensing     it is fine, but only paid licences can read Power BI on it

Every one carries its evidence. A recommendation that says "scale up" without
naming the stage, the days, and the operations actually refused is the generic
sentence review already rejected once.
"""

from __future__ import annotations

import pandas as pd

from . import (
    DOMINANT_WORKSPACE_PCT,
    FREE_VIEWER_CU,
    FREE_VIEWER_SKU,
    F_SKUS,
    IDLE_DAYS,
    IDLE_PCT,
    STAGE_LABEL,
    STAGE_RANK,
    SUSTAINED_HIGH_PCT,
    THROTTLED_DAYS_FOR_SCALE,
    Recommendation,
    capacity_health,
    crosses_slow_boundary,
    next_sku,
    previous_sku,
)


def _health(entities, window_days: int = 30) -> pd.DataFrame:
    return capacity_health(entities["dim_capacity"], entities["fact_capacity_cu_daily"],
                           entities["fact_throttling_event"], window_days)


# --------------------------------------------------------------------------


def scale_up(entities, window_days: int = 30) -> list[Recommendation]:
    """Capacities throttling, or with no headroom left for the next surge.

    Two cases, and the difference matters to whoever reads it. A capacity that
    has reached interactive rejection is refusing users' queries now. One
    running at ninety per cent without throttling is not hurting anyone yet, but
    has nothing left to absorb a spike, and Fabric's overage protection is only
    ten minutes deep.

    Scaling is the remedy Microsoft names first, and unlike buying hardware it
    takes effect immediately -- so the recommendation carries no order date,
    because there is no order.
    """
    health = _health(entities, window_days)
    out: list[Recommendation] = []
    for c in health.itertuples():
        throttling = c.ThrottledDays >= THROTTLED_DAYS_FOR_SCALE
        airless = c.MeanUtilisationPct >= SUSTAINED_HIGH_PCT
        if not (throttling or airless):
            continue
        step = next_sku(c.FabricSku)
        if not step:
            continue

        slow = crosses_slow_boundary(c.FabricSku, step)
        stage = STAGE_LABEL.get(c.WorstStage, c.WorstStage)
        rejected = int(c.InteractiveRejected) + int(c.BackgroundRejected)

        if throttling:
            headline = (f"Scale {c.CapacityId} to {step} — throttling on "
                        f"{c.ThrottledDays} of the last {c.WindowDays} days")
            detail = (
                f"This capacity reached {stage}. At its worst it was "
                f"{c.PeakFutureMinutes:.0f} minutes into future capacity, and "
                f"{'it refused ' + format(rejected, ',') + ' operations' if rejected else 'no operations were refused yet'}. "
                f"Fabric only absorbs ten minutes of overage before it starts "
                f"delaying interactive jobs, so this is not a spike it will "
                f"smooth away. Moving {c.FabricSku} to {step} doubles the "
                f"capacity units from {c.CapacityUnits} to {F_SKUS[step]} and "
                f"burns down the carried overage faster, because every timepoint "
                f"then has more idle compute. Scaling an F SKU takes effect "
                f"immediately."
            )
        else:
            headline = (f"Scale {c.CapacityId} to {step} — running at "
                        f"{c.MeanUtilisationPct:.0f}% with no headroom")
            detail = (
                f"Not throttling yet, and that is the point: it averages "
                f"{c.MeanUtilisationPct:.0f}% of its {c.CapacityUnits} CUs over "
                f"{c.WindowDays} days and peaked at {c.PeakUtilisationPct:.0f}%. "
                f"Fabric's overage protection is ten minutes deep, so a capacity "
                f"this close to its ceiling has almost nothing left to absorb a "
                f"surge before users start seeing 20-second delays. {step} gives "
                f"it {F_SKUS[step]} CUs. Scaling is immediate."
            )
        if slow:
            detail += (f" Note that {c.FabricSku} and {step} sit on opposite "
                       f"sides of the F256/F512 boundary, where scaling can be "
                       f"slower than usual.")

        out.append(Recommendation(
            kind="scale_up", scope="capacity", target=c.CapacityId,
            headline=headline, detail=detail,
            urgency=round(STAGE_RANK.get(c.WorstStage, 0) * 30
                          + c.ThrottledDays * 2 + c.MeanUtilisationPct / 10, 1),
            evidence={
                "region": c.Region, "datacentre": c.DatacentreId,
                "fabricSku": c.FabricSku, "capacityUnits": int(c.CapacityUnits),
                "scaleTo": step, "scaleToUnits": F_SKUS[step],
                "meanUtilisationPct": float(c.MeanUtilisationPct),
                "peakUtilisationPct": float(c.PeakUtilisationPct),
                "throttledDays": int(c.ThrottledDays),
                "windowDays": int(c.WindowDays),
                "worstStage": c.WorstStage,
                "worstStageLabel": stage,
                "peakFutureMinutes": float(c.PeakFutureMinutes),
                "interactiveRejected": int(c.InteractiveRejected),
                "backgroundRejected": int(c.BackgroundRejected),
                "isThrottling": bool(throttling),
                "crossesSlowBoundary": bool(slow),
                "immediate": True,
            },
        ))
    out.sort(key=lambda r: -r.urgency)
    return out


# --------------------------------------------------------------------------


def load_balance(entities, window_days: int = 30) -> list[Recommendation]:
    """Throttling capacities where one workspace is most of the consumption.

    Microsoft names load balancing across capacities alongside scaling, and it
    is the cheaper answer when the cause is concentrated: moving one workspace
    costs nothing per second, where the next SKU up bills continuously.

    Only offered when there is somewhere to move to -- a quieter capacity in the
    same region with room for the workspace. Advice to move something nowhere is
    not advice.
    """
    health = _health(entities, window_days)
    ws = entities["dim_workspace"]
    out: list[Recommendation] = []

    by_cap = {c.CapacityId: c for c in health.itertuples()}
    for c in health.itertuples():
        if c.ThrottledDays < THROTTLED_DAYS_FOR_SCALE:
            continue
        here = ws[ws["CapacityId"] == c.CapacityId]
        # Balancing needs something to balance. A capacity hosting one
        # workspace at 100% cannot be rebalanced -- moving it empties the
        # capacity, which is a consolidation decision and a different
        # conversation from "spread this load".
        if len(here) < 2:
            continue
        top = here.sort_values("ShareOfCapacityPct", ascending=False).iloc[0]
        if float(top["ShareOfCapacityPct"]) < DOMINANT_WORKSPACE_PCT:
            continue

        # Somewhere quieter in the same region, with room for what moves.
        wants = float(top["ShareOfCapacityPct"]) / 100.0 * float(c.CapacityUnits)
        options = [o for o in health.itertuples()
                   if o.Region == c.Region and o.CapacityId != c.CapacityId
                   and o.ThrottledDays == 0
                   and o.MeanUtilisationPct + (wants / max(o.CapacityUnits, 1)) * 100 < 80]
        if not options:
            continue
        dest = min(options, key=lambda o: o.MeanUtilisationPct)

        out.append(Recommendation(
            kind="load_balance", scope="capacity", target=c.CapacityId,
            headline=(f"Move {top['WorkspaceName']} off {c.CapacityId} — one "
                      f"workspace is {top['ShareOfCapacityPct']:.0f}% of it"),
            detail=(
                f"{c.CapacityId} threw {STAGE_LABEL.get(c.WorstStage, c.WorstStage)} "
                f"on {c.ThrottledDays} of {c.WindowDays} days, and one workspace "
                f"— {top['WorkspaceName']}, running {top['PrimaryWorkload']} — "
                f"accounts for {top['ShareOfCapacityPct']:.0f}% of what it "
                f"consumes, with {len(here) - 1} other workspace(s) sharing the "
                f"rest. Moving that one to {dest.CapacityId} "
                f"({dest.FabricSku}, averaging {dest.MeanUtilisationPct:.0f}% and "
                f"not throttling) rebalances without changing either SKU, which "
                f"costs nothing per second where scaling up bills continuously. "
                f"Both capacities are in {c.Region}, so nothing crosses a region "
                f"boundary."
            ),
            urgency=round(STAGE_RANK.get(c.WorstStage, 0) * 25
                          + float(top["ShareOfCapacityPct"]) / 4, 1),
            evidence={
                "region": c.Region, "datacentre": c.DatacentreId,
                "fabricSku": c.FabricSku, "capacityUnits": int(c.CapacityUnits),
                "workspace": top["WorkspaceName"],
                "workspaceId": top["WorkspaceId"],
                "workspaceWorkload": top["PrimaryWorkload"],
                "workspaceSharePct": float(top["ShareOfCapacityPct"]),
                "workspacesOnCapacity": int(len(here)),
                "moveTo": dest.CapacityId,
                "moveToSku": dest.FabricSku,
                "moveToUtilisationPct": float(dest.MeanUtilisationPct),
                "throttledDays": int(c.ThrottledDays),
                "windowDays": int(c.WindowDays),
                "worstStage": c.WorstStage,
                "worstStageLabel": STAGE_LABEL.get(c.WorstStage, c.WorstStage),
            },
        ))
    out.sort(key=lambda r: -r.urgency)
    return out


# --------------------------------------------------------------------------


def scale_down(entities, window_days: int = IDLE_DAYS) -> list[Recommendation]:
    """Capacities paying for compute nobody uses.

    The recommendation the old model could not make at all, because it only ever
    looked for things running out. F SKUs bill per second whether or not anything
    runs on them, so a capacity averaging a fifth of its CUs for a month is a
    standing cost with nothing to show for it.

    Never recommended for anything that throttled in the window, however idle it
    looks on average: a capacity that is quiet six days a week and overloaded on
    the seventh is sized for the seventh.
    """
    health = _health(entities, window_days)
    out: list[Recommendation] = []
    for c in health.itertuples():
        if c.ThrottledDays > 0 or c.MeanUtilisationPct >= IDLE_PCT:
            continue
        if c.DaysObserved < IDLE_DAYS:
            continue
        step = previous_sku(c.FabricSku)
        if not step:
            continue
        # After halving, would it still have room? If not, leave it alone.
        after = c.PeakUtilisationPct * c.CapacityUnits / F_SKUS[step]
        if after >= SUSTAINED_HIGH_PCT:
            continue

        keeps_free_viewers = F_SKUS[step] >= FREE_VIEWER_CU
        out.append(Recommendation(
            kind="scale_down", scope="capacity", target=c.CapacityId,
            headline=(f"Scale {c.CapacityId} down to {step} — averaging "
                      f"{c.MeanUtilisationPct:.0f}% for {c.WindowDays} days"),
            detail=(
                f"This has used {c.MeanUtilisationPct:.0f}% of its "
                f"{c.CapacityUnits} CUs on average over {c.WindowDays} days and "
                f"peaked at {c.PeakUtilisationPct:.0f}%, never throttling. F SKUs "
                f"bill per second whether or not anything runs, so the unused "
                f"half is a standing cost. At {step} the same peak would be "
                f"about {after:.0f}%, still inside its own ceiling."
                + ("" if keeps_free_viewers else
                   f" Note that {step} is below {FREE_VIEWER_SKU}: every user "
                   f"viewing Power BI content here would need a Pro or PPU "
                   f"licence, which may cost more than the capacity saves.")
            ),
            urgency=round((IDLE_PCT - c.MeanUtilisationPct) / 2, 1),
            evidence={
                "region": c.Region, "datacentre": c.DatacentreId,
                "fabricSku": c.FabricSku, "capacityUnits": int(c.CapacityUnits),
                "scaleTo": step, "scaleToUnits": F_SKUS[step],
                "meanUtilisationPct": float(c.MeanUtilisationPct),
                "peakUtilisationPct": float(c.PeakUtilisationPct),
                "peakAfterScaleDownPct": round(float(after), 1),
                "windowDays": int(c.WindowDays),
                "throttledDays": 0,
                "losesFreeViewers": not keeps_free_viewers,
            },
        ))
    out.sort(key=lambda r: -r.urgency)
    return out


# --------------------------------------------------------------------------


def licensing(entities, window_days: int = 30) -> list[Recommendation]:
    """Capacities one rung below the licence that pays for itself.

    F64 is where Power BI content becomes readable on a Free licence; below it
    every viewer needs Pro or PPU. A tenant with a hundred readers on an F32 is
    buying a hundred Pro licences to avoid one SKU step, which is a commercial
    decision no amount of utilisation monitoring will surface.
    """
    caps = entities["dim_capacity"]
    ws = entities["dim_workspace"]
    out: list[Recommendation] = []
    for cap in caps.itertuples():
        step = next_sku(cap.FabricSku)
        if cap.CapacityUnits >= FREE_VIEWER_CU or step != FREE_VIEWER_SKU:
            continue
        here = ws[ws["CapacityId"] == cap.CapacityId]
        pbi = here[here["PrimaryWorkload"] == "Power BI"]
        out.append(Recommendation(
            kind="licensing", scope="capacity", target=cap.CapacityId,
            headline=(f"{cap.CapacityId} is one step below {FREE_VIEWER_SKU} — "
                      f"every Power BI viewer here needs a paid licence"),
            detail=(
                f"This is an {cap.FabricSku} at {cap.CapacityUnits} CUs. On any "
                f"capacity below {FREE_VIEWER_SKU}, each user viewing Power BI "
                f"content needs Pro or Premium Per User; at {FREE_VIEWER_SKU} or "
                f"larger a Free licence and a viewer role are enough. Stepping up "
                f"doubles the capacity units to {FREE_VIEWER_CU} and removes the "
                f"per-viewer licence entirely. "
                + (f"{len(pbi)} of the {len(here)} workspaces here run Power BI "
                   f"as their primary workload."
                   if len(pbi) else
                   f"None of the {len(here)} workspaces here runs Power BI as its "
                   f"primary workload, so size the viewer population before acting.")
            ),
            urgency=round(20.0 + len(pbi) * 6, 1),
            evidence={
                "region": cap.Region, "datacentre": cap.DatacentreId,
                "fabricSku": cap.FabricSku,
                "capacityUnits": int(cap.CapacityUnits),
                "stepTo": FREE_VIEWER_SKU, "stepToUnits": FREE_VIEWER_CU,
                "workspaces": int(len(here)),
                "powerBiWorkspaces": int(len(pbi)),
                "rule": ("Fabric licensing: F64 or larger lets Free-licensed users "
                         "view Power BI content with a viewer role; below F64 each "
                         "viewer needs Pro, PPU or a trial."),
                "source": "https://learn.microsoft.com/en-us/fabric/enterprise/licenses",
            },
        ))
    out.sort(key=lambda r: -r.urgency)
    return out


# --------------------------------------------------------------------------


def all_recommendations(entities, window_days: int = 30) -> list[dict]:
    """Every recommendation, most urgent first, kinds interleaved.

    Not grouped by kind: a planner wants the most pressing thing, and which of
    the four engines produced it is a detail of how this was computed.
    """
    recs = (scale_up(entities, window_days) + load_balance(entities, window_days)
            + scale_down(entities) + licensing(entities, window_days))
    recs.sort(key=lambda r: -r.urgency)
    return [r.to_dict() for r in recs]
