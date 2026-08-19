"""Run history and human decisions -- the memory that closes the loop.

Without this the agent has no past. It cannot say "this is new", cannot notice
that a region it flagged last week is fixed, and cannot tell that somebody
already approved the recommendation it is about to repeat. Every Monday looks
like the first Monday.

Two append-only JSON Lines files, kept deliberately dumb so they work
identically on a laptop and on OneLake Files, and so a human can read them:

    state/run_history.jsonl   one line per run   -- what we found, and when
    state/decisions.jsonl     one line per click -- what a person decided

Append-only matters. A decision is a record of what someone chose at a point in
time; rewriting it would erase the audit trail that makes the approve step
worth anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HISTORY_FILE = "run_history.jsonl"
DECISIONS_FILE = "decisions.jsonl"

# Movement smaller than this is noise -- an unfulfilled ticket accrues a little
# more exposure every day, so exact equality between runs never happens.
MATERIAL_CHANGE = 0.05

STATUS_NEW = "new"
STATUS_RESOLVED = "resolved"
STATUS_WORSE = "worse"
STATUS_BETTER = "better"
STATUS_UNCHANGED = "unchanged"

APPROVE = "approve"
REJECT = "reject"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # One malformed line must not cost us the whole history.
            continue
    return rows


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


# --------------------------------------------------------------------------
# run history
# --------------------------------------------------------------------------


def load_history(state_dir: str | Path) -> list[dict]:
    return _read(Path(state_dir) / HISTORY_FILE)


def record_run(state_dir: str | Path, finding: dict, run_at: str | None = None) -> dict:
    """Snapshot this run: the totals plus exposure per region."""
    row = {
        "run_at": run_at or utc_now(),
        "as_of": finding.get("as_of"),
        "tickets_total": finding["summary"]["tickets_total"],
        "tickets_flagged": finding["summary"]["tickets_flagged"],
        "revenue_exposure_usd": finding["summary"]["revenue_exposure_usd"],
        "regions": {
            r["Region"]: {
                "exposure": r["RevenueExposureUSD"],
                "flagged": r["TicketsFlagged"],
            }
            for r in finding["regions"]
        },
    }
    _append(Path(state_dir) / HISTORY_FILE, row)
    return row


def previous_run(history: list[dict], before_run_at: str | None = None) -> dict | None:
    """The most recent prior run. `before_run_at` excludes the current one."""
    rows = [r for r in history if not before_run_at or r.get("run_at") < before_run_at]
    return rows[-1] if rows else None


def compare_runs(previous: dict | None, finding: dict) -> dict:
    """What changed since last time, per region and in total."""
    if previous is None:
        return {
            "first_run": True,
            "summary": "First run — no previous period to compare against.",
            "regions": [],
            "exposure_delta_usd": 0.0,
        }

    prev_regions = previous.get("regions", {})
    changes = []
    for r in finding["regions"]:
        name = r["Region"]
        now = float(r["RevenueExposureUSD"])
        before = float(prev_regions.get(name, {}).get("exposure", 0.0))
        delta = round(now - before, 2)

        if before == 0 and now > 0:
            status = STATUS_NEW
        elif before > 0 and now == 0:
            status = STATUS_RESOLVED
        elif before > 0 and abs(delta) / before > MATERIAL_CHANGE:
            status = STATUS_WORSE if delta > 0 else STATUS_BETTER
        else:
            status = STATUS_UNCHANGED

        if status != STATUS_UNCHANGED or now > 0:
            changes.append(
                {
                    "region": name,
                    "status": status,
                    "before_usd": round(before, 2),
                    "now_usd": round(now, 2),
                    "delta_usd": delta,
                }
            )

    # Regions that had exposure and have dropped out of the frame entirely.
    seen = {r["Region"] for r in finding["regions"]}
    for name, prev in prev_regions.items():
        if name not in seen and float(prev.get("exposure", 0)) > 0:
            changes.append(
                {
                    "region": name,
                    "status": STATUS_RESOLVED,
                    "before_usd": round(float(prev["exposure"]), 2),
                    "now_usd": 0.0,
                    "delta_usd": -round(float(prev["exposure"]), 2),
                }
            )

    total_delta = round(
        finding["summary"]["revenue_exposure_usd"]
        - float(previous.get("revenue_exposure_usd", 0.0)),
        2,
    )
    return {
        "first_run": False,
        "previous_run_at": previous.get("run_at"),
        "previous_exposure_usd": previous.get("revenue_exposure_usd"),
        "exposure_delta_usd": total_delta,
        "summary": _change_sentence(total_delta, changes),
        "regions": changes,
    }


def _change_sentence(total_delta: float, changes: list[dict]) -> str:
    from .formatting import money

    new = [c["region"] for c in changes if c["status"] == STATUS_NEW]
    gone = [c["region"] for c in changes if c["status"] == STATUS_RESOLVED]
    worse = [c["region"] for c in changes if c["status"] == STATUS_WORSE]

    parts = []
    if total_delta > 0:
        parts.append(f"Exposure is up {money(abs(total_delta))} since the last run")
    elif total_delta < 0:
        parts.append(f"Exposure is down {money(abs(total_delta))} since the last run")
    else:
        parts.append("Exposure is unchanged since the last run")

    if new:
        parts.append(f"newly affected: {', '.join(new)}")
    if gone:
        parts.append(f"now clear: {', '.join(gone)}")
    if worse:
        parts.append(f"worsening: {', '.join(worse)}")
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


@dataclass
class Decision:
    as_of: str
    region: str
    decision: str
    by: str
    reason: str = ""
    at: str = ""

    def to_dict(self) -> dict:
        return {
            "at": self.at or utc_now(),
            "as_of": self.as_of,
            "region": self.region,
            "decision": self.decision,
            "by": self.by,
            "reason": self.reason,
        }


def record_decision(
    state_dir: str | Path,
    as_of: str,
    region: str,
    decision: str,
    by: str,
    reason: str = "",
) -> dict:
    """Write one human decision. Append-only: nothing is ever overwritten."""
    decision = decision.strip().lower()
    if decision not in (APPROVE, REJECT):
        raise ValueError(f"decision must be '{APPROVE}' or '{REJECT}', got {decision!r}")
    if not by:
        raise ValueError("a decision needs an owner -- pass who made it")

    row = Decision(as_of=as_of, region=region, decision=decision, by=by, reason=reason)
    _append(Path(state_dir) / DECISIONS_FILE, row.to_dict())
    return row.to_dict()


def load_decisions(state_dir: str | Path) -> list[dict]:
    return _read(Path(state_dir) / DECISIONS_FILE)


def latest_decisions(decisions: list[dict]) -> dict:
    """Most recent decision per region. Later entries supersede earlier ones."""
    out: dict[str, dict] = {}
    for row in sorted(decisions, key=lambda r: r.get("at", "")):
        region = row.get("region")
        if region:
            out[region] = row
    return out


# --------------------------------------------------------------------------
# what a decision means for the NEXT run
# --------------------------------------------------------------------------

#: A rejected region comes back only if it gets materially worse. Below this it
#: stays quiet -- someone already looked and said no, and repeating it is how a
#: channel learns to ignore the agent.
REOPEN_GROWTH = 0.25

#: Cycles a finding may go unanswered before it is escalated. Two weeks of
#: silence on a live revenue risk is itself the finding.
ESCALATE_AFTER = 2

ALERT_NEW = "new"
ALERT_PENDING = "pending"
ALERT_ESCALATE = "escalate"
ALERT_REOPENED = "reopened"
ALERT_SUPPRESSED = "suppressed"
ALERT_RESOLVED = "resolved"


def alert_state(
    region: str,
    exposure_now: float,
    exposure_before: float,
    decision: dict | None,
    unanswered_cycles: int = 0,
) -> dict:
    """Decide how -- or whether -- to raise a region again.

    This is the rule set that turns a repeating notification into a workflow.
    Without it every Monday reposts the same finding at a slightly larger
    number, and the channel stops reading it.
    """
    # Fixed. The strongest possible signal, and worth saying out loud.
    if exposure_before > 0 and exposure_now == 0:
        return {
            "region": region,
            "state": ALERT_RESOLVED,
            "show": True,
            "note": f"Resolved — exposure was {_m(exposure_before)}, now zero.",
        }

    if decision and decision.get("decision") == APPROVE:
        # Approved but still exposed: not new, not silent. It is a promise
        # somebody made that has not landed yet, so it stays visible with its
        # age rather than being re-raised as a discovery.
        return {
            "region": region,
            "state": ALERT_PENDING,
            "show": True,
            "note": (
                f"Approved by {decision.get('by','someone')} on "
                f"{str(decision.get('at',''))[:10]} — action still pending, "
                f"{_m(exposure_now)} still exposed."
            ),
        }

    if decision and decision.get("decision") == REJECT:
        grew = exposure_before > 0 and (
            (exposure_now - exposure_before) / exposure_before > REOPEN_GROWTH
        )
        if grew:
            return {
                "region": region,
                "state": ALERT_REOPENED,
                "show": True,
                "note": (
                    f"Re-opened — rejected on {str(decision.get('at',''))[:10]} "
                    f"({decision.get('reason') or 'no reason given'}), but exposure "
                    f"has since grown from {_m(exposure_before)} to {_m(exposure_now)}."
                ),
            }
        return {
            "region": region,
            "state": ALERT_SUPPRESSED,
            "show": False,
            "note": (
                f"Suppressed — rejected by {decision.get('by','someone')}: "
                f"{decision.get('reason') or 'no reason given'}."
            ),
        }

    if unanswered_cycles >= ESCALATE_AFTER:
        return {
            "region": region,
            "state": ALERT_ESCALATE,
            "show": True,
            "note": (
                f"No decision after {unanswered_cycles} cycles — "
                f"{_m(exposure_now)} has been exposed and unaddressed since."
            ),
        }

    return {"region": region, "state": ALERT_NEW, "show": True, "note": ""}


def unanswered_cycles(history: list[dict], decisions: list[dict], region: str) -> int:
    """How many runs have reported this region since anyone last ruled on it."""
    ruled_at = ""
    for row in decisions:
        if row.get("region") == region and row.get("at", "") > ruled_at:
            ruled_at = row.get("at", "")
    return sum(
        1
        for run in history
        if run.get("run_at", "") > ruled_at
        and float(run.get("regions", {}).get(region, {}).get("exposure", 0)) > 0
    )


def _m(value: float) -> str:
    from .formatting import money

    return money(value)


def decision_note(latest: dict | None) -> str:
    """One line for the card, so a repeat finding says who already saw it."""
    if not latest:
        return ""
    when = str(latest.get("at", ""))[:10]
    who = latest.get("by", "unknown")
    if latest.get("decision") == APPROVE:
        return f"Approved by {who} on {when} — action pending, still exposed."
    reason = latest.get("reason") or "no reason given"
    return f"Rejected by {who} on {when} — {reason}"
