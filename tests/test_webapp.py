"""The web application's API layer.

The endpoints are called directly rather than through a test client: that keeps
the suite free of an HTTP dependency, and every bug these tests were written for
was in the payload rather than in the routing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp"))

import api  # noqa: E402
import riskindex  # noqa: E402


@pytest.fixture(scope="module")
def regions():
    return [r["region"] for r in api.overview()["regions"]]


def test_every_region_detail_is_json_serialisable(regions):
    """Regression: `region_detail` cleaned `threshold` and `growth` but passed
    `features` and `spikes` through raw. One NaN buried in an anomaly row made
    the whole endpoint 500, so northcentralus had no detail panel at all while
    its neighbours worked -- the kind of failure that looks like a fluke until
    somebody clicks the wrong row in a demo.
    """
    assert regions, "no regions to check"
    for name in regions:
        payload = api.region_detail(name)
        body = json.dumps(payload)          # raises on NaN / Infinity
        assert "NaN" not in body, f"{name} carries a NaN"
        assert "Infinity" not in body, f"{name} carries an Infinity"


def test_region_detail_shapes_are_what_the_ui_reads(regions):
    """The other half of the same bug: the client called `.filter()` on
    `features`, which is a summary object rather than a list. The TypeError left
    the panel showing 'Loading...' with the real error only in the console.
    """
    for name in regions:
        d = api.region_detail(name)
        assert isinstance(d["features"], dict), f"{name}: features must be an object"
        for key in ("blocked_features", "features_available", "features_checked"):
            assert key in d["features"], f"{name}: features missing {key}"
        assert isinstance(d["features"]["blocked_features"], list)

        assert isinstance(d["spikes"], list), f"{name}: spikes must be a list"
        for spike in d["spikes"]:
            for key in ("period", "value", "baseline", "matched", "match_strength"):
                assert key in spike, f"{name}: spike missing {key}"

        assert isinstance(d["tickets"], list)
        assert d["threshold"]["region"] == name


def test_an_unknown_region_is_a_404_not_a_crash():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        api.region_detail("atlantis")
    assert exc.value.status_code == 404


def test_decision_rejects_an_unknown_verb():
    """Approve/Reject is the human-review gate; anything else must not be
    silently written into the audit trail."""
    from fastapi import HTTPException

    class _Req:
        cookies: dict = {}

    with pytest.raises(HTTPException) as exc:
        api.record_decision(_Req(), {"region": "westeurope", "decision": "maybe"})
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        api.record_decision(_Req(), {"region": "", "decision": "approve"})
    assert exc.value.status_code == 400
def test_every_outcome_label_the_ui_prints_can_be_explained():
    """The tickets table prints an outcome word on every row. The assistant
    replied "there is no category called 'no denial' in the data" -- honest,
    and useless, because the word was on screen beside the question. Any
    category the classifier can emit must be explainable before a reader asks.
    """
    from module5 import classifier

    snap = api.get_snapshot()
    explained = snap["outcomeCategories"]

    for category in classifier.CATEGORIES:
        label = category.replace("_", " ")
        assert label in explained, f"{label!r} is printable but not explainable"
        assert len(explained[label]) > 40, f"{label!r} has no real definition"


@pytest.mark.parametrize("question", [
    "what do you mean by no denial here?",
    "what does denied unfulfilled mean",
    "what does same day approved mean?",
    "what are the outcome categories?",
])
def test_the_fallback_explains_outcomes_without_an_llm(question):
    """These must work when the model is rate-limited, which it routinely is."""
    from assistant.agent import _deterministic

    answer = _deterministic(question, api.get_snapshot())
    assert "could not match" not in answer
    assert "requests" in answer


# --- claims the "How to read this page" panels make -------------------------
#
# These panels are prose on screen. Prose does not fail loudly when the data
# moves underneath it -- it just quietly becomes false, in the one place a
# reviewer is most likely to trust. Each test below pins one such claim.


def test_the_funnel_stages_are_a_real_funnel():
    """Every stage must be a subset of the one above and share one denominator.

    The first version put demand spikes in the last row and printed "7 of 60 =
    12%" -- two quantities that are not a subset of each other, divided.
    """
    ov = api.overview()
    c, total = ov["categoryCounts"], ov["kpis"]["total"]
    denied = total - c["no_denial"]
    past_allowance = denied - c["same_day_approved"]
    stages = [total, denied, past_allowance, c["denied_unfulfilled"]]

    assert stages == sorted(stages, reverse=True), f"not monotonic: {stages}"
    # The third stage is the failure count the KPI strip reports, so the funnel
    # and the tiles cannot disagree.
    assert past_allowance == ov["kpis"]["failed"]


def test_the_regions_lead_time_inversion_example_is_still_true():
    """The Regions explainer names two regions and their exact figures. If the
    extract changes, that sentence becomes a confident falsehood on screen."""
    r = {x["region"]: x for x in api.overview()["regions"]}
    ci, nc = r["centralindia"], r["northcentralus"]

    assert round(ci["utilisation"], 1) == 81.8, "explainer prints 81.8%"
    assert round(nc["utilisation"], 1) == 89.1, "explainer prints 89.1%"
    assert (ci["leadTime"], nc["leadTime"]) == (45, 30), "explainer prints 45d vs 30d"
    assert ci["sku"] == "Intel-highmem" and nc["sku"] == "GPU-class"
    # The point of the example: lower utilisation, yet more urgent.
    assert ci["utilisation"] < nc["utilisation"]
    assert ci["daysUntilOrder"] < nc["daysUntilOrder"]


def test_action_due_means_the_order_by_date_has_passed():
    """The KPI subtitle says "order-by date reached or passed".

    Three states, not two. `daysUntilOrder is None` means the backtested model
    projects no crossing at all, which is the opposite of overdue -- reading it
    as 0 counted the calmest regions as the most urgent.
    """
    ov = api.overview()
    due_statuses = ("breached", "overdue", "due_now")
    for region in ov["regions"]:
        days, status = region["daysUntilOrder"], region["status"]
        if days is None:
            assert status not in due_statuses, (
                f"{region['region']}: {status} with no projected crossing")
            continue
        assert (status in due_statuses) == (days <= 0), \
            f"{region['region']}: {status} vs {days}"


def test_the_two_tabs_never_disagree_about_a_crossing_date():
    """Regions and Forecast answer the same question and must not differ.

    They did: module 1 fitted its own least-squares trend while the Forecast tab
    used the backtest winner, so canadaeast was 10 days apart on two screens.
    """
    forecasts = {f["region"]: f for f in api.forecast_all()["forecasts"]}
    for flag in api.threshold()["regions"]:
        f = forecasts.get(flag["region"])
        if f is None or f["alreadyBreached"]:
            continue
        mine = str(flag["cross_date"])[:10] if flag["cross_date"] else None
        assert mine == f["crossingDate"], (
            f"{flag['region']}: Regions says {mine}, "
            f"Forecast says {f['crossingDate']}")


def test_free_tier_customers_appear_and_sort_last():
    """The Customers explainer used to claim they "never rank here". They do --
    at $0, at the bottom. Saying otherwise while they sit on screen is worse
    than the limitation itself."""
    cs = api.customers()["customers"]
    free = [c for c in cs if c["tier"] == "Free"]

    assert free, "Free-tier customers are present in this extract"
    assert all(c["arr"] == 0 and c["exposure"] == 0 for c in free)
    assert [c["tier"] for c in cs][-len(free):] == ["Free"] * len(free)
    assert all(cs[i]["exposure"] >= cs[i + 1]["exposure"] for i in range(len(cs) - 1))
def test_a_zero_revenue_failure_explains_itself_instead_of_showing_zeroes():
    """A Free-tier failure priced "$0 x 86% x 23.2 days / 365 = $0.00" is
    arithmetically right and useless -- it reads as a broken calculator rather
    than as the real blind spot it is. One incident in this extract went 138
    days unfulfilled and scored zero.
    """
    rows = [t for r in api.overview()["regions"]
            for t in api.region_detail(r["region"])["tickets"]]
    zero_arr_failures = [t for t in rows if t["isFlagged"] and t["arr"] == 0]
    assert zero_arr_failures, "this extract has Free-tier failures to describe"

    for t in zero_arr_failures:
        w = t["workingOut"]
        assert "= $0.00" not in w, f"{t['incidentId']} still shows a zero multiplication"
        assert "Real failure" in w
        assert f"{t['days']:.1f} days" in w        # the delay is still stated
        assert t["tier"] in w                       # and why it prices at zero

    # Paying customers keep the arithmetic -- that is the whole point of it.
    paying = [t for t in rows if t["isFlagged"] and t["arr"] > 0]
    assert paying and all("/ 365 =" in t["workingOut"] for t in paying)


def test_spike_timing_reads_as_english_even_inside_the_period():
    """days_before_spike goes negative for an event inside the spike's own
    period, which rendered as "-6 day(s) before". The module already computes
    the right phrasing; the UI must use it."""
    for r in api.overview()["regions"]:
        for s in api.region_detail(r["region"])["spikes"]:
            if not s["matched"]:
                continue
            assert s["event_timing"], "matched spike must carry a readable timing"
            assert not s["event_timing"].startswith("-")


def test_the_as_of_date_explains_that_it_is_the_end_of_the_sample():
    """Today is months past the extract's last date. Shown bare, that reads as
    stale data and is the first thing a reviewer challenges."""
    shell = (ROOT / "webapp" / "static" / "shell.js").read_text()
    assert "Data to ${asOf}" in shell, "header must not imply the data is current"
    assert "sample extract ends" in shell, "and must explain why that date"


def test_the_working_out_line_reconciles_when_a_reader_checks_it():
    """The column exists so nobody has to trust the total. If the figures it
    prints do not reproduce the stated answer, it destroys trust instead --
    which is what a rounded percentage did: "95% x 25.5 days" for a 95.402%
    share over 25.458 days checked to $37,491 against a stated $37,430.81.

    Units are exact, so only the day count is rounded and the drift is tiny.
    """
    import re

    rows = [t for r in api.overview()["regions"]
            for t in api.region_detail(r["region"])["tickets"]
            if t["isFlagged"] and t["arr"] > 0]
    assert rows

    pattern = re.compile(
        r"\$([\d,]+) annual revenue x ([\d,]+) of ([\d,]+) units missing "
        r"x ([\d.]+) days / 365 = \$([\d,]+\.\d\d)")

    def n(s):
        return float(s.replace(",", ""))

    for row in rows:
        m = pattern.match(row["workingOut"])
        assert m, row["workingOut"]
        arr, blocked, requested, days, stated = (n(m.group(i)) for i in range(1, 6))

        assert requested > 0
        recomputed = arr * (blocked / requested) * days / 365
        # Only the day count is rounded, to 2dp, so the drift is a fraction of
        # a percent. A reader checking this lands on the same figure.
        assert abs(recomputed - stated) <= max(0.05, stated * 0.001), (
            f"{row['incidentId']}: line states {stated}, its own figures give "
            f"{recomputed:.2f}")

    assert "days rounded for display" in rows[0]["workingOut"]


# --- the datacentre and reason layers added after the 13-Aug review ---------


def test_every_ticket_is_attributed_to_a_datacentre_in_its_own_region():
    """Region -> data centre -> ticket is the drill-down an engineer works in.
    A ticket attributed to a data centre in the wrong region would make the
    region totals and the data-centre totals disagree."""
    import ontology

    onto = ontology.build()
    fact, dim = onto["fact_capacity_request"], onto["dim_datacentre"]
    by_dc = dict(zip(dim["DatacentreId"], dim["Region"], strict=True))

    assert fact["DatacentreId"].notna().all(), "every ticket needs a data centre"
    for row in fact.itertuples():
        assert by_dc[row.DatacentreId] == row.Region, row.IncidentId


def test_attribution_is_deterministic():
    """Both new columns are derived, not stored. If they moved between runs, a
    figure quoted in a review would stop being true the next morning -- the
    same defect that forced the reporting pack to be rebuilt."""
    import ontology

    signatures = set()
    for _ in range(3):
        o = ontology.build()
        f = o["fact_capacity_request"].sort_values("IncidentId")
        signatures.add((tuple(f["DatacentreId"]), tuple(f["DenialReason"])))
    assert len(signatures) == 1


def test_a_reason_is_recorded_for_every_refusal_and_nothing_else():
    """A reason on a request that was never refused invites someone to count it."""
    import ontology

    fact = ontology.build()["fact_capacity_request"]
    refused = fact["DeniedDate"].notna()
    has_reason = fact["DenialReason"].astype(str) != ""

    assert (has_reason & ~refused).sum() == 0, "reason set on a request never denied"
    assert has_reason.sum() > 0
    # The unknown bucket must actually contain something, or the human-review
    # path cannot be shown to exist.
    from ontology.attribution import UNKNOWN_REASON
    assert (fact["DenialReason"] == UNKNOWN_REASON).sum() >= 1


def test_region_distribution_reconciles_to_the_ticket_total():
    """The layer that connects "11 regions" to "60 requests". If it does not
    add up, it creates the confusion it was added to remove."""
    ov = api.overview()
    dist = ov["regionDistribution"]

    assert sum(r["requests"] for r in dist) == ov["kpis"]["total"]
    assert round(sum(r["sharePct"] for r in dist)) == 100
    assert dist == sorted(dist, key=lambda r: -r["requests"]), "busiest first"


def test_every_reason_carries_an_action_and_says_who_handles_it():
    """A cause with no next step is just a label."""
    for scope in (api.overview()["reasons"],
                  api.region_detail("southcentralus")["reasons"]):
        assert scope
        for r in scope:
            assert r["action"], r["reason"]
            assert r["handledBy"]
            # Anything without an owning module must be flagged for a human
            # rather than given an invented automated fix.
            assert r["needsHuman"] == (r["handledBy"] == "human review")


def test_outcome_labels_use_the_vocabulary_the_review_asked_for():
    labels = api.overview()["outcomeLabels"]
    assert "FTR" in labels["no_denial"]
    assert "SLA" in labels["same_day_approved"]
    assert "SLA breached" in labels["denied_then_approved_late"]
    assert "Not approved" in labels["denied_unfulfilled"]


# --- the four drill-down views added for the Monday review ------------------


def test_risk_is_scored_independently_at_each_level():
    """Review was explicit: "each is calculated by their own scores -- not
    100 on top and 20/20/20/20 underneath". So the parts must be free to
    disagree with the whole, and every score must show its working."""
    import riskindex

    dcs = api.datacentres()["datacentres"]
    customers = api.customers()["customers"]
    assert dcs and customers

    for row in dcs + customers:
        risk = row["risk"]
        assert 0 <= risk["score"] <= 100
        assert risk["band"] == riskindex.band(risk["score"])
        # Every component present and rankable, so a score can be taken apart.
        assert set(risk["components"]) == set(riskindex.WEIGHTS)
        assert risk["drivers"] == sorted(risk["drivers"],
                                         key=lambda d: -d["contribution"])

    # Datacentre scores are not a share of their region's score.
    assert len({round(d["risk"]["score"]) for d in dcs}) > 1


def test_thin_evidence_sites_are_marked_not_smoothed():
    """A site with one refused request has a 100% failure rate and almost no
    evidence. Hiding that would be overclaiming, so it is flagged instead."""
    dcs = api.datacentres()["datacentres"]
    for d in dcs:
        assert d["lowEvidence"] == (d["requests"] < 3), d["datacentre"]
    assert any(d["lowEvidence"] for d in dcs)


def test_the_datacentre_view_says_how_much_of_the_estate_it_shows():
    """Only sites with activity are listed. Without the total it looks like
    the whole estate when it is a fraction of it."""
    d = api.datacentres()
    assert d["withActivity"] == len(d["datacentres"])
    assert d["totalSites"] >= d["withActivity"]


def test_every_customer_gets_a_recommendation_grounded_in_their_own_spread():
    """Not the per-region fix repeated -- the account-level question is where
    this customer already has room."""
    for c in api.customers()["customers"]:
        rec = c["recommendation"]
        assert rec["headline"] and rec["detail"]
        if c["failedRequests"]:
            # It must name the region it is talking about.
            assert c["worstRegion"] in rec["headline"] + rec["detail"]


def test_the_incident_list_carries_every_filter_it_offers():
    d = api.incidents()
    assert len(d["incidents"]) == 60
    for row in d["incidents"]:
        assert row["datacentre"], row["incidentId"]
        assert row["outcomeLabel"]
    # A filter with no matching rows would be a dead control.
    for region in d["regions"]:
        assert any(r["region"] == region for r in d["incidents"])
    for reason in d["reasons"]:
        assert any(r["reason"] == reason for r in d["incidents"])


def test_reason_view_totals_agree_with_the_overview():
    r = api.reasons()
    assert sum(x["count"] for x in r["reasons"]) == r["totalFailed"]
    assert [x["reason"] for x in r["reasons"]] == \
           [x["reason"] for x in api.overview()["reasons"]]


def test_clicking_a_datacentre_opens_that_datacentre():
    """It used to navigate to the parent region, which threw away the level of
    detail the reader had just asked for. Every listed site must therefore have
    its own detail, and that detail must show the score's working."""
    listed = api.datacentres()["datacentres"]
    assert listed

    for row in listed[:5]:
        d = api.datacentre_detail(row["datacentre"])
        assert d["datacentre"] == row["datacentre"]
        assert d["region"] == row["region"]
        # The score shown in the list and in the detail must agree.
        assert d["risk"]["score"] == row["risk"]["score"]
        assert d["requests"] == row["requests"]
        assert len(d["tickets"]) == row["requests"]
        # Every component is spelled out, so the number can be argued with.
        assert d["risk"]["drivers"]
        assert set(d["componentLabels"]) >= {dr["component"] for dr in d["risk"]["drivers"]}


