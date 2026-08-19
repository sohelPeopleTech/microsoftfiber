"""End to end, including the safety property that matters most: a failing
classifier blocks publication, so a wrong answer never reaches a reviewer as
though it were a finding.

The web application is the only delivery surface, so "blocked" is a property of
the result rather than of a send that did or did not happen -- there is nothing
to post, and nothing that can fail separately from the run.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from module5 import pipeline, state
from module5.config import Config
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return pipeline.run(WORKBOOK, out_dir=tmp_path_factory.mktemp("out"))


def test_pipeline_runs_and_passes_the_classifier_gate(result):
    assert result.blocked is False
    assert result.finding["classifier_evaluation"]["passed"] is True
    assert result.finding["status"] == "pending_human_review"


def test_writes_every_artefact(tmp_path):
    pipeline.run(WORKBOOK, out_dir=tmp_path)
    for name in (
        "finding.json",
        "finding.md",
        "region_exposure.csv",
        "customer_exposure.csv",
        "tickets_classified.csv",
    ):
        assert (tmp_path / name).exists(), name

    finding = json.loads((tmp_path / "finding.json").read_text())
    assert finding["module"] == "module5_capacity_denial_revenue_impact"
    assert finding["summary"]["tickets_total"] == 60


def test_nothing_is_sent_anywhere(tmp_path):
    """No outbound delivery exists any more -- the run only writes files.

    Asserted rather than assumed: the removal of the Teams path is exactly the
    kind of thing that gets partially reintroduced by a later change.
    """
    res = pipeline.run(WORKBOOK, out_dir=tmp_path)
    assert not hasattr(res, "delivery")
    assert not hasattr(res, "card")
    assert not (tmp_path / "adaptive_card.json").exists()
    assert not (tmp_path / "teams_payload.json").exists()


def test_failing_classifier_blocks_publication(tmp_path):
    """The gate: a wrong classifier must not be presented as a finding."""
    from module5 import ingest

    real = ingest.load_expected_classifications(WORKBOOK)
    corrupted = real.copy()
    corrupted.loc[0, "ExpectedCategory"] = "no_denial"
    corrupted_path = tmp_path / "bad_labels.csv"
    corrupted.to_csv(corrupted_path, index=False)

    res = pipeline.run(WORKBOOK, expected_source=corrupted_path, out_dir=tmp_path)
    assert res.blocked is True
    assert "Publication blocked" in res.blocked_reason
    assert res.finding["status"] == "blocked_classifier_evaluation_failed"

    # Artefacts are still written -- a blocked run has to be debuggable.
    assert (tmp_path / "finding.json").exists()


def test_missing_labels_downgrade_to_a_skip_not_a_crash(tmp_path):
    empty = tmp_path / "no_labels.csv"
    pd.DataFrame({"IncidentId": [], "ExpectedCategory": []}).to_csv(empty, index=False)
    res = pipeline.run(WORKBOOK, expected_source=empty, out_dir=tmp_path)
    assert res.blocked is True  # zero scored rows is not a pass


def test_headline_names_the_top_region(result):
    top = result.regions.iloc[0]["Region"]
    assert top in result.markdown.split("\n")[2]
    assert "Held for human review" in result.markdown


def test_approve_and_reject_are_recorded_with_an_audit_trail(tmp_path):
    """The human-review gate, in the home it now has.

    Approve/Reject used to be card actions. They are recorded through the same
    append-only decisions log whether the click came from the CLI or the web
    application, so the audit trail did not move when the card went away.
    """
    state_dir = tmp_path / "state"
    state.record_decision(state_dir, "2026-01-28", "westeurope", "approve", "ops@x", "")
    state.record_decision(state_dir, "2026-01-28", "uksouth", "reject", "ops@x", "already ordered")

    rows = state.load_decisions(state_dir)
    assert [r["decision"] for r in rows] == ["approve", "reject"]
    assert rows[1]["reason"] == "already ordered"
    assert all(r["by"] == "ops@x" for r in rows)


def test_config_overrides_change_the_output(tmp_path):
    one = pipeline.run(WORKBOOK, config=Config(top_n_regions=1), out_dir=tmp_path)
    assert len(one.recommendations) == 1

    strict = pipeline.run(
        WORKBOOK,
        config=Config(meaningful_delay_hours=1000.0, tier_delay_hours={}),
        out_dir=tmp_path,
        write_outputs=False,
    )
    # A 1000h cut-off forgives almost every delay, so exposure must fall.
    assert (
        strict.finding["summary"]["revenue_exposure_usd"]
        < one.finding["summary"]["revenue_exposure_usd"]
    )


def test_csv_and_xlsx_sources_agree(tmp_path):
    """The CSV extract carries no reference sheets, so it needs them passed in."""
    res = pipeline.run(
        "data/ICM_Data.csv",
        subscription_source=WORKBOOK,
        expected_source=WORKBOOK,
        out_dir=tmp_path,
        write_outputs=False,
    )
    assert res.finding["summary"]["tickets_total"] == 60
