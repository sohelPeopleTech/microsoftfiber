"""Recommendations composed by the modules, not written in advance.

Review rejected the previous version in one line: "this is too general -- if you
gave this to ChatGPT it would say the same thing. Nothing calculatory is
happening here." That was fair. The action text came from a static dict, so
every site with the same cause got the same sentence.

    before   "Model a hardware change, or raise the ceiling if headroom exists."
    after    "3 of westeurope-dc01's 4 capacities need a larger SKU. The worst is
              westeurope-dc01-cap02, an F8 throttling on 25 of 30 days; moving it
              to F16 takes it from 8 to 16 CU and applies immediately. It has
              refused 424 operations in that window."

    The "after" example above used to describe swapping the site onto different
    hardware, for a cost delta and a 45-day lead time. It was replaced wholesale
    rather than reworded: a Fabric customer has no hardware to swap, nothing to
    take offline and nothing to wait for, so every number in that sentence was
    unactionable even though each was correctly computed.

Every sentence below is produced by the module that owns the cause, using that
facility's own numbers:

    Threshold reached           module1 -- safety line, what raising it releases,
                                and when the order has to be placed
    Insufficient capacity       the F-SKU scale that actually helps -- which
    Hardware failure            capacities in the building are short, what rung
                                each moves to, and what that leaves them running
                                at. Immediate; there is nothing to order
    everything else             no module owns it, so the recommendation says
                                who to talk to rather than inventing a fix

WHY IT REFUSES SOMETIMES
    Four of the seven causes have no automated remediation. Rather than dress
    those up, they return a human-review instruction naming the specific site
    and the specific number of incidents. A recommendation that cannot be
    computed should say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import module1
from ontology import attribution
# Imported as `planning.x`, not `src.planning.x`: the app puts ROOT/src on
# sys.path and nothing else. module2 is gone from here -- it modelled hardware
# conversions, and Fabric scales an F SKU instead.
from planning import F_SKUS, recommend, scale


@dataclass
class Remediation:
    reason: str
    count: int
    #: The computed sentence. This is the thing review asked for.
    action: str
    handled_by: str
    needs_human: bool
    #: Supporting arithmetic, for the detail view.
    options: list = field(default_factory=list)
    revenue_loss: float = 0.0

    def to_dict(self) -> dict:
        return {
            "reason": self.reason, "count": self.count, "action": self.action,
            "handledBy": self.handled_by, "needsHuman": self.needs_human,
            "options": self.options, "revenueLoss": round(self.revenue_loss, 2),
        }


def _site(onto, datacentre_id: str):
    dim = onto["dim_datacentre"]
    row = dim[dim["DatacentreId"].astype(str) == str(datacentre_id)]
    return None if row.empty else row.iloc[0]


# --------------------------------------------------------------------------
# module 1 -- the safety line and the order date
# --------------------------------------------------------------------------


def _threshold_remediation(onto, site, count: int, crossing_for=None) -> tuple[str, list]:
    """What module 1 says about this facility's headroom and order date."""
    cores = float(site["DeployedUnits"] or 0)
    used = float(site["UsedUnits"] or 0)
    line = float(site["ThresholdPct"] or 0)
    lead = int(site["LeadTimeDays"] or 0)
    headroom = cores * line / 100.0 - used

    options = []
    for candidate in (line + 5, line + 10):
        if candidate > 100:
            continue
        options.append({
            "kind": "threshold",
            "thresholdPct": round(candidate, 1),
            "releasesCores": round(cores * (candidate - line) / 100.0, 1),
            "headroomAfter": round(cores * candidate / 100.0 - used, 1),
        })

    # What module 1 already knows about the region's order timing.
    order_by = ""
    try:
        flag = module1.project_region(onto, str(site["Region"]),
                                      crossing_for=crossing_for)
        if getattr(flag, "act_by_date", None):
            order_by = (f" The region's order-by date is "
                        f"{str(flag.act_by_date)[:10]}"
                        f"{' and has passed' if (flag.days_until_action or 0) < 0 else ''}.")
    except Exception:
        pass

    usable = [o for o in options if o["headroomAfter"] > 0]
    if usable:
        best = usable[0]
        action = (
            f"{count} request(s) hit the {line:.0f}% safety line at "
            f"{site['DatacentreId']}, which holds {cores:.0f} cores with "
            f"{used:.0f} committed — "
            f"{f'{headroom:.0f} cores of headroom' if headroom >= 0 else f'already {abs(headroom):.0f} cores past the line'}. "
            f"Raising the line to {best['thresholdPct']:.0f}% releases "
            f"{best['releasesCores']:.0f} cores and leaves "
            f"{best['headroomAfter']:.0f} spare.{order_by}"
        )
    else:
        action = (
            f"{count} request(s) hit the {line:.0f}% safety line at "
            f"{site['DatacentreId']}. No safety line up to 100% releases enough — "
            f"{used:.0f} of {cores:.0f} cores are already committed, so this is "
            f"procurement, not policy. {site['SKUClass']} takes {lead} days to "
            f"arrive.{order_by}"
        )
    return action, options


