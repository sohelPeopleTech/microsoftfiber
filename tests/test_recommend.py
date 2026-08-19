"""Recommendation content -- step 5, plus the in-app follow-up answer."""

from __future__ import annotations

import pandas as pd
import pytest

from module5 import aggregate, recommend
from module5.classifier import classify
from module5.config import Config
from module5.recommend import (
    MODE_DELAY,
    MODE_MIXED,
    MODE_UNFULFILLED,
    classify_failure_mode,
    suggest_threshold,
    why_ranked,
)
from module5.revenue import estimate_impact
from tests.conftest import make_ticket


def test_one_recommendation_per_top_region(regions, priced):
    config = Config(top_n_regions=3)
    recs = recommend.recommend(regions, priced, config)
    assert len(recs) == 3
    assert [r.region for r in recs] == regions["Region"].head(3).tolist()
    assert [r.rank for r in recs] == [1, 2, 3]


def test_recommendations_never_cover_a_clean_region(regions, priced, config):
    recs = recommend.recommend(regions, priced, config)
    clean = set(regions.loc[regions["TicketsFlagged"] == 0, "Region"])
    assert not clean & {r.region for r in recs}


def test_every_recommendation_names_an_action_and_a_reason(regions, priced, config):
    for rec in recommend.recommend(regions, priced, config):
        assert rec.region in rec.action
        assert len(rec.rationale) > 40
        assert rec.evidence, "a recommendation with no named ticket is unauditable"
        assert rec.requires_human_review is True


def test_placeholder_arr_forces_the_caveat(regions, priced):
    recs = recommend.recommend(regions, priced, Config(arr_reference_is_placeholder=True))
    assert all("illustrative" in r.caveat for r in recs)

    real = recommend.recommend(
        regions, priced, Config(arr_reference_is_placeholder=False)
    )
    assert all(r.caveat == "" for r in real)


@pytest.mark.parametrize(
    "delayed, unfulfilled, expected",
    [
        (0, 3, MODE_UNFULFILLED),
        (3, 0, MODE_DELAY),
        (1, 4, MODE_UNFULFILLED),
        (4, 1, MODE_DELAY),
        (2, 2, MODE_MIXED),
    ],
)
def test_failure_mode_picks_the_right_lever(delayed, unfulfilled, expected):
    row = pd.Series({"DelayedCount": delayed, "UnfulfilledCount": unfulfilled})
    assert classify_failure_mode(row) == expected


def test_threshold_lands_on_an_observed_capacity_rung():
    """A recommended ceiling has to be a size someone can actually provision."""
    tickets = pd.concat(
        [
            make_ticket(IncidentId="1", CurrentLimitCapacity=4, AdditionalLimitCapacity=4,
                        NewLimitCapacity=8),
            make_ticket(IncidentId="2", CurrentLimitCapacity=8, AdditionalLimitCapacity=8,
                        NewLimitCapacity=16),
            make_ticket(IncidentId="3", CurrentLimitCapacity=64,
                        AdditionalLimitCapacity=64, NewLimitCapacity=128),
            make_ticket(IncidentId="4", CurrentLimitCapacity=256,
                        AdditionalLimitCapacity=256, NewLimitCapacity=512),
        ],
        ignore_index=True,
    )
    priced = estimate_impact(classify(tickets))
    threshold, coverage = suggest_threshold(priced, "eastus2", coverage_target=0.75)
    assert threshold == 128.0  # covers 3 of 4; 512 would be over-provisioning
    assert coverage == pytest.approx(0.75)


def test_threshold_is_none_when_nothing_failed():
    clean = make_ticket(ApprovedDate=pd.Timestamp("2025-01-01T06:00:00Z"))
    priced = estimate_impact(classify(clean))
    assert suggest_threshold(priced, "eastus2") == (None, None)


def test_why_ranked_answers_the_docs_example(regions):
    answer = why_ranked("uksouth", regions)
    assert "uksouth" in answer
    assert "#" in answer and "exposure" in answer


def test_why_ranked_explains_a_clean_region():
    """Every extract in hand has failures everywhere, so build the clean case:
    a region that never failed still has to get an answer, not a blank."""
    tickets = pd.concat(
        [
            make_ticket(IncidentId="1", Region="eastus2"),  # 10 days late
            make_ticket(
                IncidentId="2",
                Region="uksouth",
                ApprovedDate=pd.Timestamp("2025-01-01T06:00:00Z"),  # same day
            ),
        ],
        ignore_index=True,
    )
    regions = aggregate.by_region(estimate_impact(classify(tickets)))
    answer = why_ranked("uksouth", regions)
    assert "none of its" in answer
    assert "Zero exposure" in answer


def test_why_ranked_handles_an_unknown_region(regions):
    answer = why_ranked("marsnorth1", regions)
    assert "No capacity requests on record" in answer
