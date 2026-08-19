"""Optional LLM rewrite of the finding, with a grounding check.

The model is given the computed finding and asked to write it up. It is never
asked to compute anything, and the result is verified before use: every dollar
figure and every region name in the generated prose must already exist in the
payload. A single invented figure fails the whole rewrite, and the caller falls
back to the deterministic text from report.py.

This is the cheap version of the guarantee that matters here -- a finance
channel reading "$4.1M exposure" needs that number to have come out of pandas,
not out of a language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .formatting import money
from .llm import LLMConfig, LLMUnavailable, chat
from .recommend import Recommendation

SYSTEM_PROMPT = """You write short internal findings for a Microsoft Fabric \
capacity team, read in the application by finance and sales leadership.

Rules, in order of importance:
1. Use ONLY the figures given to you. Never compute, round, infer, or invent a \
number, a region name, an incident ID, or a customer name. If a figure is not \
in the input, it does not go in the output.
2. Keep the ranking and the recommended actions exactly as given. You are \
rewriting the wording, not the analysis.
3. Lead with the single most important sentence. A reader on a phone should get \
the point from the first line.
4. Plain business English. No marketing language, no hedging, no emoji, no \
headings beyond what you are asked for. Region names are identifiers, not \
prose: write them exactly as given (westeurope, not West Europe).
5. Keep it under 200 words. State the recommendation as an action someone can \
take, and say it is pending human review.

Write EXACTLY TWO SENTENCES and nothing else. No bullets, no headings, no \
markdown, no sign-off.

Sentence one: the single most important fact, leading with the money.
Sentence two: what is being recommended, in outline.

