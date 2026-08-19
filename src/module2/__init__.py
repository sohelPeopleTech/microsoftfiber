"""Module 2 -- SKU Migration & Conversion Calculator.

Shifting capacity from one hardware class to another is not a 1:1 swap: a
denser unit does more work, costs more, and takes a different time to
provision. Without a calculation that says so, "just move it to the faster
hardware" is a guess.

Deliberately a pure function. It reads no data at call time -- inventory,
ratio and reference figures are arguments -- which is why it was buildable
before any of the other modules' inputs existed, and why it can be called from
inside a Module 5 recommendation without dragging a pipeline along.
"""

from __future__ import annotations

from .calculator import (
    Conversion,
    convert_like_for_like,
    convert_same_footprint,
    migrate_region,
)
from .conversion import ConversionPlan, datacentres_for, plan_conversion

__all__ = [
    "Conversion",
    "ConversionPlan",
    "convert_like_for_like",
    "convert_same_footprint",
    "datacentres_for",
    "migrate_region",
    "plan_conversion",
]
