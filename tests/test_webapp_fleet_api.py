"""The endpoints the map and the drill-down are built on.

Every session on this project has found its defects by using the product rather
than by running the suite, because the suite tested modules and the product is a
web app. These are deliberately shallow and broad: they call each new endpoint
the way the page does and assert the shape the page relies on, so a rename or a
dropped key fails here rather than on screen.
"""

from __future__ import annotations

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
              "status", "crossingDate", "capacities", "sites", "units",
              "skuClass", "leadTimeDays", "unavailableFeatures",
              "allFabricWorkloads", "recommendations"}
    for p in api.capacity_map()["points"]:
        missing = needed - set(p)
        assert not missing, f"{p['region']} is missing {sorted(missing)}"
        assert set(p["recommendations"]) == {"procurement", "workload_change",
                                             "licensing"}


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


# --------------------------------------------------------------------------
# capacities
# --------------------------------------------------------------------------


def test_capacities_scope_to_a_datacentre():
    d = api.capacities(datacentre="southcentralus-dc01")
    assert d["count"] > 0
    assert {c["datacentre"] for c in d["capacities"]} == {"southcentralus-dc01"}
    assert d["totalUnits"] == sum(c["deployedUnits"] for c in d["capacities"])


def test_capacities_scope_to_a_region_and_sum_to_the_site_totals():
    region = api.capacities(region="southcentralus")
    sites = api.get_ontology()["dim_datacentre"]
    want = sites[sites["Region"] == "southcentralus"]["DeployedUnits"].sum()
    assert region["totalUnits"] == pytest.approx(want, abs=1.0)


def test_capacity_rows_carry_the_hardware_the_table_prints():
    d = api.capacities(datacentre="southcentralus-dc01")
    for c in d["capacities"]:
        assert c["vendor"] and c["model"] and c["cpu"]
        assert c["memoryGB"] and c["nodes"] >= 1
        assert c["fabricSku"].startswith("F")


def test_the_fleet_baseline_is_the_whole_estate_not_the_filter():
    """"Fleet" must mean everything, whatever the page is scoped to.

    If the baseline narrowed with the filter, a region running uniformly poor
    hardware would be compared against itself and report that every capacity in
    it was perfectly normal -- which is exactly the comparison that makes the
    hardware case visible in the first place.
    """
    whole = api.capacities()
    one_site = api.capacities(datacentre="southcentralus-dc01")
    one_region = api.capacities(region="centralindia")

    assert one_site["count"] < whole["count"]
    for scope in (whole, one_site, one_region):
        assert scope["fleet"]["capacities"] == whole["count"], (
            "the fleet baseline changed with the filter")
        assert scope["fleet"]["incidentsPerNode"] == whole["fleet"]["incidentsPerNode"]
        assert scope["fleet"]["regions"] > 1, "a fleet of one region is not a fleet"

    # And the per-row comparison uses that same baseline.
    for c in one_site["capacities"]:
        assert c["fleetIncidentsPerNode"] == whole["fleet"]["incidentsPerNode"]


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
    d = api.capacity_detail("centralindia-dc10-cap01")
    assert len(d["utilisation"]) > 100, "expected the full daily window"
    assert d["hardware"]["vendor"] and d["hardware"]["memoryGB"]
    assert d["health"]["fleetIncidentsPerNode"] > 0
    # This is the capacity the workload-change case is built on.
    assert any(r["kind"] == "workload_change" for r in d["recommendations"])


# --------------------------------------------------------------------------
# recommendations
# --------------------------------------------------------------------------


def test_recommendations_filter_by_kind_and_region():
    all_of = api.recommendations(limit=500)
    moves = api.recommendations(kind="workload_change", limit=500)
    assert moves["total"] < all_of["total"]
    assert {r["kind"] for r in moves["recommendations"]} == {"workload_change"}

    one = api.recommendations(region="centralindia", limit=500)
    assert {r["evidence"]["region"] for r in one["recommendations"]} == {"centralindia"}


def test_counts_by_kind_describe_the_whole_set_not_the_filter():
    """The filter chips print these counts; scoping them to the current filter
    would make every chip read as its own total."""
    filtered = api.recommendations(kind="licensing", limit=5)
    assert filtered["countsByKind"]["procurement"] > 0
    assert filtered["shown"] <= 5


def test_early_raises_are_surfaced_separately():
    """They are the finding. Buried among a hundred routine overdue purchases
    nobody would ever see them."""
    d = api.recommendations(limit=1)
    assert d["earlyRaises"] > 0


def test_lead_times_show_movement_with_a_supplier():
    d = api.lead_times()
    assert d["classes"], "no lead-time history"
    for cls, drift in d["classes"].items():
        assert drift["supplier"], f"{cls} has no supplier"
        assert drift["was"] > 0 and drift["now"] > 0


# --------------------------------------------------------------------------
# the third outcome
# --------------------------------------------------------------------------


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
