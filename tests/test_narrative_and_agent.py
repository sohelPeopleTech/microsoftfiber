"""The LLM layer's two safety properties, and the assistant's Q&A router.

No network: `llm.chat` is monkeypatched. What is being tested is not the model,
it is what happens around it -- an ungrounded figure must never be published,
and an unconfigured deployment must never break a run.
"""

from __future__ import annotations

import pytest

from module5 import agent, narrative, pipeline
from module5.llm import LLMConfig, LLMUnavailable
from tests.conftest import WORKBOOK

FALLBACK = "DETERMINISTIC FALLBACK TEXT"


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return pipeline.run(WORKBOOK, out_dir=tmp_path_factory.mktemp("out"))


# --- narrative -----------------------------------------------------------


def test_disabled_by_default(result):
    assert result.narrative.used_llm is False
    assert result.finding["narrative"]["used_llm"] is False


def test_unconfigured_llm_falls_back_without_raising(tmp_path, monkeypatch):
    # Every provider variable, not just the classic Azure OpenAI ones. Clearing
    # half of them left the Foundry endpoint configured for anyone with it in
    # their environment -- so the test asserted a fallback that never happened.
    for var in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_AD_TOKEN",
        "AZURE_FOUNDRY_ENDPOINT",
        "AZURE_FOUNDRY_DEPLOYMENT",
        "AZURE_FOUNDRY_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    res = pipeline.run(WORKBOOK, out_dir=tmp_path, use_llm=True)
    assert res.narrative.used_llm is False
    assert "not configured" in res.narrative.detail
    assert res.finding["summary"]["revenue_exposure_usd"] > 0  # run still complete


def test_grounded_rewrite_is_accepted(result, monkeypatch):
    top = result.finding["regions"][0]
    grounded = (
        f"{top['Region']} carries the largest exposure this period at "
        f"{narrative.money(top['RevenueExposureUSD'])}."
    )
    monkeypatch.setattr(narrative, "chat", lambda *a, **k: grounded)
    out = narrative.write_narrative(
        result.finding, result.recommendations, FALLBACK,
        config=LLMConfig(endpoint="https://x", deployment="d", api_key="k"),
    )
    assert out.used_llm is True
    assert out.text == grounded


def test_figures_handed_to_the_model_are_not_rejected(result):
    """Regression: the check once rejected per-incident and subtotal figures
    that build_prompt had just supplied. A check that cries wolf gets ignored."""
    prompt = narrative.build_prompt(result.finding, result.recommendations)
    top = result.finding["regions"][0]
    subtotal = sum(r.revenue_exposure_usd for r in result.recommendations)
    echoed = (
        f"{top['Region']} leads at {narrative.money(top['RevenueExposureUSD'])}; "
        f"the top three total {narrative.money(subtotal)} of the "
        f"{narrative.money(result.finding['summary']['revenue_exposure_usd'])} "
        f"exposure. Evidence: {result.recommendations[0].evidence[0]}"
    )
    assert narrative.check_grounding(echoed, prompt, result.finding) == []


def test_prompt_supplies_the_subtotal_so_the_model_need_not_add_up(result):
    prompt = narrative.build_prompt(result.finding, result.recommendations)
    subtotal = sum(r.revenue_exposure_usd for r in result.recommendations)
    assert narrative.money(subtotal) in prompt
    assert "Combined exposure" in prompt


def test_prettified_region_name_is_rejected(result):
    """'West Europe' is not westeurope -- these are searchable identifiers."""
    prompt = narrative.build_prompt(result.finding, result.recommendations)
    bad = narrative.check_grounding(
        "West Europe and East US 2 need attention.", prompt, result.finding
    )
    assert any("westeurope" in b for b in bad)
    assert any("eastus2" in b for b in bad)


def test_invented_dollar_figure_is_rejected(result, monkeypatch):
    monkeypatch.setattr(
        narrative, "chat", lambda *a, **k: "westeurope exposure is $9.99M this period."
    )
    out = narrative.write_narrative(
        result.finding, result.recommendations, FALLBACK,
        config=LLMConfig(endpoint="https://x", deployment="d", api_key="k"),
    )
    assert out.used_llm is False
    assert out.text == FALLBACK
    assert "$9.99M" in out.rejected_tokens


def test_invented_region_is_rejected(result, monkeypatch):
    monkeypatch.setattr(
        narrative, "chat", lambda *a, **k: "westus3 is the most affected region."
    )
    out = narrative.write_narrative(
        result.finding, result.recommendations, FALLBACK,
        config=LLMConfig(endpoint="https://x", deployment="d", api_key="k"),
    )
    assert out.used_llm is False
    assert "westus3" in out.rejected_tokens


def test_llm_error_falls_back(result, monkeypatch):
    def boom(*a, **k):
        raise LLMUnavailable("HTTP 429: Too Many Requests")

    monkeypatch.setattr(narrative, "chat", boom)
    out = narrative.write_narrative(
        result.finding, result.recommendations, FALLBACK,
        config=LLMConfig(endpoint="https://x", deployment="d", api_key="k"),
    )
    assert out.used_llm is False
    assert "429" in out.detail