# --------------------------------------------------------------------------
# module 2 -- the scale that actually helps
# --------------------------------------------------------------------------


def _scale_remediation(onto, site, reason: str, count: int) -> tuple[str, list]:
    """What to do about a capacity-shortage cause at this site, in Fabric terms.

    This replaced a hardware conversion. The old text read "switching its 184
    cores from Intel-standard to AMD-highmem raises work capacity by 42 units
    for +18% cost, arriving in 45 days, and the region can spare the site while
    it is taken offline" -- five claims, none of which a Fabric customer can act
    on or verify. There is no hardware to switch, no site to take offline and
    no 45 days to wait.

    What a Fabric admin does instead is move the capacities that are actually
    throttling up the SKU ladder, one at a time, and it takes effect
    immediately. So the options are the capacities in this building that need
    it, each with the rung to move to and what that leaves them running at.
    """
    dc = str(site["DatacentreId"])
    caps = onto["dim_capacity"]
    here = caps[caps["DatacentreId"].astype(str) == dc]
    if here.empty:
        return (f"{count} request(s) failed at {dc} for {reason.lower()}. "
                f"No Fabric capacities are recorded in this data centre."), []

    health = recommend._health(onto).set_index("CapacityId")

    options = []
    for _, row in here.iterrows():
        cid = str(row["CapacityId"])
        if cid not in health.index:
            continue
        view = scale.capacity_scale_view(row, health.loc[cid])
        target = view["recommended"]
        cur = view["current"]
        if not target or F_SKUS[target] <= cur["capacityUnits"]:
            continue
        after = next(o for o in view["options"] if o["sku"] == target)
        options.append({
            "kind": "scale",
            "capacityId": cid,
            "fromSku": cur["sku"],
            "toSku": target,
            "cuBefore": cur["capacityUnits"],
            "cuAfter": after["capacityUnits"],
            "meanPct": cur["meanPct"],
            "peakPct": cur["peakPct"],
            "peakAfterPct": after["peakAfterPct"],
            "throttledDays": cur["throttledDays"],
            "windowDays": cur["windowDays"],
            "worstStage": cur["worstStage"],
            "interactiveRejected": cur["interactiveRejected"],
            "cuDeltaPct": after["cuDeltaPct"],
            "gainsFreeViewers": after["gainsFreeViewers"],
            "crossesSlowBoundary": after["crossesSlowBoundary"],
            "immediate": True,
        })

    # Worst first: what is refusing the most work is what to fix first.
    options.sort(key=lambda o: (-o["throttledDays"], -o["interactiveRejected"]))

    if options:
        first = options[0]
        refused = ""
        if first["interactiveRejected"]:
            refused = (f" It has refused {first['interactiveRejected']:,} "
                       f"operations in that window.")
        action = (
            f"{count} request(s) failed at {dc} for {reason.lower()}. "
            f"{len(options)} of its {len(here)} capacities need a larger SKU. "
            f"The worst is {first['capacityId']}, an {first['fromSku']} throttling "
            f"on {first['throttledDays']} of {first['windowDays']} days; moving it "
            f"to {first['toSku']} takes it from {first['cuBefore']} to "
            f"{first['cuAfter']} CU and applies immediately.{refused}"
        )
    else:
        action = (
            f"{count} request(s) failed at {dc} for {reason.lower()}, but none of "
            f"its {len(here)} capacities is short of Capacity Units — none is "
            f"throttling and none is out of headroom. The constraint is not the "
            f"size of the capacities here, so scaling them would not have "
            f"admitted these requests."
        )
    return action, options


# --------------------------------------------------------------------------


def for_site(onto, datacentre_id: str, denied, revenue_by_reason=None,
             crossing_for=None) -> list[Remediation]:
    """A computed recommendation per distinct cause at one facility."""
    site = _site(onto, datacentre_id)
    if site is None or denied is None or not len(denied):
        return []

    revenue_by_reason = revenue_by_reason or {}
    out = []
    for reason, count in denied["DenialReason"].value_counts().items():
        reason = str(reason)
        meta = attribution.REASONS.get(reason, {})
        module = meta.get("module")
        options: list = []

        if module == "module1":
            action, options = _threshold_remediation(onto, site, int(count),
                                                     crossing_for)
        elif module == "module2":
            action, options = _scale_remediation(onto, site, reason, int(count))
        else:
            # No module owns this. Name the site and the volume, then hand it
            # to a person rather than inventing a fix.
            action = (
                f"{count} request(s) at {site['DatacentreId']} — {meta.get('detail', '')} "
                f"{meta.get('action', 'Needs human review.')}"
            ).strip()

        out.append(Remediation(
            reason=reason, count=int(count), action=action,
            handled_by=module or "manual review",
            needs_human=module is None,
            options=options,
            revenue_loss=float(revenue_by_reason.get(reason, 0.0)),
        ))

    out.sort(key=lambda r: (-r.revenue_loss, -r.count))
    return out


__all__ = ["for_site", "Remediation"]