def test_an_unknown_datacentre_is_a_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        api.datacentre_detail("nowhere-dc99")
    assert exc.value.status_code == 404


def test_datacentre_detail_tickets_belong_to_that_site():
    """A ticket appearing under the wrong site would make the site totals and
    the region totals disagree."""
    import ontology

    fact = ontology.build()["fact_capacity_request"]
    for row in api.datacentres()["datacentres"][:5]:
        d = api.datacentre_detail(row["datacentre"])
        expected = set(fact[fact["DatacentreId"] == row["datacentre"]]["IncidentId"].astype(str))
        assert {t["incidentId"] for t in d["tickets"]} == expected


def test_risk_weights_come_from_config_not_from_code():
    """Asked in review: "on what basis is refused 40 points?" The honest answer
    is that it was chosen, so it belongs beside the other numbers a reviewer
    might argue with rather than buried in a module."""
    import json
    import riskindex
    from module5.config import Config

    raw = json.loads((ROOT / "config.json").read_text())
    assert "risk_weights" in raw, "weights must be a setting"
    assert raw["notes"].get("risk_weights"), "and must say what they are"

    served = api.datacentres()["weights"]
    assert served == riskindex.resolve_weights(Config.load(ROOT / "config.json").risk_weights)
    assert abs(sum(served.values()) - 1.0) < 1e-6


