"""The in-dashboard assistant.

Two things people usually get wrong when they put a chatbot on a dashboard, and
both are avoided here deliberately:

**It invents numbers.** A model asked "which region is worst?" will happily
produce a plausible region and a plausible dollar figure. So this one is never
asked to recall anything -- a complete, current snapshot of the platform is
computed from the ontology and handed to it with every question, and the answer
is then checked: any money figure, region name or incident ID that is not in
that snapshot fails the answer, and the user gets the deterministic reply
instead.

**It is a keyword lookup wearing a chat interface.** The earlier version matched
"worst" to a canned sentence and fell over on "which two regions should I fix
first, and why is one more urgent than the other?" Because the model receives
the whole snapshot rather than a matched row, it can compare, rank, combine
modules and follow up -- which is the part that makes it worth having.

Falls back to the deterministic router when the model is unavailable, so the
panel always answers something true.
"""

from __future__ import annotations

from .agent import ask, build_snapshot, check_grounding

__all__ = ["ask", "build_snapshot", "check_grounding"]
