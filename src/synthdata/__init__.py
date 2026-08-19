"""Synthetic datasets for the entities ICM does not give us.

The real extract carries nine columns: incident, subscription, tenant, region,
three capacity numbers and two dates. Four of the six modules need entities
that simply are not in it -- hardware, usage over time, deal events, feature
availability. Rather than leave those modules unbuildable, we generate the
missing entities.

Two rules make that safe rather than reckless:

**Everything is tagged.** Every generated row carries `IsSynthetic` and a
`Provenance` string. Nothing can be mistaken for a business figure, and the
product can show the distinction. This matters more than it sounds -- three
placeholder datasets (ARR, tier, lead time) were already doing load-bearing
work before anyone wrote them down as placeholders.

**Everything is derived from the real data.** Regions are the eleven real
regions; subscriptions are the twenty-three real subscriptions; usage curves
follow the request volume actually observed per region; deal events are placed
where demand actually jumped. Invented data that contradicts the real data is
worse than no data, because it makes every downstream module disagree with the
tickets underneath it.

Seeded, so two runs produce identical output and a figure quoted on Monday is
still true on Friday.
"""

from __future__ import annotations

SEED = 20260813

__all__ = ["generate", "SEED"]
