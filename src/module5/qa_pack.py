"""Pre-computed answers -- the assistant's floor when the model is unavailable.

The Q&A router is deterministic: the same question over the same finding always
produces the same answer. That means the answers do not have to be computed at
the moment somebody asks -- they can be computed once per run and published as
a lookup table.

The consequence is worth stating plainly: the in-app assistant always answers
something true, even with no LLM configured, no network, and no key. The
answers are byte-identical to what `agent.answer()` would have returned live,
because they came from it.

What this does NOT do is answer a question nobody anticipated. Novel phrasings
fall through to the same "here is what I can answer" reply the live router
gives, which is the honest failure mode -- it never guesses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import agent

#: Questions worth pre-answering, and the keywords the router matches them on.
#: Order matters -- the first entry whose keywords all appear wins, so put the
#: specific intents above the general ones.
#: Keywords are matched as substrings, so they are written as stems --
#: "calculat" catches calculate, calculated and calculation. Getting this wrong
#: is the difference between a working assistant and one that says "I can
#: answer questions about..." to a question it can obviously answer.
CANNED = [
    ("methodology", ["calculat"], "how is exposure calculated?"),
    ("methodology", ["how", "work"], "how does this work?"),
    ("methodology", ["method"], "what is the method?"),
    ("methodology", ["formula"], "what is the formula?"),
    ("methodology", ["cut-off"], "what is the cut-off?"),
    ("methodology", ["cutoff"], "what is the cutoff?"),
    ("methodology", ["explain"], "explain how this is worked out"),
    ("customers", ["customer"], "which customers are affected?"),
    ("customers", ["who", "affect"], "who is affected?"),
    ("customers", ["subscription"], "which subscriptions are affected?"),
    ("top_region", ["worst"], "which region is worst?"),
    ("top_region", ["highest"], "which region has the highest exposure?"),
    ("top_region", ["priorit"], "which region should we prioritise?"),
    ("top_region", ["fix", "first"], "which region should we fix first?"),
    ("top_region", ["which", "region"], "which region needs attention?"),
    ("summary", ["total"], "what is the total exposure?"),
    ("summary", ["summar"], "give me the summary"),
    ("summary", ["overall"], "what is the overall position?"),
    ("summary", ["how many"], "how many requests failed?"),
    ("summary", ["how much"], "how much revenue is exposed?"),
]


def build(result, include_incidents: bool = True) -> pd.DataFrame:
    """One row per answerable question. Columns: key, keywords, question, answer.

    `key` is what the router looks up; `keywords` is the fallback match for free
    text. Regions and incidents get a row each so "why is uksouth ranked lower?"
    and "show me incident 677681988" both resolve without a model.
    """
    rows = []

    def add(key: str, keywords: list[str], question: str) -> None:
        answer = agent.answer(question, result, allow_llm=False)
        rows.append(
            {
                "key": key,
                "keywords": " ".join(keywords).lower(),
                "question": question,
                "intent": answer.intent,
                "answer": answer.text,
            }
        )

    for key, keywords, question in CANNED:
        add(key, keywords, question)

    # Every region, so "why is <region> ranked lower?" always resolves.
    for region in result.regions["Region"]:
        add(f"region:{region}", [region], f"why is {region} ranked lower?")

    # Every flagged incident, so a ticket number pasted into chat gets its
    # arithmetic back. Unflagged ones are skipped -- their answer is "$0" and
    # the table would triple in size for no benefit.
    if include_incidents:
        flagged = result.priced[result.priced["IsFlagged"]]
        for incident in flagged["IncidentId"]:
            add(f"incident:{incident}", [str(incident)], f"show me incident {incident}")

    # The catch-all returned when nothing matches.
    add("fallback", [], "what is the weather in seattle")
    rows[-1]["key"] = "fallback"

    return pd.DataFrame(rows)


def write(result, out_dir: str | Path) -> Path:
    """Publish the pack next to the other outputs."""
    pack = build(result)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "qa_pack.json"
    path.write_text(
        json.dumps(
            {
                "as_of": result.finding["as_of"],
                "answers": pack.to_dict(orient="records"),
            },
            indent=2,
        )
    )
    pack.to_csv(out_dir / "qa_pack.csv", index=False)
    return path


def lookup(pack: pd.DataFrame, question: str) -> str:
    """Match a question to a pre-computed answer.

    The same matching the live router performs, kept here so the offline
    behaviour can be tested rather than guessed at.
    """
    q = question.lower().strip()

    # An incident number is unambiguous -- check it before anything else.
    for row in pack.itertuples():
        if row.key.startswith("incident:") and row.key.split(":", 1)[1] in q:
            return row.answer

    # Then a named region.
    for row in pack.itertuples():
        if row.key.startswith("region:") and row.key.split(":", 1)[1].lower() in q:
            return row.answer

    # Then the canned intents, first whose keywords all appear.
    for row in pack.itertuples():
        keywords = [k for k in str(row.keywords).split() if k]
        if keywords and all(k in q for k in keywords):
            return row.answer

    fallback = pack[pack["key"] == "fallback"]
    return fallback.iloc[0]["answer"] if len(fallback) else ""


#: The questions offered as one-click buttons in the application's FAQ panel.
BUTTON_QUESTIONS = [
    "Which region is worst?",
    "What is the total exposure?",
    "Which customers are affected?",
    "How is exposure calculated?",
]


def faq(result, include_regions: bool = True) -> list[tuple[str, str]]:
    """Question/answer pairs for the application's FAQ panel.

    Answers come from the same router that serves the chat, so a button and a
    typed question cannot give different answers.
    """
    from . import agent

    pairs = [(q, agent.answer(q, result, allow_llm=False).text) for q in BUTTON_QUESTIONS]

    if include_regions:
        # One entry per region that actually carries exposure -- "why is X
        # ranked lower" is meaningless for a region with nothing wrong, and
        # the button list stays short.
        ranked = result.regions[result.regions["RevenueExposureUSD"] > 0]
        for region in ranked["Region"].head(6):
            pairs.append(
                (
                    f"Why is {region} ranked #{int(ranked.loc[ranked['Region'] == region, 'Rank'].iloc[0])}?",
                    agent.answer(f"why is {region} ranked lower?", result, allow_llm=False).text,
                )
            )
    return pairs
