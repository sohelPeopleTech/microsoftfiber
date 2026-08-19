"""Module 1 -- Capacity Lifecycle & Lead-Time-Aware Threshold Engine.

Capacity alerts normally fire when usage is already near the limit. By then it
can be too late: if the hardware takes 45 days to provision and you are 30 days
from the threshold, the alert arrives fifteen days after the decision needed
making.

This module moves the alarm backwards by the lead time. It forecasts when a
region will cross its safety threshold, subtracts how long that region's
hardware actually takes to provision, and flags the request as due on the
resulting date -- which is often while usage still looks comfortable.

    cross date  -  lead time  =  order-by date
    order-by date <= today    =>  the request is due now

The awkward, useful consequence is that a region at 71% can be more urgent than
one at 94%, because the first runs Intel-highmem (45 days) and the second runs
AMD-highmem (10). That inversion is the entire point of the module.
"""

from __future__ import annotations

from .threshold import (
    ThresholdFlag,
    project_region,
    project_all,
    due_requests,
)

__all__ = ["ThresholdFlag", "project_region", "project_all", "due_requests"]
