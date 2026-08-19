"""Module 5 -- Capacity-Denial Revenue Impact Calculator.

Pipeline shape (design doc, "How We'll Achieve It"):

    ingest    Bronze -> Silver -> Gold ticket frame
    classify  four-outcome date comparison  (step 1)
    evaluate  score against the pre-labelled sample  (step 2)
    revenue   join ARR, size the exposure  (step 3)
    aggregate group by region, rank by exposure  (step 4)
    recommend one specific recommendation per top region  (step 5)
    publish   write artefacts the web app reads, held for human review  (step 6)

Everything below `pipeline.run()` is pure pandas -- no network, no model -- so
the numbers are reproducible and testable. The LLM and delivery layers sit at the
edges and can be switched off without changing a single figure.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "aggregate",
    "classifier",
    "config",
    "ingest",
    "pipeline",
    "recommend",
    "report",
    "revenue",
]
