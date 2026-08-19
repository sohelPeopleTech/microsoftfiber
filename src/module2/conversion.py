"""Module 2b -- conversion planner.

The calculator next door compares a region before and after a hardware change.
That is the easy half. The half that decides whether the work can actually
happen is what occurs **during** it: capacity taken offline to be converted is
capacity customers cannot use, and a region running at 97% has nowhere to put
that load.

So this asks a different question. Not "what would the region look like
afterwards", but "how much can come out at once without breaking anything, and
how long does the whole thing take at that rate".

The answer is frequently *none*, which is the finding. A planner that always
produces a plan is not planning.

Three constraints, in the order they bite:

    headroom    spare capacity today = deployed - in use. Anything taken
                offline beyond this is a shortfall customers feel.
    delivery    replacement hardware has to be on the floor before the old
                comes out, so the lead time gates the first tranche.
    capability  the target hardware may be less capable per unit, so holding
                capacity flat can need MORE units than were removed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .calculator import sku_from_dim

#: Default assumption until real inventory arrives. The workbook gives a
#: region total and nothing below it, so the split into datacentres is an
#: assumption, not a measurement -- it is surfaced in every result rather than
#: hidden, because the whole answer scales with it.
DEFAULT_DATACENTRES = 10

#: Per-region overrides, to be filled in as real inventory arrives. Anything
#: absent falls back to DEFAULT_DATACENTRES.
DATACENTRES_PER_REGION: dict[str, int] = {}


def datacentres_for(region: str) -> int:
    return DATACENTRES_PER_REGION.get(region, DEFAULT_DATACENTRES)

#: Headroom to keep back while converting. Running a migration with zero slack
#: means the first unexpected request during the window is a denial.
DEFAULT_SAFETY_MARGIN_PCT = 5.0


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    """"1 datacentre" / "3 datacentres" -- these strings are read by people."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


@dataclass
class Tranche:
    number: int
    units_out: float
    available_during: float
    required: float
    shortfall: float
    safe: bool


@dataclass
class ConversionPlan:
    region: str
    from_sku: str
    to_sku: str
    datacentres: int
    units_per_datacentre: float
    deployed_units: float
    used_units: float
    headroom_units: float
    safety_margin_units: float
    max_offline_units: float
    can_convert_a_whole_datacentre: bool
    feasible: bool
    blocker: str
    tranche_size: float
    tranche_count: int
    tranches: list = field(default_factory=list)
    capacity_before: float = 0.0
    capacity_after: float = 0.0
    capacity_delta: float = 0.0
    units_to_hold_capacity_flat: float = 0.0
    extra_units_needed: float = 0.0
    #: Racks needed to hold capacity flat, as a multiple of what the converted
    #: datacentres physically hold. Above 1.0 the replacement does not fit.
    footprint_multiple: float = 1.0
    fits_in_footprint: bool = True
    cost_delta_pct: float = 0.0
    lead_time_days: int = 0
    options: list = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _options(plan_bits: dict) -> list[dict]:
    """What would make an infeasible conversion possible."""
    need = plan_bits["shortfall_for_one_dc"]
    return [
        {
            "option": "Add capacity first",
            "detail": (
                f"{need:,.0f} more units of headroom before the first datacentre "
                f"comes out."
            ),
            "blocks_on": "procurement",
        },
        {
            "option": "Wait for demand to fall",
            "detail": (
                f"Utilisation must drop below "
                f"{plan_bits['utilisation_needed']:.0f}% for a whole datacentre "
                f"to be taken offline safely. It is "
                f"{plan_bits['utilisation_now']:.0f}% today."
            ),
            "blocks_on": "demand",
        },
        {
            "option": "Convert in smaller tranches",
            "detail": (
                f"Take {plan_bits['tranche_size']:,.0f} units offline at a time "
                f"instead of a full {plan_bits['units_per_dc']:,.0f}-unit "
                f"datacentre -- {_plural(plan_bits['tranche_count'], 'pass', 'passes')}."
            )
            if plan_bits["tranche_size"] > 0
            else "No tranche is small enough; there is no usable headroom at all.",
            "blocks_on": "time",
        },
    ]


