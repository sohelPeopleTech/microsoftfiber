"""Backend for the Capacity Intelligence app.

The ontology is built once at startup and held in memory -- it is 1,800 rows,
and rebuilding per request would make the threshold slider feel broken.

Two kinds of endpoint, and the distinction matters for what "interactive"
means here:

  **served**    data that does not change with input -- regions, spikes,
                features. Filtering and sorting these happens in the browser.
  **computed**  results that genuinely depend on what the user chose. Moving
                the safety threshold re-runs Module 1; changing a SKU re-runs
                Module 2. Those cannot be precomputed, which is exactly why the
                static page could not do them.
"""

from __future__ import annotations

import os
import logging
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

#: Warm-up progress goes here. Container Apps collects stdout, so the startup
#: timings are visible in Log Analytics without extra wiring.
log = logging.getLogger("capacity")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import assistant  # noqa: E402
import module1, module2, module3, module4, module6  # noqa: E402
import ontology  # noqa: E402
from ontology import attribution  # noqa: E402
import riskindex  # noqa: E402
import remediation  # noqa: E402
import forecast  # noqa: E402
import anomaly  # noqa: E402
import admission  # noqa: E402
import ratecard  # noqa: E402
from module5 import pipeline, state  # noqa: E402
from module5.config import Config  # noqa: E402
from module5.env import load_dotenv  # noqa: E402
from module5.llm import LLMConfig  # noqa: E402

# BEFORE importing auth, not after. auth reads APP_USERS and APP_SECRET_KEY at
# import time, so loading .env below its import meant those settings were never
# seen: the app kept serving the built-in demo account while a real one sat
# configured in the file, and said so in a startup warning nobody had reason to
# disbelieve.
load_dotenv(ROOT / ".env")

