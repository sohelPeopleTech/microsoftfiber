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
import admission  # noqa: E402
import forecast as forecast_module  # noqa: E402
import ratecard  # noqa: E402
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


def test_throttling_can_outrank_utilisation():
    """Urgency is not a ranking of how full something is.

    The version of this test that stood here checked the property through lead
    time: somewhere in the fleet, a region less full than another needed its
    order raised sooner because its hardware took longer to arrive. Fabric has
    no hardware and no order, so that particular illustration is gone -- but the
    property it protected is the whole reason this product exists, and it now
    holds through the thing that actually hurts.

    A capacity averaging sixty per cent that spends a week refusing queries is a
    worse problem than one sitting steadily at ninety and refusing nothing. If
    that ever stops being visible, the product is ranking by utilisation and
    calling it urgency.
    """
    sites = [d for d in api.datacentres()["datacentres"] if d["requests"]]
    assert len(sites) >= 2

    scored = [(d["datacentre"], d["siteUtilisationPct"],
               (d["risk"] or {}).get("components", {}).get("throttling", 0.0),
               (d["risk"] or {}).get("score", 0.0))
              for d in sites]

    inversions = [(a[0], b[0]) for a in scored for b in scored
                  if a[1] < b[1]              # a is less full
                  and a[2] > b[2]             # but more of it is refusing work
                  and a[3] > b[3]]            # and it outranks b
    assert inversions, (
        "no site that is less full than another outranks it on throttling -- "
        "the risk score is tracking utilisation, which the utilisation column "
        "already shows")


def test_needing_action_means_the_decision_falls_in_this_review_cycle():
    """The KPI counts regions at or past their safety line.

    This used to assert that anything actionable had `daysUntilAction <= 0`,
    which held only because "overdue" was the only amber state that ever fired
    -- and it fired because a hardware provisioning lead time outran the days
    left before a crossing. There is no lead time now: a region is actionable
    when it is already over the line, or when the decision falls inside the
    review cycle, so a positive number is the normal case for an amber region.

    Three states, not two. `daysUntilAction is None` means the backtested model
    projects no crossing at all, which is the opposite of urgent -- reading it
    as 0 counted the calmest regions as the most pressing.
    """
    from module1.threshold import DEFAULT_REVIEW_DAYS

    ov = api.overview()
    due_statuses = ("breached", "overdue", "due_now")
    for region in ov["regions"]:
        days, status = region["daysUntilAction"], region["status"]
        if days is None:
            assert status not in due_statuses, (
                f"{region['region']}: {status} with no projected crossing")
            continue
        assert (status in due_statuses) == (days <= DEFAULT_REVIEW_DAYS), (
            f"{region['region']}: {status} with {days} days to decide, against a "
            f"{DEFAULT_REVIEW_DAYS}-day review cycle")
        if status == "breached":
            assert days <= 0, f"{region['region']} is breached but not yet due"


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
    """Region -> capacity pool -> ticket is the drill-down an engineer works in.
    A ticket attributed to a capacity pool in the wrong region would make the
    region totals and the data-centre totals disagree."""
    import dimensional

    entities = dimensional.build()
    fact, dim = entities["fact_capacity_request"], entities["dim_datacentre"]
    by_dc = dict(zip(dim["DatacentreId"], dim["Region"], strict=True))

    assert fact["DatacentreId"].notna().all(), "every ticket needs a capacity pool"
    for row in fact.itertuples():
        assert by_dc[row.DatacentreId] == row.Region, row.IncidentId


def test_attribution_is_deterministic():
    """Both new columns are derived, not stored. If they moved between runs, a
    figure quoted in a review would stop being true the next morning -- the
    same defect that forced the reporting pack to be rebuilt."""
    import dimensional

    signatures = set()
    for _ in range(3):
        o = dimensional.build()
        f = o["fact_capacity_request"].sort_values("IncidentId")
        signatures.add((tuple(f["DatacentreId"]), tuple(f["DenialReason"])))
    assert len(signatures) == 1


def test_a_reason_is_recorded_for_every_refusal_and_nothing_else():
    """A reason on a request that was never refused invites someone to count it."""
    import dimensional

    fact = dimensional.build()["fact_capacity_request"]
    refused = fact["DeniedDate"].notna()
    has_reason = fact["DenialReason"].astype(str) != ""

    assert (has_reason & ~refused).sum() == 0, "reason set on a request never denied"
    assert has_reason.sum() > 0
    # The unknown bucket must actually contain something, or the human-review
    # path cannot be shown to exist.
    from dimensional.attribution import UNKNOWN_REASON
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
    import dimensional

    fact = dimensional.build()["fact_capacity_request"]
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
                {"failureRate": .5, "pressure": .5, "unresolved": .5, "throttling": .5},
                {"failureRate": -.1, "pressure": .4, "unresolved": .4, "throttling": .3}):
        with _pytest.raises(ValueError):
            riskindex.resolve_weights(bad)


