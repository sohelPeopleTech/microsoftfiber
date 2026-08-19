"""Follow-up Q&A -- "anyone can ask the agent questions in that same chat".

Intent routing is deterministic and answers come from the same computed frames
that produced the posted message, so the chat can never contradict the card.
The LLM is a fallback for phrasings the router does not recognise, and even
then it answers only from a fact sheet built out of those same frames.

Questions the router handles directly:

    "why is uksouth ranked lower?"          ranking explanation
    "where does eastus2 rank?"              same path
    "which region is worst?"                top of the ranking
    "what's the total exposure?"            portfolio summary
    "show me incident 603081913"            per-ticket arithmetic
    "which customers are affected?"         customer leaderboard
    "how is exposure calculated?"           methodology, incl. the cut-off
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from .formatting import money
from .llm import LLMConfig, LLMUnavailable, chat
from .recommend import why_ranked
from .revenue import explain_ticket

QA_SYSTEM_PROMPT = """You answer follow-up questions about a capacity-denial \
revenue analysis in the in-app assistant.

Answer ONLY from the fact sheet provided. If the fact sheet does not contain \
the answer, say so plainly and name what you would need -- never guess a \
number, a region, or a customer. Do not recompute anything. Two or three \
sentences, plain business English, no headings, no emoji."""

_INCIDENT_RE = re.compile(r"\b(\d{6,})\b")


@dataclass
class Answer:
    text: str
    intent: str
    used_llm: bool = False

    def to_dict(self) -> dict:
        return {"intent": self.intent, "used_llm": self.used_llm, "text": self.text}


def _find_region(question: str, regions: pd.DataFrame) -> str | None:
    lowered = question.lower()
    matches = [r for r in regions["Region"] if r.lower() in lowered]
    # Longest match first: "eastus2" must not be shadowed by "eastus".
    return max(matches, key=len) if matches else None


def _methodology(result) -> str:
    config = result.finding["config"]
    return (
        f"A request counts as failed if it was denied and then approved more "
        f"than {config['meaningful_delay_hours']:.0f} hours later, or denied and "
        f"never approved. Exposure for each failed request is the customer's ARR "
        f"x the share of their requested capacity that was missing x the days "
        f"they went without it, annualised over "
        f"{config['annualisation_days']:.0f} days; an unfulfilled request accrues "
        f"from its denial date to {result.finding['as_of']}, capped at "
        f"{config['unfulfilled_cap_days']:.0f} days. ARR affected is different: "
        f"it is the whole ARR of every affected customer, counted once each, so "
        f"it reads as blast radius rather than as loss."
    )


def _summary_answer(result) -> str:
    s = result.finding["summary"]
    return (
        f"As of {s['as_of']}: {s['tickets_flagged']} of {s['tickets_total']} "
        f"capacity requests failed -- {s['delayed_count']} approved late "
        f"(median {s['median_delay_days']:.0f} days, worst "
        f"{s['max_delay_days']:.0f}) and {s['unfulfilled_count']} never "
        f"fulfilled. That touches {s['customers_affected']} customer(s) across "
        f"{s['regions_affected']} of {s['regions_total']} regions, "
        f"{money(s['arr_affected_usd'])} in affected ARR, and "
        f"{money(s['revenue_exposure_usd'])} of risk-adjusted exposure."
    )


def _top_region_answer(result) -> str:
    if result.regions.empty or not result.recommendations:
        return "No region has any exposure in this period."
    rec = result.recommendations[0]
    return (
        f"{rec.region}, at {money(rec.revenue_exposure_usd)} exposure across "
        f"{rec.tickets_flagged} failed request(s) affecting "
        f"{rec.customers_affected} customer(s) worth "
        f"{money(rec.arr_affected_usd)} in ARR. Recommendation: {rec.action}"
    )


def _customers_answer(result, limit: int = 5) -> str:
    customers = result.finding.get("customers", [])
    if not customers:
        return "No customers were affected in this period."
    lines = ["Most affected customers by exposure:"]
    for c in customers[:limit]:
        lines.append(
            f"  - {c['SubscriptionId']} ({c['SubscriptionTier']}, "
            f"{money(c['ARR_USD'])} ARR): {c['TicketsFlagged']} failed "
            f"request(s) in {c['Regions']}, "
            f"{money(c['RevenueExposureUSD'])} exposure"
        )
    return "\n".join(lines)


def _incident_answer(result, incident_id: str) -> str | None:
    match = result.priced[result.priced["IncidentId"].astype(str) == incident_id]
    if match.empty:
        return None
    from .config import Config

    config = Config(**result.finding["config"])
    return explain_ticket(match.iloc[0], config)


def _fact_sheet(result, limit: int = 8) -> str:
    """Everything the fallback model is allowed to know."""
    lines = [_summary_answer(result), "", "Regions, ranked by exposure:"]
    for r in result.finding["regions"][:limit]:
        lines.append(
            f"  {r['Rank']}. {r['Region']}: {money(r['RevenueExposureUSD'])} "
            f"exposure, {r['TicketsFlagged']} failed of {r['TicketsTotal']} "
            f"({r['DelayedCount']} delayed, {r['UnfulfilledCount']} unfulfilled), "
            f"{r['CustomersAffected']} customer(s), "
            f"{money(r['ARRAffectedUSD'])} ARR affected"
        )
    lines += ["", "Recommendations:"]
    for rec in result.recommendations:
        lines.append(f"  {rec.rank}. {rec.region}: {rec.action} ({rec.rationale})")
    lines += ["", "Method:", _methodology(result)]
    if result.recommendations and result.recommendations[0].caveat:
        lines += ["", f"Caveat: {result.recommendations[0].caveat}"]
    return "\n".join(lines)


def answer(
    question: str,
    result,
    llm_config: LLMConfig | None = None,
    allow_llm: bool = True,
) -> Answer:
    """Route one follow-up question to a grounded answer."""
    q = question.lower().strip()

    incident = _INCIDENT_RE.search(question)
    if incident:
        text = _incident_answer(result, incident.group(1))
        if text:
            return Answer(text, intent="incident_lookup")
        return Answer(
            f"Incident {incident.group(1)} is not in this extract.",
            intent="incident_lookup",
        )

    # A named region wins over a general "how does this work" -- "explain why
    # uksouth is lower" is a question about uksouth, not about the method.
    region = _find_region(question, result.regions)
    if region and any(
        k in q for k in ("rank", "lower", "higher", "why", "where", "compare", "worse",
                         "better", "position", "explain")
    ):
        return Answer(why_ranked(region, result.regions), intent="ranking_explanation")

    # Stems, not whole words: "calculat" catches calculate/calculated/calculation.
    # Matching too narrowly here is what makes an assistant look stupid on a
    # question it can obviously answer.
    if any(k in q for k in ("calculat", "how is", "how do", "how does", "method",
                            "formula", "cut-off", "cutoff", "why 48", "definition",
                            "work out", "worked out", "explain")):
        return Answer(_methodology(result), intent="methodology")

    if any(k in q for k in ("worst", "highest", "top region", "which region", "most exposure")):
        return Answer(_top_region_answer(result), intent="top_region")

    if any(k in q for k in ("customer", "subscription", "tenant", "who is affected")):
        return Answer(_customers_answer(result), intent="customers")

    if any(k in q for k in ("total", "summary", "overall", "how many", "how much")):
        return Answer(_summary_answer(result), intent="summary")

    if region:
        return Answer(why_ranked(region, result.regions), intent="ranking_explanation")

    if not allow_llm:
        return Answer(_unrecognised(result), intent="unrecognised")

    try:
        text = chat(
            QA_SYSTEM_PROMPT,
            f"Fact sheet:\n{_fact_sheet(result)}\n\nQuestion: {question}",
            llm_config,
        )
        return Answer(text, intent="llm_fallback", used_llm=True)
    except LLMUnavailable:
        return Answer(_unrecognised(result), intent="unrecognised")


def _unrecognised(result) -> str:
    regions = ", ".join(result.regions["Region"].head(5))
    return (
        "I can answer questions about this period's capacity-denial findings: "
        "how a region ranks and why, the totals, which customers were affected, "
        "a specific incident ID, or how exposure is calculated. "
        f"Regions in this extract include {regions}."
    )
