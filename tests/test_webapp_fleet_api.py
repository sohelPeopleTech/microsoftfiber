"""The endpoints the map and the drill-down are built on.

Every session on this project has found its defects by using the product rather
than by running the suite, because the suite tested modules and the product is a
web app. These are deliberately shallow and broad: they call each new endpoint
the way the page does and assert the shape the page relies on, so a rename or a
dropped key fails here rather than on screen.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT / "src"))

import api  # noqa: E402


# --------------------------------------------------------------------------
# the map
# --------------------------------------------------------------------------


def test_map_returns_every_region_with_coordinates():
    d = api.capacity_map()
    regions = {p["region"] for p in d["points"]}
    assert regions == set(api.get_ontology()["dim_region"]["Region"])
    for p in d["points"]:
        assert p["lat"] is not None and p["lon"] is not None, f"{p['region']} unplaced"
        assert -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180


def test_map_carries_what_the_marker_card_prints():
    """The card exists so a planner does not open four tabs. If a key it reads
    disappears the card renders blanks and the point is lost."""
    needed = {"region", "displayName", "city", "utilisation", "thresholdPct",
              "status", "crossingDate", "capacities", "sites", "capacityUnits",
              "unavailableFeatures", "allFabricWorkloads", "recommendations"}
    for p in api.capacity_map()["points"]:
        missing = needed - set(p)
        assert not missing, f"{p['region']} is missing {sorted(missing)}"
        assert set(p["recommendations"]) == {"scale_up", "load_balance",
                                             "scale_down", "licensing"}


def test_map_says_which_of_its_numbers_are_real():
    """Half this payload is Microsoft's and half is invented. A reader looking
    at one screen has no way to tell them apart unless the payload says so."""
    prov = api.capacity_map()["provenance"]
    assert prov["coordinates"].startswith("REAL")
    assert prov["featureAvailability"].startswith("REAL")
    assert prov["capacityAndUsage"].startswith("GENERATED")


def test_feature_availability_on_the_map_is_the_real_table():
    """Not the seeded random draw the old feature matrix used.

    southcentralus is the US region Microsoft currently lists most gaps against;
    if this ever comes back empty the map has quietly fallen back to the
    generated matrix.
    """
    by = {p["region"]: p for p in api.capacity_map()["points"]}
    assert by["southcentralus"]["unavailableFeatures"], (
        "southcentralus should carry the gaps Microsoft publishes")
    assert not by["westus2"]["unavailableFeatures"], (
        "westus2 has the full set in Microsoft's table")


def test_map_region_answers_the_four_questions_the_marker_raises():
    """How many buildings, what is in each, when it crosses, what to change.

    Review asked for exactly these when a marker is clicked, and they were four
    separate tabs before. If any of these keys disappears the drill-down renders
    a section with nothing under it.
    """
    d = api.map_region("centralindia")

    # How many, and what is in each.
    assert d["totals"]["sites"] == len(d["sites"]) > 1
    for s in d["sites"]:
        assert s["skuMix"], f"{s['datacentre']} lists no SKUs"
        assert s["capacityUnits"] > 0 and s["capacities"] > 0
        assert s["meanUtilisationPct"] >= 0
        assert s["worstStage"] in ("none", "interactive_delay",
                                   "interactive_rejection", "background_rejection")

    # When it crosses, and when it is actually full -- different questions.
    t = d["threshold"]
    assert "crossingDate" in t and "saturationDate" in t
    if t["crossingDate"] and t["saturationDate"]:
        assert t["saturationDate"] > t["crossingDate"], (
            "running out must come after crossing the safety line, which is a "
            "margin below full")

    # What to change.
    assert d["recommendations"], "no advice for a region under pressure"
    assert set(d["recommendationCounts"]) <= {"scale_up", "load_balance",
                                              "scale_down", "licensing"}


def test_map_region_sites_reconcile_with_the_region_total():
    d = api.map_region("centralindia")
    assert sum(s["capacityUnits"] for s in d["sites"]) == d["totals"]["capacityUnits"]
    assert sum(s["capacities"] for s in d["sites"]) == d["totals"]["capacities"]


def test_map_region_shows_that_sites_differ():
    """The drill-down exists because buildings are not interchangeable. If they
    all held the same units on the same hardware there would be nothing to open."""
    d = api.map_region("centralindia")
    assert len({s["capacityUnits"] for s in d["sites"]}) > 1, "identical units"
    assert len({s["capacities"] for s in d["sites"]}) > 1, "identical capacity counts"


def test_unknown_region_on_the_map_is_a_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        api.map_region("atlantis")
    assert e.value.status_code == 404


def test_available_workloads_are_derived_not_invented():
    """Microsoft publishes only the gaps, so the available side is computed.

    Nine workloads are named in the Fabric overview; a region supports all of
    them less any the published exceptions sit inside. Both sides have to be
    present, because a planner choosing where to put a workload is asking what
    the region *can* do and the gaps answer only half of that.
    """
    d = api.map_region("southcentralus")
    ok, part, gaps = (d["workloadsAvailable"], d["workloadsPartlyAffected"],
                      d["unavailableFeatures"])
    assert ok, "no workloads reported as available"
    assert gaps, "southcentralus has published gaps"
    assert part, "gaps must be attributed to the areas they sit in"
    # Nothing can be both fully available and missing a feature.
    assert not (set(ok) & set(part)), f"{set(ok) & set(part)} is in both lists"


def test_a_region_with_no_gaps_reports_all_nine():
    """westus2 carries the full set in Microsoft's table."""
    d = api.map_region("westus2")
    assert len(d["workloadsAvailable"]) == 9, d["workloadsAvailable"]
    assert not d["unavailableFeatures"] and not d["workloadsPartlyAffected"]