def test_a_bad_weight_set_is_rejected_rather_than_renormalised():
    """A silently-fixed config edit produces scores nobody can reconcile."""
    import pytest as _pytest
    import riskindex

    for bad in ({"failureRate": 1.0},
                {"failureRate": .5, "pressure": .5, "unresolved": .5, "leadTime": .5},
                {"failureRate": -.1, "pressure": .4, "unresolved": .4, "leadTime": .3}):
        with _pytest.raises(ValueError):
            riskindex.resolve_weights(bad)


def test_changing_the_weights_changes_the_score():
    """Otherwise the setting is decorative."""
    import riskindex

    args = dict(requests=1, denied=1, unresolved=1, utilisation_pct=97.2,
                threshold_pct=85, lead_time_days=45, busiest_unresolved=2)
    default = riskindex.score(**args).score
    even = riskindex.score(**args, weights={
        "failureRate": .25, "pressure": .25, "unresolved": .25, "leadTime": .25}).score
    assert default != even


def test_methodology_publishes_the_weights_the_run_used():
    m = api.methodology()
    assert m["riskWeights"]
    assert {w["component"] for w in m["riskWeights"]} == set(api.datacentres()["weights"])
    for w in m["riskWeights"]:
        assert w["label"] and w["label"] != w["component"]
    assert "starting position" in m["riskWeightsNote"].lower() or \
           "not a derived" in m["riskWeightsNote"].lower()


def test_every_ticket_row_carries_its_site_and_reason():
    """Module 5 loads tickets through its own ingest path, which never sees the
    two columns the ontology adds. Reading them off that frame with getattr
    silently produced "" for every row -- the Data centre column rendered blank
    and every Reason showed a dash, while the panel directly above the table
    listed the same reasons correctly.
    """
    import ontology

    fact = ontology.build()["fact_capacity_request"]
    expected = {
        str(r.IncidentId): (str(r.DatacentreId), str(r.DenialReason or ""))
        for r in fact.itertuples()
    }

    seen = 0
    for region in api.overview()["regions"]:
        for row in api.region_detail(region["region"])["tickets"]:
            want_dc, want_reason = expected[row["incidentId"]]
            assert row["datacentre"] == want_dc, row["incidentId"]
            assert row["reason"] == want_reason, row["incidentId"]
            assert row["datacentre"], "no ticket may render a blank site"
            seen += 1
    assert seen == len(fact)

    # The same rows reached through the other two views must agree.
    for row in api.incidents()["incidents"]:
        assert (row["datacentre"], row["reason"]) == expected[row["incidentId"]]


def test_every_explainer_supplies_every_field_it_renders():
    """The Overview lost its `sources` key in a restructure and rendered
    "Built from: undefined" on the busiest page in the product. Nothing failed,
    because a missing key in a template literal is a string, not an error."""
    import re

    src = (ROOT / "webapp" / "static" / "pages.js").read_text()
    calls = re.findall(r"howto\(\{(.*?)\n  \}\)", src, re.S)
    assert len(calls) >= 6, f"expected one explainer per page, found {len(calls)}"

    for block in calls:
        for key in ("answers:", "steps:", "next:", "sources:"):
            assert key in block, f"explainer missing {key}: {block[:80]}"


