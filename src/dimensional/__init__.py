"""The shared entity model every module reads from.

Six modules were specified separately, and building them separately is how a
platform ends up with six different answers to "which region is worst". The
fix is that Region, Subscription, CapacityRequest, SKU, Feature and Event are
defined **once**, here, and each module becomes a view over them rather than a
pipeline of its own.

    dim_region              11 regions, with their hardware and lead time
    dim_subscription        23 customers, with tier and revenue
    dim_sku                  5 hardware classes, cost/performance/lead time
    dim_feature              6 product features
    fact_capacity_request   60 tickets -- the spine
    fact_usage_daily      1650 region-days of utilisation
    fact_event              18 deal / customer-success events
    bridge_feature_region   66 feature x region availability states

Two properties are enforced rather than hoped for:

**Referential integrity.** Every fact's Region resolves to a dim_region row,
every SubscriptionId to a dim_subscription row, every SKUClass to a dim_sku
row. A join that silently drops rows is how a total stops reconciling.

**Provenance per column.** Some of this is a real ICM extract and some is
generated. `sources()` says which is which, and the product surfaces it -- so
nobody quotes a synthetic lead time as a commitment.
"""

from __future__ import annotations

from .build import ENTITIES, build, sources, validate

__all__ = ["build", "validate", "sources", "ENTITIES"]
