"""Run history and decisions -- the memory that stops the agent repeating itself."""

from __future__ import annotations

import pytest

from module5 import pipeline, state
from tests.conftest import WORKBOOK


def _finding(exposure: float, regions: dict, as_of="2026-01-28") -> dict:
    return {
        "as_of": as_of,
        "summary": {
            "tickets_total": 60,
            "tickets_flagged": len(regions),
            "revenue_exposure_usd": exposure,
        },
        "regions": [
            {"Region": r, "RevenueExposureUSD": v, "TicketsFlagged": 1}
            for r, v in regions.items()
        ],
    }


# --- history -------------------------------------------------------------


def test_first_run_says_so(tmp_path):
    changes = state.compare_runs(None, _finding(100.0, {"eastus2": 100.0}))
    assert changes["first_run"] is True
    assert "no previous period" in changes["summary"].lower()


def test_history_survives_a_round_trip(tmp_path):
    f = _finding(1000.0, {"eastus2": 600.0, "uksouth": 400.0})
    state.record_run(tmp_path, f, run_at="2026-01-01T00:00:00+00:00")
    history = state.load_history(tmp_path)
    assert len(history) == 1
    assert history[0]["regions"]["eastus2"]["exposure"] == 600.0


@pytest.mark.parametrize(
    "before, now, expected",
    [
        (0.0, 500.0, state.STATUS_NEW),
        (500.0, 0.0, state.STATUS_RESOLVED),
        (500.0, 900.0, state.STATUS_WORSE),
        (500.0, 100.0, state.STATUS_BETTER),
        (500.0, 505.0, state.STATUS_UNCHANGED),  # 1% drift is not news
    ],
)
def test_change_status(tmp_path, before, now, expected):
    state.record_run(tmp_path, _finding(before, {"eastus2": before}),
                     run_at="2026-01-01T00:00:00+00:00")
    prev = state.previous_run(state.load_history(tmp_path))
    changes = state.compare_runs(prev, _finding(now, {"eastus2": now}))
    assert changes["regions"][0]["status"] == expected


def test_a_region_that_drops_out_entirely_is_reported_resolved(tmp_path):
    state.record_run(tmp_path, _finding(500.0, {"uksouth": 500.0}),
                     run_at="2026-01-01T00:00:00+00:00")
    prev = state.previous_run(state.load_history(tmp_path))
    changes = state.compare_runs(prev, _finding(0.0, {}))
    assert [c["region"] for c in changes["regions"]] == ["uksouth"]
    assert changes["regions"][0]["status"] == state.STATUS_RESOLVED


def test_total_delta_and_sentence(tmp_path):
    state.record_run(tmp_path, _finding(1000.0, {"eastus2": 1000.0}),
                     run_at="2026-01-01T00:00:00+00:00")
    prev = state.previous_run(state.load_history(tmp_path))
    changes = state.compare_runs(prev, _finding(1500.0, {"eastus2": 1500.0}))
    assert changes["exposure_delta_usd"] == 500.0
    assert "up" in changes["summary"]


def test_corrupt_line_does_not_lose_the_history(tmp_path):
    state.record_run(tmp_path, _finding(100.0, {"eastus2": 100.0}),
                     run_at="2026-01-01T00:00:00+00:00")
    (tmp_path / state.HISTORY_FILE).open("a").write("{not json\n")
    state.record_run(tmp_path, _finding(200.0, {"eastus2": 200.0}),
                     run_at="2026-01-02T00:00:00+00:00")
    assert len(state.load_history(tmp_path)) == 2


# --- decisions -----------------------------------------------------------


def test_decision_round_trip(tmp_path):
    state.record_decision(tmp_path, "2026-01-28", "westeurope", "approve", "yaswanth")
    latest = state.latest_decisions(state.load_decisions(tmp_path))
    assert latest["westeurope"]["decision"] == "approve"
    assert latest["westeurope"]["by"] == "yaswanth"


def test_later_decision_supersedes_earlier(tmp_path):
    state.record_decision(tmp_path, "2026-01-28", "westeurope", "approve", "a",
                          )
    state.record_decision(tmp_path, "2026-01-28", "westeurope", "reject", "b",
                          reason="threshold already raised")
    latest = state.latest_decisions(state.load_decisions(tmp_path))
    assert latest["westeurope"]["decision"] == "reject"
    assert latest["westeurope"]["by"] == "b"
    # ...but the original is still on record. Append-only is the point.
    assert len(state.load_decisions(tmp_path)) == 2


def test_a_decision_needs_an_owner(tmp_path):
    with pytest.raises(ValueError, match="owner"):
        state.record_decision(tmp_path, "2026-01-28", "westeurope", "approve", "")


def test_only_approve_or_reject(tmp_path):
    with pytest.raises(ValueError, match="approve"):
        state.record_decision(tmp_path, "2026-01-28", "westeurope", "maybe", "a")


def test_decision_note_reads_for_a_human(tmp_path):
    state.record_decision(tmp_path, "2026-01-28", "westeurope", "approve", "yaswanth")
    latest = state.latest_decisions(state.load_decisions(tmp_path))
    note = state.decision_note(latest["westeurope"])
    assert "Approved by yaswanth" in note and "action pending" in note

    state.record_decision(tmp_path, "2026-01-28", "uksouth", "reject", "sashi",
                          reason="already being raised")
    latest = state.latest_decisions(state.load_decisions(tmp_path))
    assert "already being raised" in state.decision_note(latest["uksouth"])