The detail is rendered separately in a structured panel below your text -- \
region by region, with problem, cause, impact, action and evidence each in its \
own labelled field. Do not restate that detail; your job is the opening line \
a reader sees before the panel. Under 45 words total."""

# Anything the model may legitimately say a dollar amount or region about.
_MONEY_RE = re.compile(r"\$\s?[\d][\d,.]*\s?[MKB]?", re.IGNORECASE)
_REGION_RE = re.compile(r"\b(?:east|west|north|south|central)[a-z0-9]+\b", re.IGNORECASE)


@dataclass
class NarrativeResult:
    text: str
    used_llm: bool
    detail: str = ""
    rejected_tokens: list = None

    def to_dict(self) -> dict:
        return {
            "used_llm": self.used_llm,
            "detail": self.detail,
            "rejected_tokens": self.rejected_tokens or [],
        }


def _normalise(token: str) -> str:
    return token.replace(" ", "").replace(",", "").upper()


def allowed_figures(prompt: str) -> set[str]:
    """Every money string the model is permitted to reproduce.

    Defined as "whatever was in its input", extracted from the prompt itself
    rather than rebuilt from the finding. Those two drifting apart is what makes
    a grounding check reject figures it handed the model a moment earlier --
    and a check that cries wolf gets switched off.

    The consequence is that the prompt must *contain* every figure the write-up
    legitimately needs, including aggregates. That is why build_prompt computes
    the top-N subtotal instead of leaving the model to add three numbers up.
    """
    return {_normalise(token) for token in _MONEY_RE.findall(prompt)}


def check_grounding(text: str, prompt: str, finding: dict) -> list[str]:
    """Return any dollar figure or region name that was not in the input.

    Deliberately covers money and place names only -- the two classes where an
    invented value is both plausible-looking and expensive. Percentages and
    counts are left to the prompt's instructions; they are checkable by a
    reader against the figures beside them, and a dollar amount is not.
    """
    allowed_money = allowed_figures(prompt)
    known_regions = {r["Region"].lower() for r in finding["regions"]}

    bad = [
        token.strip()
        for token in _MONEY_RE.findall(text)
        if _normalise(token) not in allowed_money
    ]
    bad += [
        token
        for token in _REGION_RE.findall(text)
        if token.lower() not in known_regions
    ]

    # "West Europe" for westeurope. Reads nicer and is wrong: these are Azure
    # region identifiers, and someone pasting one back into ICM or a Fabric
    # query needs the exact string. Caught by squashing the text and looking
    # for a canonical name that is not present in its canonical form.
    squashed = re.sub(r"[^a-z0-9]", "", text.lower())
    lowered = text.lower()
    bad += [
        f"{region} (written as a display name)"
        for region in known_regions
        if region in squashed and region not in lowered
    ]
    return bad


def build_prompt(finding: dict, recommendations: list[Recommendation]) -> str:
    s = finding["summary"]
    lines = [
        "Findings to write up (all figures are final -- reuse them verbatim):",
        "",
        f"Period end: {s['as_of']}",
        f"Capacity requests reviewed: {s['tickets_total']} across "
        f"{s['regions_total']} regions",
        f"Failed requests: {s['tickets_flagged']} "
        f"({s['delayed_count']} approved past the "
        f"{finding['config']['meaningful_delay_hours']:.0f}h cut-off, "
        f"{s['unfulfilled_count']} never fulfilled)",
        f"Customers affected: {s['customers_affected']}",
        f"ARR of affected customers: {money(s['arr_affected_usd'])}",
        f"Risk-adjusted revenue exposure: {money(s['revenue_exposure_usd'])}",
        "",
        "Ranked regions:",
    ]
    for region in finding["regions"][:5]:
        lines.append(
            f"  {region['Rank']}. {region['Region']}: "
            f"{money(region['RevenueExposureUSD'])} exposure, "
            f"{region['TicketsFlagged']} failed request(s) "
            f"({region['DelayedCount']} delayed, "
            f"{region['UnfulfilledCount']} unfulfilled), "
            f"{region['CustomersAffected']} customer(s), "
            f"{money(region['ARRAffectedUSD'])} ARR affected"
        )

    lines += ["", "Recommended actions (keep these as the actions):"]
    for rec in recommendations:
        lines += [
            f"  {rec.rank}. {rec.region} -- {rec.action}",
            f"     Reason: {rec.rationale}",
        ]
        for evidence in rec.evidence:
            lines.append(f"     Evidence: {evidence}")

    # Pre-computed so the model never has to add anything up. A write-up that
    # leads on "the top three regions account for X" is the natural shape, and
    # this is the difference between it quoting our arithmetic and doing its own.
    if recommendations:
        subtotal = sum(r.revenue_exposure_usd for r in recommendations)
        total = finding["summary"]["revenue_exposure_usd"]
        share = subtotal / total if total else 0.0
        lines += [
            "",
            f"Combined exposure of the {len(recommendations)} region(s) above: "
            f"{money(subtotal)}, which is {share:.0%} of the "
            f"{money(total)} total.",
        ]

    if recommendations and recommendations[0].caveat:
        lines += ["", f"Mandatory caveat to include: {recommendations[0].caveat}"]

    lines += ["", "This finding is pending human review; nothing has been changed."]
    return "\n".join(lines)


def write_narrative(
    finding: dict,
    recommendations: list[Recommendation],
    fallback_text: str,
    config: LLMConfig | None = None,
    enabled: bool = True,
) -> NarrativeResult:
    """LLM wording if it is available and grounded; deterministic text otherwise."""
    if not enabled:
        return NarrativeResult(fallback_text, used_llm=False, detail="LLM disabled.")

    config = config or LLMConfig.from_env()
    prompt = build_prompt(finding, recommendations)
    try:
        text = chat(SYSTEM_PROMPT, prompt, config)
    except LLMUnavailable as exc:
        return NarrativeResult(fallback_text, used_llm=False, detail=str(exc))

    # Checked against the prompt the model actually got, not a reconstruction.
    ungrounded = check_grounding(text, prompt, finding)
    if ungrounded:
        return NarrativeResult(
            fallback_text,
            used_llm=False,
            detail=(
                "Rewrite rejected: figures not present in the computed finding "
                f"({', '.join(sorted(set(ungrounded))[:5])}). Posted the "
                "deterministic text instead."
            ),
            rejected_tokens=sorted(set(ungrounded)),
        )

    return NarrativeResult(text, used_llm=True, detail=config.describe())