import auth  # noqa: E402  -- webapp-local, see webapp/auth.py

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Fit the forecasts in the background so the first visitor does not wait.

    Adding ARIMA and SARIMA took a cold /api/forecast from well under a second
    to about five, because nine candidates are now backtested over six folds for
    every region. The result is cached for the life of the process, so this only
    ever costs the first caller -- which, on a link sent out for review, is the
    reviewer. Warming on a daemon thread keeps startup non-blocking: the server
    accepts connections immediately and the work is finished before anyone has
    logged in and reached the tab.
    """
    import threading
    import time

    def warm() -> None:
        # Order matters: each of these builds something the next one needs, and
        # doing them explicitly means the log shows which stage is slow rather
        # than one opaque wait.
        #
        # Warming only the forecast was not enough. On a 0.5-CPU container the
        # first /api/overview still took 144 seconds, because the ontology and
        # the module 5 pipeline were built on that request while the forecast
        # thread competed for the same core. The health probe had already passed,
        # so the platform reported the app ready while the first real visitor
        # waited over two minutes.
        for label, fn in (
            ("ontology", get_ontology),
            ("module5", get_module5),
            ("anomalies", get_anomalies),
            ("overview", overview),
            ("forecast", forecast_all),
        ):
            start = time.monotonic()
            try:
                fn()
                log.info("warmed %s in %.1fs", label, time.monotonic() - start)
            except Exception:
                # A cold cache is a slow page, not a broken one. Never let
                # warming take down a server that would otherwise serve.
                log.warning("warming %s failed; it will build on first use", label)

    threading.Thread(target=warm, name="warm-caches", daemon=True).start()
    yield


app = FastAPI(title="Capacity Intelligence", lifespan=_lifespan)

STATIC = Path(__file__).parent / "static"
TICKETS = ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx"
SYNTH = ROOT / "data" / "synthetic"
#: Real reference tables, kept apart from the generated ones so the
#: distinction survives a glance at the directory listing.
REFERENCE = ROOT / "data" / "reference"

#: Same path the CLI writes to, deliberately -- a decision recorded in the app
#: and one recorded with `--decide` land in the same append-only log, and the
#: next pipeline run reads both.
STATE_DIR = ROOT / "out" / "state"

#: Every tab is a real URL. FastAPI serves the same shell for all of them and
#: the client router picks the renderer, so deep links and browser-back work
#: without a build step.
TABS = ("/", "/map", "/recommendations", "/regions", "/datacentres", "/customers", "/incidents",
        "/reasons", "/forecast", "/policy", "/actions", "/methodology")

#: Deep pages. Review was explicit that selecting a facility must open that
#: facility's own page rather than expanding a panel in place -- "do not drill
#: through, open that page completely" -- so a site has a real URL that can be
#: linked, bookmarked and reached from either the region or the site list.
DEEP = ("/region", "/datacentre", "/customer", "/capacity")

#: Reachable without a session. Everything else redirects (pages) or 401s (API).
PUBLIC = {"/login", "/logout", "/health"}


@lru_cache(maxsize=1)
def get_ontology():
    return ontology.build(TICKETS, SYNTH, REFERENCE)


@lru_cache(maxsize=1)
def get_module5():
    return pipeline.run(TICKETS, config=Config.load(ROOT / "config.json"),
                        write_outputs=False)


@lru_cache(maxsize=1)
def get_config():
    return Config.load(ROOT / "config.json")


@lru_cache(maxsize=1)
def get_risk_weights():
    """Weights from config.json, validated once. A bad edit fails here rather
    than silently producing scores nobody can reconcile."""
    return riskindex.resolve_weights(get_config().risk_weights)


@lru_cache(maxsize=1)
def get_demand():
    return module3.demand_by_period(get_ontology(), "M")


def _clean(value):
    """NaN is not JSON. Neither is numpy.

    `df.where(df.notna(), None)` looks like it handles this and does not -- on a
    float column pandas coerces the None straight back to NaN, and the failure
    only appears for inputs that happen to produce a null (here, raising the
    threshold until regions become 'stable' and have no order-by date).
    """
    if value is None:
        return None
    if isinstance(value, float):
        return None if (value != value or value in (float("inf"), float("-inf"))) else value
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        item = value.item()
        return _clean(item) if isinstance(item, float) else item
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    # Nested structures reach here too -- a plan carries a list of tranches, and
    # a NaN buried one level down fails the response just as hard as a top-level
    # one.
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _records(df):
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict("records")]


# --------------------------------------------------------------------------
# sign-in
# --------------------------------------------------------------------------


@app.middleware("http")
async def require_session(request: Request, call_next):
    """One gate in front of everything, rather than a decorator per route.

    A missed decorator is a silently public endpoint; a middleware that defaults
    to closed cannot be forgotten. Static assets stay open because the login
    page needs its stylesheet before there is a session to check.
    """
    path = request.url.path
    if path in PUBLIC or path.startswith("/static/"):
        return await call_next(request)

    if auth.read_session(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)

    # An expired session mid-review should not turn a fetch into a login page
    # rendered inside a table cell -- the client watches for 401 and redirects.
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _login_page(error: str = "") -> HTMLResponse:
    html = (STATIC / "login.html").read_text(encoding="utf-8")
    if error:
        html = html.replace("<!--ERROR-->", f'<p class="error">{error}</p>')
    return HTMLResponse(html)


@app.get("/login")
def login_form(request: Request):
    if auth.read_session(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/", status_code=303)
    return _login_page()


@app.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...)):
    if not auth.authenticate(username, password):
        # Deliberately does not say which half was wrong.
        return _login_page("Sign-in failed. Check your username and password.")

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        auth.COOKIE_NAME, auth.issue_session(username),
        max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax",
        # A secure cookie is dropped by the browser over plain HTTP, so this
        # cannot simply be hardcoded on: the same build has to work on
        # http://127.0.0.1 locally and behind a TLS terminator when shared.
        # Set COOKIE_SECURE=1 whenever the browser will see https.
        secure=os.environ.get("COOKIE_SECURE", "").strip() in ("1", "true", "yes"),
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@app.get("/health")
def health():
    """Unauthenticated, for the Fabric pipeline to poll. Says nothing sensitive."""
    return {"status": "ok"}


@app.get("/api/me")
def me(request: Request):
    username = auth.read_session(request.cookies.get(auth.COOKIE_NAME))
    return auth.profile(username)


# --------------------------------------------------------------------------
# served
# --------------------------------------------------------------------------


def _partial_grants() -> dict:
    """Requests met in part, from the generated overlay.

    Kept out of `categoryCounts` deliberately. Those five categories are
    computed from the extract and every published failure figure derives from
    them; adding an invented sixth would move numbers that have been reviewed.
    """
    pg = get_ontology()["fact_partial_grant"]
    if pg.empty:
        return {"count": 0, "units": 0, "shortfallUnits": 0, "regions": [],
                "note": "No partial fulfilment recorded."}
    return {
        "count": int(len(pg)),
        "units": int(pg["PartiallyGrantedUnits"].sum()),
        "shortfallUnits": int(pg["ShortfallUnits"].sum()),
        "medianGrantedPct": round(float(pg["GrantedPct"].median()), 1),
        "regions": sorted(pg["Region"].unique().tolist()),
        "note": ("Partial fulfilment is generated. The ICM extract records none "
                 "— every row in it grants the whole request or none of it — so "
                 "these are illustrative of a state the export cannot express."),
    }


@app.get("/api/overview")
def overview():
    onto, m5 = get_ontology(), get_module5()
    summary = m5.finding["summary"]
    exposure = {r["Region"]: r for r in m5.finding["regions"]}
    growth = {r["Region"]: r for r in _records(module3.growth_ranking(get_demand()))}
    coverage = {r["Region"]: r for r in _records(module6.region_summary(onto))}
    flags = _records(module1.project_all(onto, crossing_for=_forecast_crossing))
    spikes = module4.explain_anomalies(get_demand(), onto["fact_event"])

    regions = []
    for f in flags:
        name = f["region"]
        e = exposure.get(name, {})
        regions.append({
            "region": name,
            "status": f["status"],
            "utilisation": f["current_utilisation_pct"],
            "sku": f["sku_class"],
            "leadTime": f["lead_time_days"],
            "daysUntilOrder": f["days_until_order"],
            "reason": f["reason"],
            "exposure": e.get("RevenueExposureUSD", 0),
            "failed": e.get("TicketsFlagged", 0),
            "customers": e.get("CustomersAffected", 0),
            "arrAffected": e.get("ARRAffectedUSD", 0),
            "growth": growth.get(name, {}).get("AbsoluteChange", 0),
            "coverage": coverage.get(name, {}).get("CoveragePct", 0),
            "spikes": sum(1 for s in spikes if s.region == name),
        })

    return {
        "asOf": summary["as_of"],
        "kpis": {
            "exposure": summary["revenue_exposure_usd"],
            "arrAffected": summary["arr_affected_usd"],
            "failed": summary["tickets_flagged"],
            "total": summary["tickets_total"],
            "customers": summary["customers_affected"],
            "spikesExplained": sum(1 for s in spikes if s.match_strength == "strong"),
            "spikesTotal": len(spikes),
        },
        # The four outcomes, so the funnel can be built from one denominator.
        # It previously mixed request counts with spike counts and printed
        # "7 of 60 = 12%" for two quantities that are not a subset of each other.
        "categoryCounts": m5.finding["category_counts"],
        # A third outcome the extract cannot express. Every ICM row grants the
        # whole ask or none of it, and reviewers describe part-fills happening,
        # so the state exists in the business and not in the export. Carried
        # separately rather than folded into the counts above: those are drawn
        # from the extract and must keep matching it.
        "partial": _partial_grants(),
        "outcomeLabels": OUTCOME_LABELS,
        # Review feedback: the page jumped from "11 regions" straight to "60
        # requests" with nothing joining them. This is the missing layer --
        # which region the demand actually came from.
        "regionDistribution": _region_distribution(onto),
        "reasons": _reason_breakdown(onto),
        "regions": regions,
        "skus": sorted(onto["dim_sku"]["SKUClass"]),
        "provenance": _records(ontology.sources(onto.tables)),
    }


def _region_distribution(onto) -> list[dict]:
    """Requests per region, so 60 can be traced back to 11.

    Sorted by volume: "which region is our highest-request region" was the
    question, and the answer should be the first row.
    """
    fact = onto["fact_capacity_request"]
    total = len(fact) or 1
    rows = []
    for region, grp in fact.groupby("Region"):
        failed = _failed_rows(grp)
        rows.append({
            "region": region,
            "requests": int(len(grp)),
            "sharePct": round(len(grp) / total * 100, 1),
            "failed": int(len(failed)),
            "customers": int(grp["SubscriptionId"].nunique()),
            "datacentres": int(grp["DatacentreId"].nunique()),
        })
    return sorted(rows, key=lambda r: -r["requests"])


@lru_cache(maxsize=1)
def _failed_incident_ids() -> frozenset:
    """The incidents that actually failed -- SLA breached, or never fulfilled.

    One definition, used everywhere. A request denied and then approved inside
    its SLA is not a target, so it is not counted anywhere: not in a tile, not
    in a table column, not in a reason total, not in a recommendation. Two
    numbers for the same idea is what made the screen unreadable.
    """
    priced = get_module5().priced
    return frozenset(priced[priced["IsFlagged"]]["IncidentId"].astype(str))


def _failed_rows(fact):
    """The failing subset of a ticket frame."""
    return fact[fact["IncidentId"].astype(str).isin(_failed_incident_ids())]


def _reason_breakdown(onto, region: str | None = None) -> list[dict]:
    """Why requests failed, and what can be done about each.

    Counts failures only. "westeurope has 4 failures" tells an engineer
    nothing; "3 hit the capacity ceiling, 1 was hardware" tells them which fix
    to reach for -- but only if the 4 is the same 4 shown everywhere else.
    """
    fact = onto["fact_capacity_request"]
    if region:
        fact = fact[fact["Region"] == region]
    denied = _failed_rows(fact)
    denied = denied[denied["DenialReason"] != ""]
    total = len(denied) or 1

    rows = []
    for name, meta in attribution.REASONS.items():
        n = int((denied["DenialReason"] == name).sum())
        if not n:
            continue
        rows.append({
            "reason": name,
            "count": n,
            "sharePct": round(n / total * 100, 1),
            "detail": meta["detail"],
            "action": meta["action"],
            "handledBy": meta["module"] or "human review",
            "needsHuman": meta["module"] is None,
        })
    return sorted(rows, key=lambda r: -r["count"])


def _region_context(onto):
    """Utilisation, threshold and lead time per region -- the inputs the risk
    index needs that do not live on a ticket."""
    flags = {f["region"]: f for f in _records(module1.project_all(onto, crossing_for=_forecast_crossing))}
    return {
        name: {
            "utilisation": float(f.get("current_utilisation_pct") or 0),
            "threshold": float(f.get("threshold_pct") or 85),
            "leadTime": float(f.get("lead_time_days") or 0),
            "status": f.get("status", ""),
            "sku": f.get("sku_class", ""),
        }
        for name, f in flags.items()
    }


@lru_cache(maxsize=1)
def _region_thresholds() -> dict:
    """Each region's own safety threshold, from the ontology.

    Derived there as the capacity-weighted mean of its facilities' thresholds,
    so a region cannot advertise a safety line its buildings are not holding.
    """
    dim = get_ontology()["dim_region"]
    if "ThresholdPct" not in dim.columns:
        return {}
    return {str(r.Region): float(r.ThresholdPct) for r in dim.itertuples()
            if r.ThresholdPct == r.ThresholdPct}


def _region_threshold(region: str, override: float | None = None) -> float:
    """The line to judge a region by: its own, unless one was forced."""
    if override is not None:
        return float(override)
    return _region_thresholds().get(str(region), 85.0)


@lru_cache(maxsize=1)
def _cores_pending_by_region() -> dict:
    """Cores requested and still not delivered, per region.

    The count review actually wants next to a region: not how many tickets
    failed, but how much capacity the region still owes its customers.
    """
    priced = get_module5().priced
    failed = priced[priced["IsFlagged"]]
    return {str(r): float(g["BlockedUnits"].sum())
            for r, g in failed.groupby("Region")}


@lru_cache(maxsize=1)
def _waiting_customers_by_region() -> dict:
    """Distinct customers with an unmet request, per region."""
    priced = get_module5().priced
    failed = priced[priced["IsFlagged"]]
    return {str(r): int(g["SubscriptionId"].nunique())
            for r, g in failed.groupby("Region")}


@lru_cache(maxsize=1)
def _fleet_failure_rate() -> float:
    """Share of all requests in the extract that failed.

    The prior the risk index shrinks thin samples toward. Computed from the same
    `_failed_incident_ids()` everything else uses, so the baseline and the
    entity rates being compared with it are the same measurement.
    """
    fact = get_ontology()["fact_capacity_request"]
    return (len(_failed_rows(fact)) / len(fact)) if len(fact) else 0.0


def _score_group(grp, ctx, busiest_unresolved) -> dict:
    """Risk for one region / datacentre / customer, from its own rows.

    Failure means `_failed_incident_ids()` here as it does everywhere else.
    This used to score `DenialReason != ""`, which counts a request denied and
    then approved inside its SLA -- the one category review said must not be
    counted anywhere. The effect was sites showing "0 failed" in the table and
    banding *high* on the score beside it.
    """
    denied = _failed_rows(grp)
    unresolved = int((grp["NewLimitCapacity"] < grp["RequestedCapacity"]).sum())
    return riskindex.score(
        requests=len(grp), denied=len(denied), unresolved=unresolved,
        utilisation_pct=ctx.get("utilisation", 0), threshold_pct=ctx.get("threshold", 85),
        lead_time_days=ctx.get("leadTime", 0), busiest_unresolved=busiest_unresolved,
        weights=get_risk_weights(),
        prior_rate=_fleet_failure_rate(),
    ).to_dict()


@app.get("/api/datacentres")
def datacentres():
    """Every datacentre, scored. The review's point: a region tells you which
    country to worry about, a datacentre tells you which building."""
    onto = get_ontology()
    fact = onto["fact_capacity_request"]
    ctx = _region_context(onto)
    priced = {str(r["incidentId"]): r for r in _ticket_rows(get_module5().priced, slice(None))}

    sites_by_id = {str(r["DatacentreId"]): r
                   for _, r in onto["dim_datacentre"].iterrows()}
    groups = list(fact.groupby("DatacentreId"))
    busiest = max((int((g["NewLimitCapacity"] < g["RequestedCapacity"]).sum())
                   for _, g in groups), default=1) or 1

    rows = []
    for dc, grp in groups:
        region = str(grp["Region"].iloc[0])
        denied = _failed_rows(grp)
        denied = denied[denied["DenialReason"] != ""]
        loss = sum(float(priced.get(str(i), {}).get("exposure", 0)) for i in grp["IncidentId"])
        site = sites_by_id.get(str(dc))
        site_dep = float(site["DeployedUnits"]) if site is not None else 0.0
        site_used = float(site["UsedUnits"]) if site is not None else 0.0
        site_thr = float(site["ThresholdPct"]) if site is not None else 0.0
        site_util = (site_used / site_dep * 100.0) if site_dep else 0.0
        rows.append({
            "datacentre": str(dc),
            "region": region,
            "hardware": str(ctx.get(region, {}).get("sku", "") or ""),
            # This facility's own safety line and how full it actually is. The
            # region page prints both in its table, but neither reached the
            # assistant, so asked what the "80%" and "over" against
            # southcentralus-dc01 meant it correctly answered that it had no
            # such figure -- while the figure sat on screen beside it.
            "thresholdPct": round(site_thr, 1),
            "siteUtilisationPct": round(site_util, 1),
            "overThreshold": bool(site_util > site_thr),
            "requests": int(len(grp)),
            "failed": int(len(denied)),
            "customers": int(grp["SubscriptionId"].nunique()),
            "revenueLoss": round(loss, 2),
            "topReason": (denied["DenialReason"].mode().iloc[0] if len(denied) else ""),
            "utilisation": ctx.get(region, {}).get("utilisation", 0),
            "leadTime": ctx.get(region, {}).get("leadTime", 0),
            "risk": _score_group(grp, ctx.get(region, {}), busiest),
            # Flagged rather than smoothed away: a 100% failure rate over one
            # request is arithmetic, not evidence, and the reader should see
            # which it is before acting on the ranking.
            "lowEvidence": bool(len(grp) < 3),
        })
    rows.sort(key=lambda r: -r["risk"]["score"])
    return {
        "datacentres": rows,
        # Only sites that have seen a request appear. Saying how many did not
        # keeps the count honest -- otherwise the view looks like the whole
        # estate when it is the active part of it.
        "totalSites": int(len(onto["dim_datacentre"])),
        "withActivity": len(rows),
        "weights": get_risk_weights(),
        "componentLabels": riskindex.COMPONENT_LABELS,
    }


def _migration_options(onto, datacentre_id: str) -> list[dict]:
    """Every alternative hardware class for one facility, costed.

    Sized to that facility's own cores, not the region's -- you convert a
    building. Feasibility still asks the region, because the load has to go
    somewhere while the site is down.
    """
    dim = onto["dim_datacentre"]
    row = dim[dim["DatacentreId"].astype(str) == datacentre_id]
    if row.empty:
        return []
    site = row.iloc[0]
    current, region = str(site["SKUClass"]), str(site["Region"])
    units = float(site["DeployedUnits"] or 0)

    out = []
    for target in sorted(onto["dim_sku"]["SKUClass"]):
        if target == current:
            continue
        try:
            src = module2.calculator.sku_from_dim(onto["dim_sku"], current)
            tgt = module2.calculator.sku_from_dim(onto["dim_sku"], target)
            conv = module2.convert_same_footprint(src, tgt, units).to_dict()
            plan = module2.plan_conversion(onto, region, target, convert_datacentres=1)
        except (KeyError, ValueError):
            continue
        out.append({
            "toSku": target,
            "coresAfter": _clean(conv["to_units"]),
            "capacityAfter": _clean(conv["capacity_after"]),
            "capacityDelta": _clean(conv["capacity_delta"]),
            "costDeltaPct": _clean(conv["cost_delta_pct"]),
            "leadTimeDays": _clean(conv["lead_time_days"]),
            "feasible": bool(plan.can_convert_a_whole_datacentre),
        })
    # Best capability gain first -- that is the question being asked.
    out.sort(key=lambda o: -o["capacityDelta"])
    return out


def _threshold_remediation(onto, datacentre_id: str) -> dict:
    """What a threshold denial at this facility actually needs.

    Two levers, both quantified: how many cores the current safety line is
    holding back, and how much a higher line would release. Neither is a
    hardware change, so offering one would be answering a different question.
    """
    dim = onto["dim_datacentre"]
    row = dim[dim["DatacentreId"].astype(str) == datacentre_id]
    if row.empty:
        return {}
    s = row.iloc[0]
    cores = float(s["DeployedUnits"] or 0)
    used = float(s["UsedUnits"] or 0)
    current = float(s["ThresholdPct"] or 0)

    options = []
    for pct_line in (current + 5, current + 10):
        if pct_line > 100:
            continue
        options.append({
            "thresholdPct": round(pct_line, 1),
            "releasesCores": round(cores * (pct_line - current) / 100.0, 1),
            "headroomAfter": round(cores * pct_line / 100.0 - used, 1),
        })

    return {
        "currentPct": current,
        "cores": round(cores, 1),
        "usedCores": round(used, 1),
        "headroomNow": round(cores * current / 100.0 - used, 1),
        "options": options,
        "leadTimeDays": _clean(s.get("LeadTimeDays")),
    }


@app.get("/api/datacentre/{datacentre_id}")
def datacentre_detail(datacentre_id: str):
    """One site: its tickets, who was hit, why, and how its risk was built.

    A separate endpoint rather than folding it into /api/datacentres because
    clicking a site should open that site -- bouncing the reader to the region
    view loses their place and throws away the level of detail they just asked
    for.
    """
    onto = get_ontology()
    dim = onto["dim_datacentre"]
    row = dim[dim["DatacentreId"].astype(str) == datacentre_id]
    if row.empty:
        raise HTTPException(404, f"unknown datacentre {datacentre_id!r}")

    site = row.iloc[0]
    region = str(site["Region"])
    fact = onto["fact_capacity_request"]
    here = fact[fact["DatacentreId"].astype(str) == datacentre_id]

    ctx = _region_context(onto).get(region, {})
    busiest = max(
        (int((g["NewLimitCapacity"] < g["RequestedCapacity"]).sum())
         for _, g in fact.groupby("DatacentreId")), default=1) or 1

    ids = set(here["IncidentId"].astype(str))
    tickets = [r for r in _ticket_rows(get_module5().priced, slice(None))
               if str(r["incidentId"]) in ids]

    denied = _failed_rows(here)
    denied = denied[denied["DenialReason"] != ""]
    priced_by_id = {r["incidentId"]: r for r in tickets}

    # One remediation per cause, with the migration arithmetic attached where a
    # hardware change is the fix. Review was blunt that a generic line "is what
    # ChatGPT would say" -- so where module2 owns the cause, the options are
    # computed for this facility's own core count rather than described.
    # One source of truth for the recommendation text, shared with the region
    # view -- the two disagreeing about what to do would be worse than either
    # being wrong.
    loss_by_reason = {}
    for name in denied["DenialReason"].unique():
        ids = denied[denied["DenialReason"] == name]["IncidentId"].astype(str)
        loss_by_reason[str(name)] = sum(
            float(priced_by_id.get(i, {}).get("exposure", 0)) for i in ids)

    reasons = []
    for rec in remediation.for_site(onto, datacentre_id, denied, loss_by_reason,
                                   crossing_for=_forecast_crossing):
        entry = rec.to_dict()
        entry["detail"] = attribution.REASONS.get(rec.reason, {}).get("detail", "")
        # Split the options by kind so the page can render each shape.
        entry["migration"] = [o for o in rec.options if o.get("kind") == "conversion"]
        entry["threshold"] = [o for o in rec.options if o.get("kind") == "threshold"]
        reasons.append(entry)

    return {
        "datacentre": datacentre_id,
        "region": region,
        "cores": _clean(site.get("DeployedUnits")),
        "coresFree": _clean(site.get("FreeUnits")),
        "thresholdPct": _clean(site.get("ThresholdPct")),
        "headroom": _clean(site.get("HeadroomToThreshold")),
        "failedCount": len([x for x in tickets if x.get("isFlagged")]),
        "recommendations": reasons,
        "hardware": str(row.iloc[0].get("SKUClass") or ""),
        "leadTimeDays": _clean(row.iloc[0].get("LeadTimeDays")),
        "deployedUnits": _clean(row.iloc[0].get("DeployedUnits")),
        "utilisation": ctx.get("utilisation", 0),
        "threshold": ctx.get("threshold", 85),
        "requests": int(len(here)),
        "failedCount": len([x for x in tickets if x.get("isFlagged")]),
        "customers": int(here["SubscriptionId"].nunique()),
        "revenueLoss": round(sum(float(x["exposure"]) for x in tickets), 2),
        "risk": _score_group(here, ctx, busiest),
        "lowEvidence": bool(len(here) < 3),
        "reasons": reasons,
        "tickets": tickets,
        "componentLabels": riskindex.COMPONENT_LABELS,
    }


@app.get("/api/incidents")
def incidents():
    """Every ticket, flat and filterable. The bottom of the drill-down."""
    rows = _ticket_rows(get_module5().priced, slice(None))
    return {
        "incidents": rows,
        "regions": sorted({r["region"] for r in rows}),
        "reasons": sorted({r["reason"] for r in rows if r["reason"]}),
        "outcomes": sorted({r["outcomeLabel"] for r in rows}),
    }


@app.get("/api/reasons")
def reasons():
    """The problem view: each cause, where it bites, and what to do."""
    onto = get_ontology()
    fact = onto["fact_capacity_request"]
    priced = {str(r["incidentId"]): r for r in _ticket_rows(get_module5().priced, slice(None))}
    denied = _failed_rows(fact)
    denied = denied[denied["DenialReason"] != ""]

    rows = []
    for r in _reason_breakdown(onto):
        here = denied[denied["DenialReason"] == r["reason"]]
        by_region = (here.groupby("Region").size()
                     .sort_values(ascending=False).head(5).items())
        rows.append({
            **r,
            "revenueLoss": round(sum(
                float(priced.get(str(i), {}).get("exposure", 0)) for i in here["IncidentId"]), 2),
            "regions": [{"region": k, "count": int(v)} for k, v in by_region],
            "datacentres": int(here["DatacentreId"].nunique()),
            "customers": int(here["SubscriptionId"].nunique()),
        })
    return {"reasons": rows, "totalFailed": int(len(denied))}


def _forecast_crossing(region: str, threshold_pct: float):
    """The crossing date from the backtested winner, for module 1 to plan on.

    One forecast in the product. Before this, the Regions tab and the Forecast
    tab each computed their own and disagreed by up to ten days about the same
    region.
    """
    try:
        f = _forecast(region, float(threshold_pct))
    except Exception:
        # Only here does module 1 fall back to its own fit -- a forecast that
        # fails must not take the Regions tab down with it.
        return None
    # A date, or False meaning "it ran, and there is no crossing". The
    # distinction matters: False is an answer and must not be overridden.
    return f.crossing_date or False


@lru_cache(maxsize=1)
def get_anomalies():
    """Outliers per region, and the dates the forecast must not train on."""
    onto = get_ontology()
    return anomaly.detect_all(onto["fact_usage_daily"], onto["fact_event"])


@app.get("/api/anomalies")
def anomalies():
    found = get_anomalies()
    total = sum(len(a.outliers) for a in found.values())
    explained = sum(sum(1 for o in a.outliers if o.explained) for a in found.values())
    return {
        "regions": {r: a.to_dict() for r, a in found.items()},
        "total": total, "explained": explained, "unexplained": total - explained,
        "method": {
            "fence": f"Tukey {anomaly.IQR_MULTIPLIER} x IQR on the residual",
            "detrendWindow": anomaly.TREND_WINDOW,
            "season": anomaly.SEASON,
            "eventWindowDays": anomaly.EVENT_WINDOW_DAYS,
        },
    }


@lru_cache(maxsize=1)
def _forced_model() -> str | None:
    """A model imposed on every region, or None to let the backtest decide.

    Set in config.json so the decision is a setting rather than a code change,
    and so it is visible to anyone reading the configuration rather than buried
    in a module. When set, the accuracy given up is measured per region and
    reported on screen -- an imposed model must not be presented as a chosen one.
    """
    import json
    try:
        with open(ROOT / "config.json") as fh:
            name = json.load(fh).get("forecast_force_model")
    except Exception:
        return None
    return str(name) if name else None


#: Match module 1's projection limit. With a shorter horizon the Forecast tab
#: reported "no crossing" for a region the Regions tab had already given an
#: order-by date -- the same answer, over two different windows, which reads as
#: a contradiction.
FORECAST_HORIZON_DAYS = int(module1.threshold.MAX_PROJECTION_DAYS)


@lru_cache(maxsize=64)
def _forecast(region: str, threshold: float):
    onto = get_ontology()
    excluded = get_anomalies().get(region)
    return forecast.forecast_region(
        onto["fact_usage_daily"], region, threshold_pct=threshold,
        horizon_days=FORECAST_HORIZON_DAYS,
        exclude_anomalies=excluded.excluded_dates if excluded else None,
        force_model=_forced_model(),
    )


@app.get("/api/forecast")
def forecast_all(threshold: Annotated[float | None, Query(ge=50.0, le=99.0)] = None):
    """Every region's projection, judged against that region's own threshold.

    `threshold` omitted uses each region's own line, which is what the Regions
    tab does -- the two must agree or the same region shows two crossing dates.
    """
    onto = get_ontology()
    out = []
    for region in sorted(onto["dim_region"]["Region"]):
        out.append(_trim_for_plotting(_clean(
            _forecast(region, _region_threshold(region, threshold)).to_dict())))
    # Soonest crossing first; regions already past the line lead.
    out.sort(key=lambda f: (f["crossingDate"] is None and not f["note"], f["crossingDate"] or ""))
    return {
        "forecasts": out,
        "thresholdPct": threshold,
        "thresholdIsPerRegion": threshold is None,
        "candidates": sorted(forecast.CANDIDATES),
        "arimaAvailable": "arima" in forecast.CANDIDATES,
        # Two different horizons, and conflating them misreports both: HORIZON is
        # the length of each backtest fold, projectionDays is how far the chart
        # actually runs.
        "horizonDays": forecast.HORIZON,
        "projectionDays": FORECAST_HORIZON_DAYS,
        "folds": forecast.BACKTEST_FOLDS,
    }


#: Shortest window the chart will ever draw.
MIN_PLOT_DAYS = 90
#: Days of headroom drawn past the crossing, so the marker is not on the edge.
PLOT_MARGIN_DAYS = 30


def _trim_for_plotting(d: dict, trim: bool = True) -> dict:
    """Compute a year ahead, but only plot as far as the chart needs.

    The projection has to run the full 365 days or the Regions tab and this one
    disagree about late crossings. Drawing all 365 is a different mistake: with
    149 days of history the chart comes out 71% forecast, so the only real data
    on it is squeezed into the left third and the eye reads the speculation as
    the finding. Trimming to the crossing plus a margin keeps every date this
    page reports on the chart while leaving history the larger share of it.

    `trim=False` keeps the whole year. The region page asks for it, and the
    ratio that makes trimming necessary here does not hold there: that chart
    also carries eighteen months of demand history, so a year of projection is
    a minority of its width rather than 71% of it. The flags below are still
    computed either way -- a caller drawing the full year needs the warning
    about extrapolation more than a caller drawing part of it, not less.
    """
    proj = d.get("projection") or []
    if not proj:
        return d
    if not trim:
        d["plottedDays"] = len(proj)
        d["saturationBeyondChart"] = False
        d["extrapolatedBeyondHistory"] = len(proj) > len(d.get("history") or [])
        return d
    # The headline date only. A region past its line is headlined by when it
    # fills; one still under it, by when it crosses. Stretching the chart to
    # cover both puts saturation dates as far out as November on it and undoes
    # the trim -- canadacentral crosses in March and fills in June.
    target = d.get("saturationDate") if d.get("alreadyBreached") else d.get("crossingDate")
    keep = MIN_PLOT_DAYS
    if target:
        i = next((n for n, pt in enumerate(proj) if pt["date"] == target), None)
        if i is not None:
            keep = max(keep, i + 1 + PLOT_MARGIN_DAYS)
    d["projection"] = proj[:keep]
    #: The saturation date is still computed over the full year and still worth
    #: quoting -- it just is not on the chart. Saying so beats letting a reader
    #: hunt the axis for a date that was never drawn.
    last = proj[keep - 1]["date"] if keep <= len(proj) else proj[-1]["date"]
    d["saturationBeyondChart"] = bool(
        d.get("saturationDate") and d["saturationDate"] > last)
    d["plottedDays"] = len(d["projection"])
    #: True when the projection outruns the history it was fitted on. Reviewers
    #: should discount a crossing date that sits out there, so it is stated
    #: rather than left for them to work out from the axis.
    d["extrapolatedBeyondHistory"] = len(d["projection"]) > len(d.get("history") or [])
    return d


@app.get("/api/forecast/{region}")
def forecast_one(region: str,
                 threshold: Annotated[float | None, Query(ge=50.0, le=99.0)] = None,
                 full: Annotated[bool, Query()] = False):
    onto = get_ontology()
    if region not in set(onto["dim_region"]["Region"]):
        raise HTTPException(404, f"unknown region {region!r}")
    d = _trim_for_plotting(_clean(
        _forecast(region, _region_threshold(region, threshold)).to_dict()),
        trim=not full)
    found = get_anomalies().get(region)
    d["anomalies"] = found.to_dict() if found else None
    return d


@app.get("/api/capacity-policy")
def capacity_policy(
    enterprise: Annotated[float | None, Query()] = None,
    premium: Annotated[float | None, Query()] = None,
    standard: Annotated[float | None, Query()] = None,
    free: Annotated[float | None, Query()] = None,
):
    """Replay every region's requests under a tier reserve.

    The complaint this answers: nothing is held back, so whoever asks first
    wins and an Enterprise request can find a region already emptied.
    """
    reserve = admission.DEFAULT_RESERVE
    if None not in (enterprise, premium, standard, free):
        try:
            reserve = admission.validate_reserve({
                "Enterprise": enterprise, "Premium": premium,
                "Standard": standard, "Free": free})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    onto = get_ontology()
    sims = admission.simulate_all(onto, reserve, failed_ids=_failed_incident_ids())
    rows = [_clean(s.to_dict()) for s in sims.values()]
    rows.sort(key=lambda r: -r["wouldHavePrevented"])
    return {
        "regions": rows,
        "reserve": reserve,
        "defaultReserve": admission.DEFAULT_RESERVE,
        "totals": {
            "admitted": sum(r["admitted"] for r in rows),
            "denied": sum(r["denied"] for r in rows),
            "actualFailures": sum(r["actualFailures"] for r in rows),
            "wouldHavePrevented": sum(r["wouldHavePrevented"] for r in rows),
        },
        "pools": _records(onto["dim_capacity_pool"]),
        "skuLadder": admission.F_SKUS,
        "unitsPerCu": admission.UNITS_PER_CU,
    }


@app.get("/api/trend")
def trend(region: str | None = None):
    demand = get_demand()
    if region:
        demand = demand[demand["Region"] == region]
    series = demand.groupby("Period")["Value"].sum().reset_index().sort_values("Period")
    return {"points": [{"period": p, "value": float(v)}
                       for p, v in zip(series["Period"], series["Value"])]}


@app.get("/api/spikes")
def spikes(region: str | None = None):
    found = module4.explain_anomalies(get_demand(), get_ontology()["fact_event"])
    if region:
        found = [a for a in found if a.region == region]
    return {"spikes": [{k: _clean(v) for k, v in a.to_dict().items()} for a in found]}


#: The planning engines are pure functions over the ontology and the ontology
#: is cached, so their output is too. Recomputing incident rates across three
#: hundred capacities on every map hover is work nobody asked for.
@lru_cache(maxsize=1)
def _recommendations() -> list:
    from planning import recommend as planning_recommend

    return planning_recommend.all_recommendations(get_ontology())


@lru_cache(maxsize=1)
def _capacity_health():
    from planning import capacity_health

    onto = get_ontology()
    return capacity_health(onto["dim_capacity"],
                           onto["fact_operational_incident"],
                           onto["fact_capacity_usage_daily"])


def _availability(region: str) -> dict:
    """What Fabric runs in a region, and what does not.

    The published table lists only the gaps, so the available side is derived:
    the nine workloads Microsoft names, less any the gaps sit inside. That
    distinction matters because a region can support "all Fabric workloads" and
    still be missing pieces of two of them.
    """
    avail = get_ontology()["bridge_region_fabric_availability"].set_index("Region")
    if region not in avail.index:
        return {"allFabricWorkloads": None, "powerBIOnly": None,
                "workloadsAvailable": [], "workloadsPartlyAffected": [],
                "unavailableFeatures": []}
    a = avail.loc[region]

    def _split(col):
        raw = a.get(col)
        return [x for x in str(raw).split(";") if x] if isinstance(raw, str) else []

    affected = _split("WorkloadsPartlyAffected")
    # "Fabric platform" is where a platform-level feature lands; it is not one
    # of the nine workloads. Kept apart so the counts on screen reconcile: six
    # clean workloads plus three with gaps is nine, and lumping the platform in
    # made that four and the arithmetic unfollowable.
    return {
        "allFabricWorkloads": bool(a["AllFabricWorkloads"]),
        "powerBIOnly": bool(a["PowerBIOnly"]),
        "workloadCount": 9,
        "workloadsAvailable": _split("WorkloadsAvailable"),
        "workloadsPartlyAffected": [w for w in affected if w != "Fabric platform"],
        "platformAffected": "Fabric platform" in affected,
        "unavailableFeatures": _split("UnavailableFeatures"),
    }


@app.get("/api/map")
def capacity_map():
    """Every region as a point, with enough to decide without opening it.

    The landing screen review asked for: "you yourself think you're a capacity
    manager, you are sitting in front of all your data centres, you have your
    map in front". A marker therefore carries the four things that would
    otherwise be four tabs -- how full, whether it crosses its line and when,
    what is waiting to be bought, and what Fabric will not run there.

    Coordinates are Azure's own published figures, and so is the workload
    availability. Everything else on the marker is generated, which is why the
    payload says which is which rather than presenting one flat set of numbers.
    """
    onto = get_ontology()
    geo = onto["dim_region_geography"].set_index("Region")
    avail = onto["bridge_region_fabric_availability"].set_index("Region")
    caps = onto["dim_capacity"]
    recs = _recommendations()

    flags = {f["region"]: f for f in _records(
        module1.project_all(onto, crossing_for=_forecast_crossing))}
    exposure = {r["Region"]: r for r in get_module5().finding["regions"]}

    by_region_kind: dict[tuple, int] = {}
    for r in recs:
        key = (r["evidence"].get("region"), r["kind"])
        by_region_kind[key] = by_region_kind.get(key, 0) + 1

    cap_counts = caps.groupby("Region").agg(
        capacities=("CapacityId", "count"),
        units=("DeployedUnits", "sum"),
        sites=("DatacentreId", "nunique"),
    )

    points = []
    for region in sorted(onto["dim_region"]["Region"]):
        f = flags.get(region, {})
        e = exposure.get(region, {})
        g = geo.loc[region] if region in geo.index else None
        av = _availability(region)
        gaps = av["unavailableFeatures"]
        c = cap_counts.loc[region] if region in cap_counts.index else None
        points.append({
            "region": region,
            "displayName": str(g["DisplayName"]) if g is not None else region,
            "city": str(g["City"]) if g is not None else "",
            "lat": float(g["Latitude"]) if g is not None else None,
            "lon": float(g["Longitude"]) if g is not None else None,
            "utilisation": f.get("current_utilisation_pct"),
            "thresholdPct": f.get("threshold_pct"),
            "status": f.get("status"),
            "crossingDate": f.get("cross_date"),
            "daysUntilOrder": f.get("days_until_order"),
            "leadTimeDays": f.get("lead_time_days"),
            "skuClass": f.get("sku_class"),
            "capacities": int(c["capacities"]) if c is not None else 0,
            "sites": int(c["sites"]) if c is not None else 0,
            "units": int(c["units"]) if c is not None else 0,
            "coresPending": e.get("CoresPending", 0),
            "failed": e.get("TicketsFlagged", 0),
            "exposure": e.get("RevenueExposureUSD", 0),
            "allFabricWorkloads": av["allFabricWorkloads"],
            "powerBIOnly": av["powerBIOnly"],
            "unavailableFeatures": gaps,
            "workloadCount": av["workloadCount"],
            "workloadsAvailable": av["workloadsAvailable"],
            "workloadsPartlyAffected": av["workloadsPartlyAffected"],
            "platformAffected": av["platformAffected"],
            "recommendations": {
                kind: by_region_kind.get((region, kind), 0)
                for kind in ("procurement", "workload_change", "licensing")
            },
        })
    return {
        "points": points,
        "asOf": str(onto["fact_usage_daily"]["Date"].max()),
        "provenance": {
            "coordinates": "REAL - Azure Resource Manager region metadata",
            "featureAvailability": "REAL - Microsoft Learn Fabric region availability",
            "capacityAndUsage": "GENERATED - see data/synthetic",
        },
    }


@app.get("/api/map/{region}")
def map_region(region: str):
    """Everything behind one marker, fetched when it is clicked.

    The marker card answers "is this region a problem". This answers the four
    questions that follow and previously took four tabs: how many buildings are
    there and what is in each, when does it cross its line, what has to change,
    and why. Kept out of `/api/map` so the map itself stays a small payload --
    eleven of these would be most of the fleet.
    """
    onto = get_ontology()
    if region not in set(onto["dim_region"]["Region"]):
        raise HTTPException(404, f"unknown region {region!r}")

    health = _capacity_health()
    mine = health[health["Region"] == region]
    hw = onto["dim_hardware"].set_index("SKUClass")
    sites_dim = onto["dim_datacentre"]
    sites_dim = sites_dim[sites_dim["Region"] == region]

    flags = {f["region"]: f for f in _records(
        module1.project_all(onto, crossing_for=_forecast_crossing))}
    f = flags.get(region, {})
    fc = _trim_for_plotting(_clean(
        _forecast(region, _region_threshold(region)).to_dict()), trim=True)

    # One row per building, with what is actually in it.
    sites = []
    for s in sites_dim.itertuples():
        here = mine[mine["DatacentreId"] == s.DatacentreId]
        spec = hw.loc[s.SKUClass] if s.SKUClass in hw.index else None
        used_pct = (float(s.UsedUnits) / float(s.DeployedUnits) * 100
                    if float(s.DeployedUnits) else 0.0)
        sites.append({
            "datacentre": s.DatacentreId,
            "skuClass": s.SKUClass,
            "vendor": str(spec["Vendor"]) if spec is not None else "",
            "model": str(spec["Model"]) if spec is not None else "",
            "cpu": str(spec["Cpu"]) if spec is not None else "",
            "memoryGB": int(spec["MemoryGB"]) if spec is not None else None,
            "coresPerNode": int(spec["CoresPerNode"]) if spec is not None else None,
            "leadTimeDays": int(s.LeadTimeDays),
            "capacities": int(len(here)),
            "skuMix": {sku: int(n) for sku, n in
                       sorted(here["FabricSku"].value_counts().items())},
            "units": int(s.DeployedUnits),
            "usedUnits": round(float(s.UsedUnits), 1),
            "freeUnits": round(float(s.FreeUnits), 1),
            "utilisationPct": round(used_pct, 1),
            "thresholdPct": round(float(s.ThresholdPct), 1),
            "headroomToThreshold": round(float(s.HeadroomToThreshold), 1),
            "pastThreshold": bool(used_pct >= float(s.ThresholdPct)),
            "nodes": int(here["Nodes"].sum()) if len(here) else 0,
            "incidents": int(here["Incidents"].sum()) if len(here) else 0,
            "seriousIncidents": int(here["SeriousIncidents"].sum()) if len(here) else 0,
            "incidentsPerNode": (round(float(here["Incidents"].sum())
                                       / float(here["Nodes"].sum()), 2)
                                 if len(here) and here["Nodes"].sum() else 0.0),
            "freeViewerCapable": int(here["SupportsFreeViewers"].sum()) if len(here) else 0,
        })
    sites.sort(key=lambda s: -s["units"])

    recs = [r for r in _recommendations() if r["evidence"].get("region") == region]
    by_kind: dict[str, list] = {}
    for r in recs:
        by_kind.setdefault(r["kind"], []).append(r)

    av = _availability(region)
    gaps = av["unavailableFeatures"]

    fleet_rate = float(health["FleetRate"].iloc[0]) if len(health) else 0.0
    return {
        "region": region,
        "status": f.get("status"),
        "utilisation": f.get("current_utilisation_pct"),
        "thresholdPct": f.get("threshold_pct"),
        "reason": f.get("reason"),
        "skuClass": f.get("sku_class"),
        "leadTimeDays": f.get("lead_time_days"),
        "daysUntilOrder": f.get("days_until_order"),
        "orderByDate": f.get("order_by_date"),
        "threshold": {
            "crossingDate": fc.get("crossingDate"),
            "crossingEarliest": fc.get("crossingEarliest"),
            "crossingLatest": fc.get("crossingLatest"),
            "saturationDate": fc.get("saturationDate"),
            "alreadyBreached": bool(fc.get("alreadyBreached")),
            "model": fc.get("model"),
            "note": ("Crossing the safety line is not running out. The line is a "
                     "margin below full; the saturation date is when there is "
                     "nothing left at all."),
        },
        "sites": sites,
        "totals": {
            "sites": len(sites),
            "capacities": int(len(mine)),
            "units": int(mine["DeployedUnits"].sum()) if len(mine) else 0,
            "nodes": int(mine["Nodes"].sum()) if len(mine) else 0,
            "incidents": int(mine["Incidents"].sum()) if len(mine) else 0,
            "hardwareClasses": sorted(mine["SKUClass"].unique().tolist()) if len(mine) else [],
            "skuMix": {sku: int(n) for sku, n in
                       sorted(mine["FabricSku"].value_counts().items())} if len(mine) else {},
            "sitesPastThreshold": sum(1 for s in sites if s["pastThreshold"]),
            "freeViewerCapable": int(mine["SupportsFreeViewers"].sum()) if len(mine) else 0,
        },
        "fleetIncidentsPerNode": round(fleet_rate, 3),
        "recommendations": by_kind,
        "recommendationCounts": {k: len(v) for k, v in by_kind.items()},
        "unavailableFeatures": gaps,
        "allFabricWorkloads": av["allFabricWorkloads"],
        "workloadCount": av["workloadCount"],
        "workloadsAvailable": av["workloadsAvailable"],
        "workloadsPartlyAffected": av["workloadsPartlyAffected"],
        "platformAffected": av["platformAffected"],
        "workloadNote": (
            "The nine workloads Microsoft names for Fabric. A workload listed as "
            "affected still runs here -- named features inside it do not."),
    }


@app.get("/api/recommendations")
def recommendations(kind: Annotated[str | None, Query()] = None,
                    region: Annotated[str | None, Query()] = None,
                    limit: Annotated[int, Query(ge=1, le=500)] = 100):
    """What to do, most urgent first.

    Filterable because the three kinds answer to different people: procurement
    goes to whoever raises purchase orders, a workload change goes to whoever
    owns the hardware, and the licensing case is commercial.
    """
    recs = _recommendations()
    if kind:
        recs = [r for r in recs if r["kind"] == kind]
    if region:
        recs = [r for r in recs if r["evidence"].get("region") == region]
    counts: dict[str, int] = {}
    for r in _recommendations():
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {
        "recommendations": recs[:limit],
        "total": len(recs),
        "shown": min(len(recs), limit),
        "countsByKind": counts,
        # Early raises and workload changes are the two the utilisation number
        # cannot produce, so they are counted separately rather than left for a
        # reader to find among a hundred routine overdue purchases.
        "earlyRaises": sum(1 for r in _recommendations()
                           if r["kind"] == "procurement"
                           and r["evidence"].get("raisedEarly")),
    }


@app.get("/api/capacities")
def capacities(region: Annotated[str | None, Query()] = None,
               datacentre: Annotated[str | None, Query()] = None):
    """The Fabric capacities themselves: SKU, hardware, how full, how healthy.

    The grain review kept asking for and the product did not have -- "these are
    the SKUs there in this data centre, this is the capacity available, this is
    what we don't have".
    """
    health = _capacity_health()
    if region:
        health = health[health["Region"] == region]
    if datacentre:
        health = health[health["DatacentreId"] == datacentre]

    hw = get_ontology()["dim_hardware"].set_index("SKUClass")
    rows = []
    for c in health.itertuples():
        spec = hw.loc[c.SKUClass] if c.SKUClass in hw.index else None
        rows.append({
            "capacityId": c.CapacityId,
            "datacentre": c.DatacentreId,
            "region": c.Region,
            "fabricSku": c.FabricSku,
            "capacityUnits": int(c.CapacityUnits),
            "deployedUnits": int(c.DeployedUnits),
            "utilisationPct": round(float(c.UtilisationPct), 1),
            "skuClass": c.SKUClass,
            "vendor": c.Vendor,
            "model": c.Model,
            "nodes": int(c.Nodes),
            "cpu": str(spec["Cpu"]) if spec is not None else "",
            "memoryGB": int(spec["MemoryGB"]) if spec is not None else None,
            "storageTB": float(spec["StorageTB"]) if spec is not None else None,
            "incidents": int(c.Incidents),
            "seriousIncidents": int(c.SeriousIncidents),
            "downtimeHours": round(c.DowntimeMinutes / 60.0, 1),
            "incidentsPerNode": float(c.IncidentRate),
            "fleetIncidentsPerNode": float(c.FleetRate),
            "rateVsFleet": float(c.RateVsFleet),
            "supportsFreeViewers": bool(c.SupportsFreeViewers),
        })
    rows.sort(key=lambda r: (-r["capacityUnits"], r["capacityId"]))
    total_units = sum(r["deployedUnits"] for r in rows)
    used = sum(r["deployedUnits"] * r["utilisationPct"] / 100.0 for r in rows)

    # What "the fleet" is, stated rather than assumed. The baseline every rate
    # on this page is measured against is the whole estate, and it stays the
    # whole estate when the page is filtered to one region -- otherwise a region
    # of uniformly bad hardware would compare itself against itself and report
    # that everything was normal.
    everything = _capacity_health()
    return {
        "capacities": rows,
        "count": len(rows),
        "fleet": {
            "capacities": int(len(everything)),
            "sites": int(everything["DatacentreId"].nunique()),
            "regions": int(everything["Region"].nunique()),
            "nodes": int(everything["Nodes"].sum()),
            "incidents": int(everything["Incidents"].sum()),
            # Not re-rounded. `capacity_health` already rounds this, and
            # rounding it again here produced 1.88 in the summary against 1.875
            # on every row -- the same quantity printed two ways, which is the
            # split this project keeps removing.
            "incidentsPerNode": float(everything["FleetRate"].iloc[0])
            if len(everything) else 0.0,
            "means": ("every capacity in the estate, across all regions — not "
                      "just the ones listed here"),
        },
        "skuMix": {sku: sum(1 for r in rows if r["fabricSku"] == sku)
                   for sku in sorted({r["fabricSku"] for r in rows})},
        "totalUnits": total_units,
        "usedUnits": round(used, 1),
        "freeUnits": round(total_units - used, 1),
        "freeViewerCapable": sum(1 for r in rows if r["supportsFreeViewers"]),
        "note": ("Capacities, their hardware and their per-capacity utilisation "
                 "are generated. The Fabric SKU ladder and the F64 licensing "
                 "rule are real."),
    }


@app.get("/api/capacity/{capacity_id}")
def capacity_detail(capacity_id: str):
    """One capacity, with its own history, incidents and any advice about it."""
    onto = get_ontology()
    health = _capacity_health()
    row = health[health["CapacityId"] == capacity_id]
    if row.empty:
        raise HTTPException(404, f"unknown capacity {capacity_id!r}")
    c = row.iloc[0]

    usage = onto["fact_capacity_usage_daily"]
    series = usage[usage["CapacityId"] == capacity_id].sort_values("Date")
    ops = onto["fact_operational_incident"]
    mine = ops[ops["CapacityId"] == capacity_id].sort_values("OpenedDate", ascending=False)
    hw = onto["dim_hardware"].set_index("SKUClass")
    spec = hw.loc[c["SKUClass"]] if c["SKUClass"] in hw.index else None

    return {
        "capacityId": capacity_id,
        "datacentre": c["DatacentreId"],
        "region": c["Region"],
        "fabricSku": c["FabricSku"],
        "capacityUnits": int(c["CapacityUnits"]),
        "deployedUnits": int(c["DeployedUnits"]),
        "nodes": int(c["Nodes"]),
        "supportsFreeViewers": bool(c["SupportsFreeViewers"]),
        "hardware": {
            "skuClass": c["SKUClass"], "vendor": c["Vendor"], "model": c["Model"],
            "cpu": str(spec["Cpu"]) if spec is not None else "",
            "coresPerNode": int(spec["CoresPerNode"]) if spec is not None else None,
            "memoryGB": int(spec["MemoryGB"]) if spec is not None else None,
            "storageTB": float(spec["StorageTB"]) if spec is not None else None,
        },
        "utilisation": _records(series[["Date", "UtilisationPct", "UsedUnits", "TotalUnits"]]),
        "health": {
            "incidents": int(c["Incidents"]),
            "seriousIncidents": int(c["SeriousIncidents"]),
            "downtimeHours": round(float(c["DowntimeMinutes"]) / 60.0, 1),
            "incidentsPerNode": float(c["IncidentRate"]),
            "rawIncidentsPerNode": float(c["RawRate"]),
            "fleetIncidentsPerNode": float(c["FleetRate"]),
            "rateVsFleet": float(c["RateVsFleet"]),
            "shrunkTowardFleet": (
                "Rate is shrunk toward the fleet average in proportion to how "
                "many nodes stand behind it, so a one-node capacity with a bad "
                "month does not outrank a fleet."),
        },
        "incidents": _records(mine[["OperationalIncidentId", "OpenedDate", "Severity",
                                    "IncidentType", "DowntimeMinutes",
                                    "ImpactedCustomers"]].head(50)),
        "recommendations": [r for r in _recommendations() if r["target"] == capacity_id],
    }


@app.get("/api/lead-times")
def lead_times():
    """Lead time per hardware class, and how it has moved.

    The table that makes "do not wait for the usual trigger" arguable rather
    than assertable.
    """
    from planning import lead_time_drift

    onto = get_ontology()
    drift = lead_time_drift(onto["dim_lead_time_history"])
    return {
        "classes": drift,
        "history": _records(onto["dim_lead_time_history"]),
        "note": ("Lead-time history is generated. Present-day values match the "
                 "lead times already used across the product."),
    }


@app.get("/api/features")
def features(region: str | None = None):
    onto = get_ontology()
    bridge = onto["bridge_feature_region"]
    if region:
        bridge = bridge[bridge["Region"] == region]
    return {
        "cells": _records(bridge[["Feature", "Region", "Status"]]),
        "features": sorted(onto["dim_feature"]["Feature"]),
        "regions": sorted(onto["dim_region"]["Region"]),
    }


#: The review asked for standard incident vocabulary rather than plain English
#: invented here -- FTR and SLA are words the capacity team already uses, so a
#: reader does not have to learn ours before reading the number.
OUTCOME_LABELS = {
    "no_denial": "Approved first time (within FTR)",
    "same_day_approved": "Denied, then approved within SLA",
    "denied_then_approved_late": "Denied, then approved — SLA breached",
    "denied_unfulfilled": "Not approved — still unfulfilled",
    "data_quality_error": "Could not be classified",
}


def _working_out(t, arr: float, share: float, days: float) -> str:
    """The arithmetic in words, or the reason there isn't any."""
    if not bool(t.IsFlagged):
        return "No failure - nothing at risk."

    if arr <= 0:
        # Still a failure, and worth saying so plainly rather than showing a
        # multiplication by zero that implies nothing went wrong.
        return (
            f"Real failure: {share:.0%} of the requested capacity was missing for "
            f"{days:.1f} days. Exposure is $0 because this is a {t.SubscriptionTier}-tier "
            f"customer with no recorded revenue - the measure is revenue at risk, and "
            f"there is no revenue to put at risk. The delay is not excused by that."
        )

    # Units rather than a percentage. The whole point of showing the sum is
    # that nobody has to trust the total, so a reader who checks it and lands
    # somewhere else is worse off than one never shown the arithmetic at all.
    # A rounded percentage guarantees that: this row read "x 95% x 25.5 days"
    # for 95.402% over 25.458 days, and checking it gave $37,491 against a
    # stated $37,430.81. "83 of 87 units" is exact, and it is also the thing
    # the capacity team actually thinks in.
    blocked = float(t.BlockedUnits or 0)
    requested = float(t.RequestedCapacity or 0)
    return (
        f"${arr:,.0f} annual revenue x {blocked:,.0f} of {requested:,.0f} units "
        f"missing x {days:.2f} days / 365 = "
        f"${float(t.RevenueExposureUSD or 0):,.2f} (days rounded for display)"
    )