def test_changing_the_weights_changes_the_score():
    """Otherwise the setting is decorative."""
    import riskindex

    args = dict(requests=1, denied=1, unresolved=1, utilisation_pct=97.2,
                threshold_pct=85, throttling_share=1.00, busiest_unresolved=2)
    default = riskindex.score(**args).score
    even = riskindex.score(**args, weights={
        "failureRate": .25, "pressure": .25, "unresolved": .25, "throttling": .25}).score
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
    two columns the dimensional model adds. Reading them off that frame with getattr
    silently produced "" for every row -- the Capacity pool column rendered blank
    and every Reason showed a dash, while the panel directly above the table
    listed the same reasons correctly.
    """
    import dimensional

    fact = dimensional.build()["fact_capacity_request"]
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


def test_the_scale_calculator_works_on_a_capacity_not_a_building():
    """You scale a capacity, not a country and not a building.

    The calculator this replaced asked which capacity pool to take offline and
    which hardware class to convert it to. Fabric exposes neither, and the unit
    an admin actually changes is one capacity's F SKU.
    """
    opt = api.scale_options_index()
    assert opt["capacities"] and opt["regions"] and opt["skuLadder"]
    for c in opt["capacities"]:
        assert c["capacityId"] and c["region"] and c["sku"]
        assert c["capacityUnits"] > 0

    # Worst first: the list exists to be acted on.
    days = [c["throttledDays"] for c in opt["capacities"]]
    assert days == sorted(days, reverse=True)

    cap = opt["capacities"][0]
    target = next(k for k in opt["skuLadder"]
                  if opt["skuLadder"][k] > cap["capacityUnits"])
    r = api.scale_capacity(cap["capacityId"], target)

    assert r["current"]["sku"] == cap["sku"]
    assert r["selected"]["sku"] == target
    assert r["selected"]["capacityUnits"] == opt["skuLadder"][target]
    # The whole point of the reframe: there is nothing to wait for.
    assert r["selected"]["immediate"] is True
    assert "leadTime" not in str(r) and "lead_time" not in str(r)


def test_scaling_up_relieves_the_measured_peak():
    """Utilisation is consumption over a ceiling, and only the ceiling moves.

    If this drifts, the calculator is telling someone a bigger SKU will not help
    when it will, or the reverse.
    """
    opt = api.scale_options_index()
    cap = opt["capacities"][0]
    r = api.scale_capacity(cap["capacityId"])
    cur = r["current"]
    for o in r["options"]:
        expected = cur["peakPct"] * cur["capacityUnits"] / o["capacityUnits"]
        assert o["peakAfterPct"] == pytest.approx(expected, abs=0.15), o["sku"]
        if o["capacityUnits"] > cur["capacityUnits"]:
            assert o["peakAfterPct"] < cur["peakPct"]


def test_the_calculator_and_the_recommendation_engines_never_disagree():
    """Two things in the product answer "should this be scaled". They have to
    agree, or a reader gets a second opinion nobody asked for.

    An earlier version of the calculator picked a rung whenever a larger one
    scored better -- which is always -- and recommended upgrading 272 of 317
    capacities, including an F64 running at 42%.
    """
    from planning import recommend

    entities = api.get_entities()
    wants_up = {r.target for r in recommend.scale_up(entities)}
    wants_down = {r.target for r in recommend.scale_down(entities)}
    ladder = api.scale_options_index()["skuLadder"]

    for c in api.scale_options_index()["capacities"]:
        r = api.scale_capacity(c["capacityId"])
        rec = r["recommended"]
        up = bool(rec) and ladder[rec] > c["capacityUnits"]
        down = bool(rec) and ladder[rec] < c["capacityUnits"]
        assert up == (c["capacityId"] in wants_up), (
            f"{c['capacityId']}: calculator says up={up}, scale_up says "
            f"{c['capacityId'] in wants_up}")
        assert down == (c["capacityId"] in wants_down), c["capacityId"]


def test_the_calculator_names_what_else_a_move_costs():
    """Two lines on the ladder cost something other than compute to cross, and
    a calculator that only reported CU would let someone walk into either."""
    from planning import FREE_VIEWER_CU

    r = api.scale_capacity(api.scale_options_index()["capacities"][0]["capacityId"])
    cur = r["current"]["capacityUnits"]
    for o in r["options"]:
        assert o["gainsFreeViewers"] == (cur < FREE_VIEWER_CU <= o["capacityUnits"])
        assert o["losesFreeViewers"] == (o["capacityUnits"] < FREE_VIEWER_CU <= cur)
    assert any(o["crossesSlowBoundary"] for o in r["options"]) or cur >= 512


def test_scaling_to_the_sku_it_already_runs_is_rejected():
    """A no-op that returns a result looks like an answer."""
    from fastapi import HTTPException

    cap = api.scale_options_index()["capacities"][0]
    with pytest.raises(HTTPException) as exc:
        api.scale_capacity(cap["capacityId"], cap["sku"])
    assert exc.value.status_code == 400
    assert "already" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        api.scale_capacity("nowhere-dc01-cap09", "F64")
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        api.scale_capacity(cap["capacityId"], "F7")
    assert exc.value.status_code == 400


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


def test_every_site_in_the_region_is_listed():
    """The table used to show only sites carrying a denial, which hid seven of
    southcentralus's ten buildings -- all ten of which are past their own safety
    threshold. The page leads with threshold status, so a table filtered by
    failures was answering a different question from the one in the heading.

    A capacity pool over its line with nothing yet failed is precisely the case
    worth seeing, because it is the one still cheap to fix.
    """
    entities = api.get_entities()
    for region in api.overview()["regions"]:
        name = region["region"]
        listed = {x["datacentre"] for x in api.region_detail(name)["datacentres"]}
        expected = set(entities["dim_datacentre"]
                       .loc[entities["dim_datacentre"]["Region"] == name, "DatacentreId"]
                       .astype(str))
        assert listed == expected, f"{name}: {expected - listed} missing from the table"


def test_the_region_reports_over_threshold_and_activity_separately():
    """Two different counts that were being conflated: how many sites are past
    their line, and how many have had a request raised against them."""
    for region in api.overview()["regions"]:
        d = api.region_detail(region["region"])
        assert d["sitesOverThreshold"] == sum(
            1 for x in d["datacentres"] if x["overThreshold"])
        assert d["sitesWithActivity"] == sum(
            1 for x in d["datacentres"] if x["requests"] > 0)
        for x in d["datacentres"]:
            assert x["overThreshold"] == (x["utilisationPct"] > x["thresholdPct"])


def test_every_listed_site_reports_its_capacity_position():
    """Review asked for CU held, CU left and the site's own threshold."""
    for region in api.overview()["regions"]:
        for x in api.region_detail(region["region"])["datacentres"]:
            for key in ("capacityUnits", "capacityUnitsFree", "thresholdPct",
                        "headroom", "revenueLoss"):
                assert x[key] is not None, f"{x['datacentre']} missing {key}"
            assert 0 < x["thresholdPct"] <= 100


