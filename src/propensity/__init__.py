"""Propensity -- how likely is this capacity request to fail?

Modules 1 to 6 describe what already happened. This asks a forward question
about a single request: given its size, the customer's tier, and how full the
region already is, what is the chance it gets denied-and-delayed or never
fulfilled?

Useful because it changes when you can act. Today a failure is discovered after
the fact, in Module 5, priced in lost revenue. A propensity score is available
the moment the request arrives -- early enough to approve it directly, or to
raise capacity before the denial happens.

Two disciplines make this honest rather than decorative:

**No leakage.** Every feature must be knowable when the request is raised.
Days-unavailable, exposure and the outcome category are consequences of the
failure, and a model fed those would score ~1.0 and mean nothing.

**No overclaiming.** 60 tickets and 30 failures support a small, regularised,
interpretable model and nothing more. Performance is reported from
cross-validation against a baseline, never from the training set, and the
sample size is stated wherever a score is shown.
"""

from __future__ import annotations

from .model import (
    FEATURES,
    PropensityModel,
    build_training_frame,
    evaluate,
    train,
)

__all__ = [
    "FEATURES",
    "PropensityModel",
    "build_training_frame",
    "train",
    "evaluate",
]
