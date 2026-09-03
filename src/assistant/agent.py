"""Snapshot -> answer -> grounding check.

The whole platform state is about 4 KB of JSON. That is small enough to hand to
the model in full, which removes an entire class of failure: there is no
retrieval step to pick the wrong rows, and no question that is "out of scope"
because the matching missed.
"""

from __future__ import annotations

import json
import re

from module5.formatting import money
from module5.llm import LLMConfig, LLMUnavailable, chat

SYSTEM = """You are the assistant inside a Microsoft Fabric capacity operations dashboard. \
The people asking run capacity for Microsoft -- they decide where to add capacity \
and which customers to unblock. They are not the customers themselves, so refer \
to customers in the third person.

THE DATA BELOW IS THE ONLY THING YOU KNOW. Never use outside knowledge about \
Azure, Fabric, or anything else. Never estimate, extrapolate or invent a figure. \
If the answer is not in the data, say exactly what is missing and stop.

HOW TO ANSWER
- Two to four sentences. No preamble, no restating the question, no sign-off.
- Lead with the answer, then the reason. Never the other way round.
- Quote figures exactly as they appear in the data. Do not round differently, \
do not convert, do not sum unless the sum is asked for.
- Region names exactly as written: westeurope, not West Europe.
- A region or capacity pool is either "in risk" or "not in risk", and the amount \
past its line is "the threshold is utilised by X%". Never say breached, \
approaching, overdue or due -- those words were removed from the product and \
the reader will not see them on screen.
- Do not volunteer that figures are generated, placeholder or illustrative. \
The reader already knows this is a pilot running on a sample. Answer the \
question that was asked. If someone asks directly where the data comes from \
or whether it is real, answer plainly and completely from dataCaveats -- \
never deny it and never dress it up.
- No markdown headings, no bullet lists unless comparing three or more things.
- Never mention a field name. Write "the decision window passed 32 days ago", not "daysUntilAction of -32". The reader has never seen the schema.
- The camelCase keys are never words. Say "ARR affected", "revenue exposure", \
"failed requests", "days left to decide" -- never arrAffected, \
exposureUsd, failedRequests, daysUntilAction, whyThisStatus. A leaked key is \
the single most common way this answer reads as machine output.

WHAT THE FIELDS MEAN
- outcomeCategories: the four labels the tickets table prints, each with its \
definition and count. When asked what an outcome means -- "no denial", "same \
day approved", "denied then approved late", "denied unfulfilled" -- answer \
from here. These are on screen, so never reply that the category is not in \
the data.
- exposure: customer revenue at risk from failed capacity requests, in USD. \
Not money lost.
- arrAffected: the whole annual revenue of every affected customer. Blast \
radius, always larger than exposure.
- status: breached = already over the safety line; overdue = the decision \
window has passed; approaching = still time; stable = not heading for a crossing.
- daysUntilAction: days left to decide before the region crosses its line. \
Negative means that point has passed. Scaling itself is immediate -- a Fabric \
capacity is scaled in Azure and takes effect at once -- so this is decision \
time, never delivery time. There is nothing to order and nothing to wait for.
- decisionWindowDays: how long the organisation allows itself to notice, agree \
and act. A policy figure, the same for every region.
- coverage: share of product features live in that region.
- growth: change in requested capacity, in units, not percent.
- customers: identified by the first 8 characters of their subscription id. Use that short form when naming one.
- failedRequests: individual tickets. Each carries its own working-out; quote it when asked how a figure was reached.
- conversionReadiness: whether a region can take hardware offline to convert it. \
canTakeADatacentreOffline is the go/no-go; unitsFreeToTakeOffline is spare capacity \
after a safety margin; unitsPerDatacentre is what one datacentre needs. Say the \
datacentre count is an assumption whenever you use these.

If asked what to do, ground the recommendation in a figure from the data."""


# --------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------