@lru_cache(maxsize=1)
def _customer_names() -> dict:
    """SubscriptionId -> display name. Review: an id beside an incident id of
    the same shape forces the reader to work out which is which first."""
    dim = get_ontology()["dim_subscription"]
    return dict(zip(dim["SubscriptionId"].astype(str), dim["CustomerName"], strict=True))


@lru_cache(maxsize=1)
def _ticket_attributes() -> dict:
    """Datacentre and denial reason, keyed by incident.

    Module 5 loads tickets through its own ingest path, so its priced frame has
    never seen the two columns the ontology adds. Reading them off `priced` with
    getattr silently returned "" for every row -- the site column was blank and
    every reason showed as a dash, while the panel directly above listed the
    same reasons correctly. Joined here so every view gets them from one place.
    """
    fact = get_ontology()["fact_capacity_request"]
    return {
        str(r.IncidentId): {
            "datacentre": str(getattr(r, "DatacentreId", "") or ""),
            "reason": str(getattr(r, "DenialReason", "") or ""),
        }
        for r in fact.itertuples()
    }


@lru_cache(maxsize=1)
def _pool_sku() -> dict:
    pool = get_ontology()["dim_capacity_pool"]
    return dict(zip(pool["Region"], pool["EquivalentSKU"], strict=True))


