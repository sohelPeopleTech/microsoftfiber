"""Recommendations composed by the modules, not written in advance.

Review rejected the previous version in one line: "this is too general -- if you
gave this to ChatGPT it would say the same thing. Nothing calculatory is
happening here." That was fair. The action text came from a static dict, so
every site with the same cause got the same sentence.

    before   "Model a hardware change, or raise the ceiling if headroom exists."
    after    "Switch westeurope-dc01 from GPU-class to AMD-highmem: 92 units
              becomes 92, work capacity 238 -> 119, cost -55%, 10-day lead
              time. Region has 120 units of spare capacity, so the site can be
              taken offline now."

Every sentence below is produced by the module that owns the cause, using that
facility's own numbers:

    Threshold reached           module1 -- safety line, what raising it releases,
                                and when the order has to be placed
    Insufficient capacity       module2 -- the conversion that actually helps,
    Hardware failure                       costed and feasibility-checked
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
import module2
from ontology import attribution


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
        if getattr(flag, "order_by_date", None):
            order_by = (f" The region's order-by date is "
                        f"{str(flag.order_by_date)[:10]}"
                        f"{' and has passed' if (flag.days_until_order or 0) < 0 else ''}.")
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
# module 2 -- the conversion that actually helps
# --------------------------------------------------------------------------


def _conversion_remediation(onto, site, reason: str, count: int) -> tuple[str, list]:
    """What module 2 says about swapping this facility's hardware."""
    current = str(site["SKUClass"])
    region = str(site["Region"])
    units = float(site["DeployedUnits"] or 0)

    options = []
    for target in sorted(onto["dim_sku"]["SKUClass"]):
        if target == current:
            continue
        try:
            src = module2.calculator.sku_from_dim(onto["dim_sku"], current)
            tgt = module2.calculator.sku_from_dim(onto["dim_sku"], target)
            conv = module2.convert_same_footprint(src, tgt, units).to_dict()
            plan = module2.plan_conversion(onto, region, target, convert_datacentres=1)
        except (KeyError, ValueError):
            continue
        options.append({
            "kind": "conversion",
            "toSku": target,
            "coresAfter": round(float(conv["to_units"]), 1),
            "capacityAfter": round(float(conv["capacity_after"]), 1),
            "capacityDelta": round(float(conv["capacity_delta"]), 1),
            "costDeltaPct": round(float(conv["cost_delta_pct"]), 1),
            "leadTimeDays": int(conv["lead_time_days"]),
            "feasible": bool(plan.can_convert_a_whole_datacentre),
            "spareUnits": round(float(plan.max_offline_units), 1),
        })

    # The useful swap is one that adds capability and can actually be scheduled.
    gains = [o for o in options if o["capacityDelta"] > 0 and o["feasible"]]
    gains.sort(key=lambda o: -o["capacityDelta"])
    options.sort(key=lambda o: -o["capacityDelta"])

    if gains:
        best = gains[0]
        action = (
            f"{count} request(s) failed at {site['DatacentreId']} for "
            f"{reason.lower()}. Switching its {units:.0f} cores from {current} to "
            f"{best['toSku']} raises work capacity by {best['capacityDelta']:.0f} units "
            f"for {best['costDeltaPct']:+.0f}% cost, arriving in {best['leadTimeDays']} days. "
            f"The region has {best['spareUnits']:.0f} spare units, so the site can be "
            f"taken offline for the work."
        )
    elif options:
        blocked = [o for o in options if o["capacityDelta"] > 0 and not o["feasible"]]
        if blocked:
            action = (
                f"{count} request(s) failed at {site['DatacentreId']} for "
                f"{reason.lower()}. {blocked[0]['toSku']} would add "
                f"{blocked[0]['capacityDelta']:.0f} work units, but the region cannot "
                f"spare this site while the work runs. Add capacity elsewhere in "
                f"{region} first, then convert."
            )
        else:
            action = (
                f"{count} request(s) failed at {site['DatacentreId']} for "
                f"{reason.lower()}. {current} is already the densest hardware "
                f"available here — no swap adds capacity, so this needs more units "
                f"rather than different ones."
            )
    else:
        action = (f"{count} request(s) failed at {site['DatacentreId']} for "
                  f"{reason.lower()}. No conversion could be modelled.")
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
            action, options = _conversion_remediation(onto, site, reason, int(count))
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