def test_affected_is_not_the_same_as_absent():
    """A region can have "all Fabric workloads" and still lack features in two.

    Reading "Real-Time Intelligence unavailable" off southcentralus would be
    wrong -- the workload runs, two named features inside it do not -- and that
    is exactly the misreading the split exists to prevent.
    """
    d = api.map_region("southcentralus")
    assert d["allFabricWorkloads"] is True
    assert "Real-Time Intelligence" in d["workloadsPartlyAffected"]
    assert len(d["workloadsAvailable"]) + len(
        [w for w in d["workloadsPartlyAffected"] if w != "Fabric platform"]) == 9


def test_the_workload_counts_reconcile_to_nine():
    """Six clean plus three with gaps must be nine, or the screen is unreadable.

    "Fabric platform" is where a platform-level feature lands and is not one of
    the nine workloads. Counting it alongside them made southcentralus read as
    "6 available, 4 affected" -- ten things out of nine -- and there was no way
    for a reader to make that add up.
    """
    for region in ("southcentralus", "eastus", "westus2"):
        d = api.map_region(region)
        total = d["workloadCount"]
        assert total == 9
        assert len(d["workloadsAvailable"]) + len(d["workloadsPartlyAffected"]) == total, (
            f"{region}: {len(d['workloadsAvailable'])} + "
            f"{len(d['workloadsPartlyAffected'])} does not make {total}")
        assert "Fabric platform" not in d["workloadsPartlyAffected"]


def test_a_workload_missing_a_feature_is_not_reported_as_absent():
    """The distinction the wording exists to protect.

    southcentralus supports every Fabric workload; three of them lack named
    features. Anything that lets a reader conclude those three do not run is
    wrong, and "6 of 9 available" -- the first attempt -- did exactly that.
    """
    d = api.map_region("southcentralus")
    assert d["allFabricWorkloads"] is True, (
        "if this region ever genuinely loses a workload the copy has to change")
    assert d["workloadsPartlyAffected"], "expected workloads with feature gaps"


def test_the_map_marker_carries_both_sides_too():
    by = {p["region"]: p for p in api.capacity_map()["points"]}
    for region in ("southcentralus", "westus2"):
        p = by[region]
        assert "workloadsAvailable" in p and p["workloadsAvailable"]


# --------------------------------------------------------------------------
# capacities
# --------------------------------------------------------------------------