def test_no_decision_is_a_blank_not_a_placeholder():
    assert state.decision_note(None) == ""


# --- end to end ----------------------------------------------------------


def test_second_run_reports_change_and_carries_the_decision(tmp_path):
    first = pipeline.run(WORKBOOK, out_dir=tmp_path)
    assert first.finding["changes"]["first_run"] is True

    state.record_decision(tmp_path / "state", first.finding["as_of"],
                          "westeurope", "approve", "yaswanth")

    second = pipeline.run(WORKBOOK, out_dir=tmp_path)
    assert second.finding["changes"]["first_run"] is False
    # Same extract, so nothing moved -- and the run says so rather than
    # inventing a change.
    assert second.finding["changes"]["exposure_delta_usd"] == 0.0

    note = next(r.decision_note for r in second.recommendations
                if r.region == "westeurope")
    assert "Approved by yaswanth" in note

    # The decision attaches to the region it belongs to, and nowhere else.
    notes = {r.region: r.decision_note for r in second.recommendations}
    assert "Approved by yaswanth" in notes["westeurope"]
    assert not notes.get("eastus2")


# --- what a decision means for the next run --------------------------------


def _prev(exposure):
    return {"regions": {"westeurope": {"exposure": exposure}}}


def test_resolved_beats_everything():
    """Fixed is the strongest signal -- report it even if it was rejected."""
    for decision in (None,
                     {"decision": "approve", "by": "a"},
                     {"decision": "reject", "by": "a", "reason": "no"}):
        s = state.alert_state("westeurope", 0.0, 5000.0, decision)
        assert s["state"] == state.ALERT_RESOLVED and s["show"] is True


def test_approved_but_unfixed_shows_as_pending_not_new():
    s = state.alert_state("westeurope", 5000.0, 5000.0,
                          {"decision": "approve", "by": "yaswanth", "at": "2026-08-12"})
    assert s["state"] == state.ALERT_PENDING
    assert s["show"] is True
    assert "still pending" in s["note"]


def test_rejected_stays_quiet():
    s = state.alert_state("westeurope", 5200.0, 5000.0,
                          {"decision": "reject", "by": "sashi", "reason": "already raised"})
    assert s["state"] == state.ALERT_SUPPRESSED
    assert s["show"] is False


def test_rejected_reopens_when_it_gets_materially_worse():
    """A no is not forever -- 30% growth overrides it."""
    s = state.alert_state("westeurope", 6600.0, 5000.0,
                          {"decision": "reject", "by": "sashi", "reason": "already raised"})
    assert s["state"] == state.ALERT_REOPENED
    assert s["show"] is True
    assert "grown" in s["note"]


def test_silence_escalates():
    s = state.alert_state("westeurope", 5000.0, 5000.0, None, unanswered_cycles=2)
    assert s["state"] == state.ALERT_ESCALATE
    assert "No decision after 2 cycles" in s["note"]


def test_first_sighting_is_plain_new():
    s = state.alert_state("westeurope", 5000.0, 0.0, None, unanswered_cycles=0)
    assert s["state"] == state.ALERT_NEW
    assert s["note"] == ""


def test_unanswered_cycles_counts_runs_since_the_last_decision():
    history = [
        {"run_at": "2026-01-01T00:00:00", "regions": {"westeurope": {"exposure": 100}}},
        {"run_at": "2026-01-08T00:00:00", "regions": {"westeurope": {"exposure": 100}}},
        {"run_at": "2026-01-15T00:00:00", "regions": {"westeurope": {"exposure": 100}}},
    ]
    assert state.unanswered_cycles(history, [], "westeurope") == 3

    decisions = [{"region": "westeurope", "at": "2026-01-09T00:00:00"}]
    assert state.unanswered_cycles(history, decisions, "westeurope") == 1

    # A region with no exposure in a run does not count as ignored.
    quiet = [{"run_at": "2026-02-01T00:00:00", "regions": {"westeurope": {"exposure": 0}}}]
    assert state.unanswered_cycles(quiet, [], "westeurope") == 0


def test_a_suppressed_region_leaves_the_recommendations_and_the_json(tmp_path):
    """The finding JSON and the written finding must agree on what was reported."""
    first = pipeline.run(WORKBOOK, out_dir=tmp_path)
    dropped = first.recommendations[-1].region
    state.record_decision(tmp_path / "state", first.finding["as_of"], dropped,
                          "reject", "sashi", reason="already in flight")

    second = pipeline.run(WORKBOOK, out_dir=tmp_path)
    assert dropped not in [r.region for r in second.recommendations]
    assert dropped not in [r["region"] for r in second.finding["recommendations"]]
    assert f". {dropped} --" not in second.markdown
    # ...but it is still in the ranking data, not deleted from the analysis.
    assert dropped in list(second.regions["Region"])
    assert second.finding["alert_states"][dropped]["state"] == state.ALERT_SUPPRESSED