def _consumption_basis(t) -> dict:
    """What the capacity they could not get would have billed.

    A second, independent basis for the same failure -- units they could not
    consume x the published rate x the hours they went without. Closer to
    forgone revenue than the ARR apportionment, and it is what a cost estimator
    produces, so the two can be compared rather than one taken on faith.
    """
    if not bool(t.IsFlagged):
        return {}
    sku = _pool_sku().get(str(t.Region), ratecard.DEFAULT_SKU)
    est = ratecard.estimate(
        units_unavailable=float(getattr(t, "BlockedUnits", 0) or 0),
        days=float(getattr(t, "DaysUnavailable", 0) or 0),
        sku=sku,
    )
    return {**est.to_dict(), "workingOut": est.working_out}


def _ticket_rows(priced, mask):
    """Per-incident rows carrying the whole calculation, not just the total.

    A shared dashboard has to answer "which of these is mine, and how was my
    number worked out" -- so every row names its customer and shows the
    arithmetic rather than a figure the reader has to take on trust.
    """
    attrs = _ticket_attributes()
    rows = []
    for t in priced[mask].sort_values("RevenueExposureUSD", ascending=False).itertuples():
        blocked = float(t.BlockedUnits or 0)
        requested = float(t.RequestedCapacity or 0)
        share = float(t.CapacityShare or 0)
        days = float(t.DaysUnavailable or 0)
        arr = float(t.ARR_USD or 0)
        rows.append({
            "incidentId": str(t.IncidentId),
            "subscriptionId": str(t.SubscriptionId),
            "customerShort": str(t.SubscriptionId)[:8],
            "customerName": _customer_names().get(str(t.SubscriptionId), str(t.SubscriptionId)[:8]),
            "tier": str(t.SubscriptionTier),
            "region": str(t.Region),
            "datacentre": attrs.get(str(t.IncidentId), {}).get("datacentre", ""),
            "reason": attrs.get(str(t.IncidentId), {}).get("reason", ""),
            "outcome": str(t.Category).replace("_", " "),
            "outcomeLabel": OUTCOME_LABELS.get(str(t.Category), str(t.Category)),
            "ticketStatus": str(getattr(t, "TicketStatus", "") or ""),
            "had": float(t.CurrentLimitCapacity or 0),
            "askedFor": float(t.AdditionalLimitCapacity or 0),
            "endedWith": float(t.NewLimitCapacity or 0),
            "requested": requested,
            "blocked": blocked,
            "sharePct": round(share * 100, 1),
            "days": round(days, 1),
            "arr": arr,
            "exposure": float(t.RevenueExposureUSD or 0),
            "isFlagged": bool(t.IsFlagged),
            # The sum in words, so nobody has to trust the total.
            #
            # A zero-ARR customer needs a sentence, not a multiplication. Free
            # tier pays nothing, so every term after the first is irrelevant and
            # "$0 x 86% x 23.2 days / 365 = $0.00" reads as a broken calculator
            # rather than as the real limitation it is: this failure happened,
            # and the metric cannot see it.
            "workingOut": _working_out(t, arr, share, days),
            # The consumption basis alongside the ARR one. They answer
            # different questions and are deliberately both reported.
        })
    return rows