def test_prompt_carries_the_numbers_and_the_caveat(result):
    prompt = narrative.build_prompt(result.finding, result.recommendations)
    assert result.finding["regions"][0]["Region"] in prompt
    assert "pending human review" in prompt
    assert "illustrative" in prompt  # placeholder-ARR caveat is mandatory


def test_config_never_exposes_the_credential():
    config = LLMConfig(
        endpoint="https://x.openai.azure.com", deployment="gpt-4o", api_key="SECRET"
    )
    assert "SECRET" not in config.describe()
    assert "gpt-4o" in config.describe()


# --- agent Q&A -----------------------------------------------------------


@pytest.mark.parametrize(
    "question, intent",
    [
        ("why is uksouth ranked lower?", "ranking_explanation"),
        ("where does eastus2 rank?", "ranking_explanation"),
        ("which region is worst?", "top_region"),
        ("what's the total exposure?", "summary"),
        ("which customers are affected?", "customers"),
        ("how is exposure calculated?", "methodology"),
        ("show me incident 603081913", "incident_lookup"),
    ],
)
def test_router_picks_the_right_intent_without_an_llm(result, question, intent):
    ans = agent.answer(question, result, allow_llm=False)
    assert ans.intent == intent
    assert ans.used_llm is False
    assert len(ans.text) > 20


def test_longest_region_match_wins(result):
    """'eastus2' must not be answered as 'eastus'."""
    ans = agent.answer("why is eastus2 ranked where it is?", result, allow_llm=False)
    assert "eastus2" in ans.text


def test_unknown_incident_is_reported_not_invented(result):
    ans = agent.answer("what about incident 111111111?", result, allow_llm=False)
    assert "not in this extract" in ans.text


def test_unrecognised_question_offers_what_it_can_do(result):
    ans = agent.answer("what is the weather in seattle", result, allow_llm=False)
    assert ans.intent == "unrecognised"
    assert "I can answer questions" in ans.text


def test_llm_fallback_only_sees_computed_facts(result, monkeypatch):
    captured = {}

    def fake_chat(system, user, config=None):
        captured["user"] = user
        return "A grounded answer."

    monkeypatch.setattr(agent, "chat", fake_chat)
    ans = agent.answer("summarise the situation for my VP", result, allow_llm=True)
    assert ans.used_llm is True
    assert "Fact sheet:" in captured["user"]
    assert result.finding["regions"][0]["Region"] in captured["user"]


def test_agent_answers_match_the_written_finding(result):
    """The assistant and the finding must not disagree -- same numbers, same source."""
    top_region = result.finding["regions"][0]["Region"]
    ans = agent.answer("which region is worst?", result, allow_llm=False)
    assert top_region in ans.text
    assert top_region in result.markdown


# --- written finding composition ------------------------------------------


def test_structured_blocks_always_render(result):
    """The manager's ask: fixed labelled fields, not prose. Every ranked region
    gets an action, a stated reason, and its own named evidence -- so a reader
    can disagree with a specific line rather than with the conclusion."""
    from module5 import report

    md = report.to_markdown(result.finding, result.recommendations)
    assert "### Ranked by revenue exposure" in md
    assert "### Recommendations" in md

    for rank, rec in enumerate(result.recommendations, 1):
        assert f"**{rank}. {rec.region}" in md, rec.region
    assert md.count("- **Do:**") == len(result.recommendations)
    assert md.count("- **Why:**") == len(result.recommendations)

    # Evidence names real incidents rather than summarising them away.
    for incident in result.recommendations[0].evidence:
        assert incident in md


def test_caveat_survives_however_the_headline_was_written(result):
    """The caveat is mandatory -- never dropped assuming the model complied."""
    from module5 import report

    md = report.to_markdown(result.finding, result.recommendations)
    assert "illustrative" in md, "caveat must always be present"
    assert md.count("Held for human review") == 1


def test_a_rejection_can_carry_a_reason(result, tmp_path):
    """A rejection with no reason cannot drive suppression vs re-open.

    The reason used to be a text input on the card; it is now a field on the
    decision record, written identically by the CLI and the web application.
    """
    from module5 import state

    state.record_decision(tmp_path, result.finding["as_of"], "westeurope",
                          "reject", "ops@example.com", "already in flight")
    row = state.load_decisions(tmp_path)[-1]
    assert row["decision"] == "reject"
    assert row["reason"] == "already in flight"
    assert row["by"] == "ops@example.com"


@pytest.mark.parametrize(
    "question, intent",
    [
        ("what's the formula?", "methodology"),
        ("how does this work?", "methodology"),
        ("explain the calculation", "methodology"),
        ("how do you work this out?", "methodology"),
        # A named region beats the general "explain" -- it is a question about
        # that region, not about the method.
        ("explain why uksouth is lower", "ranking_explanation"),
        ("why is eastus2 where it is?", "ranking_explanation"),
    ],
)
def test_router_handles_natural_phrasings(result, question, intent):
    assert agent.answer(question, result, allow_llm=False).intent == intent