def test_the_swap_calculator_works_on_a_site_not_a_region():
    """You take a building offline, not a country. The calculator was working
    at region level, which is not where the work happens."""
    opt = api.swap_options()
    assert opt["sites"] and opt["hardware"] and opt["regions"]
    for s in opt["sites"]:
        assert s["datacentre"] and s["region"] and s["currentHardware"]

    site = next(s for s in opt["sites"] if s["hasActivity"])
    target = next(h for h in opt["hardware"] if h != site["currentHardware"])
    r = api.swap(site["datacentre"], target)

    assert r["currentHardware"] == site["currentHardware"]
    assert r["targetHardware"] == target
    # Sized to that site, not the whole region.
    assert r["conversion"]["from_units"] == pytest.approx(site["units"], rel=1e-6)
    # And feasibility is still asked of the region, because the load has to go
    # somewhere while the building is down.
    assert r["feasibility"]["region"] == site["region"]
    assert "can_convert_a_whole_datacentre" in r["feasibility"]


def test_swapping_to_the_hardware_it_already_runs_is_rejected():
    """A no-op that returns a result looks like an answer."""
    from fastapi import HTTPException

    site = api.swap_options()["sites"][0]
    with pytest.raises(HTTPException) as exc:
        api.swap(site["datacentre"], site["currentHardware"])
    assert exc.value.status_code == 400
    assert "already runs" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        api.swap("nowhere-dc01", "AMD-highmem")
    assert exc.value.status_code == 404


# --- review feedback: identity, reconciliation, per-site capacity -----------


def test_customers_are_named_not_just_numbered():
    """A subscription id sits beside an incident id of the same shape, so the
    reader has to work out which is which before they can read the row."""
    names = {c["customerName"] for c in api.customers()["customers"]}
    assert names and all(n and not n.isdigit() for n in names)
    assert len(names) == len(api.customers()["customers"]), "names must be unique"

    # And the name must reach every surface that shows a ticket.
    for region in api.overview()["regions"]:
        for t in api.region_detail(region["region"])["tickets"]:
            assert t["customerName"], t["incidentId"]
    for t in api.incidents()["incidents"]:
        assert t["customerName"]


def test_one_failure_count_everywhere():
    """The screen previously carried both a denial count and a failure count,
    and a row could show "Failed 1" beside a recommendation saying "2 requests
    failed". One definition now: SLA breached, or never fulfilled.
    """
    for region in api.overview()["regions"]:
        d = api.region_detail(region["region"])
        assert "deniedCount" not in d, "the denial count must not come back"
        # Site rows add up to the region figure.
        assert sum(x["failed"] for x in d["datacentres"]) == d["failedCount"]
        for site in d["datacentres"]:
            assert "denied" not in site
            # And the recommendations count the same rows as the column.
            assert sum(r["count"] for r in site["recommendations"]) == site["failed"]


def test_only_sites_with_a_failure_are_listed():
    """A site where nothing failed has nothing to action."""
    for region in api.overview()["regions"]:
        for x in api.region_detail(region["region"])["datacentres"]:
            assert x["failed"] > 0, x["datacentre"]


def test_every_listed_site_reports_its_capacity_position():
    """Review asked for cores held, cores left and the site's own threshold."""
    for region in api.overview()["regions"]:
        for x in api.region_detail(region["region"])["datacentres"]:
            for key in ("cores", "coresFree", "thresholdPct", "headroom", "revenueLoss"):
                assert x[key] is not None, f"{x['datacentre']} missing {key}"
            assert 0 < x["thresholdPct"] <= 100


def test_each_denial_cause_carries_computed_remediation():
    """"If you gave this to ChatGPT it would say the same thing" -- so a cause
    the platform owns must produce arithmetic for that facility, not prose."""
    seen_migration = seen_threshold = False
    for row in api.datacentres()["datacentres"]:
        d = api.datacentre_detail(row["datacentre"])
        for rec in d["recommendations"]:
            assert rec["action"]
            if rec.get("migration"):
                seen_migration = True
                for m in rec["migration"]:
                    # Sized to this facility, not the region.
                    assert m["coresAfter"] > 0 and m["leadTimeDays"] > 0
                    assert m["toSku"] != d["hardware"]
            if rec.get("threshold"):
                seen_threshold = True
                for o in rec["threshold"]:
                    assert 0 < o["thresholdPct"] <= 100
                    assert o["releasesCores"] > 0
    assert seen_migration, "no hardware-owned cause produced migration options"
    assert seen_threshold, "no threshold-owned cause produced threshold options"


# --- the forecasting / policy programme (review items 16-20) ---------------


def test_the_forecast_model_is_chosen_by_backtest_not_by_hand():
    """Review asked for several models and for performance to be reported.
    The point is that the choice is made by held-out error, so different
    regions must be free to pick different winners."""
    d = api.forecast_all()
    assert len(d["candidates"]) >= 6

    forced = api._forced_model()
    chosen = set()
    for f in d["forecasts"]:
        assert f["scores"], f["region"]
        chosen.add(f["model"])
        # Scored on held-out folds and ranked, whether or not the winner is used.
        assert all(s["folds"] > 0 for s in f["scores"])
        best = min(f["scores"], key=lambda s: s["rmse"])

        if forced:
            # A model may be imposed, but it must never be presented as though
            # the evidence picked it. The override has to declare itself, name
            # what the backtest wanted, and carry the cost of the difference.
            assert f["model"] == forced, f"{f['region']}: forced model not applied"
            assert f["forced"], f"{f['region']}: override is not declared"
            assert f["forced"]["wouldHaveChosen"] == best["model"]
            assert f["forced"]["costPct"] >= 0
        else:
            assert f["model"] == best["model"] or not f["beatsNaive"]
            # A model that cannot beat naive must not be used.
            if not f["beatsNaive"]:
                assert f["model"] == "naive"

    if not forced:
        assert len(chosen) > 1, "every region picked the same model — selection is not working"
    else:
        assert chosen == {forced}


def test_an_imposed_model_still_reports_the_full_ranking():
    """Forcing a model must not hide the evidence against it. The alternative
    is a screen that shows one model and no way to tell what it cost."""
    if not api._forced_model():
        pytest.skip("no model is being forced")
    for f in api.forecast_all()["forecasts"]:
        assert len(f["scores"]) >= 6, f"{f['region']}: ranking was truncated"
        assert any(s["model"] != f["model"] for s in f["scores"]), \
            f"{f['region']}: only the imposed model is listed"


def test_a_crossing_date_carries_its_uncertainty():
    """A single date implies a precision 150 days of history cannot support."""
    for f in api.forecast_all()["forecasts"]:
        if not f["crossingDate"]:
            continue
        assert f["crossingEarliest"] and f["crossingLatest"]
        assert f["crossingEarliest"] <= f["crossingDate"] <= f["crossingLatest"]


def test_a_region_already_past_its_line_is_not_given_a_forecast():
    """That is a present condition, not a projection."""
    for f in api.forecast_all()["forecasts"]:
        if "Already at" in (f["note"] or ""):
            assert f["crossingDate"] is None


def test_anomalies_are_detected_excluded_and_not_all_explained():
    """Review: remove spikes before training so a signed deal does not become
    the trend — and refuse to attribute a cause that is not there."""
    a = api.anomalies()
    assert a["total"] > 0
    assert a["explained"] + a["unexplained"] == a["total"]
    assert a["unexplained"] > 0, "a detector that explains everything is guessing"

    for region in a["regions"].values():
        box = region["boxplot"]
        assert box["lowerFence"] < box["q1"] <= box["median"] <= box["q3"] < box["upperFence"]
        for o in region["outliers"]:
            assert o["explained"] == (o["eventType"] is not None)