def build_snapshot(entities, m5, flags, growth, coverage, spikes, provenance,
                   customers=None, incidents=None, conversions=None,
                   datacentres=None, cores_pending=None) -> dict:
    """Everything the assistant is allowed to know, in one object."""
    summary = m5.finding["summary"]
    exposure = {r["Region"]: r for r in m5.finding["regions"]}
    growth_by = {r["Region"]: r for r in growth}
    cover_by = {r["Region"]: r for r in coverage}

    datacentres = datacentres or []
    regions = []
    for f in flags:
        name = f["region"]
        e = exposure.get(name, {})
        # Review replaced "breached"/"approaching" on screen with a plain
        # in-risk / not-in-risk state and "threshold utilised by X%". The
        # assistant kept the old vocabulary, so it described a region as
        # "approaching" while the page beside it said "Not in risk".
        line = float(f.get("threshold_pct") or 0)
        util = float(f.get("current_utilisation_pct") or 0)
        at_risk = util > line
        # Counts, pre-computed. Asked how many capacity pools in southcentralus
        # were in risk, the model counted the facility rows itself and answered
        # "seven" against an actual ten. Models read reliably and count badly,
        # so the count is done here and handed over as a number.
        here_sites = [d for d in (datacentres or []) if d.get("region") == name]
        regions.append({
            "region": name,
            "thresholdStatus": "In risk" if at_risk else "Not in risk",
            "dataCentreCount": len(here_sites),
            "dataCentresInRisk": sum(1 for d in here_sites
                                     if d.get("thresholdStatus") == "In risk"),
            "dataCentresWithRequests": sum(1 for d in here_sites if d.get("requests")),
            # Capacity this region still owes. It is on the Regions table and
            # the region page but was never in the snapshot, so asked how many
            # cores were pending the assistant correctly said it could not tell.
            "coresPending": round(float((cores_pending or {}).get(name, 0.0)), 1),
            "thresholdPct": line,
            "thresholdUtilisedByPct": round(util - line, 1) if at_risk else 0.0,
            # The raw module-1 status ("approaching", "breached", "due_now") is
            # deliberately not passed. Telling the model not to use a word while
            # handing it that word in the data loses every time -- it kept
            # answering "the status is approaching" beside a page reading "Not in
            # risk". thresholdStatus above is the vocabulary the product uses.
            "utilisationPct": f["current_utilisation_pct"],
            "daysUntilAction": f["days_until_action"],
            "decisionWindowDays": f["decision_window_days"],
            "whyThisStatus": f["reason"],
            "exposureUsd": round(float(e.get("RevenueExposureUSD", 0)), 2),
            "exposureDisplay": money(float(e.get("RevenueExposureUSD", 0))),
            "failedRequests": int(e.get("TicketsFlagged", 0)),
            "totalRequests": int(e.get("TicketsTotal", 0)),
            "customersAffected": int(e.get("CustomersAffected", 0)),
            "arrAffectedDisplay": money(float(e.get("ARRAffectedUSD", 0))),
            "arrAffectedUsd": round(float(e.get("ARRAffectedUSD", 0)), 2),
            "growthUnits": round(float(growth_by.get(name, {}).get("AbsoluteChange", 0)), 1),
            "featureCoveragePct": round(float(cover_by.get(name, {}).get("CoveragePct", 0)), 1),
            "rank": int(e.get("Rank", 0)),
        })

    # Counted here, not by the model. Asked how many regions were in risk it
    # answered "6" and then listed three, of which one was not in risk at all --
    # the snapshot said "Not in risk" for canadacentral perfectly clearly. A
    # small model tallying eleven rows gets it wrong; reading a number does not.
    in_risk = [r["region"] for r in regions if r["thresholdStatus"] == "In risk"]
    return {
        "asOf": summary["as_of"],
        "datacentres": datacentres or [],
        "regionsInRiskCount": len(in_risk),
        "regionsInRisk": in_risk,
        "regionsNotInRiskCount": len(regions) - len(in_risk),
        "coresPendingTotal": round(sum(float(v) for v in (cores_pending or {}).values()), 1),
        "totals": {
            "exposureDisplay": money(summary["revenue_exposure_usd"]),
            "exposureUsd": summary["revenue_exposure_usd"],
            "arrAffectedDisplay": money(summary["arr_affected_usd"]),
            # The raw value matters as much as the display string: the
            # grounding check reads numbers out of this snapshot, so a figure
            # carried only as "$1.93M" is invisible to it and gets rejected as
            # invented -- while being the number printed on the KPI tile.
            "arrAffectedUsd": summary["arr_affected_usd"],
            "requestsTotal": summary["tickets_total"],
            "requestsFailed": summary["tickets_flagged"],
            "approvedLate": summary["delayed_count"],
            "neverFulfilled": summary["unfulfilled_count"],
            "customersAffected": summary["customers_affected"],
            "regions": summary["regions_total"],
            # The two non-failing outcomes. Without these the assistant knows
            # only how many requests failed, so it cannot account for the other
            # half of the table the reader is looking at.
            "sameDayApproved": summary["same_day_count"],
            "noDenial": summary["no_denial_count"],
        },
        # Every outcome label the interface prints, with what it means. The
        # tickets table shows these words; an assistant that cannot define a
        # word on screen looks broken even when it is being honest -- and it
        # was, replying "there is no category called 'no denial' in the data".
        "outcomeCategories": {
            "no denial": (
                f"The request was never denied -- it was approved on the normal path. "
                f"{summary['no_denial_count']} of {summary['tickets_total']} requests. "
                f"Not a failure, and carries no exposure."
            ),
            "same day approved": (
                f"Denied, then approved inside that customer's tier allowance, so it "
                f"counts as normal turnaround rather than a service failure. "
                f"{summary['same_day_count']} of {summary['tickets_total']} requests. "
                f"Carries no exposure."
            ),
            "denied then approved late": (
                f"Denied, then approved after the tier allowance had passed. The "
                f"customer eventually got the capacity but carried the shortfall "
                f"meanwhile. {summary['delayed_count']} of {summary['tickets_total']} "
                f"requests. Counts as a failure and is priced."
            ),
            "denied unfulfilled": (
                f"Denied and never approved -- the customer still does not have the "
                f"capacity. {summary['unfulfilled_count']} of {summary['tickets_total']} "
                f"requests. Counts as a failure, and the days keep accruing, so these "
                f"carry the largest exposure."
            ),
            # Currently zero, but the classifier can emit it and the table would
            # print it -- so it has to be explainable before that happens, not
            # after somebody asks about a word the assistant has never heard of.
            "data quality error": (
                f"The ticket's dates could not be read as a sequence -- an approval "
                f"before its denial, or a missing date. "
                f"{summary['data_quality_count']} of {summary['tickets_total']} requests. "
                f"Excluded from the impact figures rather than guessed at."
            ),
        },
        "regions": regions,
        # A shared dashboard gets asked "which of these is mine" -- so the
        # assistant has to know customers and individual tickets, not just
        # regional totals. Without these it answers "the data does not include
        # customer identifiers", which is what it did before.
        "customers": customers or [],
        "failedRequests": incidents or [],
        # "Can we convert this region's hardware?" is asked of the dashboard,
        # and the answer turns on capacity free *during* the work -- which is
        # nowhere else in this snapshot.
        "conversionReadiness": conversions or [],
        "spikes": [
            {
                "region": a.region, "period": a.period,
                "value": a.value, "baseline": a.baseline,
                "explained": a.match_strength == "strong",
                "event": a.event_type or None,
                "finding": a.recommendation,
            }
            for a in spikes
        ],
        "howItWorks": {
            "exposureFormula": "customer ARR x share of capacity missing x days without it / 365",
            "failureDefinition": "denied then approved past the tier's SLA, or denied and never approved",
            "slaByTier": "Enterprise 48h, Premium 72h, Standard 96h, Free 96h",
            "orderByRule": "forecast crossing date minus the hardware lead time",
            "conversionRule": (
                "a datacentre can only be taken offline to convert it if the "
                "capacity left running still covers what customers are using"
            ),
        },
        "dataCaveats": [
            "Datacentres per region is an assumption, not a measurement -- the "
            "source data gives a region total only. Every conversion figure "
            "scales with it.",
            "Customer revenue (ARR) is placeholder data, so dollar amounts are "
            "illustrative. Region rankings hold; absolute dollars do not.",
            "Hardware type, usage history, business events and feature availability "
            "are generated, not from ICM.",
            "The ICM extract has no ticket status field, so 'never fulfilled' may "
            "include requests still being worked.",
        ],
        "provenance": [
            {"entity": p["Entity"], "rows": p["Rows"],
             "source": "generated" if p["FullySynthetic"] else "real + generated"}
            for p in provenance
        ],
    }