@app.get("/api/customers")
def customers():
    """Every affected customer, ranked. The view a shared dashboard needs."""
    m5 = get_module5()
    priced = m5.priced
    flagged = priced[priced["IsFlagged"]]

    out = []
    for sub, grp in flagged.groupby("SubscriptionId"):
        regions = sorted(grp["Region"].unique())
        out.append({
            "subscriptionId": str(sub),
            "regionBreakdown": [
                {"region": r, "requests": int(n)}
                for r, n in grp.groupby("Region").size().sort_values(ascending=False).items()
            ],
            "customerShort": str(sub)[:8],
            "customerName": _customer_names().get(str(sub), str(sub)[:8]),
            "tier": str(grp["SubscriptionTier"].iloc[0]),
            "arr": float(grp["ARR_USD"].max()),
            "exposure": float(grp["RevenueExposureUSD"].sum()),
            "failedRequests": int(len(grp)),
            "totalRequests": int((priced["SubscriptionId"] == sub).sum()),
            "regions": regions,
            "worstRegion": str(grp.loc[grp["RevenueExposureUSD"].idxmax(), "Region"]),
        })
    out.sort(key=lambda c: -c["exposure"])

    # A risk score per customer, from that customer's own rows, plus one
    # strategic line. Review asked for a recommendation at customer level that
    # is not just the region advice repeated: the useful thing to tell an
    # account team is where this customer already has room, not what to do to
    # a datacentre they have never heard of.
    onto = get_ontology()
    fact = onto["fact_capacity_request"]
    ctx = _region_context(onto)
    busiest = max(
        (int((g["NewLimitCapacity"] < g["RequestedCapacity"]).sum())
         for _, g in fact.groupby("SubscriptionId")), default=1) or 1

    for c in out:
        grp = fact[fact["SubscriptionId"].astype(str) == c["subscriptionId"]]
        worst = c["worstRegion"]
        c["risk"] = _score_group(grp, ctx.get(worst, {}), busiest)
        c["recommendation"] = _customer_recommendation(c, grp, ctx)

    return {"customers": out, "totalAffected": len(out)}


def _customer_recommendation(customer: dict, grp, ctx) -> dict:
    """One line for the account team, grounded in this customer's own spread.

    Deliberately not a per-ticket fix -- those live on the region and reason
    tabs. The question here is different: given where this customer is failing
    and where they are not, what should they be told to do differently.
    """
    failing = grp[grp["DenialReason"] != ""]
    if failing.empty:
        return {"headline": "No action needed.",
                "detail": "No refused requests on record for this customer."}

    worst = customer["worstRegion"]
    worst_ctx = ctx.get(worst, {})

    # Somewhere this customer already operates that is not under pressure.
    healthy = [
        r for r in customer["regions"]
        if r != worst
        and ctx.get(r, {}).get("utilisation", 100) < ctx.get(r, {}).get("threshold", 85)
    ]
    reasons = failing["DenialReason"].value_counts()
    top_reason = str(reasons.index[0])

    if healthy:
        return {
            "headline": f"Look at moving growth out of {worst}.",
            "detail": (
                f"{len(failing)} refused request(s) here, mostly '{top_reason}'. "
                f"{worst} is at {worst_ctx.get('utilisation', 0):.0f}% against a "
                f"{worst_ctx.get('threshold', 85):.0f}% safety line, while this customer "
                f"already runs in {', '.join(healthy)} with headroom. Worth asking "
                f"whether the next workload has to land in {worst}."
            ),
        }

    return {
        "headline": f"Capacity work needed in {worst} — no alternative region for this customer.",
        "detail": (
            f"{len(failing)} refused request(s), mostly '{top_reason}'. Every region "
            f"this customer runs in is at or past its safety line, so there is nowhere "
            f"to move the workload. This one needs hardware, not a conversation."
        ),
    }