def test_capacities_scope_to_a_datacentre():
    d = api.capacities(datacentre="southcentralus-dc01")
    assert d["count"] > 0
    assert {c["datacentre"] for c in d["capacities"]} == {"southcentralus-dc01"}
    assert d["totalCapacityUnits"] == sum(c["capacityUnits"] for c in d["capacities"])


def test_capacities_scope_to_a_region_and_sum_to_the_site_totals():
    region = api.capacities(region="southcentralus")
    caps = api.get_ontology()["dim_capacity"]
    want = caps[caps["Region"] == "southcentralus"]["CapacityUnits"].sum()
    assert region["totalCapacityUnits"] == want


def test_capacity_rows_carry_what_the_table_prints():
    d = api.capacities(datacentre="southcentralus-dc01")
    for c in d["capacities"]:
        assert c["fabricSku"].startswith("F")
        assert c["capacityUnits"] > 0
        assert c["worstStage"] in ("none", "interactive_delay",
                                   "interactive_rejection", "background_rejection")
        assert c["windowDays"] > 1, "a single day is not evidence to size on"


def test_free_viewer_count_matches_the_rows():
    d = api.capacities(region="canadacentral")
    assert d["freeViewerCapable"] == sum(
        1 for c in d["capacities"] if c["supportsFreeViewers"])


def test_unknown_capacity_is_a_404_not_an_empty_page():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        api.capacity_detail("no-such-capacity")
    assert e.value.status_code == 404


def test_capacity_detail_has_a_history_and_its_own_advice():
    """westeurope-dc04-cap01 is the F8 that reaches background rejection."""
    d = api.capacity_detail("westeurope-dc04-cap01")
    assert len(d["consumption"]) > 100, "expected the full daily window"
    assert d["cuSecondsPerDay"] == d["capacityUnits"] * 86_400
    assert d["health"]["worstStage"] == "background_rejection"
    assert d["throttlingEvents"], "a throttling capacity with no events"
    assert d["workspaces"], "a capacity with no workspaces cannot be balanced"
    assert any(r["kind"] == "scale_up" for r in d["recommendations"])


# --------------------------------------------------------------------------
# recommendations
# --------------------------------------------------------------------------


def test_recommendations_filter_by_kind_and_region():
    all_of = api.recommendations(limit=500)
    moves = api.recommendations(kind="load_balance", limit=500)
    assert moves["total"] < all_of["total"]
    assert {r["kind"] for r in moves["recommendations"]} == {"load_balance"}

    one = api.recommendations(region="centralindia", limit=500)
    assert {r["evidence"]["region"] for r in one["recommendations"]} == {"centralindia"}


def test_counts_by_kind_describe_the_whole_set_not_the_filter():
    """The filter chips print these counts; scoping them to the current filter
    would make every chip read as its own total."""
    filtered = api.recommendations(kind="licensing", limit=5)
    assert filtered["countsByKind"]["scale_up"] > 0
    assert filtered["shown"] <= 5


def test_capacities_actually_throttling_are_counted_separately():
    """Those are refusing user operations now, as opposed to merely being short
    of headroom. Buried in one total nobody would tell them apart."""
    d = api.recommendations(limit=1)
    assert d["throttling"] > 0
    assert d["throttling"] <= d["countsByKind"].get("scale_up", 0)


def test_partial_grants_are_reported_without_moving_the_counts():
    """Partial fulfilment is generated and the five categories are not.

    Folding an invented sixth state into counts drawn from the extract would
    move published failure figures, so it is carried alongside.
    """
    ov = api.overview()
    p = ov["partial"]
    assert p["count"] > 0 and p["units"] > 0
    assert "generated" in p["note"].lower()
    assert sum(ov["categoryCounts"].values()) == ov["kpis"]["total"], (
        "the five categories must still account for every request")


def test_every_new_route_is_a_served_tab():
    """A page the router knows and the server does not is a 404 on refresh."""
    for path in ("/map", "/recommendations"):
        assert path in api.TABS, f"{path} is not served"
    assert "/capacity" in api.DEEP, "/capacity/<id> would not survive a refresh"


# --------------------------------------------------------------------------
# the page reads what the endpoint sends
# --------------------------------------------------------------------------


