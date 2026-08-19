"""Module 6 -- Live Feature-Availability Matrix.

"Is this feature live in that region?" is scattered across reference documents,
so the answer is either stale or nobody looks it up. This is the one queryable
table, plus the two questions the other modules actually ask of it:

    is_available()          one feature, one region -- a direct yes/no
    check_expansion()       before recommending a region, what does it lack?

The second is why this module is not merely a lookup. Modules 1, 4 and 5 all
recommend putting capacity somewhere. A recommendation to expand into a region
where the customer's feature is not live is a bad recommendation, and only this
table knows that.
"""

from __future__ import annotations

from .matrix import (
    Availability,
    availability_matrix,
    check_expansion,
    feature_summary,
    is_available,
    region_summary,
)

__all__ = [
    "Availability",
    "availability_matrix",
    "is_available",
    "check_expansion",
    "region_summary",
    "feature_summary",
]
