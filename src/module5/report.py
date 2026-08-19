"""Rendering -- one finding, two surfaces.

The design doc is explicit that the deliverable is "a written finding and
recommendation", so the pipeline produces a single `finding` dict and this
module renders it two ways:

  to_markdown()       what a person reads -- out/finding.md, and the written
                      finding the web application shows on its Actions tab
  finding["..."]      what another module or the agent's Q&A layer queries

Both come from the same numbers, so the assistant's answer can never drift
from the written finding.
"""

from __future__ import annotations

from .config import Config
from .formatting import money
from .recommend import Recommendation

REVIEW_FOOTER = (
    "_Held for human review -- nothing has been changed. "
    "Approve or reject each recommendation in the application, or ask the "
    "assistant a follow-up (e.g. \"why is uksouth ranked lower?\")._"
)


def build_finding(
    summary: dict,
    regions_ranked: list,
    recommendations: list[Recommendation],
    data_quality: dict,
    evaluation: dict,
    config: Config,
) -> dict:
    """The canonical result object. Everything else renders from this."""
    return {
        "module": "module5_capacity_denial_revenue_impact",
        "as_of": summary.get("as_of"),
        "status": "pending_human_review",
        "summary": summary,
        "regions": regions_ranked,
        "recommendations": [r.to_dict() for r in recommendations],
        "data_quality": data_quality,
        "classifier_evaluation": evaluation,
        "config": config.to_dict(),
    }


def headline_sentence(summary: dict, recommendations: list[Recommendation]) -> str:
    """The one line that has to survive being read on a phone."""
    if not recommendations:
        return (
            f"No capacity-denial revenue impact detected as of {summary['as_of']}: "
            f"{summary['tickets_total']} requests reviewed, none delayed past the "
            f"cut-off or left unfulfilled."
        )
    top = recommendations[0]
    return (
        f"{top.region} shows the highest revenue exposure this period -- "
        f"{top.tickets_flagged} request(s) were delayed or unfulfilled, affecting "
        f"{money(top.arr_affected_usd)} in customer revenue "
        f"({money(top.revenue_exposure_usd)} at risk). {top.action}"
    )


def to_markdown(finding: dict, recommendations: list[Recommendation]) -> str:
    s = finding["summary"]
    lines = [
        "## Capacity-Denial Revenue Impact -- "
        f"{s['tickets_flagged']} failed request(s) as of {s['as_of']}",
        "",
        f"**{headline_sentence(s, recommendations)}**",
        "",
        f"Across {s['regions_total']} region(s): {s['tickets_total']} capacity "
        f"requests reviewed, {s['delayed_count']} approved past the "
        f"{finding['config']['meaningful_delay_hours']:.0f}h cut-off and "
        f"{s['unfulfilled_count']} never fulfilled. "
        f"{s['customers_affected']} customer(s) affected, "
        f"{money(s['arr_affected_usd'])} ARR in the blast radius, "
        f"{money(s['revenue_exposure_usd'])} risk-adjusted exposure.",
        "",
        "### Ranked by revenue exposure",
        "",
        "| # | Region | Failed | Delayed | Unfulfilled | Customers | ARR affected | Exposure |",
        "|---|--------|-------:|--------:|------------:|----------:|-------------:|---------:|",
    ]

    for r in finding["regions"][:10]:
        lines.append(
            f"| {r['Rank']} | {r['Region']} | {r['TicketsFlagged']} | "
            f"{r['DelayedCount']} | {r['UnfulfilledCount']} | "
            f"{r['CustomersAffected']} | {money(r['ARRAffectedUSD'])} | "
            f"{money(r['RevenueExposureUSD'])} |"
        )

    lines += ["", "### Recommendations", ""]
    if not recommendations:
        lines.append("No action required this period.")
    for rec in recommendations:
        lines += [
            f"**{rec.rank}. {rec.region} -- {money(rec.revenue_exposure_usd)} exposure**",
            "",
            f"- **Do:** {rec.action}",
            f"- **Why:** {rec.rationale}",
        ]
        for e in rec.evidence:
            lines.append(f"- {e}")
        lines.append("")

    dq = finding["data_quality"]
    if not dq.get("is_clean", True):
        lines += ["### Data quality", ""]
        lines += [f"- {line.strip('- ')}" for line in dq.get("summary_lines", [])]
        lines.append("")

    ev = finding.get("classifier_evaluation") or {}
    if ev:
        verdict = "passed" if ev.get("passed") else "FAILED"
        lines.append(
            f"_Classifier {verdict} against the labelled sample: "
            f"{ev.get('n_correct', 0)}/{ev.get('n_scored', 0)} correct "
            f"({ev.get('accuracy', 0):.0%})._"
        )
    if recommendations and recommendations[0].caveat:
        lines.append(f"_{recommendations[0].caveat}_")

    lines += ["", REVIEW_FOOTER]
    return "\n".join(lines)
