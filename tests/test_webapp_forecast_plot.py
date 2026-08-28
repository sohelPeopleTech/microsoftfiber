"""How much of the projection each page plots.

The Forecast tab trims to the headline date so that history keeps most of the
frame; the region page draws the whole year, because that chart also carries
eighteen months of demand and a year ahead is a minority of its width. Both
behaviours are deliberate and neither is obvious from reading the caller, so
they are pinned here -- a trim that silently started applying to the region page
would shorten a projection nobody asked to shorten, and the only visible symptom
would be a chart that stops early.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp"))

from api import MIN_PLOT_DAYS, PLOT_MARGIN_DAYS, _trim_for_plotting  # noqa: E402


def _payload(days=365, crossing="2026-03-30", saturation="2026-06-23",
             breached=False, history=150):
    """A forecast shaped like the real one: a year of daily points."""
    import datetime as dt

    start = dt.date(2026, 1, 29)
    return {
        "history": [{"date": str(start - dt.timedelta(days=history - i)), "value": 70.0}
                    for i in range(history)],
        "projection": [{"date": str(start + dt.timedelta(days=i)),
                        "value": 80.0, "lower": 78.0, "upper": 82.0}
                       for i in range(days)],
        "crossingDate": crossing,
        "saturationDate": saturation,
        "alreadyBreached": breached,
    }


def test_full_year_is_kept_when_trim_is_off():
    d = _trim_for_plotting(_payload(), trim=False)
    assert len(d["projection"]) == 365
    assert d["plottedDays"] == 365
    assert d["projection"][-1]["date"] == "2027-01-28"


def test_trim_stops_shortly_after_the_crossing():
    d = _trim_for_plotting(_payload(), trim=True)
    assert len(d["projection"]) < 365, "the whole year should not be plotted"
    # The crossing plus its margin is on the chart, and not much more.
    dates = [p["date"] for p in d["projection"]]
    assert "2026-03-30" in dates
    assert len(dates) == dates.index("2026-03-30") + 1 + PLOT_MARGIN_DAYS


def test_trim_never_draws_less_than_the_floor():
    """A region crossing tomorrow still gets a readable window."""
    d = _trim_for_plotting(_payload(crossing="2026-01-30"), trim=True)
    assert len(d["projection"]) >= MIN_PLOT_DAYS


def test_untrimmed_still_warns_about_extrapolation():
    """The region page draws further, so it needs the warning more, not less."""
    d = _trim_for_plotting(_payload(history=150), trim=False)
    assert d["extrapolatedBeyondHistory"] is True


def test_untrimmed_puts_nothing_beyond_the_chart_edge():
    """Saturation cannot be off a chart that draws the whole year."""
    d = _trim_for_plotting(_payload(), trim=False)
    assert d["saturationBeyondChart"] is False


def test_breached_region_trims_to_saturation_not_crossing():
    """A region past its line is headlined by when it fills, so that is the target."""
    d = _trim_for_plotting(
        _payload(crossing=None, saturation="2026-03-26", breached=True), trim=True)
    assert "2026-03-26" in [p["date"] for p in d["projection"]]


@pytest.mark.parametrize("trim", [True, False])
def test_empty_projection_is_returned_untouched(trim):
    d = _trim_for_plotting({"history": [], "projection": []}, trim=trim)
    assert d["projection"] == []


# --------------------------------------------------------------------------
# the KPIs must describe the model that is actually drawing the line
# --------------------------------------------------------------------------


def test_the_error_shown_belongs_to_the_model_in_use():
    """The KPIs read scores[0], which is the backtest winner, not the model used.

    The model is forced to one choice for every region. Where the backtest
    would have picked something else, the page printed that other model's
    accuracy: northcentralus showed holt_winters' 1.06% under "Model used:
    sarima", whose own error is 1.11%.

    westeurope is the case that matters. sarima scores -0.2% against the naive
    baseline there -- it is beaten by assuming nothing changes -- while
    theil_sen, the backtest winner, is positive. The page was reporting the
    winner's skill for a forecast the winner did not produce, so a region where
    the modelling actively hurt read as a region where it helped.
    """
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "webapp"))
    import api

    js = (_P(__file__).resolve().parents[1] / "webapp" / "static" / "pages.js").read_text()
    body = js[js.index('kpi("Model used"'):js.index('All ${f.scores.length} models scored')]
    assert "scores[0]" not in body, (
        "a forecast KPI still reads scores[0] — that is the backtest winner, "
        "which is a different model wherever the choice is forced")
    assert "scoreFor(f)" in body

    forced = []
    for region in [r["region"] for r in api.overview()["regions"]]:
        f = api.forecast_one(region)
        if not f.get("scores"):
            continue
        used = f["model"]
        row = next((s for s in f["scores"] if s["model"] == used), None)
        assert row is not None, f"{region}: {used} is in use but was never scored"
        if f["scores"][0]["model"] != used:
            forced.append((region, used, round(row["mape"], 2),
                           f["scores"][0]["model"], round(f["scores"][0]["mape"], 2)))

    assert forced, (
        "no region uses a model other than the backtest winner, so the two "
        "figures cannot differ here and this test proves nothing — check "
        "whether the forced-model setting is still in play")