def test_the_drilldown_header_reads_only_totals_that_exist():
    """`num(t.units)` on a payload with no `units` prints 0, not an error.

    The region header shipped reading `t.units` and `t.nodes` -- two fields left
    behind by the Azure model and absent from this endpoint -- and rendered
    "0 units - 0 nodes" under every region on the map, including one holding 412
    CU across 27 capacities. Nothing failed: undefined reached a number
    formatter, which answered 0. The zero looked like a measurement.

    So this reads the field names straight out of the template and checks the
    endpoint actually sends them.
    """
    import re

    js = (ROOT / "webapp" / "static" / "pages.js").read_text()
    body = js[js.index("function mapDetail("):]
    body = body[:body.index("\n}")]
    used = set(re.findall(r"\bt\.([A-Za-z_]\w*)", body))
    assert used, "mapDetail no longer reads totals through `t.` -- update this test"

    region = api.capacity_map()["points"][0]["region"]
    totals = api.map_region(region)["totals"]
    missing = sorted(used - set(totals))
    assert not missing, (
        f"mapDetail renders {missing} but /api/map/{{region}} does not send them, "
        f"so each prints as 0. Sent: {sorted(totals)}")


def test_a_region_with_capacities_reports_capacity_units():
    """The header's own numbers, asserted as numbers rather than as keys."""
    for p in api.capacity_map()["points"]:
        t = api.map_region(p["region"])["totals"]
        if t["capacities"]:
            assert t["capacityUnits"] > 0, f"{p['region']} holds capacities but 0 CU"
            assert t["sites"] > 0


# --------------------------------------------------------------------------
# the vocabulary, in what the endpoints actually send
# --------------------------------------------------------------------------

#: Hardware classes are *data*, not source text. `test_static_assets.py` greps
#: pages.js and cannot see them: the template says `${esc(x.hardware)}` and the
#: string "Intel-highmem" arrives at render time from dim_sku.
#:
#: Three tables shipped that way after the pages themselves had been converted
#: -- the data-centre list printed "Intel-highmem 45d" next to a column of F
#: SKUs, the capacity-pool table carried a vendor class beside its Fabric SKU
#: equivalent, and the assistant was handed a provisioning lead time for a
#: platform that has none. All three were found by rendering the pages in a
#: browser and reading them, which is not something a test suite does.
#: Vendor names, and the vocabulary of a platform you wait for and rack up.
#:
#: The second half of this was added after "1 request(s) hit the 80% safety line
#: at southcentralus-dc01, which holds 184 cores with 155 committed" was found
#: on screen. That sentence is composed in Python and shipped inside a payload,
#: so neither the source scan (which reads pages.js) nor the earlier version of
#: this scan (which only looked for vendor names) could see it.
AZURE_VALUES = re.compile(
    r"\b(Intel-\w+|AMD-\w+|GPU-class|PowerEdge|ProLiant"
    r"|cores?|provision(?:ing|ed)?|lead[- ]time|hardware"
    r"|order-by|procurement)\b", re.I)

#: Keys that would carry them even if today's values happen to be blank.
AZURE_KEYS = {"hardware", "leadTime", "leadTimeDays", "currentHardware",
              "coresDeployed", "coresFree", "cores", "SKUClass"}


def _walk(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node[:40]):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


ENDPOINTS = [
    ("overview", lambda: api.overview()),
    ("datacentres", lambda: api.datacentres()),
    ("capacity-policy", lambda: api.capacity_policy()),
    ("datacentre detail", lambda: api.datacentre_detail(
        api.datacentres()["datacentres"][0]["datacentre"])),
    ("region detail", lambda: api.region_detail(
        api.overview()["regions"][0]["region"])),
    ("scale options", lambda: api.scale_options_index()),
    ("assistant snapshot", lambda: api._snapshot_datacentres()),
]


#: The provenance table describes the other tables, so it has to be able to name
#: what they contain. dim_sku really is one row per hardware class, and its cost
#: index really is relative to AMD-standard -- module 2 and the propensity model
#: still read that table. A provenance note that could not say so would be worse
#: than useless.
#:
#: This is the whole exemption list, and it is metadata about generated tables
#: rather than anything a capacity admin is told. It replaced KNOWN_GAP, which
#: named three paths module1's threshold engine reached while it was still
#: subtracting a hardware provisioning lead time from the crossing date. That
#: engine is converted, so those paths are gone.
SELF_DESCRIBING = {"$.provenance[].Provenance", "$.provenance[].Grain"}