# --------------------------------------------------------------------------
# grounding
# --------------------------------------------------------------------------

_MONEY = re.compile(r"\$\s?[\d][\d,.]*\s?[MKB]?", re.IGNORECASE)
_INCIDENT = re.compile(r"\b\d{6,}\b")


_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def _money_value(token: str) -> float | None:
    """"$73K" -> 73000.0. Returns None if it will not parse."""
    raw = token.replace("$", "").replace(",", "").strip()
    multiplier = 1.0
    if raw[-1:].upper() in ("K", "M", "B"):
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9}[raw[-1].upper()]
        raw = raw[:-1]
    try:
        return float(raw) * multiplier
    except ValueError:
        return None


def _snapshot_values(snapshot: dict) -> list[float]:
    """Every number the snapshot contains, however it is formatted."""
    blob = json.dumps(snapshot)
    out = []
    for token in _NUMBER.findall(blob):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def _is_grounded_money(token: str, values: list[float]) -> bool:
    """True if the figure matches something in the data once rounding is allowed.

    The model is entitled to write $73,052 for a stored 73052.49, or $76K for
    76308.97. Comparing formatted strings rejects both -- which is a check
    crying wolf at correct answers, and a check nobody trusts gets switched off.
    """
    value = _money_value(token)
    if value is None:
        return True                      # not a figure we can judge
    for candidate in values:
        if candidate == 0:
            if value == 0:
                return True
            continue
        # Tolerance widens with magnitude, because "$76K" is a legitimate way
        # to say 76308.97 but "$76K" is not a legitimate way to say 84000.
        if abs(candidate - value) <= max(1.0, abs(candidate) * 0.01):
            return True
        # Rounded forms, but only at the magnitude they belong to. Allowing
        # a millions-tolerance on a five-figure number let $84,000 pass as a
        # rounding of $76,309.
        if candidate >= 1_000 and abs(round(candidate / 1_000) * 1_000 - value) < 1:
            return True
        if candidate >= 1_000_000 and abs(
            round(candidate / 1_000_000, 2) * 1_000_000 - value
        ) < abs(candidate) * 0.005:
            return True
    return False