def test_the_reserve_simulation_separates_policy_from_procurement():
    """The useful distinction: a region that would still deny is out of
    capacity, which no allocation policy can fix."""
    d = api.capacity_policy()
    t = d["totals"]
    assert t["admitted"] + t["denied"] == sum(
        r["admitted"] + r["denied"] for r in d["regions"])
    assert abs(sum(d["reserve"].values()) - 1.0) < 1e-6

    for r in d["regions"]:
        assert r["wouldHavePrevented"] == max(0, r["actualFailures"] - r["denied"])
    assert any(r["wouldHavePrevented"] > 0 for r in d["regions"])
    assert any(r["denied"] > 0 for r in d["regions"])


def test_a_bad_reserve_is_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        api.capacity_policy(enterprise=0.9, premium=0.9, standard=0.9, free=0.9)
    assert exc.value.status_code == 400


def test_capacity_is_also_expressed_as_a_fabric_sku():
    """Fabric is sold as an F-SKU rated in Capacity Units, not as raw compute."""
    d = api.capacity_policy()
    assert d["pools"] and d["skuLadder"]
    for p in d["pools"]:
        assert p["EquivalentSKU"] in d["skuLadder"]
        assert p["CapacityUnits"] == pytest.approx(p["DeployedUnits"] * d["unitsPerCu"], rel=1e-6)


def test_every_priced_failure_reports_both_revenue_bases():
    """ARR apportionment and consumption rate answer different questions, so
    both are shown rather than one taken on faith."""
    rows = [t for r in api.overview()["regions"]
            for t in api.region_detail(r["region"])["tickets"] if t["isFlagged"]]
    assert rows
    for t in rows:
        basis = t["consumptionBasis"]
        assert basis and basis["workingOut"]
        # Placeholder rates must announce themselves.
        assert basis["isPlaceholder"] is True
        # Recomputed from the figures the row prints. Those are rounded for
        # display -- capacity units to 2dp, hours to 1dp -- so a reader
        # checking it lands within a fraction of a percent rather than exactly,
        # the same tolerance the ARR basis carries.
        recomputed = basis["capacityUnits"] * basis["ratePerCuHour"] * basis["hours"]
        assert abs(recomputed - basis["amount"]) <= max(0.5, basis["amount"] * 0.001), (
            f"{t['incidentId']}: states {basis['amount']}, its own figures give {recomputed:.2f}")


def test_the_region_view_carries_a_recommendation_per_site():
    """Review: the recommendation must sit beside the data centre scope. The
    region page is where someone decides which building to open, and a cause
    on its own does not support that decision."""
    for region in api.overview()["regions"]:
        for site in api.region_detail(region["region"])["datacentres"]:
            recs = site["recommendations"]
            assert recs, site["datacentre"]
            # One entry per distinct cause -- a site with two problems needs
            # two fixes, and they may have different owners.
            assert len(recs) == site["reasonCount"]
            assert sum(r["count"] for r in recs) == site["failed"]
            for r in recs:
                assert r["action"] and r["handledBy"]
                assert r["needsHuman"] == (r["handledBy"] == "manual review")


def test_recommendations_are_computed_not_canned():
    """Review: "this is too general -- ChatGPT would say the same thing.
    Nothing calculatory is happening here." So a cause a module owns must
    produce a sentence containing that facility's own numbers, and two
    different sites with the same cause must not read identically.
    """
    from ontology import attribution

    canned = {m.get("action", "") for m in attribution.REASONS.values()}
    by_reason: dict[str, set] = {}

    for row in api.datacentres()["datacentres"]:
        d = api.datacentre_detail(row["datacentre"])
        for rec in d["recommendations"]:
            assert rec["action"]
            if rec["needsHuman"]:
                continue
            # Never the static string from the dict.
            assert rec["action"] not in canned, rec["reason"]
            # Names the facility it is about, and carries a number.
            assert row["datacentre"] in rec["action"], rec["action"]
            assert any(ch.isdigit() for ch in rec["action"])
            by_reason.setdefault(rec["reason"], set()).add(rec["action"])

    # The same cause at different facilities must yield different advice.
    assert any(len(v) > 1 for v in by_reason.values()), \
        "every site with the same cause got the same sentence"


def test_region_and_site_views_give_the_same_recommendation():
    """Two screens disagreeing about what to do would be worse than either
    being wrong, so both read from one generator."""
    for region in api.overview()["regions"]:
        for site in api.region_detail(region["region"])["datacentres"]:
            detail = api.datacentre_detail(site["datacentre"])
            assert ({r["reason"]: r["action"] for r in site["recommendations"]}
                    == {r["reason"]: r["action"] for r in detail["recommendations"]})


def test_the_two_failure_types_are_the_only_targets():
    """Review: "there is no point keeping denied-and-approved-within-SLA. Our
    targets are denied-then-approved-after-SLA-breach and denied-never-approved."
    Those two must account for every failure and every dollar of revenue loss;
    the in-SLA outcomes carry none.
    """
    ov = api.overview()
    c, k = ov["categoryCounts"], ov["kpis"]

    targets = c["denied_then_approved_late"] + c["denied_unfulfilled"]
    handled = c["no_denial"] + c["same_day_approved"]
    assert targets == k["failed"], "the two targets must be exactly the failures"
    assert targets + handled == k["total"], "and the four must still reconcile to 60"

    # Nothing handled inside SLA may carry revenue loss.
    in_sla = {ov["outcomeLabels"]["no_denial"], ov["outcomeLabels"]["same_day_approved"]}
    for region in ov["regions"]:
        for t in api.region_detail(region["region"])["tickets"]:
            if t["outcomeLabel"] in in_sla:
                assert not t["isFlagged"], t["incidentId"]
                assert t["exposure"] == 0, t["incidentId"]


def test_incident_registers_default_to_failures_only():
    """Review, twice: denied-then-approved-within-SLA is not a target and
    should not be sitting in the register. It is filtered out by default and
    the hidden count is stated rather than the rows silently vanishing."""
    src = (ROOT / "webapp" / "static" / "pages.js").read_text()

    # All three detail registers filter on isFlagged unless showAll is passed.
    assert src.count("|| showAll)") == 3, "region, customer and site registers must all filter"
    for page in ('PAGES["/region"] = async (view, name, showAll = false)',
                 'PAGES["/customer"] = async (view, sub, showAll = false)',
                 'PAGES["/datacentre"] = async (view, id, showAll = false)'):
        assert page in src, page
    # And each wires a toggle back to itself, so nothing is unreachable.
    assert src.count('wireRegisterToggle(view, "') == 3
    assert "handled within SLA are not listed" in src


def test_a_denial_reason_never_contradicts_the_sites_own_capacity():
    """The reason used to be drawn from a distribution keyed on the incident id,
    so canadacentral sat at 75% utilisation with 921 cores free while its
    requests were denied for "insufficient capacity" -- and the recommendation
    engine then computed threshold arithmetic for sites nowhere near their
    threshold. It is now derived from the site's actual position.
    """
    import ontology

    o = ontology.build()
    fact = o["fact_capacity_request"]
    sites = o["dim_datacentre"].set_index("DatacentreId")

    failed = fact[fact["DenialReason"] != ""]
    assert len(failed)

    for r in failed.itertuples():
        s = sites.loc[r.DatacentreId]
        wanted = float(r.AdditionalLimitCapacity)
        if r.DenialReason == "Insufficient capacity":
            assert wanted > float(s.FreeUnits), (
                f"{r.IncidentId}: denied for capacity but {s.FreeUnits} cores were free")
        elif r.DenialReason == "Threshold reached":
            assert wanted > float(s.HeadroomToThreshold), (
                f"{r.IncidentId}: denied on threshold but {s.HeadroomToThreshold} of headroom")
            assert wanted <= float(s.FreeUnits), (
                f"{r.IncidentId}: physically short, so the cause is capacity not policy")


