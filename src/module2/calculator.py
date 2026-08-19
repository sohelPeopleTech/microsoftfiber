"""The conversion arithmetic.

Two questions get asked about a hardware change, and they have different
answers, so both are modelled explicitly rather than collapsed into one number:

  **Like-for-like** -- "I need the same amount of work done. How many units of
  the new class, and what does that cost?" The unit count usually *falls* when
  moving to denser hardware, which is the case for the migration.

  **Same footprint** -- "I have N units of rack. If they were the new class,
  how much more work could I do?" The unit count stays put and capacity moves.

Both take the conversion ratio from relative performance rather than assuming
1:1, which is the mistake the module exists to prevent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SKU:
    """A hardware class. Cost and performance are relative to a baseline."""

    name: str
    relative_performance: float
    relative_cost: float
    lead_time_days: int = 0

    def __post_init__(self) -> None:
        if self.relative_performance <= 0:
            raise ValueError(f"{self.name}: relative_performance must be > 0")
        if self.relative_cost < 0:
            raise ValueError(f"{self.name}: relative_cost cannot be negative")


@dataclass
class Conversion:
    mode: str
    from_sku: str
    to_sku: str
    from_units: float
    to_units: float
    conversion_ratio: float          # target units that equal one source unit
    capacity_before: float           # work units, baseline-relative
    capacity_after: float
    capacity_delta: float
    cost_before: float
    cost_after: float
    cost_delta: float
    cost_delta_pct: float
    lead_time_days: int
    headroom_units: float | None = None      # vs a stated requirement
    covers_requirement: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        direction = "saves" if self.cost_delta < 0 else "adds"
        cost = (
            f"{direction} {abs(self.cost_delta_pct):.0f}% cost"
            if self.cost_delta
            else "no cost change"
        )
        if self.mode == "like_for_like":
            return (
                f"{self.from_units:,.0f} × {self.from_sku} → "
                f"{self.to_units:,.0f} × {self.to_sku} for the same work; {cost}; "
                f"{self.lead_time_days}-day lead time."
            )
        return (
            f"{self.from_units:,.0f} units switched from {self.from_sku} to "
            f"{self.to_sku}: capacity {self.capacity_before:,.0f} → "
            f"{self.capacity_after:,.0f} work units "
            f"({self.capacity_delta:+,.0f}); {cost}; "
            f"{self.lead_time_days}-day lead time."
        )


def conversion_ratio(source: SKU, target: SKU) -> float:
    """Target units that do the work of one source unit.

    A target twice as capable needs half as many units, so the ratio is the
    inverse of the performance ratio -- getting this the wrong way round is
    exactly the 1:1 assumption the module exists to prevent.
    """
    return source.relative_performance / target.relative_performance


def convert_like_for_like(
    source: SKU,
    target: SKU,
    units: float,
    required_work_units: float | None = None,
) -> Conversion:
    """Replace `units` of source with however much target does the same work."""
    if units < 0:
        raise ValueError("units cannot be negative")

    ratio = conversion_ratio(source, target)
    to_units = units * ratio

    work = units * source.relative_performance
    cost_before = units * source.relative_cost
    cost_after = to_units * target.relative_cost

    headroom = covers = None
    if required_work_units is not None:
        headroom = round(work - required_work_units, 4)
        covers = headroom >= 0

    return Conversion(
        mode="like_for_like",
        from_sku=source.name,
        to_sku=target.name,
        from_units=units,
        to_units=round(to_units, 4),
        conversion_ratio=round(ratio, 6),
        capacity_before=round(work, 4),
        capacity_after=round(work, 4),          # by definition, work is held constant
        capacity_delta=0.0,
        cost_before=round(cost_before, 4),
        cost_after=round(cost_after, 4),
        cost_delta=round(cost_after - cost_before, 4),
        cost_delta_pct=round(
            ((cost_after - cost_before) / cost_before * 100) if cost_before else 0.0, 2
        ),
        lead_time_days=target.lead_time_days,
        headroom_units=headroom,
        covers_requirement=covers,
    )


def convert_same_footprint(
    source: SKU,
    target: SKU,
    units: float,
    required_work_units: float | None = None,
) -> Conversion:
    """Keep the same unit count, change the class, and see what capacity does."""
    if units < 0:
        raise ValueError("units cannot be negative")

    before = units * source.relative_performance
    after = units * target.relative_performance
    cost_before = units * source.relative_cost
    cost_after = units * target.relative_cost

    headroom = covers = None
    if required_work_units is not None:
        headroom = round(after - required_work_units, 4)
        covers = headroom >= 0

    return Conversion(
        mode="same_footprint",
        from_sku=source.name,
        to_sku=target.name,
        from_units=units,
        to_units=units,
        conversion_ratio=round(conversion_ratio(source, target), 6),
        capacity_before=round(before, 4),
        capacity_after=round(after, 4),
        capacity_delta=round(after - before, 4),
        cost_before=round(cost_before, 4),
        cost_after=round(cost_after, 4),
        cost_delta=round(cost_after - cost_before, 4),
        cost_delta_pct=round(
            ((cost_after - cost_before) / cost_before * 100) if cost_before else 0.0, 2
        ),
        lead_time_days=target.lead_time_days,
        headroom_units=headroom,
        covers_requirement=covers,
    )


# --------------------------------------------------------------------------
# the ontology-aware wrapper
# --------------------------------------------------------------------------


def sku_from_dim(dim_sku, name: str) -> SKU:
    """Build a SKU from the ontology's dim_sku row."""
    match = dim_sku[dim_sku["SKUClass"] == name]
    if match.empty:
        known = ", ".join(sorted(dim_sku["SKUClass"]))
        raise KeyError(f"unknown SKU class {name!r}. Known: {known}")
    row = match.iloc[0]
    return SKU(
        name=name,
        relative_performance=float(row["RelativePerformance"]),
        relative_cost=float(row["RelativeCostPerUnit"]),
        lead_time_days=int(row["LeadTimeDays"]),
    )


def migrate_region(onto, region: str, to_sku: str, mode: str = "same_footprint") -> dict:
    """What converting a whole region would do, using real deployed units.

    Answers the question a Module 5 recommendation raises but cannot itself
    address: instead of raising the approval threshold, could this region serve
    the demand it already has by changing hardware?
    """
    dim_region = onto["dim_region"]
    row = dim_region[dim_region["Region"] == region]
    if row.empty:
        known = ", ".join(sorted(dim_region["Region"]))
        raise KeyError(f"unknown region {region!r}. Known: {known}")
    row = row.iloc[0]

    source = sku_from_dim(onto["dim_sku"], str(row["SKUClass"]))
    target = sku_from_dim(onto["dim_sku"], to_sku)
    units = float(row["DeployedUnits"])

    # What the region is actually carrying right now, in work units.
    usage = onto["fact_usage_daily"]
    latest = usage[usage["Region"] == region].sort_values("Date").tail(1)
    used_units = float(latest["UsedUnits"].iloc[0]) if len(latest) else 0.0
    required = used_units * source.relative_performance

    fn = convert_same_footprint if mode == "same_footprint" else convert_like_for_like
    conversion = fn(source, target, units, required_work_units=required)

    return {
        "region": region,
        "from_sku": source.name,
        "to_sku": target.name,
        "deployed_units": units,
        "used_units_now": round(used_units, 1),
        "required_work_units": round(required, 1),
        "conversion": conversion.to_dict(),
        "summary": conversion.summary(),
        "lead_time_days": target.lead_time_days,
    }