def check_grounding(answer: str, snapshot: dict, known_regions: set[str]) -> list[str]:
    """Anything asserted that the snapshot does not contain.

    Money, region names and incident numbers only -- the three that are both
    plausible-sounding and expensive to get wrong. Counts and percentages are
    checkable by a reader against the figures beside them; a dollar amount is
    not.
    """
    blob = json.dumps(snapshot)
    values = _snapshot_values(snapshot)
    allowed_ids = set(_INCIDENT.findall(blob))

    bad = [t.strip() for t in _MONEY.findall(answer)
           if not _is_grounded_money(t, values)]
    bad += [t for t in _INCIDENT.findall(answer) if t not in allowed_ids]

    lowered = answer.lower()
    squashed = re.sub(r"[^a-z0-9]", "", lowered)
    for region in known_regions:
        # "West Europe" for westeurope -- reads nicer, wrong identifier.
        if region in squashed and region not in lowered:
            bad.append(f"{region} (written as a display name)")

    invented = re.compile(r"\b(?:east|west|north|south|central)[a-z0-9]+\b")
    bad += [m for m in invented.findall(lowered) if m not in known_regions]
    return sorted(set(bad))


# --------------------------------------------------------------------------
# ask
# --------------------------------------------------------------------------


def _mentions(question: str, *terms: str) -> bool:
    """Whole-word matching, because substrings lie.

    The bug this exists to prevent was live: `"late" in q` matched
    "calcu-late-d", so every "how is this calculated?" was answered with the
    list of regions whose hardware order is late. Two different questions, one
    wrong answer, and nothing in the output admitted it had guessed.
    """
    return any(re.search(rf"\b{re.escape(t)}", question) for t in terms)


