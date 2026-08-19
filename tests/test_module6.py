"""Module 6 -- feature availability, and the gate it puts on recommendations."""

from __future__ import annotations

import pytest

import module6
import ontology
from module6.matrix import LIVE, PLANNED, PREVIEW, UNAVAILABLE
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def onto():
    return ontology.build(WORKBOOK, "data/synthetic")


def test_matrix_is_complete(onto):
    m = module6.availability_matrix(onto)
    assert m.notna().all().all(), "every feature must have a status in every region"
    assert set(m.index) == set(onto["dim_feature"]["Feature"])
    assert set(m.columns) == set(onto["dim_region"]["Region"])


def test_a_direct_question_gets_a_direct_answer(onto):
    bridge = onto["bridge_feature_region"]
    row = bridge.iloc[0]
    a = module6.is_available(onto, row["Feature"], row["Region"])
    assert a.status == row["Status"]
    assert a.available == (row["Status"] == LIVE)
    assert row["Region"] in a.note


def test_preview_is_not_available_by_default(onto):
    """Calling preview 'available' is how a recommendation becomes an outage."""
    bridge = onto["bridge_feature_region"]
    preview = bridge[bridge["Status"] == PREVIEW]
    if preview.empty:
        pytest.skip("no preview rows in this extract")
    row = preview.iloc[0]
    strict = module6.is_available(onto, row["Feature"], row["Region"])
    assert strict.available is False
    assert "no production commitment" in strict.note

    lenient = module6.is_available(onto, row["Feature"], row["Region"], minimum=PREVIEW)
    assert lenient.available is True


def test_status_is_ordered_not_just_labelled(onto):
    bridge = onto["bridge_feature_region"]
    planned = bridge[bridge["Status"] == PLANNED]
    if planned.empty:
        pytest.skip("no planned rows")
    row = planned.iloc[0]
    assert module6.is_available(onto, row["Feature"], row["Region"], minimum=PLANNED).available
    assert not module6.is_available(onto, row["Feature"], row["Region"], minimum=PREVIEW).available


def test_an_unavailable_feature_says_where_it_is_live(onto):
    bridge = onto["bridge_feature_region"]
    gone = bridge[bridge["Status"] == UNAVAILABLE]
    if gone.empty:
        pytest.skip("no unavailable rows")
    row = gone.iloc[0]
    note = module6.is_available(onto, row["Feature"], row["Region"]).note
    assert "not available" in note
    assert "live in" in note or "not live anywhere" in note


def test_a_typo_raises_rather_than_answering_no(onto):
    """Silently answering 'no' to a misspelling is worse than failing."""
    with pytest.raises(KeyError, match="Known:"):
        module6.is_available(onto, "Teleportation", "westeurope")
    with pytest.raises(KeyError, match="Known:"):
        module6.is_available(onto, onto["dim_feature"]["Feature"].iloc[0], "marsnorth1")


def test_expansion_check_lists_what_a_region_cannot_do(onto):
    region = onto["dim_region"]["Region"].iloc[0]
    result = module6.check_expansion(onto, region)
    assert result["features_checked"] == len(onto["dim_feature"])
    assert result["features_available"] + len(result["blocked_features"]) == result["features_checked"]
    assert result["clear"] == (not result["blocked_features"])
    assert region in result["summary"]


def test_expansion_check_can_be_narrowed_to_the_features_that_matter(onto):
    region = onto["dim_region"]["Region"].iloc[0]
    one = onto["dim_feature"]["Feature"].iloc[0]
    result = module6.check_expansion(onto, region, features=[one])
    assert result["features_checked"] == 1


def test_region_summary_ranks_by_coverage(onto):
    s = module6.region_summary(onto)
    assert len(s) == onto["dim_region"]["Region"].nunique()
    assert s["CoveragePct"].tolist() == sorted(s["CoveragePct"].tolist(), reverse=True)
    assert (s["Live"] + s["Preview"] + s["Planned"] + s["Unavailable"] == s["FeaturesTotal"]).all()


def test_feature_summary_covers_every_feature(onto):
    f = module6.feature_summary(onto)
    assert set(f["Feature"]) == set(onto["dim_feature"]["Feature"])
    assert (f["Live"] <= f["Regions"]).all()


def test_the_gate_would_actually_block_something(onto):
    """If every region cleared every feature, the module would be pointless."""
    blocked = [
        r for r in onto["dim_region"]["Region"]
        if not module6.check_expansion(onto, r)["clear"]
    ]
    assert blocked, "the matrix should constrain at least one region"