def test_a_region_with_room_is_not_denied_for_lack_of_room():
    """The check a reviewer makes in one glance."""
    for region in api.overview()["regions"]:
        d = api.region_detail(region["region"])
        if d["coresFree"] <= 0:
            continue
        for site in d["datacentres"]:
            for rec in site["recommendations"]:
                if rec["reason"] != "Insufficient capacity":
                    continue
                # Only defensible where that site itself was short.
                assert site["coresFree"] < site["cores"], site["datacentre"]


def test_a_forecast_never_projects_more_capacity_than_exists():
    """Utilisation is a share of deployed capacity. Trend models happily run
    past 100% -- northcentralus projected to 106% -- which is not a forecast of
    anything, it is the line leaving the physical quantity behind."""
    for f in api.forecast_all()["forecasts"]:
        for p in f["projection"]:
            assert 0 <= p["value"] <= 100, f"{f['region']} projected {p['value']}%"
            assert 0 <= p["lower"] <= 100 and 0 <= p["upper"] <= 100
            assert p["lower"] <= p["value"] <= p["upper"]


def test_an_already_breached_region_reports_a_deadline_not_a_crossing():
    """Its crossing date is history. The decision-relevant number is when it
    fills completely."""
    breached = [f for f in api.forecast_all()["forecasts"] if f["alreadyBreached"]]
    assert breached
    for f in breached:
        assert f["crossingDate"] is None
        assert "safety line" in f["note"]
        if f["saturationDate"]:
            assert f["saturationDate"] > f["history"][-1]["date"]


def test_the_forecast_chart_explains_itself():
    """A reader could not tell whether the second line was a forecast or a
    different measure -- the guess was "tickets raised after that date". A chart
    that needs verbal explanation to be read is not finished."""
    src = (ROOT / "webapp" / "static" / "pages.js").read_text()

    # A key naming every mark on the chart.
    for mark in ("Measured", "Projected", "Range the forecast could be out by",
                 "Safety line"):
        assert mark in src, mark
    # An axis label, so the units are on the picture.
    assert "how full (%)" in src
    # And a plain-language reading of what the picture means.
    assert "In plain terms:" in src
    assert "not ticket counts" in src


def test_the_headline_date_is_always_on_the_chart():
    """Each panel is headlined by one date and the chart marks it. The trim must
    never cut that one off -- the other date may fall outside, and when it does
    the page has to say so instead of leaving it to be hunted on the axis."""
    for f in api.forecast_all()["forecasts"]:
        plotted = [p["date"] for p in f["projection"]]
        headline = f["saturationDate"] if f["alreadyBreached"] else f["crossingDate"]
        if headline:
            assert headline in plotted, \
                f"{f['region']}: headline date {headline} is off the trimmed chart"
        if f["saturationDate"]:
            assert f["saturationBeyondChart"] == (f["saturationDate"] > plotted[-1]), \
                f"{f['region']}: saturationBeyondChart disagrees with the chart"


def test_history_is_not_swamped_by_the_projection():
    """A chart that is mostly forecast reads as mostly finding. Untrimmed this
    was 71% projection against 149 days of real data."""
    for f in api.forecast_all()["forecasts"]:
        hist, proj = len(f["history"]), len(f["projection"])
        share = proj / (hist + proj)
        assert share <= 0.60, f"{f['region']}: chart is {share:.0%} forecast"


def test_extrapolation_past_the_fitted_window_is_declared():
    """Projecting further than the history it was fitted on is a real weakness
    and has to be stated on the page, not left in the axis for the reader."""
    for f in api.forecast_all()["forecasts"]:
        expected = len(f["projection"]) > len(f["history"])
        assert f["extrapolatedBeyondHistory"] == expected, f["region"]


def test_risk_scores_use_the_one_definition_of_failure():
    """The scorer counted `DenialReason != ""`, which includes a request denied
    and then approved inside its SLA -- the category review said must not be
    counted anywhere. Sites showed "0 failed" in the table and banded high on
    the score printed beside it."""
    for row in api.datacentres()["datacentres"]:
        assert row["risk"]["evidence"]["denied"] == row["failed"], (
            f"{row['datacentre']}: table says {row['failed']} failed, "
            f"score used {row['risk']['evidence']['denied']}")


def test_no_site_without_failures_is_called_high_risk():
    """Five sites with nothing wrong were banded high."""
    for row in api.datacentres()["datacentres"]:
        if row["failed"] == 0:
            assert row["risk"]["band"] != "high", \
                f"{row['datacentre']}: 0 failures but banded high"


def test_one_ticket_cannot_outrank_a_measured_record():
    """A site that failed its only request shows a 100% failure rate, which is
    arithmetic on one observation. Un-shrunk, the 12 riskiest sites in the
    product were all single-ticket sites and the ranking sorted noise."""
    thin = riskindex.score(requests=1, denied=1, unresolved=1, utilisation_pct=80,
                           threshold_pct=85, lead_time_days=30,
                           busiest_unresolved=1, prior_rate=0.5)
    solid = riskindex.score(requests=20, denied=20, unresolved=1, utilisation_pct=80,
                            threshold_pct=85, lead_time_days=30,
                            busiest_unresolved=1, prior_rate=0.5)
    assert thin.evidence["rawFailureRate"] == solid.evidence["rawFailureRate"] == 1.0
    assert thin.score < solid.score, "evidence must buy rank"
    assert thin.evidence["usedFailureRate"] < solid.evidence["usedFailureRate"]


def test_shrinkage_pulls_toward_the_fleet_rate_from_both_directions():
    """It is not a penalty on bad sites -- a site with no failures on one
    request is not proven clean either, and must move up."""
    worse = riskindex.score(requests=1, denied=1, unresolved=0, utilisation_pct=50,
                            threshold_pct=85, lead_time_days=10,
                            busiest_unresolved=1, prior_rate=0.5)
    better = riskindex.score(requests=1, denied=0, unresolved=0, utilisation_pct=50,
                             threshold_pct=85, lead_time_days=10,
                             busiest_unresolved=1, prior_rate=0.5)
    assert worse.evidence["usedFailureRate"] < 1.0, "100% must be pulled down"
    assert better.evidence["usedFailureRate"] > 0.0, "0% must be pulled up"


def test_the_evidence_behind_a_score_is_always_reported():
    """A score that cannot be taken apart has to be taken on trust, which is the
    thing this index exists to avoid."""
    for row in api.datacentres()["datacentres"]:
        e = row["risk"]["evidence"]
        assert e["requests"] == row["requests"]
        assert 0.0 <= e["usedFailureRate"] <= 1.0
        assert e["shrunk"] == (abs(e["usedFailureRate"] - e["rawFailureRate"]) > 1e-9)


def test_the_prior_is_the_measured_fleet_rate_not_a_guess():
    """Shrinking toward a hardcoded number would be a different assumption
    smuggled in. It must be the same measurement the entities are compared with."""
    onto = api.get_ontology()
    fact = onto["fact_capacity_request"]
    expected = len(api._failed_rows(fact)) / len(fact)
    assert api._fleet_failure_rate() == pytest.approx(expected)
    for row in api.datacentres()["datacentres"]:
        assert row["risk"]["evidence"]["priorRate"] == pytest.approx(round(expected, 3))


