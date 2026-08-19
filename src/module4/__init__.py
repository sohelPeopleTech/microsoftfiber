"""Module 4 -- Customer-Success-Linked Anomaly Detection.

A region's demand jumps. Two very different things could have happened:

  ordinary growth   -- the trend continuing, nothing to do
  a business event  -- a deal closed, a pilot converted, and the capacity
                       request is the consequence

Told apart, they lead to opposite decisions. Confused, every spike looks like a
crisis and the real ones get lost. This module detects the spike statistically,
then looks for a business event in the days before it -- and only claims a cause
when it finds one.

The discipline that makes it useful is refusing to explain everything. Six of
the eighteen events in the feed are unlinked noise, and a detector that matched
them all would be worthless. Matching is bounded by a time window and reported
with the evidence, so a human can disagree with the specific link rather than
with the idea.

Every recommendation is held for approval -- the design doc is explicit that
nothing here is executed automatically.
"""

from __future__ import annotations

from .anomaly import (
    Anomaly,
    detect_anomalies,
    explain_anomalies,
    match_events,
)

__all__ = ["Anomaly", "detect_anomalies", "match_events", "explain_anomalies"]
