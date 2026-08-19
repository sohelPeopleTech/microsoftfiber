"""The pre-computed answer pack -- a conversation without a hosted endpoint."""

from __future__ import annotations

import pandas as pd
import pytest

from module5 import agent, pipeline, qa_pack
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return pipeline.run(WORKBOOK, out_dir=tmp_path_factory.mktemp("out"))


@pytest.fixture(scope="module")
def pack(result):
    return qa_pack.build(result)


def test_every_region_and_flagged_incident_is_answerable(pack, result):
    keys = set(pack["key"])
    for region in result.regions["Region"]:
        assert f"region:{region}" in keys
    for incident in result.priced.loc[result.priced["IsFlagged"], "IncidentId"]:
        assert f"incident:{incident}" in keys


@pytest.mark.parametrize(
    "question, must_contain",
    [
        ("which region is worst?", "westeurope"),
        ("why is uksouth ranked lower?", "uksouth"),
        ("show me incident 677681988", "677681988"),
        ("what's the total exposure?", "$146K"),
        ("how is exposure calculated?", "ARR"),
        ("which customers are affected?", "Enterprise"),
    ],
)
def test_lookup_matches_the_way_a_flow_would(pack, question, must_contain):
    assert must_contain in qa_pack.lookup(pack, question)


def test_precomputed_answer_equals_the_live_one(pack, result):
    """The whole premise: a canned answer is what the router would have said."""
    for question in ("which region is worst?", "why is uksouth ranked lower?",
                     "show me incident 677681988", "how is exposure calculated?"):
        assert qa_pack.lookup(pack, question) == agent.answer(
            question, result, allow_llm=False
        ).text


def test_incident_number_wins_over_a_region_name(pack):
    """'incident 677681988 in westeurope' is about the incident."""
    answer = qa_pack.lookup(pack, "what about incident 677681988 in westeurope?")
    assert "677681988" in answer and "units blocked" in answer


def test_unknown_question_falls_back_without_guessing(pack):
    answer = qa_pack.lookup(pack, "what is the weather in seattle")
    assert "I can answer questions" in answer


def test_pack_is_written_with_the_run(result, tmp_path):
    path = qa_pack.write(result, tmp_path)
    assert path.exists()
    assert (tmp_path / "qa_pack.csv").exists()
    loaded = pd.read_csv(tmp_path / "qa_pack.csv")
    assert {"key", "keywords", "question", "answer"} <= set(loaded.columns)


@pytest.mark.parametrize(
    "question, intent",
    [
        ("how do you calculate this?", "methodology"),
        ("explain the calculation", "methodology"),
        ("what's the formula?", "methodology"),
        ("how does this work?", "methodology"),
        ("which region should we prioritise?", "top_region"),
        ("how many requests failed?", "summary"),
        ("how much revenue is exposed?", "summary"),
        ("give me a summary", "summary"),
        ("which subscriptions are affected?", "customers"),
    ],
)
def test_natural_phrasings_resolve(pack, question, intent):
    """Stems, not exact words -- a near-miss must not fall to the catch-all."""
    answer = qa_pack.lookup(pack, question)
    assert "I can answer questions" not in answer, f"{question!r} fell through"
    expected = pack[pack["intent"] == intent]["answer"].tolist()
    assert answer in expected


# --- the FAQ buttons -------------------------------------------------------


def test_faq_pairs_cover_the_common_questions(result):
    pairs = qa_pack.faq(result)
    questions = [q for q, _ in pairs]
    assert "Which region is worst?" in questions
    assert any(q.startswith("Why is ") for q in questions)
    assert all(len(a) > 20 for _, a in pairs), "every button must reveal a real answer"


def test_faq_only_offers_regions_that_actually_failed(result):
    pairs = qa_pack.faq(result)
    clean = set(result.regions.loc[result.regions["RevenueExposureUSD"] == 0, "Region"])
    for question, _ in pairs:
        assert not any(c in question for c in clean), (
            "a region with no exposure has nothing to explain"
        )


def test_faq_answers_match_the_live_router(result):
    """A button and a typed question must never disagree."""
    from module5 import agent

    pairs = dict(qa_pack.faq(result, include_regions=False))
    for question, answer in pairs.items():
        assert answer == agent.answer(question, result, allow_llm=False).text
