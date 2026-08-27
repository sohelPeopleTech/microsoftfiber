"""Tunable policy for Module 5.

Every number a reviewer might argue with lives here, not buried in the logic.
Load overrides from JSON with `Config.load()`; see config.json at the repo root.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # --- Classifier -------------------------------------------------------
    # The "defined cut-off for what counts as a meaningful delay" required by
    # the design doc. A denial cleared within this many hours is treated as
    # normal turnaround, not a service failure.
    #
    # Calibration note (see docs/CALIBRATION.md): in the labelled sample the
    # same-day bucket runs up to 29h and the late bucket starts at 145h, so any
    # value in [30, 145) reproduces the ground truth. 48h is chosen as the
    # round "two business days" boundary near the middle of that safe band.
    meaningful_delay_hours: float = 48.0

    # Per-tier override. A bigger customer is owed a faster answer, so the
    # allowance tightens as the tier rises. Enterprise is 48h because that is
    # the one figure the business named; the rest ladder out from it.
    #
    # These are not guesses: in the labelled sample every tier has a wide gap
    # between its fastest "late" case and its slowest "same-day" case
    # (Enterprise 29-480h, Premium 22-145h, Standard 28-173h, Free 7-151h), and
    # each value below sits inside its tier's band -- so the classifier still
    # reproduces the ground truth exactly. Swap them the moment a real SLA
    # table exists; nothing else has to change.
    tier_delay_hours: dict = field(
        default_factory=lambda: {
            "Enterprise": 48.0,
            "Premium": 72.0,
            "Standard": 96.0,
            "Free": 96.0,
        }
    )

    # region -> the team accountable for acting on a finding. Empty until an
    # org mapping exists; the card falls back to a generic owner and says so.
    region_owners: dict = field(default_factory=dict)
    default_owner: str = "Capacity Operations"

    # Severity bands for a delayed approval, in days.
    severity_medium_days: float = 7.0
    severity_high_days: float = 21.0

    # --- Revenue-impact estimator ----------------------------------------
    # ARR is annual, so exposure is pro-rated against this many days.
    annualisation_days: float = 365.0

    # A never-fulfilled ticket accrues exposure from its denial date to
    # `as_of`. Cap it so one very old open ticket cannot dominate the ranking.
    unfulfilled_cap_days: float = 365.0

    # Evaluation date. None => the latest date present in the ticket data,
    # which keeps runs over a fixed extract reproducible.
    as_of: str | None = None

    # --- Reporting --------------------------------------------------------
    top_n_regions: int = 3
    trend_period: str = "M"  # pandas offset alias for the cumulative trend view
    currency: str = "USD"

    # --- Provenance -------------------------------------------------------
    # Flipped to False once the real subscription tier/ARR reference lands.
    # Drives the "illustrative figure" disclaimer on every output.
    arr_reference_is_placeholder: bool = True

    # --- Risk index -------------------------------------------------------
    # How much each measured thing contributes to a region / datacentre /
    # customer risk score. These are a STARTING POSITION, not a derived
    # relationship: there is nothing in a 60-ticket sample to fit them against,
    # and the propensity model on the same data reports no signal at all
    # (cross-validated AUC 0.52 against 0.50 for chance). Deriving them would
    # produce a confident-looking number with nothing behind it.
    #
    # They live here for the same reason the delay cut-off does -- so a
    # reviewer who disagrees changes a setting rather than the code, and every
    # run records the weights it used. Must sum to 1.0.
    risk_weights: dict = field(
        default_factory=lambda: {
            "failureRate": 0.40,   # share of this entity's requests refused
            "pressure": 0.25,      # how close its region is to the safety line
            "unresolved": 0.20,    # backlog against the busiest in the view
            # Was leadTime, at the same weight: Fabric has nothing to
            # provision and nothing to wait for.
            "throttling": 0.15,    # share of its capacities refusing work
        }
    )

    #: Imposes one forecasting model on every region instead of using the model
    #: the backtest measured as most accurate there. None means evidence-based
    #: selection, which is the default and the defensible position; a name here
    #: is a stated instruction, and the accuracy it gives up is reported per
    #: region on screen rather than absorbed silently.
    forecast_force_model: str | None = None

    notes: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            return cls()
        data = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)

    def cutoff_for(self, tier: str | None) -> float:
        """Delay allowance for a subscription tier.

        An unknown or missing tier falls back to the global figure rather than
        being excused entirely -- a customer we cannot classify is still owed
        an answer.
        """
        if not tier:
            return self.meaningful_delay_hours
        return float(self.tier_delay_hours.get(tier, self.meaningful_delay_hours))

    def owner_for(self, region: str) -> str:
        owner = self.region_owners.get(region)
        return owner or f"{self.default_owner} — {region}"

    def sla_summary(self) -> str:
        """One line naming every cut-off in force, for the report footer."""
        if not self.tier_delay_hours:
            return f"{self.meaningful_delay_hours:.0f}h for all tiers"
        parts = ", ".join(
            f"{t} {h:.0f}h" for t, h in sorted(self.tier_delay_hours.items())
        )
        return f"{parts} (others {self.meaningful_delay_hours:.0f}h)"