@app.get("/api/customer/{subscription_id}")
def customer_detail(subscription_id: str):
    """One customer: every request they made, and how each was priced."""
    m5 = get_module5()
    priced = m5.priced
    mask = priced["SubscriptionId"].astype(str) == subscription_id
    if not mask.any():
        raise HTTPException(404, f"no requests for subscription {subscription_id!r}")

    rows = _ticket_rows(priced, mask)
    failed = [r for r in rows if r["isFlagged"]]
    return {
        "subscriptionId": subscription_id,
        "customerName": _customer_names().get(subscription_id, subscription_id[:8]),
        "customerShort": subscription_id[:8],
        "tier": rows[0]["tier"] if rows else "",
        "arr": rows[0]["arr"] if rows else 0,
        "requests": rows,
        "failedCount": len(failed),
        "totalCount": len(rows),
        "exposure": round(sum(r["exposure"] for r in rows), 2),
        "regions": sorted({r["region"] for r in rows}),
    }


@app.get("/api/region/{name}")
def region_detail(name: str):
    onto, m5 = get_ontology(), get_module5()
    if name not in set(onto["dim_region"]["Region"]):
        raise HTTPException(404, f"unknown region {name!r}")

    rows = _ticket_rows(m5.priced, m5.priced["Region"] == name)

    flag = module1.project_region(onto, name, crossing_for=_forecast_crossing)
    check = module6.check_expansion(onto, name)
    found = [a.to_dict() for a in
             module4.explain_anomalies(get_demand(), onto["fact_event"])
             if a.region == name]
    growth = module3.growth_ranking(get_demand())
    row = growth[growth["Region"] == name]

    # Everything goes through _clean, including the nested structures. Missing
    # it on `features` and `spikes` made this endpoint 500 for any region whose
    # anomaly rows carried a NaN -- a whole region's detail panel dead, from one
    # unserialisable float buried two levels down.
    fact = onto["fact_capacity_request"]
    here = fact[fact["Region"] == name]
    sites = onto["dim_datacentre"]
    sites = sites[sites["Region"] == name].set_index("DatacentreId")
    priced_here = {r["incidentId"]: r for r in rows}

    # Every facility in the region, not only the ones a ticket landed on.
    # Filtering to sites with a failure hid seven of southcentralus's ten
    # buildings -- all ten are past their own safety threshold, and the page
    # leads with threshold status, so a table answering "where did something
    # fail" beneath a heading about being in risk was answering a different
    # question from the one asked.
    by_site = {str(k): v for k, v in here.groupby("DatacentreId")}
    empty = here.iloc[0:0]

    datacentres = []
    for dc in sorted(sites.index.astype(str)):
        grp = by_site.get(dc, empty)
        # Failures only, everywhere. The reason breakdown, the recommendation
        # and the column all count the same rows.
        denied = _failed_rows(grp)
        denied = denied[denied["DenialReason"] != ""]
        tickets = [priced_here.get(str(i), {}) for i in grp["IncidentId"]]
        failed = [x for x in tickets if x.get("isFlagged")]
        # Oldest unresolved request at this site -- review asked how long each
        # has been sitting, because a 45-day-old denial is a different problem
        # from one raised yesterday.
        open_days = [x.get("days", 0) for x in failed
                     if "unfulfilled" in str(x.get("outcomeLabel", "")).lower()]
        meta = sites.loc[dc] if dc in sites.index else None
        dep = float(meta["DeployedUnits"]) if meta is not None else 0.0
        used = float(meta["UsedUnits"]) if meta is not None else 0.0
        thr = float(meta["ThresholdPct"]) if meta is not None else 0.0
        util = (used / dep * 100.0) if dep else 0.0
        datacentres.append({
            "datacentre": str(dc),
            "utilisationPct": round(util, 1),
            # The status the page is actually about. A site can be over its line
            # with nothing having failed there yet, which is the case worth
            # seeing before it becomes a denial.
            "overThreshold": bool(util > thr),
            "requests": int(len(grp)),
            "failed": len(failed),
            "customers": int(grp["SubscriptionId"].nunique()),
            "revenueLoss": round(sum(float(x.get("exposure", 0)) for x in tickets), 2),
            "oldestOpenDays": round(max(open_days), 1) if open_days else None,
            "topReason": (denied["DenialReason"].mode().iloc[0] if len(denied) else ""),
            "reasonCount": int(denied["DenialReason"].nunique()),
            # Review asked for the recommendation to sit beside the data centre
            # scope, not only on the site's own page: the region view is where
            # someone decides which building to open, and they cannot decide
            # that from a cause alone.
            "recommendations": [
                r.to_dict() for r in remediation.for_site(
                    onto, str(dc), denied, crossing_for=_forecast_crossing)
            ],
            "cores": _clean(meta["DeployedUnits"]) if meta is not None else None,
            "coresFree": _clean(meta["FreeUnits"]) if meta is not None else None,
            "thresholdPct": _clean(meta["ThresholdPct"]) if meta is not None else None,
            "headroom": _clean(meta["HeadroomToThreshold"]) if meta is not None else None,
        })
    # Worst first: over its line, then by failures, then by what it cost.
    datacentres.sort(key=lambda d: (not d["overThreshold"], -d["failed"],
                                    -d["revenueLoss"], d["datacentre"]))

    all_sites = onto["dim_datacentre"]
    all_sites = all_sites[all_sites["Region"] == name]

    return {
        "region": name,
        "datacentres": datacentres,
        "siteCount": int(len(all_sites)),
        "sitesWithActivity": sum(1 for d in datacentres if d["requests"] > 0),
        "sitesOverThreshold": sum(1 for d in datacentres if d["overThreshold"]),
        "cores": _clean(all_sites["DeployedUnits"].sum()),
        "coresFree": _clean(all_sites["FreeUnits"].sum()),
        "hardware": str(all_sites["SKUClass"].iloc[0]) if len(all_sites) else "",
        "failedCount": len([r for r in rows if r["isFlagged"]]),
        "revenueLoss": round(sum(float(r["exposure"]) for r in rows), 2),
        "reasons": _reason_breakdown(onto, name),
        # Same enrichment the region table carries, so the row and the page it
        # opens cannot show different figures for the same region.
        "threshold": {
            **_clean(flag.to_dict()),
            "cores_pending": round(_cores_pending_by_region().get(name, 0.0), 1),
            "customers_waiting": int(_waiting_customers_by_region().get(name, 0)),
        },
        "features": _clean(check),
        "spikes": _clean(found),
        "growth": _records(row)[0] if len(row) else None,
        "tickets": rows,
    }


# --------------------------------------------------------------------------
# computed -- these are why the app is not a static page
# --------------------------------------------------------------------------


@app.get("/api/threshold")
def threshold(pct: Annotated[float | None, Query(ge=50.0, le=99.0)] = None,
              trend_days: Annotated[int, Query(ge=7, le=120)] = 45):
    """Module 1 at each region's own safety threshold, or at a forced one.

    `pct` omitted -- the normal case -- gives every region its own line. Supplying
    it forces all regions to the same figure, which is what the what-if control
    on the Regions tab does and is a recalculation, not a filter.
    """
    flags = _records(module1.project_all(get_ontology(), threshold_pct=pct,
                                         trend_days=trend_days,
                                         crossing_for=_forecast_crossing))
    # Review asked the region table to answer "how many cores are still owed"
    # and "how many customers are waiting" without a drill-down, so both are
    # attached here rather than left for the reader to assemble across tabs.
    pending = _cores_pending_by_region()
    customers = _waiting_customers_by_region()
    for f in flags:
        region = str(f["region"])
        f["cores_pending"] = round(pending.get(region, 0.0), 1)
        f["customers_waiting"] = int(customers.get(region, 0))
        f["free_units"] = round(float(f["deployed_units"]) - float(f["used_units"]), 1)
        # "Breached by 12.2%" was rejected in review: a breach reads as a fault,
        # and the capacity consumed past the line is still the region's own, not
        # something extra. The agreed phrasing is that the threshold itself has
        # been used into.
        over = float(f["current_utilisation_pct"]) - float(f["threshold_pct"])
        f["threshold_used_pct"] = round(over, 1) if over > 0 else 0.0
        f["at_risk"] = bool(over > 0)
    return {
        "thresholdPct": pct,
        "thresholdIsPerRegion": pct is None,
        "trendDays": trend_days,
        "regions": flags,
        "actionable": sum(1 for f in flags
                          if f["status"] in ("breached", "overdue", "due_now")),
    }


@app.get("/api/convert")
def convert(region: str, to_sku: str, mode: str = "same_footprint"):
    """Re-run Module 2 for a chosen target. The calculator, live."""
    try:
        result = module2.migrate_region(get_ontology(), region, to_sku, mode=mode)
        result["conversion"] = {k: _clean(v) for k, v in result["conversion"].items()}
        return {k: (_clean(v) if not isinstance(v, dict) else v) for k, v in result.items()}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/swap-options")
def swap_options():
    """Which sites can be swapped, and to what.

    The calculator used to work at region level, which is not where the work
    happens -- you take a building offline, not a country. This lists every
    site with the hardware it runs, so the target list can exclude what it
    already has.
    """
    onto = get_ontology()
    dim = onto["dim_datacentre"]
    fact = onto["fact_capacity_request"]
    active = set(fact["DatacentreId"].astype(str))

    sites = []
    for r in dim.itertuples():
        sites.append({
            "datacentre": str(r.DatacentreId),
            "region": str(r.Region),
            "currentHardware": str(r.SKUClass),
            "units": _clean(r.DeployedUnits),
            "hasActivity": str(r.DatacentreId) in active,
        })
    sites.sort(key=lambda s: (s["region"], s["datacentre"]))
    return {
        "sites": sites,
        "hardware": sorted(onto["dim_sku"]["SKUClass"]),
        "regions": sorted(dim["Region"].unique()),
    }


@app.get("/api/swap")
def swap(datacentre: str, to_sku: str, mode: str = "same_footprint"):
    """Model swapping one site onto different hardware.

    Two questions, deliberately kept apart: what the site would look like
    afterwards, and whether the work can be scheduled at all given what
    customers are running right now. A conversion that leaves the region unable
    to serve its load during the window is not a plan.
    """
    onto = get_ontology()
    dim = onto["dim_datacentre"]
    row = dim[dim["DatacentreId"].astype(str) == datacentre]
    if row.empty:
        raise HTTPException(404, f"unknown datacentre {datacentre!r}")

    site = row.iloc[0]
    current = str(site["SKUClass"])
    region = str(site["Region"])

    # Swapping a site onto the hardware it already runs is not a migration.
    # Rejected rather than quietly returning a no-op result that looks like an
    # answer.
    if to_sku == current:
        raise HTTPException(
            400,
            f"{datacentre} already runs {current}. Choose different hardware.",
        )

    try:
        source = module2.calculator.sku_from_dim(onto["dim_sku"], current)
        target = module2.calculator.sku_from_dim(onto["dim_sku"], to_sku)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    units = float(site["DeployedUnits"] or 0)
    convert = (module2.convert_same_footprint if mode == "same_footprint"
               else module2.convert_like_for_like)
    result = convert(source, target, units)

    # Feasibility is a region-level question: taking this site offline draws on
    # headroom the whole region shares.
    plan = module2.plan_conversion(onto, region, to_sku, convert_datacentres=1)

    return {
        "datacentre": datacentre,
        "region": region,
        "currentHardware": current,
        "targetHardware": to_sku,
        "units": round(units, 1),
        "conversion": _clean(result.to_dict()),
        "feasibility": _clean(plan.to_dict()),
    }


@app.get("/api/region-recommendation/{region}")
def region_recommendation(region: str):
    """What to do about a region, as opposed to about one of its buildings.

    Review drew the line clearly: a region recommendation is about the safety
    threshold and where the region's own spare capacity is; a data centre
    recommendation is about swapping that facility's hardware. Mixing them
    produced advice nobody owned. Everything here is computed from the region's
    own numbers -- no sentence is written in advance.
    """
    onto = get_ontology()
    if region not in set(onto["dim_region"]["Region"]):
        raise HTTPException(404, f"unknown region {region!r}")

    flag = module1.project_region(onto, region, crossing_for=_forecast_crossing)
    dim = onto["dim_region"]
    row = dim[dim["Region"] == region].iloc[0]
    deployed = float(row["DeployedUnits"])
    used = float(_clean(flag.to_dict()).get("used_units") or 0.0)
    line = float(flag.threshold_pct)
    free = deployed - used
    pending = _cores_pending_by_region().get(region, 0.0)
    waiting = _waiting_customers_by_region().get(region, 0)

    #: Raising the safety line releases capacity already owned. It is the only
    #: lever that costs nothing, so it is always evaluated first.
    options = []
    for candidate in (line + 5, line + 10, 100.0):
        if candidate <= line or candidate > 100:
            continue
        releases = deployed * (candidate - line) / 100.0
        options.append({
            "thresholdPct": round(candidate, 1),
            "releasesCores": round(releases, 1),
            "headroomAfter": round(deployed * candidate / 100.0 - used, 1),
            "coversPending": bool(releases >= pending),
        })

    sites = onto["dim_datacentre"]
    sites = sites[sites["Region"] == region]
    covering = next((o for o in options if o["coversPending"]), None)

    if pending <= 0:
        headline = (f"No capacity is owed in {region}. Utilisation is "
                    f"{flag.current_utilisation_pct:.1f}% against a {line:.0f}% line.")
        action = "No region-level action. Monitor."
    elif covering:
        headline = (f"{region} owes {pending:.0f} cores to {waiting} customer(s).")
        action = (f"Raising the safety line from {line:.0f}% to "
                  f"{covering['thresholdPct']:.0f}% releases "
                  f"{covering['releasesCores']:.0f} cores, which covers the "
                  f"{pending:.0f} outstanding. This is a policy change and costs "
                  f"nothing — but it consumes the margin the line exists to protect.")
    else:
        best = options[-1] if options else None
        released = best["releasesCores"] if best else 0.0
        headline = (f"{region} owes {pending:.0f} cores to {waiting} customer(s), "
                    f"with {free:.0f} free.")
        action = (f"No safety line up to 100% releases enough — even at 100% only "
                  f"{released:.0f} cores come back against {pending:.0f} owed. "
                  f"This region needs capacity added, not rationed differently. "
                  f"Hardware changes are decided per facility, not per region.")

    return {
        "region": region,
        "headline": headline,
        "action": action,
        "utilisationPct": round(float(flag.current_utilisation_pct), 1),
        "thresholdPct": round(line, 1),
        "thresholdUsedPct": round(max(0.0, float(flag.current_utilisation_pct) - line), 1),
        "deployedUnits": round(deployed, 1),
        "usedUnits": round(used, 1),
        "freeUnits": round(free, 1),
        "coresPending": round(pending, 1),
        "customersWaiting": waiting,
        "options": options,
        "siteCount": int(len(sites)),
        "sitesWithActivity": int(len({
            str(d) for d in onto["fact_capacity_request"]
            .loc[onto["fact_capacity_request"]["Region"] == region, "DatacentreId"]})),
    }