def _deterministic(question: str, snapshot: dict) -> str:
    """Used when the model is unavailable or its answer failed the check.

    In practice this does most of the work -- the Foundry deployment rate-limits
    -- so it is written to answer the questions people actually ask rather than
    to be a placeholder. Order matters: the most specific intent wins, and the
    fallback names what it could not answer instead of quietly returning totals.
    """
    regions = snapshot["regions"]
    t = snapshot["totals"]
    how = snapshot["howItWorks"]
    q = question.lower()
    worst = max(regions, key=lambda r: r["exposureUsd"]) if regions else None
    late = [r for r in regions if (r["daysUntilAction"] or 0) < 0]

    # --- how a number was produced ----------------------------------------
    asks_how = _mentions(q, "how", "why", "explain", "calculat", "formula", "work out",
                         "worked out", "mean", "derive")

    if _mentions(q, "arr", "annual", "blast radius"):
        return (
            f"ARR affected is {t['arrAffectedDisplay']} — the whole annual revenue of every "
            f"customer who hit at least one failed request, counted once each. It is not summed "
            f"per ticket: {t['customersAffected']} distinct customers were affected across "
            f"{t['requestsFailed']} failed requests, so a customer with three bad tickets still "
            f"contributes their ARR once. That is why the customer count is lower than the "
            f"request count.\n\n"
            f"It answers a different question from revenue exposure ({t['exposureDisplay']}). "
            f"ARR affected is blast radius — how much revenue sat behind a failure at all. "
            f"Exposure is risk-adjusted: {how['exposureFormula']}. Conflating the two is how a "
            f"credible estimate becomes a number nobody trusts.\n\n"
        )

    if _mentions(q, "exposure", "at risk", "risk-adjusted") and asks_how:
        return (
            f"Revenue exposure = {how['exposureFormula']}.\n\n"
            f"It is a risk-adjusted severity figure, not a booked loss — Microsoft still billed "
            f"these customers. This period it totals {t['exposureDisplay']} across "
            f"{t['requestsFailed']} failed requests. Every ticket carries its own working-out; "
            f"ask about a specific incident to see the arithmetic for it."
        )

    if _mentions(q, "customer") and _mentions(q, "count", "many", "why", "15", "number"):
        return (
            f"{t['customersAffected']} distinct customers, across {t['requestsFailed']} failed "
            f"requests — the two differ because one customer can raise several requests, and "
            f"customers are de-duplicated by subscription before counting. The requests are "
            f"tickets; the customers are who was affected."
        )

    # Any outcome label printed in the tickets table, however the reader spells
    # it. Checked before the general "how does failure work" branch, because
    # "what does denied unfulfilled mean" is a question about one label.
    outcomes = snapshot.get("outcomeCategories") or {}
    for label, meaning in outcomes.items():
        if label in q or label.replace(" ", "_") in q:
            return f"“{label}” — {meaning}"

    if _mentions(q, "outcome", "categor", "label", "status column") and outcomes:
        lines = "\n".join(f"• {label}: {meaning}" for label, meaning in outcomes.items())
        return f"Every request lands in exactly one of four outcomes:\n{lines}"

    if _mentions(q, "fail", "flagged") and asks_how:
        return (f"A request counts as failed when it was {how['failureDefinition']}. "
                f"The allowance varies by tier: {how['slaByTier']}. "
                f"{t['requestsFailed']} of {t['requestsTotal']} requests failed this period "
                f"({t['approvedLate']} approved late, {t['neverFulfilled']} never fulfilled).")

    if _mentions(q, "order", "lead time", "provision") and asks_how:
        return (f"Order-by date = {how['orderByRule']}. That is why a region at lower "
                f"utilisation can outrank a fuller one — slower hardware has to be ordered "
                f"sooner. {len(late)} region(s) have already passed that date.")

    if _mentions(q, "real", "generated", "synthetic", "placeholder", "provenance", "trust"):
        return "Data caveats:\n" + "\n".join(f"• {c}" for c in snapshot["dataCaveats"])

    # --- which thing ------------------------------------------------------
    if _mentions(q, "worst", "highest", "most exposure", "priorit", "fix first") and worst:
        return (f"{worst['region']} — {worst['exposureDisplay']} of revenue exposure across "
                f"{worst['failedRequests']} failed requests. {worst['whyThisStatus']}")

    if _mentions(q, "late", "overdue", "urgent", "due now", "decision window") and late:
        names = ", ".join(f"{r['region']} ({abs(r['daysUntilAction']):.0f}d ago)"
                          for r in late)
        return (f"{len(late)} region(s) are past the point where the decision "
                f"should have been made: {names}. Scaling itself is immediate, "
                f"so each can still be fixed today.")

    named = [r for r in regions if r["region"] in q]
    if named:
        r = named[0]
        # thresholdStatus, not the raw module-1 status: that field was removed
        # from the snapshot so the model would stop saying "approaching" beside a
        # page reading "Not in risk", and this fallback still read it -- so every
        # question that fell back raised a KeyError instead of answering.
        return (f"{r['region']}: {r['thresholdStatus'].lower()}, "
                f"{r['utilisationPct']}% utilised against a "
                f"{r['thresholdPct']}% threshold. "
                f"{r['exposureDisplay']} exposure across {r['failedRequests']} failed "
                f"requests. {r['whyThisStatus']}")

    if _mentions(q, "summary", "overall", "total", "overview", "situation", "status"):
        return (f"As of {snapshot['asOf']}: {t['requestsFailed']} of {t['requestsTotal']} "
                f"requests failed, {t['exposureDisplay']} of revenue exposure across "
                f"{t['customersAffected']} customers and {t['regions']} regions.")

    # Saying "I did not understand that" beats answering a different question
    # confidently -- which is exactly what the substring bug used to do.
    return (
        "I could not match that to something I can answer from this period's data. "
        "I can explain how exposure, ARR affected or the order-by date are calculated; "
        "rank the regions; describe any named region; look up an incident; or say which "
        "figures are real and which are generated.\n\n"
        f"For context — as of {snapshot['asOf']}: {t['requestsFailed']} of "
        f"{t['requestsTotal']} requests failed, {t['exposureDisplay']} exposure across "
        f"{t['customersAffected']} customers."
    )


