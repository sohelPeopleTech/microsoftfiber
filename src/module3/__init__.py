"""Module 3 -- Regional Demand Forecasting.

Counting what already happened tells you where capacity *was* short. This turns
the same tickets into a forward view: which regions are growing, how fast, and
what next period looks like if nothing changes.

Feeds two other modules, which is why it exists as its own thing rather than a
chart on someone's report:

    Module 1  takes the growth curve and asks "when do we cross the threshold,
              and is that sooner than the lead time?"
    Module 4  takes the same curve as its baseline and asks "is this month's
              jump normal growth, or an event?"

Deliberately a moving average, as the design doc allows. With 60 tickets over
five months there is not enough signal to justify anything fancier, and a
method a reviewer can recompute by hand beats one they have to trust.
"""

from __future__ import annotations

from .forecast import (
    demand_by_period,
    forecast_demand,
    growth_ranking,
    usage_by_period,
)

__all__ = [
    "demand_by_period",
    "forecast_demand",
    "growth_ranking",
    "usage_by_period",
]