def test_arima_and_sarima_are_real_distinct_models():
    """Review asked for both by name. They are not two labels for one model:
    ARIMA has no seasonal term, so on a series with a weekly cycle it cannot
    reproduce what SARIMA reproduces exactly."""
    import numpy as np
    import forecast as fc

    if "sarima" not in fc.CANDIDATES:            # statsmodels absent
        pytest.skip("statsmodels not installed")

    season = np.array([0, 2, 4, 3, 1, -5, -5], dtype=float)
    t = np.arange(70)
    y = 50 + 0.5 * t + season[t % 7]
    truth = 50 + 0.5 * np.arange(70, 73) + season[np.arange(70, 73) % 7]

    sarima_err = float(np.mean(np.abs(fc.sarima(y, 3) - truth)))
    arima_err = float(np.mean(np.abs(fc.arima(y, 3) - truth)))
    assert sarima_err < 0.5, "SARIMA must recover a seasonal series"
    assert arima_err > sarima_err, "a non-seasonal model cannot match a seasonal one"


def test_trend_models_recover_a_straight_line():
    """The cheapest possible check that these are the methods they are named
    after rather than stubs: on y = 10..39 the next three values are 40, 41, 42
    and every trend model must say so exactly."""
    import numpy as np
    import forecast as fc

    y = np.arange(10, 40, dtype=float)
    for name in ("drift", "linear_trend", "theil_sen", "holt"):
        got = np.asarray(fc.CANDIDATES[name](y, 3), dtype=float)
        assert np.allclose(got, [40.0, 41.0, 42.0], atol=0.01), f"{name} gave {got}"
    # naive is a level model and must NOT extrapolate.
    assert np.allclose(fc.CANDIDATES["naive"](y, 3), [39.0, 39.0, 39.0])


def test_a_model_that_skipped_a_fold_cannot_win():
    """ARIMA and SARIMA can fail to converge on a given fold. Averaging over the
    folds it managed would let it beat models that sat every one."""
    import forecast as fc

    for f in api.forecast_all()["forecasts"]:
        chosen = next((s for s in f["scores"] if s["model"] == f["model"]), None)
        if chosen and f["model"] != "naive":
            assert chosen["complete"], \
                f"{f['region']}: {f['model']} won on {chosen['folds']} folds"


def test_declared_dependencies_cover_what_is_imported():
    """A clean `pip install -e .` must produce a working install. statsmodels
    was being imported without ever being declared."""
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with open(root / "pyproject.toml", "rb") as fh:
        declared = tomllib.load(fh)["project"]["dependencies"]
    names = {d.split(">")[0].split("=")[0].strip().lower() for d in declared}
    for required in ("pandas", "numpy", "scipy", "statsmodels", "scikit-learn"):
        assert required in names, f"{required} is imported but not declared"


def test_every_screen_reports_the_same_failure_count():
    """One definition of failure, everywhere. The policy simulator kept its own
    -- "short, or denied and later approved" -- and reported 45 where every
    other screen reported 30, because it swept in requests denied and then
    approved inside their SLA."""
    overview = api.overview()["kpis"]["failed"]
    policy = sum(r["actualFailures"] for r in api.capacity_policy()["regions"])
    datacentres = sum(r["failed"] for r in api.datacentres()["datacentres"])
    assert overview == policy, f"overview {overview} vs policy tab {policy}"
    assert overview == datacentres, f"overview {overview} vs site table {datacentres}"


def test_a_reserve_cannot_prevent_more_failures_than_occurred():
    """`wouldHavePrevented` is actual minus simulated. With two different failure
    definitions it could exceed the failures that actually happened."""
    for r in api.capacity_policy()["regions"]:
        assert r["wouldHavePrevented"] <= r["actualFailures"], r["region"]


def test_a_zero_arr_failure_still_carries_a_consumption_price():
    """The rate-card basis exists precisely so a Free-tier failure is not
    invisible. It was computed for every failure and rendered nowhere, so nine
    genuine failures -- one of them 138 days long -- read as $0.00 on screen."""
    rows = api.incidents()["incidents"]
    failures = [r for r in rows if r["isFlagged"]]
    assert failures, "expected flagged failures in this extract"
    assert all(r.get("consumptionBasis") for r in failures), \
        "every failure must carry the second valuation basis"

    zero_arr = [r for r in failures if r["exposure"] == 0]
    assert zero_arr, "this extract contains Free-tier failures priced at zero"
    for r in zero_arr:
        assert r["consumptionBasis"]["amount"] > 0, (
            f"{r['incidentId']}: priced $0 by ARR and $0 by rate card -- "
            "the failure would be invisible on every screen")


def test_the_rate_card_declares_itself_a_placeholder():
    """Published rates have not landed. Nothing may present these as real
    pricing, on screen or in an export."""
    for r in api.incidents()["incidents"]:
        basis = r.get("consumptionBasis")
        if basis:
            assert basis["isPlaceholder"] is True


def test_region_row_and_region_page_agree_on_what_is_owed():
    """The row and the page it opens must not show different figures. Cores
    pending was added to the table first and the detail page kept its own."""
    rows = {r["region"]: r for r in api.threshold()["regions"]}
    for region, row in rows.items():
        detail = api.region_detail(region)["threshold"]
        assert detail["cores_pending"] == row["cores_pending"], region
        assert detail["customers_waiting"] == row["customers_waiting"], region


def test_threshold_status_is_a_state_not_a_fault():
    """Review rejected "breached": a region using capacity it holds has not
    done anything wrong. The status is binary and the amount consumed is
    reported separately."""
    for r in api.threshold()["regions"]:
        over = r["current_utilisation_pct"] - r["threshold_pct"]
        assert r["at_risk"] == (over > 0), r["region"]
        if r["at_risk"]:
            assert r["threshold_used_pct"] == pytest.approx(round(over, 1), abs=0.05)
        else:
            assert r["threshold_used_pct"] == 0.0


def test_cores_pending_counts_capacity_not_tickets():
    """"Cores pending" is what the region owes. One ticket can be worth
    hundreds of cores, so it must not track the failure count."""
    rows = api.threshold()["regions"]
    assert any(r["cores_pending"] > 0 for r in rows)
    total = sum(r["cores_pending"] for r in rows)
    assert total > len(api.overview()["kpis"]) * 30, \
        "cores pending looks like a ticket count rather than capacity"


def test_region_recommendations_are_computed_not_canned():
    """A region whose threshold can absorb what it owes must get different
    advice from one that needs capacity added."""
    recs = {r["region"]: api.region_recommendation(r["region"])
            for r in api.threshold()["regions"]}
    actions = {r["action"] for r in recs.values()}
    assert len(actions) > 1, "every region received the same recommendation"
    for region, rec in recs.items():
        if rec["coresPending"] > 0:
            assert region in rec["headline"]
            covered = any(o["coversPending"] for o in rec["options"])
            # A region whose threshold can absorb what it owes is told to move
            # the threshold; one that cannot is told it needs capacity, and must
            # not be offered a lever that does not reach.
            if covered:
                assert rec["action"].startswith("Raising the safety line"), region
            else:
                assert "needs capacity added" in rec["action"], region


def test_each_region_is_judged_against_its_own_threshold():
    """Review: "each region has their own thresholds -- this is a high utilised
    region, why should I keep the same as a low utilisation region". One figure
    imposed on eleven regions is a policy nobody chose."""
    rows = api.threshold()["regions"]
    lines = {r["region"]: r["threshold_pct"] for r in rows}
    assert len(set(lines.values())) > 1, "every region is on the same threshold"
    assert api.threshold()["thresholdIsPerRegion"] is True