def ask(
    question: str,
    snapshot: dict,
    history: list[dict] | None = None,
    llm_config: LLMConfig | None = None,
) -> dict:
    """Answer one question. Returns the answer plus how it was produced."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Ask me anything about this period's capacity findings.",
                "source": "empty", "grounded": True, "rejected": []}

    known = {r["region"] for r in snapshot["regions"]}

    turns = []
    for turn in (history or [])[-6:]:          # enough for follow-ups, not a novel
        role = "assistant" if turn.get("role") == "assistant" else "user"
        turns.append(f"{role.upper()}: {turn.get('content', '')}")
    conversation = ("\n".join(turns) + "\n") if turns else ""

    prompt = (
        f"DATA (current dashboard state):\n{json.dumps(snapshot, separators=(',', ':'))}\n\n"
        f"{'CONVERSATION SO FAR:' + chr(10) + conversation if conversation else ''}"
        f"QUESTION: {question}"
    )

    try:
        answer = chat(SYSTEM, prompt, llm_config)
    except LLMUnavailable as exc:
        return {"answer": _deterministic(question, snapshot), "source": "fallback",
                "grounded": True, "rejected": [], "detail": str(exc)}

    rejected = check_grounding(answer, snapshot, known)
    if rejected:
        return {"answer": _deterministic(question, snapshot), "source": "fallback",
                "grounded": False, "rejected": rejected,
                "detail": "answer contained figures not present in the data"}

    return {"answer": answer.strip(), "source": "model", "grounded": True, "rejected": []}