def _generalise(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


@pytest.mark.parametrize("name,call", ENDPOINTS, ids=[e[0] for e in ENDPOINTS])
def test_no_endpoint_sends_azure_hardware_as_data(name, call):
    payload = call()
    offences = []
    for path, value in _walk(payload):
        if _generalise(path) in SELF_DESCRIBING:
            continue
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf in AZURE_KEYS:
            offences.append(f"{path} = {value!r}")
        elif isinstance(value, str) and AZURE_VALUES.search(value):
            offences.append(f"{path} = {value!r}")
    assert not offences, (
        f"/{name} sends the Azure hardware model as data, which every page "
        f"rendering it prints:\n  " + "\n  ".join(offences[:12]))


def test_no_region_flag_names_hardware_any_more():
    """The sentence this suite used to carve an exemption around.

    Every region's flag rationale is printed on Overview and Regions. They said
    "projected to hit 86% on 2026-02-12 (15 days), but Intel-highmem takes 45
    days to provision -- the request needed raising 30 days ago", which named a
    vendor, a wait and a purchase, none of which exist in Fabric.
    """
    reasons = [r["reason"] for r in api.overview()["regions"] if r.get("reason")]
    assert reasons
    named = [r for r in reasons if AZURE_VALUES.search(r)]
    assert not named, (
        "a region flag still names a hardware class:\n  " + "\n  ".join(named[:5]))
    assert not [r for r in reasons if "provision" in r.lower() or "lead time" in r.lower()], (
        "a region flag still talks about provisioning or lead time")


def test_the_customer_table_reads_only_fields_the_endpoint_sends():
    """The `#` column rendered `c.rank`, which /api/customers has never sent.

    Every row printed the word "undefined" in its first cell, on a page that had
    been reviewed more than once. `num(undefined)` at least renders 0 and looks
    like a number; this rendered the string, in the table, in production.
    """
    import re

    js = (ROOT / "webapp" / "static" / "pages.js").read_text()
    body = js[js.index('$("cust-table").innerHTML'):]
    body = body[:body.index("</tbody>")]
    used = set(re.findall(r"\bc\.([A-Za-z_]\w*)", body))
    sent = set(api.customers()["customers"][0])
    missing = sorted(used - sent)
    assert not missing, (
        f"the customers table renders {missing} but /api/customers does not "
        f"send them. Sent: {sorted(sent)}")


# --------------------------------------------------------------------------
# every page reads only what its endpoint sends
# --------------------------------------------------------------------------

#: This class of defect has now shipped four times: `t.units`/`t.nodes` in the
#: region header, `t.sitesPastThreshold` under it, `c.rank` in the customers
#: table, and `r.cores` on the region page after the field was renamed to
#: `capacityUnits`. Every one rendered a confident number -- 0, or the literal
#: word "undefined" -- because reading a missing key is not an error in
#: JavaScript, and none could be caught by running the tests.
#:
#: The three fixes before this one each guarded a single template. This walks
#: them all: for each page, the object it destructures from its endpoint, and
#: every `<var>.<field>` read off it.
PAGE_CONTRACTS = [
    # (page, marker in pages.js, variable, callable returning the payload)
    ("/region", 'PAGES["/region"]', "r",
     lambda: api.region_detail(api.overview()["regions"][0]["region"])),
    ("/datacentre", 'PAGES["/datacentre"]', "x",
     lambda: api.datacentre_detail(api.datacentres()["datacentres"][0]["datacentre"])),
    ("/capacity", 'PAGES["/capacity"]', "d",
     lambda: api.capacity_detail(api.scale_options_index()["capacities"][0]["capacityId"])),
]


def _page_source(marker: str) -> str:
    js = (ROOT / "webapp" / "static" / "pages.js").read_text()
    start = js.index(marker)
    nxt = js.find('\nPAGES["', start + 1)
    return js[start:nxt if nxt > 0 else len(js)]


@pytest.mark.parametrize("page,marker,var,call",
                         PAGE_CONTRACTS, ids=[c[0] for c in PAGE_CONTRACTS])
def test_a_page_reads_only_fields_its_endpoint_sends(page, marker, var, call):
    import re

    src = _page_source(marker)
    used = set(re.findall(rf"\b{re.escape(var)}\.([A-Za-z_]\w*)", src))

    # Every key anywhere in the payload, not just the top level. These pages
    # nest `.map((r) => ...)` inside a scope that already binds `r`, so a regex
    # cannot tell `r.action` on a reason from `r.action` on the region. Matching
    # against the whole tree accepts the shadowed ones and still catches what
    # this exists for: a field that was renamed and now exists nowhere at all.
    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node[:20]:
                yield from keys(v)

    payload = call()
    sent = set(keys(payload))
    missing = sorted(used - sent)
    assert not missing, (
        f"{page} renders {missing}, which appears nowhere in what its endpoint "
        f"sends -- each prints as 0 or as the word \"undefined\".")


# --------------------------------------------------------------------------
# why a region carries revenue loss
# --------------------------------------------------------------------------


def test_every_region_with_failures_says_why_they_failed():
    """The Overview prints a cause beside the money, so one has to exist."""
    for r in api.overview()["regions"]:
        if not r["failed"]:
            continue
        c = r.get("failureCause") or {}
        assert c, f"{r['region']} has {r['failed']} failures and no cause breakdown"
        assert c["capacityCaused"] + c["otherCaused"] == r["failed"], (
            f"{r['region']}: {c['capacityCaused']} capacity + {c['otherCaused']} other "
            f"!= {r['failed']} failed")
        assert c["topCause"], f"{r['region']} has failures but no dominant cause"


def test_a_healthy_region_that_carries_loss_did_not_run_out_of_capacity():
    """The claim the Overview explainer makes, asserted against the data.

    A reader asked how a region can show a green pill and a five-figure revenue
    loss at the same time. The answer the page now gives is that the status
    forecasts the region's ceiling while the loss is history about individual
    requests -- and that in a region which is currently fine, those requests
    failed somewhere that had room.

    If that ever stops being true, the explainer is telling people something
    false and this fails rather than the sentence quietly becoming wrong.
    """
    healthy = [r for r in api.overview()["regions"]
               if r["status"] in ("stable", "approaching") and r["failed"]]
    assert healthy, "no healthy region carries a failure -- the case is untested"

    for r in healthy:
        c = r["failureCause"]
        assert c["landedOnAFullSite"] == 0, (
            f"{r['region']} is {r['status']} but {c['landedOnAFullSite']} of its "
            f"failures landed on a data centre over its own threshold -- the "
            f"Overview explainer says that never happens")


def test_a_breached_region_is_allowed_to_be_a_capacity_problem():
    """The other half. If nothing is ever capacity-caused the column says
    nothing, and the distinction it exists to draw is not being drawn."""
    breached = [r for r in api.overview()["regions"]
                if r["status"] == "breached" and r["failed"]]
    assert breached
    assert any(r["failureCause"]["capacityCaused"] > 0 for r in breached), (
        "no breached region has a capacity-caused failure, so the column never "
        "distinguishes one case from the other")


def test_the_failure_column_separates_where_they_failed_from_what_the_region_holds():
    """Two different questions, and conflating them printed something false.

    The column said "no data centre here is over its line today" whenever no
    failure had landed on a full site. Those are not the same claim. westeurope
    had neither of its failures on a full building *and* two data centres over
    their own line -- dc04 at 100% with nothing free -- so the Overview asserted
    something the region page disproved one click later.

    Both numbers are now carried, and this asserts they are actually different
    somewhere, because if they never diverge the distinction is untested and the
    wording will drift back.
    """
    regions = [r for r in api.overview()["regions"] if r["failed"]]
    assert regions

    for r in regions:
        c = r["failureCause"]
        assert c["landedOnAFullSite"] <= r["failed"], r["region"]
        assert 0 <= c["sitesOverLine"] <= c["sites"], r["region"]
        # A failure cannot land on a full site in a region that has none.
        if c["sitesOverLine"] == 0:
            assert c["landedOnAFullSite"] == 0, (
                f"{r['region']}: {c['landedOnAFullSite']} failures landed on a full "
                f"site, but the region reports no site over its line")

    diverging = [r["region"] for r in regions
                 if r["failureCause"]["sitesOverLine"] > 0
                 and r["failureCause"]["landedOnAFullSite"] == 0]
    assert diverging, (
        "no region holds a full data centre while its failures landed elsewhere, "
        "so the two figures are indistinguishable here and the column's wording "
        "is not being exercised")


def test_a_region_can_average_comfortably_and_still_hold_a_full_data_centre():
    """The thing the regional average hides, asserted rather than assumed.

    This is the case that made a reader distrust the page: westeurope reads
    83.1% against a 90% line and looks fine, while one of its data centres is at
    100% with zero free.
    """
    for r in api.overview()["regions"]:
        if r["status"] not in ("stable", "approaching", "due_now"):
            continue
        d = api.region_detail(r["region"])
        over = [s for s in d["datacentres"] if s["overThreshold"]]
        if over:
            worst = max(over, key=lambda s: s["utilisationPct"])
            assert worst["utilisationPct"] > worst["thresholdPct"]
            return
    raise AssertionError(
        "no region under its own line holds a data centre over that site's line -- "
        "the case the column exists to surface does not occur in this data")


# --------------------------------------------------------------------------
# a region can be inside its line and still be refusing work
# --------------------------------------------------------------------------


REGION_VIEWS = [
    ("overview", lambda: api.overview()["regions"]),
    ("threshold", lambda: api.threshold()["regions"]),
    ("map", lambda: api.capacity_map()["points"]),
]


@pytest.mark.parametrize("name,call", REGION_VIEWS, ids=[v[0] for v in REGION_VIEWS])
def test_every_region_view_says_whether_anything_is_refusing_work(name, call):
    """An executive reads regions, not capacities.

    The throttling was computed, shown on the capacity pages, and absent from
    every screen above them -- so westeurope read "not in risk" at 83.1% against
    a 90% line while eleven of its twenty-four capacities refused 1,481
    operations, and nothing on a region screen said so.
    """
    for row in call():
        t = row.get("throttling")
        assert t is not None, f"/{name}: {row['region']} carries no throttling figure"
        assert t["capacities"] >= t["throttling"] >= 0
        assert t["operationsRefused"] >= 0


def test_the_two_region_signals_are_allowed_to_disagree():
    """The threshold and the throttling answer different questions.

    The threshold asks whether there is room to grant more capacity here.
    Throttling asks whether anybody is being refused right now. Capacity Units
    do not pool, so a region can be comfortable on the first and severe on the
    second -- and if they never diverge, one of them is redundant and the
    distinction this exists to draw is not being drawn.
    """
    regions = api.overview()["regions"]
    calm_but_refusing = [r for r in regions
                         if r["status"] not in ("breached", "overdue")
                         and r["throttling"]["throttling"] > 0]
    assert calm_but_refusing, (
        "no region is inside its line while refusing work, so the throttling "
        "column tells the reader nothing the status column did not")

    worst = max(calm_but_refusing, key=lambda r: r["throttling"]["operationsRefused"])
    assert worst["throttling"]["worstMeanPct"] > 100, (
        f"{worst['region']} is the strongest example and its worst capacity is "
        f"only at {worst['throttling']['worstMeanPct']}%")


def test_the_three_region_views_report_the_same_throttling():
    """Overview, Regions and the map read one figure. Three definitions of the
    same thing is how the capacity-policy simulator once reported 45 failures
    where every other screen reported 30."""
    ov = {r["region"]: r["throttling"] for r in api.overview()["regions"]}
    th = {r["region"]: r["throttling"] for r in api.threshold()["regions"]}
    mp = {r["region"]: r["throttling"] for r in api.capacity_map()["points"]}
    for region, seen in ov.items():
        assert th[region] == seen, region
        assert mp[region] == seen, region
