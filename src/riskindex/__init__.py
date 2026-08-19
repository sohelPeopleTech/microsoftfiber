"""A risk index for regions, datacentres and customers.

Review asked for a score at every level, computed independently: "each is
calculated by their own scores -- not top-down, do not show 100 on top and
20/20/20/20 underneath". So this does not split a total. Each entity is scored
from its own evidence, and the parts do not have to add up to the whole.

**This is an index, not a model.** It is a weighted blend of four things that
are already measured, chosen because a reader can argue with each one. That
matters here: the propensity model on this sample reports no predictive power,
and answering that by producing a second, opaque score would be worse than
useless. Every component is returned alongside the total, so a score can always
be taken apart.

    risk = 40% failure rate + 25% pressure + 20% unresolved + 15% lead time

The failure-rate term is shrunk toward the fleet average in proportion to how
little evidence an entity has, so a site that failed its only request does not
outrank one measured over months. See `PRIOR_STRENGTH`.

  failure rate   share of this entity's requests that were refused. The most
                 direct evidence that something here is not working.
  pressure       how close the region is to its safety line, past the line
                 counting as full weight. A full region denies the next ask.
  unresolved     requests still unfulfilled, relative to the busiest entity.
                 A backlog that never clears is worse than one that does.
  lead time      how long replacement hardware takes, normalised across the
                 fleet. Slow hardware means a problem here takes longer to fix,
                 so the same failure rate is more dangerous.

Bands match the propensity tab so one word means one thing across the product:
under 35 low, 35-65 medium, above 65 high.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Fallback only. The live values come from config.json via
#: `Config.risk_weights`, for the same reason the delay cut-off does: a number
#: a reviewer might argue with should be a setting, not a line of code.
#:
#: These are a STARTING POSITION, not a derived relationship. There is nothing
#: in a 60-ticket sample to fit them against, and the propensity model on the
#: same data reports no signal (cross-validated AUC 0.52 against 0.50 for
#: chance). Fitting them would produce a confident-looking figure with nothing
#: behind it, which is worse than a stated assumption.
WEIGHTS = {
    "failureRate": 0.40,
    "pressure": 0.25,
    "unresolved": 0.20,
    "leadTime": 0.15,
}


def resolve_weights(weights: dict | None = None) -> dict:
    """Validate a weight set, or fall back to the defaults.

    A silently-renormalised or partial weight set is how a config edit produces
    a score nobody can reconcile, so this refuses both rather than guessing.
    """
    if not weights:
        return dict(WEIGHTS)

    missing = set(WEIGHTS) - set(weights)
    unknown = set(weights) - set(WEIGHTS)
    if missing or unknown:
        raise ValueError(
            f"risk_weights must name exactly {sorted(WEIGHTS)}; "
            f"missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"risk_weights must sum to 1.0, got {total:.4f}")
    if any(float(v) < 0 for v in weights.values()):
        raise ValueError("risk_weights cannot be negative")
    return {k: float(weights[k]) for k in WEIGHTS}

#: Fleet-wide worst case, so "slow hardware" means slow relative to what exists
#: rather than to an arbitrary constant.
MAX_LEAD_TIME_DAYS = 45.0

#: How much evidence a site must accumulate before its own failure rate outweighs
#: the fleet average. Expressed as pseudo-observations: at 5, a site with 5
#: requests is judged half on its own record and half on the fleet's.
#:
#: WHY THIS EXISTS
#:     Sites here see one to three requests. One request that failed is a 100%
#:     failure rate, which is arithmetic, not evidence -- a single coin flip
#:     cannot distinguish a fair coin from a two-headed one, and the fleet fails
#:     about half its requests, so one failure is precisely what a typical site
#:     looks like. Left raw, the 12 highest-risk sites in the product were all
#:     single-ticket sites, and the ranking was sorting noise.
#:
#:     Shrinking toward the fleet rate lets the components that *do* rest on real
#:     evidence -- utilisation measured daily, lead times that are a property of
#:     the hardware -- decide the ranking, until a site has enough history to
#:     earn its own number.
#:
#: 5 is a judgement, not a fitted value, and is deliberately larger than any
#: site's request count in this extract: no site's own rate should dominate yet.
PRIOR_STRENGTH = 5.0


def band(score: float) -> str:
    """Same thresholds as the propensity bands, deliberately."""
    if score > 65:
        return "high"
    return "medium" if score > 35 else "low"


@dataclass
class Risk:
    score: float
    band: str
    components: dict = field(default_factory=dict)
    drivers: list = field(default_factory=list)
    #: How much evidence sits behind the failure-rate term, and what the
    #: shrinkage did to it. Returned so a score can be taken apart rather than
    #: taken on trust.
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "band": self.band,
            "components": self.components,
            "drivers": self.drivers,
            "evidence": self.evidence,
        }


def score(
    *,
    requests: int,
    denied: int,
    unresolved: int,
    utilisation_pct: float,
    threshold_pct: float,
    lead_time_days: float,
    busiest_unresolved: int = 1,
    weights: dict | None = None,
    prior_rate: float | None = None,
    prior_strength: float = PRIOR_STRENGTH,
) -> Risk:
    """Score one entity from its own numbers.

    `busiest_unresolved` normalises the backlog term against the worst entity in
    the same view, so a datacentre is compared with other datacentres rather
    than with a region.

    `prior_rate` is the fleet failure rate. Supplied, the entity's own rate is
    shrunk toward it in proportion to how little evidence the entity has; omitted,
    the raw rate is used and thin samples will dominate the ranking.
    """
    w = resolve_weights(weights)
    if requests <= 0:
        return Risk(0.0, "low", {k: 0.0 for k in w}, [],
                    evidence={"requests": 0, "shrunk": False})

    raw_rate = min(denied / requests, 1.0)
    if prior_rate is None or prior_strength <= 0:
        failure_rate = raw_rate
    else:
        # Empirical-Bayes shrinkage: the fleet rate acts as `prior_strength`
        # imaginary prior observations, which a site's own record outweighs only
        # once it has more requests than that.
        failure_rate = ((denied + prior_strength * float(prior_rate))
                        / (requests + prior_strength))
        failure_rate = min(max(failure_rate, 0.0), 1.0)

    # Past the safety line is full weight; below it, the fraction of the way
    # there. A region at 97% and one at 120% are both simply "full".
    pressure = min(utilisation_pct / threshold_pct, 1.0) if threshold_pct else 0.0

    unresolved_ratio = min(unresolved / busiest_unresolved, 1.0) if busiest_unresolved else 0.0
    lead = min(lead_time_days / MAX_LEAD_TIME_DAYS, 1.0)

    parts = {
        "failureRate": failure_rate,
        "pressure": pressure,
        "unresolved": unresolved_ratio,
        "leadTime": lead,
    }
    total = round(sum(parts[k] * w[k] for k in w) * 100, 1)

    # Ranked contributions, so the UI can say *why* something scored highly
    # rather than only that it did.
    drivers = sorted(
        ({"component": k, "raw": round(parts[k], 3), "weight": w[k],
          "contribution": round(parts[k] * w[k] * 100, 1)} for k in w),
        key=lambda d: -d["contribution"],
    )
    return Risk(
        total, band(total), {k: round(v, 3) for k, v in parts.items()}, drivers,
        evidence={
            "requests": int(requests),
            "denied": int(denied),
            "rawFailureRate": round(raw_rate, 3),
            "usedFailureRate": round(failure_rate, 3),
            "priorRate": None if prior_rate is None else round(float(prior_rate), 3),
            "priorStrength": prior_strength if prior_rate is not None else 0.0,
            "shrunk": bool(prior_rate is not None and abs(failure_rate - raw_rate) > 1e-9),
        },
    )


#: Human wording for each component, for tooltips and the methodology page.
COMPONENT_LABELS = {
    "failureRate": "Share of requests here that were refused",
    "pressure": "How close the region is to its safety line",
    "unresolved": "Requests still unfulfilled, against the busiest in this view",
    "leadTime": "How long replacement hardware takes to arrive",
}

__all__ = ["score", "band", "Risk", "WEIGHTS", "resolve_weights",
           "COMPONENT_LABELS", "MAX_LEAD_TIME_DAYS", "PRIOR_STRENGTH"]
