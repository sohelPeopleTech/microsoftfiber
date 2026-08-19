"""A simulated capacity-request history with a *known* generating process.

The supplied extract has no relationship between a request's attributes and its
outcome -- the dates were drawn independently of size, tier and region -- so a
propensity model on it correctly reports no signal. That is the honest finding,
but it leaves the model unexercised and undemonstrable.

This generates a larger history where denial and fulfilment follow plausible
rules, so the machinery can be shown working end to end.

    WHAT THIS PROVES      the pipeline, the features, the leakage guards and the
                          validation are correct: given signal, they find it.
    WHAT IT DOES NOT      that real capacity denials are predictable. The model
                          is recovering rules written a few lines below.

Every row is tagged `IsSimulated`, and the true probability used to draw each
outcome is kept in `TrueFailureProb` -- so a model's estimate can be compared
against the answer, which is a luxury real data never gives you.

The rules, and why each is plausible:

  request size vs regional headroom   a big ask into a full region is the
                                      classic denial
  region utilisation                  a region under pressure denies more
  subscription tier                   a larger customer gets more benefit of
                                      the doubt
  hardware lead time                  slow hardware means a denial takes longer
                                      to clear, and is likelier to lapse
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SIM_PROVENANCE = "SIMULATED - outcomes drawn from documented rules, not observed"

TIER_RANK = {"Free": 0, "Standard": 1, "Premium": 2, "Enterprise": 3}

# Capacity sizes seen in the real extract -- staying on the same ladder keeps
# the simulation comparable to the data it stands in for.
SIZE_LADDER = [2, 3, 4, 8, 10, 16, 20, 32, 44, 48, 64, 67, 83, 128, 150, 256, 512]

#: How much randomness sits on top of the rules. Zero would make the outcome a
#: deterministic function of the features and any model would score ~1.0, which
#: is not a demonstration of anything. This is tuned for a realistic ceiling.
DEFAULT_NOISE = 0.55


def _logistic(x):
    return 1.0 / (1.0 + np.exp(-x))


def simulate_requests(
    onto,
    n: int = 600,
    seed: int = 20260813,
    noise: float = DEFAULT_NOISE,
    start: str = "2025-01-01",
    end: str = "2026-01-28",
) -> pd.DataFrame:
    """Generate `n` capacity requests with outcomes that follow the rules above."""
    rng = np.random.default_rng(seed)

    regions = onto["dim_region"].set_index("Region")
    subs = onto["dim_subscription"]
    usage = onto["fact_usage_daily"]
    util_by_region = usage.groupby("Region")["UtilisationPct"].mean().to_dict()

    region_names = list(regions.index)
    sub_rows = subs.to_dict("records")
    days = pd.date_range(start, end, freq="D")

    rows = []
    for i in range(n):
        region = region_names[int(rng.integers(0, len(region_names)))]
        sub = sub_rows[int(rng.integers(0, len(sub_rows)))]
        raised = days[int(rng.integers(0, len(days)))]

        current = float(SIZE_LADDER[int(rng.integers(0, len(SIZE_LADDER)))])
        additional = float(SIZE_LADDER[int(rng.integers(0, len(SIZE_LADDER)))])
        requested = current + additional

        deployed = float(regions.loc[region, "DeployedUnits"])
        util = float(util_by_region.get(region, 70.0))
        lead = float(regions.loc[region, "LeadTimeDays"])
        tier = str(sub["SubscriptionTier"])
        tier_rank = TIER_RANK.get(tier, 0)

        # --- the generating rules ----------------------------------------
        # Intercepts calibrated so the simulated marginals match the real
        # extract: ~75% of requests denied, ~20% never fulfilled.
        # Share of the region's free headroom this single request would eat.
        headroom = max(deployed * (100 - util) / 100.0, 1.0)
        pressure = min(additional / headroom, 3.0)

        denial_score = (
            1.6
            + 2.1 * pressure                     # big ask into a full region
            + 0.045 * (util - 70)                # region already under pressure
            - 0.42 * tier_rank                   # bigger customer, more latitude
            + 0.012 * lead                       # scarce hardware is guarded
            + rng.normal(0, noise)
        )
        p_denied = _logistic(denial_score)
        denied = rng.uniform() < p_denied

        approved_at = None
        if not denied:
            # Approved outright, same day.
            denied_at = None
            approved_at = raised
            p_fail = 0.0
        else:
            denied_at = raised
            # Given a denial, does it clear quickly, clear slowly, or lapse?
            lapse_score = (
                -1.1
                + 1.5 * pressure
                - 0.55 * tier_rank
                + 0.02 * lead
                + rng.normal(0, noise)
            )
            p_lapse = _logistic(lapse_score)
            if rng.uniform() < p_lapse:
                approved_at = None            # never fulfilled
            else:
                # Clearing time grows with lead time and pressure.
                base = 4 + lead * 0.5 + pressure * 22
                hours = max(1.0, rng.gamma(shape=2.0, scale=base / 2.0)) * 24
                approved_at = raised + pd.Timedelta(hours=float(hours))
            # Probability this request ends up a Module 5 failure: it was
            # denied, so it fails unless it cleared inside the tier's SLA.
            p_fail = p_denied

        granted = requested if approved_at is not None else current

        rows.append({
            "IncidentId": str(900_000_000 + i),
            "SubscriptionId": sub["SubscriptionId"],
            "TenantId": sub.get("TenantId", ""),
            "Region": region,
            "CurrentLimitCapacity": int(current),
            "AdditionalLimitCapacity": int(additional),
            "NewLimitCapacity": int(granted),
            "DeniedDate": denied_at.strftime("%Y-%m-%dT%H:%M:%S.000Z") if denied_at is not None else "",
            "ApprovedDate": approved_at.strftime("%Y-%m-%dT%H:%M:%S.000Z") if approved_at is not None else "",
            # Kept for evaluation only -- never a model feature.
            "TrueFailureProb": round(float(p_fail), 4),
            "SimPressure": round(float(pressure), 4),
            "IsSimulated": True,
            "Provenance": SIM_PROVENANCE,
        })

    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> dict:
    """Marginals worth checking before trusting a simulation."""
    denied = df["DeniedDate"].astype(str).str.len() > 0
    approved = df["ApprovedDate"].astype(str).str.len() > 0
    return {
        "rows": len(df),
        "denied": int(denied.sum()),
        "denial_rate": round(float(denied.mean()), 3),
        "never_fulfilled": int((denied & ~approved).sum()),
        "approved_outright": int((~denied).sum()),
        "regions": df["Region"].nunique(),
        "subscriptions": df["SubscriptionId"].nunique(),
    }


def as_fact_table(sim: pd.DataFrame, onto) -> pd.DataFrame:
    """Shape a simulated history like `fact_capacity_request`.

    Typed dates and the subscription tier joined on, so the propensity
    features are computed by the same code path as the real extract -- a
    result on a differently-prepared frame would not transfer.
    """
    out = sim.copy()
    for col in ("DeniedDate", "ApprovedDate"):
        out[col] = pd.to_datetime(out[col].replace("", None), utc=True, errors="coerce")
    if "SubscriptionTier" not in out.columns:
        tiers = onto["dim_subscription"][["SubscriptionId", "SubscriptionTier"]]
        out = out.merge(tiers, on="SubscriptionId", how="left")
    return out
