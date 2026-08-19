"""A second basis for revenue loss: what the unsold capacity would have billed.

Review asked to "look at Microsoft cost estimator along with your calculation".
This is that, structured so the real rate card drops in without touching any
caller.

TWO BASES, TWO QUESTIONS
    ARR apportionment (the existing one) asks *how much of this customer's
    relationship was exposed* -- their annual payment, narrowed by the share of
    their request left unmet and how long it stayed unmet. It is a severity
    ranking.

    Consumption rate (this one) asks *what would this capacity have billed had
    we been able to sell it* -- units they could not consume, multiplied by the
    published rate, for the hours they could not consume it. It is closer to
    forgone revenue and it is the number a cost estimator produces.

    They differ, deliberately, and both are reported. A big customer briefly
    short scores high on the first; a small customer short for months scores
    high on the second. Quoting only one hides half the problem.

WHAT IS REAL HERE
    Nothing yet. `RATES` is a placeholder ladder keyed by Fabric SKU. The shape
    is right -- price per capacity unit per hour is how Fabric is billed -- so
    replacing the numbers with the published rate card is a data change, not a
    code change. Every result carries `isPlaceholder` so no downstream caller
    can quietly present it as real pricing.
"""

from __future__ import annotations

from dataclasses import dataclass

#: USD per Capacity Unit per hour, by Fabric SKU. PLACEHOLDER FIGURES.
#: Shape matches how Fabric is actually billed so the published rate card can
#: replace these values directly. Larger SKUs are priced slightly better per CU,
#: which is the usual shape of a volume ladder.
RATES: dict[str, float] = {
    "F2": 0.36, "F4": 0.36, "F8": 0.35, "F16": 0.35, "F32": 0.34,
    "F64": 0.33, "F128": 0.32, "F256": 0.31, "F512": 0.30,
    "F1024": 0.29, "F2048": 0.28,
}

DEFAULT_SKU = "F64"
HOURS_PER_DAY = 24

#: Flipped to False only when the published rate card replaces RATES.
RATES_ARE_PLACEHOLDER = True


@dataclass
class Estimate:
    units_unavailable: float
    days: float
    sku: str
    rate_per_cu_hour: float
    capacity_units: float
    hours: float
    amount: float
    is_placeholder: bool = RATES_ARE_PLACEHOLDER

    def to_dict(self) -> dict:
        return {
            "unitsUnavailable": round(self.units_unavailable, 1),
            "days": round(self.days, 2),
            "sku": self.sku,
            "ratePerCuHour": self.rate_per_cu_hour,
            "capacityUnits": round(self.capacity_units, 2),
            "hours": round(self.hours, 1),
            "amount": round(self.amount, 2),
            "isPlaceholder": self.is_placeholder,
        }

    @property
    def working_out(self) -> str:
        """The sum in words, matching the discipline used everywhere else."""
        return (
            f"{self.units_unavailable:,.0f} units unavailable "
            f"= {self.capacity_units:,.1f} capacity units "
            f"x ${self.rate_per_cu_hour:.2f}/CU/hour "
            f"x {self.hours:,.0f} hours ({self.days:.1f} days) "
            f"= ${self.amount:,.2f}"
        )


def rate_for(sku: str) -> float:
    return RATES.get(sku, RATES[DEFAULT_SKU])


def estimate(units_unavailable: float, days: float, sku: str = DEFAULT_SKU,
             units_per_cu: float | None = None) -> Estimate:
    """What the capacity a customer could not get would have billed.

    `units_per_cu` converts raw compute units to Capacity Units; it defaults to
    the same constant the pool model uses, so the two cannot drift apart.
    """
    from admission import UNITS_PER_CU

    per_cu = units_per_cu if units_per_cu is not None else UNITS_PER_CU
    units_unavailable = max(0.0, float(units_unavailable))
    days = max(0.0, float(days))

    capacity_units = units_unavailable * per_cu
    hours = days * HOURS_PER_DAY
    rate = rate_for(sku)
    return Estimate(
        units_unavailable=units_unavailable, days=days, sku=sku,
        rate_per_cu_hour=rate, capacity_units=capacity_units, hours=hours,
        amount=capacity_units * rate * hours,
    )


def estimate_ticket(row, sku: str = DEFAULT_SKU) -> Estimate:
    """Estimate for one priced ticket row from the module 5 output."""
    return estimate(
        units_unavailable=float(getattr(row, "BlockedUnits", 0) or 0),
        days=float(getattr(row, "DaysUnavailable", 0) or 0),
        sku=sku,
    )


__all__ = ["RATES", "RATES_ARE_PLACEHOLDER", "DEFAULT_SKU", "HOURS_PER_DAY",
           "rate_for", "estimate", "estimate_ticket", "Estimate"]