def test_each_denial_cause_carries_computed_remediation():
    """"If you gave this to ChatGPT it would say the same thing" -- so a cause
    the platform owns must produce arithmetic for that facility, not prose."""
    seen_scale = seen_threshold = False
    for row in api.datacentres()["datacentres"]:
        d = api.datacentre_detail(row["datacentre"])
        for rec in d["recommendations"]:
            assert rec["action"]
            if rec.get("scale"):
                seen_scale = True
                for m in rec["scale"]:
                    # Computed from this building's own capacities.
                    assert m["capacityId"].startswith(row["datacentre"])
                    assert m["cuAfter"] > m["cuBefore"]
                    assert m["toSku"] != m["fromSku"]
                    # There is nothing to wait for, and the text must not imply
                    # there is -- that was the whole defect in the old model.
                    assert m["immediate"] is True
                    assert "throttling" not in m
            if rec.get("threshold"):
                seen_threshold = True
                for o in rec["threshold"]:
                    assert 0 < o["thresholdPct"] <= 100
                    assert o["releasesCores"] > 0
    assert seen_scale, "no capacity-owned cause produced scale options"
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


def test_the_rate_card_still_computes_even_though_it_is_not_shown():
    """The second valuation basis was removed from the screen on review: where
    ARR already gives a figure the two land within a few percent of each other
    and add only the question of which one counts, and the rates are
    placeholders rather than the published Fabric price.

    The module stays, because the blind spot it covers is real -- ARR cannot
    price a failure against a customer with no revenue -- and it returns the
    moment Finance supplies real rates. Testing it here keeps it working rather
    than letting it rot until someone needs it.
    """
    est = ratecard.estimate(units_unavailable=10, days=18.9)
    assert est.amount > 0
    assert est.is_placeholder is True, "placeholder rates must announce themselves"
    assert est.capacity_units == pytest.approx(10 * admission.UNITS_PER_CU)
    assert est.hours == pytest.approx(18.9 * ratecard.HOURS_PER_DAY)
    assert est.amount == pytest.approx(
        est.capacity_units * est.rate_per_cu_hour * est.hours)
    assert "capacity units" in est.working_out


def test_no_screen_shows_a_placeholder_price_beside_a_real_one():
    """Removed from the ticket rows, and it must not creep back: a made-up
    figure sitting next to a measured one is worse than not showing it."""
    rows = api.incidents()["incidents"]
    assert rows
    assert all("consumptionBasis" not in r for r in rows), \
        "the placeholder rate card is back on the incident rows"