def _demand_series(fact, events, key_col: str, key: str) -> dict:
    """Capacity asked for per month, and which months were driven by a deal.

    Review asked for demand forecasting as distinct from threshold forecasting:
    "by month you are getting 20 or 30 cores, but then sometime you'll get 100 --
    that is demand forecasting", with the spikes highlighted and attributed.

    Attribution here is direct rather than inferred. Events carry the incident
    they caused, so a spike month is one containing an event-linked ticket -- no
    pattern-matching, no window guessing. The separation is stark and it is the
    finding: event-linked requests average 338 cores against 34 for everything
    else, so a single signed deal is worth ten ordinary requests.
    """
    import pandas as pd

    rows = fact[fact[key_col].astype(str) == str(key)].copy()
    if rows.empty:
        return {"demand": [], "baselineCores": 0.0, "eventMonths": 0}

    when = pd.to_datetime(rows["DeniedDate"].fillna(rows["ApprovedDate"]), errors="coerce")
    rows["Month"] = when.dt.tz_localize(None).dt.to_period("M").astype(str)
    rows = rows[rows["Month"].notna() & (rows["Month"] != "NaT")]

    linked = {}
    if events is not None and len(events):
        for e in events.itertuples():
            inc = getattr(e, "LinkedIncidentId", None)
            if inc is not None and str(inc) != "nan":
                linked[str(inc)] = {
                    "type": str(getattr(e, "EventType", "") or ""),
                    "date": str(getattr(e, "EventDate", ""))[:10],
                }

    out = []
    for month, grp in rows.groupby("Month", sort=True):
        evs = []
        for r in grp.itertuples():
            hit = linked.get(str(r.IncidentId))
            if hit:
                evs.append({**hit, "cores": round(float(r.AdditionalLimitCapacity or 0), 1)})
        out.append({
            "month": str(month),
            "cores": round(float(grp["AdditionalLimitCapacity"].sum()), 1),
            "tickets": int(len(grp)),
            "eventDriven": bool(evs),
            "events": evs,
        })

    # Five months of tickets cannot carry a forecast, so the series is extended
    # backwards from the customer demand history -- the same generated data the
    # customer pages use, apportioned to this place by the share of its requests
    # each subscription actually raised here. Months the extract covers are left
    # exactly as they are; the fill only reaches months with no tickets at all,
    # and every filled month is flagged so the chart can draw it differently.
    out = _extend_demand_history(out, rows, key_col, key)

    # Baseline is what the place asks for when nothing has been signed. Taking
    # the median of every month instead would let the spikes raise the very line
    # they are supposed to stand out from.
    ordinary = [m["cores"] for m in out if not m["eventDriven"]]
    baseline = float(np.median(ordinary)) if ordinary else 0.0
    recorded = [m["month"] for m in out if m.get("isReal", True)]
    return {
        "demand": out,
        "baselineCores": round(baseline, 1),
        "eventMonths": sum(1 for m in out if m["eventDriven"]),
        "realMonths": len(recorded),
        # The month the ticket data actually starts. Naming it beats naming the
        # source system: "no ICM data this far back" tells a reader which system
        # to blame, not which months they can trust.
        "firstRecordedMonth": min(recorded) if recorded else None,
    }


#: How far above an ordinary month a filled month must sit before it is called
#: deal-driven. Real months are attributed from the event record instead, which
#: needs no threshold at all.
DEAL_MONTH_MULTIPLE = 2.5


def _extend_demand_history(observed: list, rows, key_col: str, key: str) -> list:
    """Fill the months before the extract starts, from the customer history.

    Regions and facilities have five months of tickets at most. Rather than
    invent a separate regional series, this reuses the per-customer history that
    already exists and weights each subscription by the share of its requests
    raised at this place -- so a customer who sent a tenth of their demand here
    contributes a tenth of theirs to the fill.
    """
    try:
        demand = get_ontology()["fact_customer_demand_monthly"]
    except KeyError:
        return observed
    if demand is None or not len(demand):
        return observed

    fact = get_ontology()["fact_capacity_request"]
    mine = fact[fact[key_col].astype(str) == str(key)]
    if mine.empty:
        return observed

    #: What share of each subscription's total requested capacity landed here.
    total_by_sub = fact.groupby("SubscriptionId")["AdditionalLimitCapacity"].sum()
    here_by_sub = mine.groupby("SubscriptionId")["AdditionalLimitCapacity"].sum()
    share = {str(s): float(here_by_sub[s]) / float(total_by_sub[s])
             for s in here_by_sub.index if float(total_by_sub.get(s, 0) or 0) > 0}
    if not share:
        return observed

    have = {m["month"] for m in observed}
    # Only extend backwards, never fill gaps inside the recorded window. A month
    # between two recorded months with no tickets is not missing data -- it is a
    # month in which nothing was asked for, and replacing that with an estimate
    # invents demand that provably did not exist. southcentralus had exactly this:
    # a generated December sitting between a recorded November and January.
    first_recorded = min(have) if have else None
    rel = demand[demand["SubscriptionId"].astype(str).isin(share)]
    filled = []
    for month, grp in rel.groupby("Month"):
        if str(month) in have:
            continue
        if first_recorded is not None and str(month) >= first_recorded:
            continue
        cores = sum(float(r.CoresRequested) * share.get(str(r.SubscriptionId), 0.0)
                    for r in grp.itertuples())
        filled.append({
            "month": str(month), "cores": round(cores, 1), "tickets": 0,
            "eventDriven": False, "events": [], "isReal": False,
        })

    for m in observed:
        m["isReal"] = True
    if not filled:
        return observed

    # Calibrate the fill to the level the extract actually shows. Uncalibrated,
    # the generated months came out at 235-1413 cores against 16-322 real ones,
    # so the chart showed demand collapsing at precisely the point the real data
    # began -- an artefact of the fill, read as a finding. Ordinary months are
    # matched to ordinary months so the deal spikes are not what sets the scale.
    def _ordinary(seq):
        vals = [m["cores"] for m in seq if not m["eventDriven"] and m["cores"] > 0]
        return float(np.median(vals)) if vals else 0.0

    def _plain(seq):
        vals = [m["cores"] for m in seq if m["cores"] > 0]
        return float(np.median(vals)) if vals else 0.0

    real_level = _ordinary(observed) or _plain(observed)
    fill_level = _plain(filled)
    if real_level > 0 and fill_level > 0:
        scale = real_level / fill_level
        for m in filled:
            m["cores"] = round(m["cores"] * scale, 1)

    # Requests behind those cores. Leaving this at zero produced a chart showing
    # capacity requested with nothing having requested it -- the first question
    # anyone asks of it, and one with no answer.
    #
    # Derived from the recorded months rather than from the generated per-customer
    # counts. Summing those gave the generated half seven to thirteen requests a
    # month against one to four in the recorded half, so request volume appeared
    # to collapse exactly where the real data started -- the same artefact the
    # cores calibration exists to prevent. Carrying the recorded cores-per-request
    # across means the two halves of the chart describe the same kind of place.
    per_request = [m["cores"] / m["tickets"] for m in observed if m["tickets"]]
    typical = float(np.median(per_request)) if per_request else 0.0
    if typical > 0:
        for m in filled:
            m["tickets"] = max(1, round(m["cores"] / typical)) if m["cores"] > 0 else 0

    # A filled month counts as deal-driven on the same test a reader applies by
    # eye: it is several times an ordinary month. Flagging it from whether any
    # contributing customer had a deal marked nine of thirteen months, which
    # makes the marking meaningless.
    if real_level > 0:
        for m in filled:
            if m["cores"] >= real_level * DEAL_MONTH_MULTIPLE:
                m["eventDriven"] = True
                m["events"] = [{"type": "Deal-sized month", "date": m["month"],
                                "cores": m["cores"]}]

    return sorted(filled + observed, key=lambda m: m["month"])


def _project_demand(months: list, periods: int = 3) -> dict:
    """Where a monthly demand series is heading.

    Deal months are left in. A signed deal is part of what this place asks for,
    and stripping them would forecast a customer that never signs anything --
    the opposite of the anomaly handling on the utilisation series, where a
    one-off spike genuinely is not the trend.
    """
    y = np.asarray([m["cores"] for m in months], dtype=float)
    if len(y) < 6:
        return {"projection": [], "model": "none",
                "note": f"Only {len(y)} month(s) of history — too few to fit a forecast."}
    try:
        folds = 3 if len(y) >= 12 else 2
        scores = forecast.backtest(y, folds=folds, horizon=1)
        best = next((s for s in scores if s.complete), None)
        naive_rmse = next((s.rmse for s in scores if s.name == "naive"), None)
        beats = bool(best and naive_rmse is not None and best.rmse < naive_rmse)
        model = best.name if beats else "naive"
        nxt = np.clip(np.asarray(forecast.CANDIDATES[model](y, periods), dtype=float), 0, None)
        last = pd.Period(months[-1]["month"], freq="M")
        return {
            "projection": [{"month": str(last + i + 1), "cores": round(float(v), 1)}
                           for i, v in enumerate(nxt)],
            "model": model,
            "note": ("" if beats else
                     "Nothing beat assuming next month looks like this one, so that is what is shown."),
        }
    except Exception:
        return {"projection": [], "model": "none",
                "note": "The history here was too irregular to fit a model to."}


def _threshold_series(region: str, threshold_pct: float) -> list:
    """Monthly utilisation against this region's own threshold, as +/- points.

    The second graph review drew: not a smooth projection but how far over or
    under the line each month actually ran. Negative is capacity still in hand.
    """
    import pandas as pd

    usage = get_ontology()["fact_usage_daily"]
    here = usage[usage["Region"] == region].copy()
    if here.empty:
        return []
    here["Month"] = pd.to_datetime(here["Date"]).dt.tz_localize(None).dt.to_period("M").astype(str)
    out = []
    for month, grp in here.groupby("Month", sort=True):
        util = float(grp["UtilisationPct"].mean())
        out.append({
            "month": str(month),
            "utilisationPct": round(util, 2),
            "thresholdPct": round(float(threshold_pct), 1),
            "deltaPct": round(util - float(threshold_pct), 2),
            "peakPct": round(float(grp["UtilisationPct"].max()), 2),
        })
    return out


@app.get("/api/demand/region/{name}")
def demand_region(name: str):
    onto = get_ontology()
    if name not in set(onto["dim_region"]["Region"]):
        raise HTTPException(404, f"unknown region {name!r}")
    line = _region_threshold(name)
    d = _demand_series(onto["fact_capacity_request"], onto["fact_event"], "Region", name)
    return {
        "scope": "region", "id": name, **d, **_project_demand(d["demand"]),
        "thresholdPct": line,
        "thresholdSeries": _threshold_series(name, line),
        "thresholdSeriesNote": "",
    }


@app.get("/api/demand/datacentre/{datacentre_id}")
def demand_datacentre(datacentre_id: str):
    onto = get_ontology()
    sites = onto["dim_datacentre"]
    row = sites[sites["DatacentreId"].astype(str) == str(datacentre_id)]
    if row.empty:
        raise HTTPException(404, f"unknown datacentre {datacentre_id!r}")
    d = _demand_series(onto["fact_capacity_request"], onto["fact_event"],
                       "DatacentreId", datacentre_id)
    return {
        "scope": "datacentre", "id": str(datacentre_id),
        "region": str(row.iloc[0]["Region"]),
        **d, **_project_demand(d["demand"]),
        "thresholdPct": round(float(row.iloc[0]["ThresholdPct"]), 1),
        # Utilisation is recorded per region per day; there is no per-site
        # series to plot. Splitting the region's curve across its ten sites
        # would draw ten identical lines and imply a measurement nobody made.
        "thresholdSeries": [],
        "thresholdSeriesNote": (
            "Utilisation over time is recorded per region, not per facility, so "
            "this chart is shown on the region page. Demand below is this site's own."
        ),
    }