def test_a_region_threshold_matches_the_sites_it_is_made_of():
    """The region's line is the capacity-weighted mean of its facilities', so a
    region cannot advertise a safety line its buildings are not holding."""
    onto = api.get_ontology()
    sites = onto["dim_datacentre"]
    for r in onto["dim_region"].itertuples():
        here = sites[sites["Region"] == r.Region]
        expected = ((here["DeployedUnits"] * here["ThresholdPct"]).sum()
                    / here["DeployedUnits"].sum())
        assert r.ThresholdPct == pytest.approx(round(expected, 1), abs=0.05), r.Region
        assert here["ThresholdPct"].min() <= r.ThresholdPct <= here["ThresholdPct"].max()


def test_forcing_one_threshold_still_works_as_a_what_if():
    """The control has to recalculate, not filter -- and must not quietly become
    the default again."""
    forced = api.threshold(pct=95.0)
    assert forced["thresholdIsPerRegion"] is False
    assert all(r["threshold_pct"] == 95.0 for r in forced["regions"])
    assert len(forced["regions"]) == len(api.threshold()["regions"])


def test_both_tabs_use_the_same_per_region_threshold():
    """The Forecast tab judging a region against 85% while the Regions tab uses
    83% is how one region ends up with two crossing dates again."""
    forecasts = {f["region"]: f for f in api.forecast_all()["forecasts"]}
    for r in api.threshold()["regions"]:
        assert forecasts[r["region"]]["thresholdPct"] == r["threshold_pct"], r["region"]


def test_demand_spikes_are_attributed_to_a_recorded_event_not_inferred():
    """Review asked for the spikes to be highlighted and explained. The link
    comes from the event record itself, so a month is only called deal-driven
    when an event names the incident in it."""
    onto = api.get_ontology()
    linked = {str(i) for i in onto["fact_event"]["LinkedIncidentId"].dropna()}
    for region in sorted(onto["dim_region"]["Region"]):
        d = api.demand_region(region)
        for month in d["demand"]:
            assert month["eventDriven"] == bool(month["events"]), region
            for ev in month["events"]:
                assert ev["type"] and ev["date"], f"{region} {month['month']}"
        assert d["demand"] == sorted(d["demand"], key=lambda m: m["month"])
    assert linked, "this extract links events to incidents"


def test_the_demand_baseline_excludes_the_spikes_it_measures():
    """Taking the median of every month would let a signed deal raise the very
    line it is supposed to stand out from."""
    for region in sorted(api.get_ontology()["dim_region"]["Region"]):
        d = api.demand_region(region)
        spikes = [m for m in d["demand"] if m["eventDriven"]]
        if spikes and len(d["demand"]) > len(spikes):
            assert d["baselineCores"] < max(m["cores"] for m in spikes), region


def test_a_site_reports_its_own_demand_and_defers_on_utilisation():
    """Tickets carry a facility, so per-site demand is real. Utilisation is only
    recorded per region, so the site must say where that chart lives rather than
    splitting a regional curve ten ways and implying a measurement nobody made."""
    d = api.demand_datacentre("southcentralus-dc01")
    assert d["scope"] == "datacentre" and d["region"] == "southcentralus"
    assert d["thresholdSeries"] == []
    assert "per region" in d["thresholdSeriesNote"]


def test_the_threshold_series_is_measured_against_the_regions_own_line():
    for region in sorted(api.get_ontology()["dim_region"]["Region"]):
        d = api.demand_region(region)
        own = api._region_threshold(region)
        assert d["thresholdPct"] == pytest.approx(own)
        for s in d["thresholdSeries"]:
            assert s["thresholdPct"] == pytest.approx(round(own, 1))
            assert s["deltaPct"] == pytest.approx(
                round(s["utilisationPct"] - s["thresholdPct"], 2), abs=0.02)


def test_customer_history_never_moves_a_reported_figure():
    """The generated series lives in its own table. Merging it into
    fact_capacity_request would have padded exposure, failure counts and cores
    pending with invented tickets -- figures already reviewed and published."""
    k = api.overview()["kpis"]
    assert k["total"] == 60 and k["failed"] == 30
    assert k["exposure"] == pytest.approx(146470.16)
    assert k["customers"] == 15

    onto = api.get_ontology()
    demand = onto["fact_customer_demand_monthly"]
    assert len(demand) > len(onto["fact_capacity_request"])
    assert "fact_customer_demand_monthly" not in {"fact_capacity_request"}


def test_real_customer_months_beat_the_generated_ones():
    """Where the extract has an answer, the extract wins."""
    import pandas as pd

    onto = api.get_ontology()
    fact = onto["fact_capacity_request"]
    when = pd.to_datetime(fact["DeniedDate"].fillna(fact["ApprovedDate"]), errors="coerce")
    obs = fact.assign(M=when.dt.tz_localize(None).dt.to_period("M").astype(str))
    real = (obs.groupby(["SubscriptionId", "M"])["AdditionalLimitCapacity"]
            .sum().round(1).to_dict())

    demand = onto["fact_customer_demand_monthly"]
    for row in demand[~demand["IsSynthetic"]].itertuples():
        key = (row.SubscriptionId, row.Month)
        assert key in real, f"{key} marked real but is not in the extract"
        assert row.CoresRequested == pytest.approx(real[key], abs=0.05)


def test_a_mostly_generated_customer_series_declares_itself():
    """A customer-level forecast shown without saying the history was invented
    would be the most misleading screen in the product."""
    subs = api.get_ontology()["dim_subscription"]["SubscriptionId"].astype(str)
    for sub in list(subs)[:6]:
        d = api.demand_customer(sub)
        assert d["realMonths"] <= len(d["demand"])
        assert d["historyIsMostlySynthetic"] == (d["realMonths"] < len(d["demand"]) / 2)
        assert all(m["isReal"] in (True, False) for m in d["demand"])


def test_customer_demand_is_deterministic():
    """Two builds must give byte-identical history, or every screenshot and
    every figure quoted from one changes under the reader."""
    import ontology as onto_mod

    a = onto_mod.build()["fact_customer_demand_monthly"]
    b = onto_mod.build()["fact_customer_demand_monthly"]
    assert a.equals(b)


def test_demand_is_forecast_at_every_level():
    """Review asked where the forecast was. Region and site demand had history
    only -- the projection existed on the customer path and nowhere else."""
    for d in (api.demand_region("canadacentral"),
              api.demand_datacentre("southcentralus-dc01")):
        assert "projection" in d and "model" in d
    d = api.demand_region("canadacentral")
    assert d["projection"], "region demand carries no forecast"
    assert all(p["cores"] >= 0 for p in d["projection"])
    months = [m["month"] for m in d["demand"]]
    assert all(p["month"] > months[-1] for p in d["projection"]), \
        "the forecast must start after the history ends"


def test_filled_demand_months_match_the_level_of_the_real_ones():
    """Uncalibrated, the generated months came out at 235-1413 cores against
    16-322 real ones, so the chart showed demand collapsing exactly where the
    real data began -- an artefact read as a finding."""
    for region in sorted(api.get_ontology()["dim_region"]["Region"]):
        d = api.demand_region(region)
        real = [m["cores"] for m in d["demand"] if m["isReal"] and not m["eventDriven"]]
        fill = [m["cores"] for m in d["demand"] if not m["isReal"] and not m["eventDriven"]]
        if len(real) >= 2 and len(fill) >= 2:
            import statistics
            assert statistics.median(fill) == pytest.approx(
                statistics.median(real), rel=0.75), region


def test_generated_demand_months_are_marked_as_such():
    """A reader must be able to tell which part of the line is recorded."""
    d = api.demand_region("canadacentral")
    assert 0 < d["realMonths"] < len(d["demand"])
    assert all("isReal" in m for m in d["demand"])
    assert [m["month"] for m in d["demand"]] == sorted(m["month"] for m in d["demand"])