def test_the_region_view_carries_a_recommendation_per_site():
    """Review: the recommendation must sit beside the capacity pool scope. The
    region page is where someone decides which building to open, and a cause
    on its own does not support that decision."""
    for region in api.overview()["regions"]:
        for site in api.region_detail(region["region"])["datacentres"]:
            recs = site["recommendations"]
            # A site with no failure has no cause and so no recommendation --
            # it is listed because it is over its threshold, not because
            # something went wrong there.
            if not site["failed"]:
                assert recs == [], site["datacentre"]
                continue
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
    from dimensional import attribution

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
    import dimensional

    o = dimensional.build()
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
        if d["capacityUnitsFree"] <= 0:
            continue
        for site in d["datacentres"]:
            for rec in site["recommendations"]:
                if rec["reason"] != "Insufficient capacity":
                    continue
                # Only defensible where that site itself was short.
                assert site["capacityUnitsFree"] < site["capacityUnits"], site["datacentre"]


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
    was 71% projection against 149 days of real data.

    The bound is 65% rather than something tighter because two requirements pull
    against each other: the headline date must be on the chart (asserted just
    above) and history should keep a meaningful share of it. A region whose
    crossing is genuinely far out cannot satisfy both, and of the two, silently
    cropping the date the page reports is the worse failure. 65% leaves history
    a third of the frame; below that the trim is not doing its job.
    """
    for f in api.forecast_all()["forecasts"]:
        hist, proj = len(f["history"]), len(f["projection"])
        share = proj / (hist + proj)
        assert share <= 0.65, f"{f['region']}: chart is {share:.0%} forecast"


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
                           threshold_pct=85, throttling_share=0.67,
                           busiest_unresolved=1, prior_rate=0.5)
    solid = riskindex.score(requests=20, denied=20, unresolved=1, utilisation_pct=80,
                            threshold_pct=85, throttling_share=0.67,
                            busiest_unresolved=1, prior_rate=0.5)
    assert thin.evidence["rawFailureRate"] == solid.evidence["rawFailureRate"] == 1.0
    assert thin.score < solid.score, "evidence must buy rank"
    assert thin.evidence["usedFailureRate"] < solid.evidence["usedFailureRate"]


def test_shrinkage_pulls_toward_the_fleet_rate_from_both_directions():
    """It is not a penalty on bad sites -- a site with no failures on one
    request is not proven clean either, and must move up."""
    worse = riskindex.score(requests=1, denied=1, unresolved=0, utilisation_pct=50,
                            threshold_pct=85, throttling_share=0.22,
                            busiest_unresolved=1, prior_rate=0.5)
    better = riskindex.score(requests=1, denied=0, unresolved=0, utilisation_pct=50,
                             threshold_pct=85, throttling_share=0.22,
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
    entities = api.get_entities()
    fact = entities["fact_capacity_request"]
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


def test_a_zero_arr_failure_still_says_why_it_is_zero():
    """Nine failures price at zero because the customer is Free-tier, one of
    them 138 days long. With the rate card off the screen, the words are the
    only thing standing between that and a row that reads like a broken
    calculator."""
    zero = [r for r in api.incidents()["incidents"]
            if r["isFlagged"] and r["exposure"] == 0]
    assert zero, "this extract contains Free-tier failures priced at zero"
    for r in zero:
        working = (r.get("workingOut") or "").lower()
        assert "free-tier" in working or "no recorded revenue" in working, \
            f"{r['incidentId']}: priced $0 with no explanation of why"
        assert "not excused" in working or "delay" in working, \
            f"{r['incidentId']}: $0 stated without noting the delay still happened"


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
    entities = api.get_entities()
    sites = entities["dim_datacentre"]
    for r in entities["dim_region"].itertuples():
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
    entities = api.get_entities()
    linked = {str(i) for i in entities["fact_event"]["LinkedIncidentId"].dropna()}
    for region in sorted(entities["dim_region"]["Region"]):
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
    for region in sorted(api.get_entities()["dim_region"]["Region"]):
        d = api.demand_region(region)
        spikes = [m for m in d["demand"] if m["eventDriven"]]
        if spikes and len(d["demand"]) > len(spikes):
            assert d["baselineCores"] < max(m["cores"] for m in spikes), region


def test_a_site_reports_its_own_demand_and_its_own_utilisation():
    """Tickets carry a facility, so per-site demand is real -- and so is per-site
    utilisation, which this endpoint used to deny having.

    It returned an empty series and a note saying utilisation was recorded per
    region only, so the chart lived on the region page. The first half was
    wrong: `fact_capacity_cu_daily` is one row per capacity per day and every
    capacity names its building, so the site has a measured series of its own.
    It is not the region's curve divided ten ways -- these assertions exist so
    nobody can quietly make it that.
    """
    d = api.demand_datacentre("southcentralus-dc01")
    assert d["scope"] == "datacentre" and d["region"] == "southcentralus"
    assert d["thresholdSeries"], "the site must carry its own utilisation series"
    assert not d["thresholdSeriesNote"], "there is nothing left to apologise for"
    assert d["thresholdSeriesProvenance"], (
        "a series built on generated CU consumption must say so on the page")

    own = api._site_threshold("southcentralus-dc01")
    assert d["thresholdPct"] == pytest.approx(round(own, 1))
    for s in d["thresholdSeries"]:
        assert s["thresholdPct"] == pytest.approx(round(own, 1))
        assert s["deltaPct"] == pytest.approx(
            round(s["utilisationPct"] - s["thresholdPct"], 2), abs=0.02)

    # Sites in one region must not all report the identical curve, which is what
    # splitting the regional series across them would have produced.
    siblings = [dc for dc, m in api._site_meta().items()
                if m["region"] == "southcentralus"][:4]
    curves = {tuple(round(p["utilisationPct"], 2) for p in
                    api.demand_datacentre(dc)["thresholdSeries"]) for dc in siblings}
    assert len(curves) > 1, (
        "every site in the region reported the same utilisation curve, which "
        "means the region's series is being divided rather than measured")


def test_a_bursting_site_is_not_drawn_off_the_top_of_its_chart():
    """100% is a ceiling for a region, not for a building.

    Region utilisation is used over deployed capacity and cannot exceed 100%, so
    the chart clamped its axis there. A Fabric capacity can consume more CU than
    it holds -- bursting, which Fabric smooths over future timepoints -- so a
    site can genuinely run past 100%: westeurope-dc04 sits near 185%. With the
    axis clamped, that line was drawn outside the viewBox and was invisible.
    """
    from pathlib import Path as _P

    js = (_P(__file__).resolve().parents[1] / "webapp" / "static" / "pages.js").read_text()
    assert "const hi = Math.min(100, Math.ceil(Math.max(...all) + 2));" not in js, (
        "the forecast chart clamps its axis to 100% unconditionally, which hides "
        "any bursting site's history line entirely")

    # And the data really does go there, so this is not a hypothetical.
    usage = api._site_usage_daily()
    over = usage[usage["UtilisationPct"] > 100]
    assert len(over), "no site bursts, so this guard is protecting nothing"


def test_every_data_centre_can_be_forecast_on_its_own_record():
    """The forecast moved off its own tab and onto the thing being forecast.

    A region-level crossing date says a geography is filling up, which nobody
    can act on; this says which building fills first, at the grain where an F
    SKU can actually be scaled. Every site has a complete daily record, so no
    site should be answering "not enough history".
    """
    sites = sorted(api._site_meta())
    assert len(sites) > 100, "the whole fleet should be forecastable, not a sample"
    for dc in sites[:6]:
        f = api.forecast_site(dc)
        assert f["datacentre"] == dc
        assert f["scope"] == "datacentre"
        assert f["model"] != "none", f"{dc} produced no model at all"
        assert len(f["history"]) >= forecast_module.MIN_HISTORY, dc
        assert f["projection"], f"{dc} produced no projection"
        # Judged against its own line, not its region's.
        assert f["thresholdPct"] == pytest.approx(api._site_threshold(dc))
        # Utilisation is a share of deployed capacity; nothing above 100% is a
        # forecast of anything.
        assert all(0 <= p["value"] <= 100 for p in f["projection"]), dc
        assert f["provenance"], "a generated series must carry its provenance"


def test_a_site_forecast_and_the_region_table_cannot_disagree():
    """One forecast per building, shared by everything that quotes it.

    The Regions tab and the Forecast tab once each fitted their own and
    disagreed by up to ten days about the same region. The columns that moved
    down to the capacity pools must not reintroduce that: `hitsThresholdIn` on the
    region page has to be the same forecast the site page draws.
    """
    import pandas as pd

    for dc in sorted(api._site_meta())[:6]:
        pos = api._site_position(dc)
        f = api.forecast_site(dc)
        assert pos["crossingDate"] == f["crossingDate"], dc
        assert pos["forecastModel"] == f["model"], dc
        if f["alreadyBreached"]:
            assert pos["hitsThresholdIn"] == 0, dc
        elif f["crossingDate"]:
            last = pd.Timestamp(f["history"][-1]["date"])
            assert pos["hitsThresholdIn"] == (
                pd.Timestamp(f["crossingDate"]) - last).days, dc
        else:
            assert pos["hitsThresholdIn"] is None, dc


def test_the_columns_that_left_the_regions_table_arrived_at_the_sites():
    """Review moved four columns off the Regions tab because they are questions
    about a building being asked of a geography. They have to exist at the grain
    they moved to, or the move was a deletion."""
    d = api.region_detail("southcentralus")
    assert d["datacentres"], "no sites to carry the columns"
    for site in d["datacentres"]:
        for field in ("hitsThresholdIn", "cuToStayUnder", "smallestSkuStep",
                      "cuPending", "customersWaiting"):
            assert field in site, f"{site['datacentre']} is missing {field}"
        # A site under its own line needs nothing added; one over it does.
        if site["utilisationPct"] > site["thresholdPct"]:
            assert site["cuToStayUnder"] > 0, site["datacentre"]
        assert site["cuPending"] >= 0 and site["customersWaiting"] >= 0

    # What the region owes is what its buildings owe, give or take rounding.
    assert sum(s["cuPending"] for s in d["datacentres"]) == pytest.approx(
        d["threshold"]["cores_pending"], abs=1.0)


def test_the_threshold_series_is_measured_against_the_regions_own_line():
    for region in sorted(api.get_entities()["dim_region"]["Region"]):
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

    entities = api.get_entities()
    demand = entities["fact_customer_demand_monthly"]
    assert len(demand) > len(entities["fact_capacity_request"])
    assert "fact_customer_demand_monthly" not in {"fact_capacity_request"}


def test_real_customer_months_beat_the_generated_ones():
    """Where the extract has an answer, the extract wins."""
    import pandas as pd

    entities = api.get_entities()
    fact = entities["fact_capacity_request"]
    when = pd.to_datetime(fact["DeniedDate"].fillna(fact["ApprovedDate"]), errors="coerce")
    obs = fact.assign(M=when.dt.tz_localize(None).dt.to_period("M").astype(str))
    real = (obs.groupby(["SubscriptionId", "M"])["AdditionalLimitCapacity"]
            .sum().round(1).to_dict())

    demand = entities["fact_customer_demand_monthly"]
    for row in demand[~demand["IsSynthetic"]].itertuples():
        key = (row.SubscriptionId, row.Month)
        assert key in real, f"{key} marked real but is not in the extract"
        assert row.CoresRequested == pytest.approx(real[key], abs=0.05)


def test_a_mostly_generated_customer_series_declares_itself():
    """A customer-level forecast shown without saying the history was invented
    would be the most misleading screen in the product."""
    subs = api.get_entities()["dim_subscription"]["SubscriptionId"].astype(str)
    for sub in list(subs)[:6]:
        d = api.demand_customer(sub)
        assert d["realMonths"] <= len(d["demand"])
        assert d["historyIsMostlySynthetic"] == (d["realMonths"] < len(d["demand"]) / 2)
        assert all(m["isReal"] in (True, False) for m in d["demand"])


def test_customer_demand_is_deterministic():
    """Two builds must give byte-identical history, or every screenshot and
    every figure quoted from one changes under the reader."""
    import dimensional as onto_mod

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
    for region in sorted(api.get_entities()["dim_region"]["Region"]):
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


def test_generated_demand_months_carry_the_requests_behind_their_cores():
    """A chart showing capacity requested with nothing having requested it is
    the first thing anyone asks about, and it had no answer."""
    for region in sorted(api.get_entities()["dim_region"]["Region"]):
        for m in api.demand_region(region)["demand"]:
            if m["cores"] > 0:
                assert m["tickets"] > 0, f"{region} {m['month']}: cores with no requests"


def test_generated_months_never_sit_inside_the_recorded_window():
    """A month between two recorded months with no tickets is not missing data --
    nothing was asked for. Filling it invents demand that provably did not exist."""
    for region in sorted(api.get_entities()["dim_region"]["Region"]):
        flags = [m["isReal"] for m in api.demand_region(region)["demand"]]
        first_real = flags.index(True) if True in flags else len(flags)
        assert all(flags[i] for i in range(first_real, len(flags))), \
            f"{region}: a generated month sits inside the recorded window"


def test_request_volume_does_not_jump_where_the_real_data_starts():
    """Summing the per-customer counts gave the generated half seven to thirteen
    requests a month against one to four recorded, so request volume appeared to
    collapse at exactly the point the real data began -- an artefact of the fill,
    read as a finding.

    The invariant is volume, not cores-per-request. Where a region's whole
    monthly demand is smaller than one typical recorded request, every generated
    month correctly gets a single request and the ratio cannot match.
    """
    import statistics

    for region in sorted(api.get_entities()["dim_region"]["Region"]):
        ms = api.demand_region(region)["demand"]
        gen = [m["tickets"] for m in ms if not m["isReal"]]
        rec = [m["tickets"] for m in ms if m["isReal"]]
        if len(gen) >= 3 and len(rec) >= 3:
            g, r = statistics.median(gen), statistics.median(rec)
            assert g <= max(r, 1) * 2.5, (
                f"{region}: generated months average {g} requests against {r} recorded")


def test_the_assistant_can_see_the_threshold_the_screen_shows():
    """Asked what the "80%" and "over" beside southcentralus-dc01 meant, the
    assistant answered that no such figure existed -- while the figure sat on
    screen beside it. The snapshot carried risk scores and failures for each
    facility but never its safety line."""
    sites = api.get_snapshot()["datacentres"]
    assert sites, "the assistant is given no facilities at all"
    for s in sites:
        assert "thresholdPct" in s and s["thresholdPct"] > 0, s["datacentre"]
        assert s["thresholdStatus"] in ("In risk", "Not in risk")
        assert s["thresholdStatus"] == (
            "In risk" if s["utilisationPct"] > s["thresholdPct"] else "Not in risk")


def test_a_facility_is_not_described_with_its_regions_utilisation():
    """The snapshot passed the region's utilisation under a per-facility key, so
    the assistant reported a regional figure as though it belonged to one
    building. The regional figure is now simply not there, which is stronger
    than naming it carefully -- it cannot be misread if it is absent.

    What it does carry is the building's own position, read from the capacities
    standing in it. This used to be checked against `UsedUnits / DeployedUnits`,
    which is the second, separately-derived estimate the pages no longer use --
    checking against it would put the assistant back on a different measurement
    from the screen beside it.
    """
    positions = api._site_cu_positions()
    sites = api.get_snapshot()["datacentres"]
    assert sites
    for s in sites:
        assert s["utilisationPct"] == positions[s["datacentre"]]["utilisationPct"], (
            f"{s['datacentre']}: snapshot says {s['utilisationPct']}%, its own "
            f"capacities say {positions[s['datacentre']]['utilisationPct']}%")
        assert "regionUtilisationPct" not in s

    # Per building, not one region figure stamped across all of them -- which is
    # what the original bug looked like on screen.
    by_region: dict[str, set] = {}
    for s in sites:
        by_region.setdefault(s["region"], set()).add(s["utilisationPct"])
    assert any(len(v) > 1 for v in by_region.values()), \
        "every site in every region reports the same utilisation"


def test_the_assistant_sees_every_facility_not_only_the_busy_ones():
    """Asked how many of southcentralus's capacity pools were over their
    threshold, the assistant answered from the 45 sites with activity while the
    region page listed all ten. A building over its line with nothing yet failed
    is exactly the one worth asking about, and it was invisible by construction."""
    entities = api.get_entities()
    sites = api.get_snapshot()["datacentres"]
    assert len(sites) == len(entities["dim_datacentre"])
    by_region = {}
    for s in sites:
        by_region.setdefault(s["region"], []).append(s)
    for region, group in by_region.items():
        page = api.region_detail(region)
        assert len(group) == page["siteCount"], region
        assert sum(1 for s in group if s["thresholdStatus"] == "In risk") \
            == page["sitesOverThreshold"], region


def test_the_fallback_answers_rather_than_raising():
    """Removing the stale status field from the snapshot broke the deterministic
    fallback, which still read it -- so every question that fell back returned a
    500 instead of the safe answer the fallback exists to provide. The fallback
    is the thing that runs when the model is unavailable, so it must never be
    the part that fails."""
    import assistant

    snap = api.get_snapshot()
    from module5.llm import LLMConfig

    # An empty config is unconfigured, so chat() raises and the fallback runs.
    # Passing no config was not enough: it falls back to the environment, so the
    # test only exercised the fallback while the model happened to be down.
    unconfigured = LLMConfig()
    assert not unconfigured.is_configured

    for q in ("tell me about southcentralus",
              "which regions are in risk",
              "how is exposure calculated",
              "something entirely unrelated to capacity"):
        result = assistant.ask(q, snap, llm_config=unconfigured)
        assert result["answer"], q
        assert result["source"] == "fallback"
        assert "approaching" not in result["answer"].lower()


def test_the_assistant_is_given_counts_rather_than_asked_to_count():
    """Asked how many capacity pools in southcentralus were in risk, the model
    counted the facility rows itself and answered "seven" against an actual ten.
    Models read reliably and count badly, so the counts are computed here."""
    snap = api.get_snapshot()
    sites = snap["datacentres"]
    for r in snap["regions"]:
        here = [d for d in sites if d["region"] == r["region"]]
        assert r["dataCentreCount"] == len(here), r["region"]
        assert r["dataCentresInRisk"] == sum(
            1 for d in here if d["thresholdStatus"] == "In risk"), r["region"]
        assert r["dataCentresWithRequests"] == sum(
            1 for d in here if d.get("requests")), r["region"]
        # And they must match the page, or the assistant and the screen disagree.
        page = api.region_detail(r["region"])
        assert r["dataCentreCount"] == page["siteCount"], r["region"]
        assert r["dataCentresInRisk"] == page["sitesOverThreshold"], r["region"]


def test_the_assistant_is_told_which_regions_are_in_risk_not_asked_to_work_it_out():
    """Asked how many regions were in risk the model answered "6" and then
    listed three, one of which the snapshot plainly marked "Not in risk". The
    data was right; the tallying was not."""
    snap = api.get_snapshot()
    truth = [r["region"] for r in api.threshold()["regions"] if r["at_risk"]]
    assert snap["regionsInRiskCount"] == len(truth)
    assert sorted(snap["regionsInRisk"]) == sorted(truth)
    assert snap["regionsNotInRiskCount"] == len(snap["regions"]) - len(truth)


def test_the_assistant_knows_what_each_region_owes():
    """Cores pending is on the Regions table and the region page, but was never
    in the snapshot -- so asked how many cores were pending the assistant
    correctly said it could not tell, about a figure on screen."""
    snap = api.get_snapshot()
    owed = api._cores_pending_by_region()
    assert snap["coresPendingTotal"] == pytest.approx(round(sum(owed.values()), 1))
    for r in snap["regions"]:
        assert r["coresPending"] == pytest.approx(
            round(owed.get(r["region"], 0.0), 1)), r["region"]


def test_growth_rate_is_derived_because_no_such_column_exists():
    """Review asked for a growth rate and asked first whether the data had one.

    It does not -- no table in the extract carries growth, trend or slope. It is
    computed from each building's own 150-day utilisation record instead, so the
    figure on the page is a measurement rather than an invented field.
    """
    entities = api.get_entities()
    for name, table in entities.tables.items():
        for col in table.columns:
            assert "growth" not in col.lower(), (
                f"{name}.{col} exists -- read it instead of deriving a rate")

    rates = api._site_growth_rates()
    assert len(rates) > 100, "not every site got a growth rate"
    assert any(v and v > 0 for v in rates.values()), "every site is flat"


def test_weeks_to_decide_and_overdue_agree_with_each_other():
    """Status is not a separate judgement: overdue means the runway is shorter
    than the lead time, and the two numbers are both on the row."""
    d = api.datacentres()
    assert d["datacentres"]
    for x in d["datacentres"]:
        assert x["planningThresholdPct"] == api.PLANNING_THRESHOLD_PCT
        assert x["leadTimeWeeks"] == api.PROCUREMENT_LEAD_WEEKS
        w = x["weeksToDecide"]
        if w is None:
            # Flat or shrinking and still under the line: no date to work back
            # from, so no claim is made either way.
            assert x["planningStatus"] == "ok", x["datacentre"]
            assert x["siteUtilisationPct"] < api.PLANNING_THRESHOLD_PCT
        else:
            expected = "overdue" if w < api.PROCUREMENT_LEAD_WEEKS else "ok"
            assert x["planningStatus"] == expected, x["datacentre"]

    # A site already past the planning line has no runway left, not a long one.
    over = [x for x in d["datacentres"]
            if x["siteUtilisationPct"] >= api.PLANNING_THRESHOLD_PCT]
    assert over, "no site is over the planning line, so this guard is idle"
    for x in over:
        assert x["weeksToDecide"] == 0.0 and x["planningStatus"] == "overdue"


def test_a_full_f32_site_is_told_to_buy_an_f8_not_an_f16():
    """Regression: the site procurement path matched a *raw compute unit*
    shortfall against the F SKU ladder, which is denominated in Capacity Units.

    The two differ by a factor of two -- `CapacityUnits` is real CU, while the
    `DeployedUnits` / `UsedUnits` columns are CU / UNITS_PER_CU -- so every site
    in the estate was told to buy one rung too much.

    One F32 running at 100%: 32 CU deployed, 32 used, and 40 deployed needed to
    sit at the 80% planning line, so it is short by 8 CU and an F8 covers it.
    Read in raw units the same site looks short by 16 and asks for an F16, which
    is twice the capacity and the next rung up the ladder.

    `admission.build_dim_capacity_pool` has always converted before reading the
    ladder; this asserts the site path does too.
    """
    one_f32_cu = admission.F_SKUS["F32"]
    got = api._site_procurement(one_f32_cu, one_f32_cu)

    assert got["procureSku"] == "F8", (
        f"a full F32 site should buy an F8; got {got['procureSku']}. "
        "A raw-unit shortfall matched against the CU ladder lands on F16.")
    assert got["procureShortfallCU"] == pytest.approx(8.0)
    assert got["procureSkuCU"] == admission.F_SKUS["F8"]

    # Handed the raw compute units instead, the same site asks for twice as
    # much. This is the mistake, stated so the caller's units stay deliberate.
    wrong = api._site_procurement(one_f32_cu / admission.UNITS_PER_CU,
                                  one_f32_cu / admission.UNITS_PER_CU)
    assert wrong["procureSku"] == "F16"

    # And through the endpoint, on a real one-F32 site.
    dc07 = next(x for x in api.datacentres()["datacentres"]
                if x["datacentre"] == "northcentralus-dc07")
    assert [s["sku"] for s in dc07["skus"]] == ["F32"], "dc07 is no longer one F32"
    assert dc07["totalCU"] == pytest.approx(32.0)
    assert dc07["procureSku"] == "F8"
    # The same figures, on the same site, through the Regions drill-down.
    assert api._site_position("northcentralus-dc07")["smallestSkuStep"] == "F8"


def test_every_site_buys_the_smallest_rung_that_covers_its_shortfall():
    """The ladder lookup, asserted against the shortfall printed beside it, so a
    unit slipping on either side of it shows up as a mismatched pair rather than
    as a number nobody can check."""
    ladder = sorted(admission.F_SKUS.items(), key=lambda kv: kv[1])
    for x in api.datacentres()["datacentres"]:
        short, sku = x["procureShortfallCU"], x["procureSku"]
        if not short or short <= 0:
            assert not sku, f"{x['datacentre']}: nothing short but asked for {sku}"
            continue
        smallest = next(name for name, cu in ladder if cu >= short)
        assert sku == smallest, (
            f"{x['datacentre']}: short by {short} CU, which an {smallest} covers, "
            f"but the row asks for an {sku}")


def test_the_cu_columns_are_capacity_units_not_raw_compute_units():
    """`totalCU` prints under a header that says CU, beside a `capacityUnits`
    subtitle that has always been real CU. They are the same quantity, so a row
    holding one F32 reading "Total CU 64" next to "32 CU" was the same
    raw-versus-CU confusion showing on screen.
    """
    rows = api.datacentres()["datacentres"]
    assert rows
    for x in rows:
        assert x["totalCU"] == pytest.approx(x["capacityUnits"], abs=0.05), (
            f"{x['datacentre']}: Total CU {x['totalCU']} disagrees with its own "
            f"{x['capacityUnits']} CU subtitle")
        # The per-SKU rows are in the same unit, and add back up to the site.
        if x["skus"]:
            assert sum(s["totalCU"] for s in x["skus"]) == pytest.approx(
                x["totalCU"], abs=0.05), x["datacentre"]


def test_a_one_capacity_site_reports_exactly_what_that_capacity_reports():
    """A building with a single capacity in it *is* that capacity. It cannot
    have a second opinion about how full it is.

    It had one. The row's used figure came from `dim_datacentre.UsedUnits` --
    the latest day's rate applied to the site's units -- while the SKU row
    beneath it read the 30-day per-capacity record, so southcentralus-dc02
    published 28 CU / 86.2% above a child saying 28.7 CU / 89.8% for the one
    F32 that is the whole building.
    """
    singles = [x for x in api.datacentres()["datacentres"] if len(x["skus"]) == 1
               and x["skus"][0]["capacityCount"] == 1]
    assert singles, "no single-capacity site to check, so this guard is idle"
    for x in singles:
        child = x["skus"][0]
        assert x["utilisedCU"] == child["utilisedCU"], (
            f"{x['datacentre']}: row says {x['utilisedCU']} CU used, its only "
            f"capacity says {child['utilisedCU']}")
        assert x["siteUtilisationPct"] == child["utilisationPct"], (
            f"{x['datacentre']}: row says {x['siteUtilisationPct']}%, its only "
            f"capacity says {child['utilisationPct']}%")


def test_every_site_is_the_cu_weighted_sum_of_its_own_sku_rows():
    """The parent is derived from the children, so the column adds up on screen.
    Weighted by CU, because an F2 at 90% and an F512 at 20% do not average to
    55% of the building."""
    for x in api.datacentres()["datacentres"]:
        if not x["skus"]:
            continue
        assert sum(s["utilisedCU"] for s in x["skus"]) == pytest.approx(
            x["utilisedCU"], abs=0.05), (
            f"{x['datacentre']}: SKU rows use "
            f"{sum(s['utilisedCU'] for s in x['skus'])} CU, row says "
            f"{x['utilisedCU']}")
        weighted = (sum(s["utilisationPct"] * s["totalCU"] for s in x["skus"])
                    / sum(s["totalCU"] for s in x["skus"]))
        assert weighted == pytest.approx(x["siteUtilisationPct"], abs=0.1), (
            f"{x['datacentre']}: SKU rows weight to {weighted:.2f}%, row says "
            f"{x['siteUtilisationPct']}%")


def test_utilisation_is_not_capped_at_one_hundred_percent():
    """`build_dim_datacentre` clipped the consumption rate at 1.0, so a site
    consuming past its nameplate reported exactly 100.0% -- northcentralus-dc07
    did, while the single capacity in it peaked above 110%. Bursting is a real
    state that smoothing absorbs; capping it removes the signal this page is
    for.
    """
    rows = api.datacentres()["datacentres"]
    over = [x for x in rows if x["siteUtilisationPct"] > 100.0]
    assert over, (
        "no site reports above 100%, which is what the clip used to guarantee")
    # And the clip is gone at source, not just routed around in the API.
    sites = api.get_entities()["dim_datacentre"]
    rate = sites["UsedUnits"] / sites["DeployedUnits"].replace(0, float("nan"))
    assert (rate > 1.0).any(), "dim_datacentre.UsedUnits is still capped"


def test_the_region_row_carries_its_site_count_and_utilised_cu():
    t = api.threshold()
    total = sum(r["datacentre_count"] for r in t["regions"])
    assert total == len(api.get_entities()["dim_datacentre"]), (
        "region site counts do not add up to the estate")
    for r in t["regions"]:
        assert r["used_units"] <= r["deployed_units"] + 1e-6, r["region"]