@app.get("/api/demand/customer/{subscription_id}")
def demand_customer(subscription_id: str):
    """One customer's demand history and where it is heading.

    The history is mostly generated -- the extract holds two or three tickets per
    customer, which cannot carry a forecast -- so every month says whether it is
    real or synthesised and the response says so at the top. Months the extract
    does cover use the real figures.
    """
    onto = get_ontology()
    try:
        demand = onto["fact_customer_demand_monthly"]
    except KeyError:
        raise HTTPException(404, "no customer demand history was built") from None
    if demand is None or not len(demand):
        raise HTTPException(404, "no customer demand history was built")
    here = demand[demand["SubscriptionId"].astype(str) == str(subscription_id)]
    if here.empty:
        raise HTTPException(404, f"unknown subscription {subscription_id!r}")
    here = here.sort_values("Month")

    months = [{
        "month": str(r.Month),
        "cores": round(float(r.CoresRequested), 1),
        "tickets": int(r.RequestCount),
        "eventDriven": bool(r.IsDealMonth),
        "isReal": not bool(r.IsSynthetic),
        "events": ([{"type": "Deal-sized month", "date": str(r.Month), "cores":
                     round(float(r.CoresRequested), 1)}] if bool(r.IsDealMonth) else []),
    } for r in here.itertuples()]

    ordinary = [m["cores"] for m in months if not m["eventDriven"]]
    baseline = float(np.median(ordinary)) if ordinary else 0.0

    fc = _project_demand(months)
    projection, model, note = fc["projection"], fc["model"], fc["note"]

    sub = onto["dim_subscription"]
    row = sub[sub["SubscriptionId"].astype(str) == str(subscription_id)]
    return {
        "scope": "customer",
        "id": str(subscription_id),
        "customerName": (str(row.iloc[0].get("CustomerName", "")) if len(row) else ""),
        "demand": months,
        "projection": projection,
        "model": model,
        "note": note,
        "baselineCores": round(baseline, 1),
        "eventMonths": sum(1 for m in months if m["eventDriven"]),
        "realMonths": sum(1 for m in months if m["isReal"]),
        # Load-bearing: most of this series was generated, and a customer-level
        # forecast presented without that stated would be the most misleading
        # screen in the product.
        "historyIsMostlySynthetic": sum(1 for m in months if m["isReal"]) < len(months) / 2,
        "thresholdSeries": [],
        "thresholdSeriesNote": "",
    }


@app.get("/api/conversion-plan")
def conversion_plan(
    region: str,
    to_sku: str,
    # Annotated, not `= Query(default)`. The bare form hands FastAPI the default
    # but leaves the Python signature defaulting to the Query object itself, so
    # calling this from a test or another module passes a Query where a number
    # is expected. That is how `threshold()` came to raise on `-Query`.
    datacentres: Annotated[int | None, Query(ge=1, le=100)] = None,
    convert_datacentres: Annotated[int, Query(ge=1, le=100)] = 1,
    safety_margin_pct: Annotated[float, Query(ge=0.0, le=50.0)] = (
        module2.conversion.DEFAULT_SAFETY_MARGIN_PCT
    ),
):
    """Module 2b -- can this region actually take a datacentre offline to convert it.

    Separate from /api/convert because it answers a different question: not what
    the region looks like afterwards, but whether the work can be scheduled at
    all given what customers are running today.
    """
    try:
        plan = module2.plan_conversion(
            get_ontology(), region, to_sku,
            datacentres=datacentres,
            convert_datacentres=convert_datacentres,
            safety_margin_pct=safety_margin_pct,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {k: _clean(v) for k, v in plan.to_dict().items()}


# --------------------------------------------------------------------------
# actions and methodology
# --------------------------------------------------------------------------


@app.get("/api/actions")
def actions():
    """Module 5's recommendations, with the gate result that governs them."""
    finding = get_module5().finding
    ev = finding["classifier_evaluation"]
    decided = {d["region"]: d for d in state.load_decisions(STATE_DIR)}
    return {
        "asOf": finding["as_of"],
        "status": finding["status"],
        "decisions": decided,
        "gate": {
            "passed": bool(ev["passed"]),
            "detail": (f"Classifier reproduced {ev['n_correct']} of {ev['n_scored']} "
                       f"labelled tickets ({ev['accuracy']:.0%})."),
        },
        "recommendations": [
            {
                "region": r.get("region", ""),
                "rank": r.get("rank", 0),
                "failureMode": r.get("failure_mode", ""),
                "headline": r.get("headline", ""),
                "action": r.get("action", ""),
                "rationale": r.get("rationale", ""),
                "problem": r.get("problem", ""),
                "cause": r.get("cause", ""),
                "impact": r.get("impact", ""),
                "effect": r.get("effect", ""),
                "owner": r.get("owner", ""),
                "evidence": r.get("evidence", []),
            }
            for r in finding.get("recommendations", [])
        ],
    }


@app.post("/api/decision")
def record_decision(request: Request, payload: dict = Body(...)):
    """Approve or reject a recommendation.

    This is the human-review gate. It used to be a button on a Teams card; the
    button moved, the record did not -- `state.record_decision` writes the same
    append-only decisions.jsonl the CLI writes, so the audit trail is one file
    however the click arrived. Append-only matters: a decision is what somebody
    chose at a point in time, and rewriting it would erase the trail that makes
    the approve step worth having.

    The next pipeline run reads these back and suppresses a rejected region,
    which is why the reason is recorded rather than optional in spirit.
    """
    region = str(payload.get("region", "")).strip()
    decision = str(payload.get("decision", "")).strip()
    reason = str(payload.get("reason", ""))[:500]
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    if not region:
        raise HTTPException(400, "region is required")

    # Attach the decision to the finding the person actually looked at, not to
    # wall-clock time -- otherwise a decision made on Monday's numbers reads as
    # though it were made on Tuesday's.
    as_of = str(get_module5().finding["as_of"])
    by = auth.read_session(request.cookies.get(auth.COOKIE_NAME)) or "unknown"

    row = state.record_decision(STATE_DIR, as_of, region, decision, by, reason)
    return {"recorded": True, "decision": {k: _clean(v) for k, v in row.items()}}


@app.get("/api/decisions")
def list_decisions():
    """The audit trail, newest last. Read by the Actions tab and by anyone who
    wants to know who approved what, when, and why."""
    return {"decisions": [{k: _clean(v) for k, v in d.items()}
                          for d in state.load_decisions(STATE_DIR)]}


@app.get("/api/methodology")
def methodology():
    """Every number a reviewer might argue with, as the current run used it."""
    onto, finding = get_ontology(), get_module5().finding
    cfg = finding["config"]
    ev = finding["classifier_evaluation"]
    return {
        "config": {k: _clean(v) for k, v in cfg.items() if k != "notes"},
        "arrIsPlaceholder": bool(cfg.get("arr_reference_is_placeholder", True)),
        "gate": {
            "passed": bool(ev["passed"]),
            "detail": (f"Reproduced {ev['n_correct']} of {ev['n_scored']} labelled "
                       f"tickets ({ev['accuracy']:.0%}) across "
                       f"{len(ev['per_category'])} categories."),
        },
        "riskWeights": [
            {"component": k, "weight": v,
             "label": riskindex.COMPONENT_LABELS.get(k, k)}
            for k, v in get_risk_weights().items()
        ],
        "riskWeightsNote": cfg.get("notes", {}).get("risk_weights", ""),
        "dataQuality": {k: (len(v) if isinstance(v, list) else _clean(v))
                        for k, v in finding["data_quality"].items()
                        if k != "summary_lines"},
        "provenance": _records(ontology.sources(onto.tables)),
    }


# --------------------------------------------------------------------------
# static and the page shell
# --------------------------------------------------------------------------

class _RevalidatingStatic(StaticFiles):
    """Static assets that must be revalidated on every load.

    Without this the browser caches pages.js heuristically, and because the app
    routes on the client the file is never refetched while navigating -- so a
    reviewer clicks through and sees the build from whenever they first opened
    the tab. That produced two separate "I do not see your changes" reports
    where the server was serving the current file all along.

    ETag still does the work: revalidation is a 304 with no body when nothing
    has changed, so this costs a round trip rather than a download.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if STATIC.exists():
    app.mount("/static", _RevalidatingStatic(directory=STATIC), name="static")


def _shell():
    return FileResponse(STATIC / "app.html")


# Registered in a loop rather than six decorated stubs -- the list is the
# routing table, and it lives next to the nav that mirrors it.
for _tab in TABS:
    app.get(_tab)(_shell)

for _deep in DEEP:
    app.get(_deep + "/{name}")(lambda name: _shell())


# --------------------------------------------------------------------------
# the assistant
# --------------------------------------------------------------------------


def _conversion_readiness(onto):
    """Per region: is there room to take a datacentre offline at all.

    Target-independent -- headroom is what gates the work, and it is the same
    whatever the replacement hardware is. The target only changes what you get
    afterwards, which /api/conversion-plan answers on demand.
    """
    out = []
    for region in sorted(onto["dim_region"]["Region"]):
        current = str(onto["dim_region"].set_index("Region").loc[region, "SKUClass"])
        plan = module2.plan_conversion(onto, region, current)
        out.append({
            "region": region,
            "currentHardware": current,
            "datacentresAssumed": plan.datacentres,
            "unitsPerDatacentre": plan.units_per_datacentre,
            "unitsFreeToTakeOffline": plan.max_offline_units,
            "canTakeADatacentreOffline": plan.can_convert_a_whole_datacentre,
            "whyNot": plan.blocker or None,
        })
    return out


@lru_cache(maxsize=1)
def _snapshot_datacentres() -> list:
    """Every facility, not only the ones a ticket landed on.

    The assistant was given the 45 sites with activity, so asked how many of
    southcentralus's data centres were over their threshold it answered from six
    while the region page listed ten. A building over its safety line with
    nothing yet failed is exactly the one worth asking about, and it was
    invisible to the assistant by construction.
    """
    onto = get_ontology()
    scored = {r["datacentre"]: r for r in datacentres()["datacentres"]}
    out = []
    for row in onto["dim_datacentre"].itertuples():
        dc = str(row.DatacentreId)
        dep, used = float(row.DeployedUnits), float(row.UsedUnits)
        thr = float(row.ThresholdPct)
        util = (used / dep * 100.0) if dep else 0.0
        s = scored.get(dc, {})
        entry = {
            "datacentre": dc,
            "region": str(row.Region),
            "thresholdPct": round(thr, 1),
            "utilisationPct": round(util, 1),
            "thresholdStatus": "In risk" if util > thr else "Not in risk",
            "coresDeployed": round(dep, 1),
            "coresFree": round(dep - used, 1),
        }
        # 65 of the 110 sites have never had a request. Carrying nulls for
        # their failure counts and risk scores added 19KB to a snapshot that
        # travels with every question, and the model then had to reason about
        # fields that say nothing. They are listed -- being over a threshold
        # with nothing yet failed is the case worth seeing -- but only with the
        # facts that exist for them.
        if s.get("requests"):
            entry.update({
                "requests": int(s["requests"]),
                "failed": int(s.get("failed", 0)),
                "revenueLoss": float(s.get("revenueLoss", 0.0)),
                "topReason": s.get("topReason", ""),
                "riskScore": (s.get("risk") or {}).get("score"),
                "riskBand": (s.get("risk") or {}).get("band"),
                "leadTimeDays": s.get("leadTime"),
            })
        else:
            entry["note"] = "no requests recorded here"
        out.append(entry)
    return out


def get_snapshot():
    """Everything the assistant may know, rebuilt only when the app restarts."""
    onto, m5 = get_ontology(), get_module5()
    return assistant.build_snapshot(
        onto=onto,
        m5=m5,
        flags=_records(module1.project_all(onto, crossing_for=_forecast_crossing)),
        growth=_records(module3.growth_ranking(get_demand())),
        coverage=_records(module6.region_summary(onto)),
        spikes=module4.explain_anomalies(get_demand(), onto["fact_event"]),
        provenance=_records(ontology.sources(onto.tables)),
        customers=customers()["customers"],
        conversions=_conversion_readiness(onto),
        # Asked "which data centres in eastus2 are in risk", the assistant
        # correctly said it could not tell -- the snapshot held regions and
        # incidents but never the facilities, so a question the Data centres tab
        # answers in one glance was unanswerable here.
        datacentres=_snapshot_datacentres(),
        cores_pending=_cores_pending_by_region(),
        incidents=[
            {k: r[k] for k in ("incidentId", "customerShort", "tier", "region",
                               "outcome", "askedFor", "days", "exposure",
                               "workingOut")}
            for r in _ticket_rows(m5.priced, m5.priced["IsFlagged"])
        ],
    )


@app.post("/api/ask")
def ask(payload: dict = Body(...)):
    """Answer a question about the dashboard, grounded in its current state."""
    question = str(payload.get("question", ""))[:500]
    history = payload.get("history") or []
    result = assistant.ask(question, get_snapshot(), history=history,
                           llm_config=LLMConfig.from_env())
    return result


@app.get("/api/suggestions")
def suggestions():
    """Starter questions, generated from what the data actually contains."""
    snap = get_snapshot()
    regions = snap["regions"]
    worst = max(regions, key=lambda r: r["exposureUsd"])["region"] if regions else "westeurope"
    late = [r["region"] for r in regions if (r["daysUntilOrder"] or 0) < 0]
    out = [
        "Which region should we fix first, and why?",
        f"Why is {worst} the worst?",
        "How is exposure calculated?",
        "Which numbers here are real and which are generated?",
    ]
    if len(late) >= 2:
        out.insert(2, f"Why is {late[0]} more urgent than {late[-1]}?")
    return {"suggestions": out[:5]}
