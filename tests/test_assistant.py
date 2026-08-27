"""The dashboard assistant -- grounded, and not crying wolf at correct answers."""

from __future__ import annotations

import pytest

from assistant.agent import _is_grounded_money, _money_value, check_grounding

SNAPSHOT = {
    "regions": [{"region": "westeurope", "exposureUsd": 76308.97,
                 "exposureDisplay": "$76K"}],
    "customers": [{"customerShort": "48840256", "exposure": 73052.48999999999}],
    "failedRequests": [{"incidentId": "677681988", "exposure": 37430.81}],
    "totals": {"exposureUsd": 146470.16, "arrAffectedDisplay": "$1.93M"},
}
REGIONS = {"westeurope"}


@pytest.mark.parametrize("token, expected", [
    ("$76K", 76000.0), ("$1.93M", 1930000.0), ("$73,052", 73052.0), ("$599", 599.0),
])
def test_money_parsing(token, expected):
    assert _money_value(token) == pytest.approx(expected)


@pytest.mark.parametrize("token", ["$73,052", "$76K", "$76,309", "$1.93M", "$37,430.81"])
def test_correct_figures_are_accepted(token):
    """These are all legitimate ways to write numbers that are in the data."""
    assert _is_grounded_money(token, [76308.97, 73052.49, 146470.16, 1930054.0, 37430.81])


@pytest.mark.parametrize("token", ["$500K", "$9.99M", "$84,000"])
def test_invented_figures_are_rejected(token):
    assert not _is_grounded_money(token, [76308.97, 73052.49, 146470.16])


def test_a_correct_answer_passes_the_whole_check():
    answer = ("Customer 48840256 is worst at $73,052 across westeurope, "
              "part of $146K total exposure.")
    assert check_grounding(answer, SNAPSHOT, REGIONS) == []


def test_an_invented_region_is_caught():
    bad = check_grounding("eastus3 has the highest exposure.", SNAPSHOT, REGIONS)
    assert "eastus3" in bad


def test_an_invented_figure_is_caught():
    bad = check_grounding("westeurope is exposed to $500K.", SNAPSHOT, REGIONS)
    assert any("500" in b for b in bad)


def test_an_invented_incident_is_caught():
    bad = check_grounding("Incident 111111111 is the worst.", SNAPSHOT, REGIONS)
    assert "111111111" in bad


def test_a_prettified_region_name_is_caught():
    bad = check_grounding("West Europe is worst.", SNAPSHOT, REGIONS)
    assert any("westeurope" in b for b in bad)


# --- regressions found in the running application ---------------------------


def test_a_displayed_figure_is_never_rejected_as_invented():
    """`$1.93M` is printed on the Overview KPI tile, and the check called it
    invented -- because the snapshot carried it only as a display *string*,
    which `_snapshot_values` cannot see. A check that rejects the number on
    screen is worse than no check: it silently swapped a correct model answer
    for a generic fallback, and nothing in the output said so.
    """
    grounded = {**SNAPSHOT, "totals": {**SNAPSHOT["totals"], "arrAffectedUsd": 1930054.0}}
    assert check_grounding("ARR affected is $1.93M.", grounded, REGIONS) == []


def test_snapshot_carries_a_raw_number_for_every_displayed_figure():
    """The guard for the above: a *Display key with no numeric sibling is the
    bug, not a formatting choice."""
    from module5 import pipeline
    from tests.conftest import WORKBOOK
    import assistant, module1, module3, module4, module6, ontology

    onto = ontology.build(WORKBOOK)
    m5 = pipeline.run(WORKBOOK, write_outputs=False)
    snap = assistant.build_snapshot(
        onto=onto, m5=m5,
        flags=module1.project_all(onto).to_dict("records"),
        growth=module3.growth_ranking(module3.demand_by_period(onto, "M")).to_dict("records"),
        coverage=module6.region_summary(onto).to_dict("records"),
        spikes=module4.explain_anomalies(module3.demand_by_period(onto, "M"), onto["fact_event"]),
        provenance=ontology.sources(onto.tables).to_dict("records"),
    )

    def check(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("Display"):
                    raw = key[: -len("Display")] + "Usd"
                    assert raw in node, f"{path}.{key} has no numeric {raw}"
                check(value, f"{path}.{key}")
        elif isinstance(node, list):
            for item in node:
                check(item, path)

    check(snap)


@pytest.mark.parametrize("question, must_not_mention", [
    # "late" is a substring of "calculated", so every "how is this calculated?"
    # was answered with the list of regions whose order is late. Two different
    # questions, one confidently wrong answer.
    ("how is exposure calculated?", "past the point where the decision"),
    ("how is the arr calculated?", "past the point where the decision"),
    ("can you explain how that was calculated", "past the point where the decision"),
])
def test_substrings_do_not_trigger_the_wrong_intent(question, must_not_mention):
    from assistant.agent import _deterministic

    snap = {
        "asOf": "2026-01-28",
        "regions": [{"region": "westeurope", "exposureUsd": 76308.97,
                     "exposureDisplay": "$76K", "failedRequests": 4,
                     "daysUntilAction": -22, "whyThisStatus": "overdue",
                     "status": "overdue", "utilisationPct": 83.1,
                     "decisionWindowDays": 7}],
        "totals": {"exposureDisplay": "$146K", "exposureUsd": 146470.16,
                   "arrAffectedDisplay": "$1.93M", "arrAffectedUsd": 1930054.0,
                   "requestsTotal": 60, "requestsFailed": 30, "approvedLate": 18,
                   "neverFulfilled": 12, "customersAffected": 15, "regions": 11},
        "howItWorks": {"exposureFormula": "ARR x share x days / 365",
                       "failureDefinition": "denied then approved late",
                       "slaByTier": "Enterprise 48h", "orderByRule": "cross minus lead time"},
        "dataCaveats": ["ARR is placeholder data."],
    }
    assert must_not_mention not in _deterministic(question, snap)


def test_the_fallback_admits_when_it_cannot_answer():
    """Answering a different question confidently is the failure mode that
    started this -- so an unmatched question must say so."""
    from assistant.agent import _deterministic

    snap = {
        "asOf": "2026-01-28", "regions": [],
        "totals": {"exposureDisplay": "$146K", "requestsTotal": 60,
                   "requestsFailed": 30, "customersAffected": 15, "regions": 11,
                   "arrAffectedDisplay": "$1.93M", "arrAffectedUsd": 1930054.0,
                   "exposureUsd": 146470.16, "approvedLate": 18, "neverFulfilled": 12},
        "howItWorks": {"exposureFormula": "", "failureDefinition": "",
                       "slaByTier": "", "orderByRule": ""},
        "dataCaveats": [],
    }
    answer = _deterministic("what is the weather in seattle", snap)
    assert "could not match" in answer
