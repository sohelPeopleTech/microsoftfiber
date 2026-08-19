"""Step 2 of the build plan: the classifier must reproduce the pre-labelled
sample exactly before anything downstream is trusted."""

from __future__ import annotations

import pandas as pd
import pytest

from module5.classifier import (
    DATA_QUALITY_ERROR,
    DENIED_THEN_APPROVED_LATE,
    DENIED_UNFULFILLED,
    NO_DENIAL,
    SAME_DAY_APPROVED,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    classify,
    classify_ticket,
    evaluate_against_labels,
    severity_for,
)
from module5.config import Config

DENIED = pd.Timestamp("2025-09-03T00:00:00Z")


def test_matches_labelled_sample_exactly(classified, expected_labels):
    result = evaluate_against_labels(classified, expected_labels)
    assert result["passed"], result["mismatches"]
    assert result["n_scored"] == 60
    assert result["accuracy"] == 1.0
    assert result["tickets_without_label"] == []
    assert result["labels_without_ticket"] == []


def test_every_labelled_category_is_represented(classified, expected_labels):
    """A 100% score is only meaningful if all four outcomes are exercised."""
    result = evaluate_against_labels(classified, expected_labels)
    assert set(result["per_category"]) == {
        NO_DENIAL,
        SAME_DAY_APPROVED,
        DENIED_THEN_APPROVED_LATE,
        DENIED_UNFULFILLED,
    }
    assert all(v["n"] > 0 for v in result["per_category"].values())


@pytest.mark.parametrize(
    "approved, expected",
    [
        (None, DENIED_UNFULFILLED),
        (DENIED + pd.Timedelta(hours=1), SAME_DAY_APPROVED),
        (DENIED + pd.Timedelta(hours=48), SAME_DAY_APPROVED),  # boundary: inclusive
        (DENIED + pd.Timedelta(hours=48, seconds=1), DENIED_THEN_APPROVED_LATE),
        (DENIED - pd.Timedelta(hours=1), DATA_QUALITY_ERROR),  # inverted dates
    ],
)
def test_cutoff_boundary(approved, expected):
    category, _ = classify_ticket(DENIED, approved, 48.0)
    assert category == expected


def test_no_denial_and_missing_everything():
    assert classify_ticket(None, DENIED, 48.0)[0] == NO_DENIAL
    assert classify_ticket(None, None, 48.0)[0] == DATA_QUALITY_ERROR


def test_delay_hours_reported():
    category, delay = classify_ticket(DENIED, DENIED + pd.Timedelta(days=6), 48.0)
    assert category == DENIED_THEN_APPROVED_LATE
    assert delay == pytest.approx(144.0)


def test_labelled_sample_is_insensitive_across_the_safe_cutoff_band(
    gold, expected_labels
):
    """Calibration claim in config.py: any cut-off in [30, 145) hours
    reproduces the ground truth. If that stops holding, the note is wrong."""
    for hours in (30.0, 48.0, 72.0, 144.0):
        result = evaluate_against_labels(
            classify(gold[0], Config(meaningful_delay_hours=hours)), expected_labels
        )
        assert result["passed"], f"cut-off {hours}h: {result['mismatches']}"


def test_cutoff_outside_the_safe_band_does_change_the_answer(gold, expected_labels):
    """Guards against a classifier that ignores the cut-off entirely."""
    savage = Config(meaningful_delay_hours=1.0, tier_delay_hours={})
    result = evaluate_against_labels(classify(gold[0], savage), expected_labels)
    assert not result["passed"]


def test_tier_cutoff_overrides_the_global_one(gold, expected_labels):
    """The footgun: with tier_delay_hours populated, editing
    meaningful_delay_hours alone changes nothing for a known tier."""
    config = Config(meaningful_delay_hours=1.0)  # tier dict left at defaults
    assert config.cutoff_for("Enterprise") == 48.0        # tier wins
    assert config.cutoff_for("Unlisted") == 1.0           # global is the fallback
    assert config.cutoff_for(None) == 1.0
    # ...and the labelled sample still passes, because the tiers are unchanged.
    assert evaluate_against_labels(classify(gold[0], config), expected_labels)["passed"]


def test_tiered_cutoffs_reproduce_the_labels(gold, expected_labels):
    """Every default sits inside its tier's safe band, so the gate still passes."""
    assert evaluate_against_labels(classify(gold[0], Config()), expected_labels)["passed"]


def test_only_failures_are_flagged(classified):
    flagged = set(classified.loc[classified["IsFlagged"], "Category"])
    assert flagged <= {DENIED_THEN_APPROVED_LATE, DENIED_UNFULFILLED}


def test_severity_bands():
    config = Config()
    assert severity_for(DENIED_UNFULFILLED, None, config) == SEVERITY_CRITICAL
    assert severity_for(DENIED_THEN_APPROVED_LATE, 3.0, config) == SEVERITY_LOW
    assert severity_for(DENIED_THEN_APPROVED_LATE, 7.0, config) == SEVERITY_MEDIUM
    assert severity_for(DENIED_THEN_APPROVED_LATE, 30.0, config) == SEVERITY_HIGH