def plan_conversion(
    onto,
    region: str,
    to_sku: str,
    datacentres: int | None = None,
    convert_datacentres: int = 1,
    safety_margin_pct: float = DEFAULT_SAFETY_MARGIN_PCT,
) -> ConversionPlan:
    """Can this region convert, how, and at what cost."""
    dim = onto["dim_region"].set_index("Region")
    if region not in dim.index:
        known = ", ".join(sorted(dim.index))
        raise KeyError(f"unknown region {region!r}. Known: {known}")
    if datacentres is None:
        datacentres = datacentres_for(region)
    if datacentres < 1:
        raise ValueError("a region has at least one datacentre")
    if not 1 <= convert_datacentres <= datacentres:
        raise ValueError(f"convert_datacentres must be between 1 and {datacentres}")

    meta = dim.loc[region]
    source = sku_from_dim(onto["dim_sku"], str(meta["SKUClass"]))
    target = sku_from_dim(onto["dim_sku"], to_sku)

    usage = onto["fact_usage_daily"]
    latest = usage[usage["Region"] == region].sort_values("Date").tail(1)
    deployed = float(meta["DeployedUnits"])
    used = float(latest["UsedUnits"].iloc[0]) if len(latest) else 0.0

    per_dc = deployed / datacentres
    headroom = deployed - used
    margin = deployed * safety_margin_pct / 100.0
    # What can genuinely come offline: spare capacity, less the slack we keep.
    max_offline = max(0.0, headroom - margin)

    whole_dc_ok = max_offline >= per_dc
    shortfall_one_dc = max(0.0, per_dc - max_offline)

    # Utilisation that would have to be reached for a full DC to come out.
    util_needed = max(0.0, (deployed - per_dc - margin) / deployed * 100.0)
    util_now = used / deployed * 100.0 if deployed else 0.0

    units_to_convert = per_dc * convert_datacentres
    tranche = min(max_offline, units_to_convert) if max_offline > 0 else 0.0
    tranche_count = int(-(-units_to_convert // tranche)) if tranche > 0 else 0

    tranches = []
    if tranche > 0:
        remaining = units_to_convert
        n = 0
        while remaining > 0.01 and n < 100:
            n += 1
            out = min(tranche, remaining)
            available = deployed - out
            tranches.append(Tranche(
                number=n, units_out=round(out, 1),
                available_during=round(available, 1),
                required=round(used, 1),
                shortfall=round(max(0.0, used - available), 1),
                safe=available >= used,
            ))
            remaining -= out

    # What the finished conversion buys, for the converted portion only.
    before = units_to_convert * source.relative_performance
    after = units_to_convert * target.relative_performance
    cost_before = units_to_convert * source.relative_cost
    cost_after = units_to_convert * target.relative_cost
    flat = before / target.relative_performance if target.relative_performance else 0.0

    # Holding capacity flat on less capable hardware needs more racks than came
    # out. Above 1.0 the replacement does not physically fit in the space
    # vacated -- a constraint no cost comparison shows.
    footprint_multiple = flat / units_to_convert if units_to_convert else 1.0

    feasible = bool(tranche > 0 and all(t.safe for t in tranches))
    if max_offline <= 0:
        blocker = (
            f"No capacity can be taken offline. {region} is using "
            f"{used:,.0f} of {deployed:,.0f} units, leaving "
            f"{headroom:,.0f} spare against a {margin:,.0f}-unit safety margin."
        )
    elif not whole_dc_ok:
        blocker = (
            f"A whole datacentre cannot come offline. Spare capacity is "
            f"{max_offline:,.0f} units; a datacentre is {per_dc:,.0f}. "
            f"Conversion is only possible in smaller tranches."
        )
    else:
        blocker = ""

    if footprint_multiple > 1.0 + 1e-9:
        footprint_note = (
            f" Holding capacity flat would take {flat:,.0f} units of {to_sku} "
            f"where {units_to_convert:,.0f} came out -- "
            f"{footprint_multiple:.1f}x the rack space, which the vacated "
            f"floor does not have."
        )
    else:
        footprint_note = ""

    dcs = _plural(convert_datacentres, "datacentre")
    passes = _plural(tranche_count, "pass", "passes")

    if not feasible:
        summary = (
            f"{region} cannot convert {convert_datacentres} of {datacentres} "
            f"datacentres to {to_sku} today. {blocker}"
        )
    elif whole_dc_ok:
        summary = (
            f"{region} can convert {dcs} to {to_sku} in {passes} of "
            f"{tranche:,.0f} units, staying above current load throughout. "
            f"Capacity changes by {after - before:+,.0f} work units and cost by "
            f"{(cost_after / cost_before - 1) * 100 if cost_before else 0:+.0f}%."
            f"{footprint_note}"
        )
    else:
        summary = (
            f"{region} can only convert in tranches of {tranche:,.0f} units -- "
            f"{passes} to cover {dcs}. A full datacentre ({per_dc:,.0f} units) "
            f"is more than the {max_offline:,.0f} units of usable headroom."
            f"{footprint_note}"
        )

    return ConversionPlan(
        region=region,
        from_sku=source.name,
        to_sku=target.name,
        datacentres=datacentres,
        units_per_datacentre=round(per_dc, 1),
        deployed_units=round(deployed, 1),
        used_units=round(used, 1),
        headroom_units=round(headroom, 1),
        safety_margin_units=round(margin, 1),
        max_offline_units=round(max_offline, 1),
        can_convert_a_whole_datacentre=whole_dc_ok,
        feasible=feasible,
        blocker=blocker,
        tranche_size=round(tranche, 1),
        tranche_count=tranche_count,
        tranches=[asdict(t) for t in tranches],
        capacity_before=round(before, 1),
        capacity_after=round(after, 1),
        capacity_delta=round(after - before, 1),
        units_to_hold_capacity_flat=round(flat, 1),
        extra_units_needed=round(max(0.0, flat - units_to_convert), 1),
        footprint_multiple=round(footprint_multiple, 2),
        fits_in_footprint=footprint_multiple <= 1.0 + 1e-9,
        cost_delta_pct=round((cost_after / cost_before - 1) * 100 if cost_before else 0.0, 1),
        lead_time_days=target.lead_time_days,
        options=_options({
            "shortfall_for_one_dc": shortfall_one_dc,
            "utilisation_needed": util_needed,
            "utilisation_now": util_now,
            "tranche_size": tranche,
            "tranche_count": tranche_count,
            "units_per_dc": per_dc,
        }) if not whole_dc_ok else [],
        summary=summary,
    )
