"""Logistic regression on request-time features.

Logistic regression rather than something stronger, deliberately: with 30
positive events a gradient-boosted model would fit the noise and score
beautifully in training. A linear model with L2 regularisation gives
coefficients a human can argue with, which is worth more here than a fraction
of a point of AUC nobody can verify.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

#: Every feature must be knowable the moment the request is raised. Anything
#: that only exists once the request has failed is leakage -- see the test
#: suite, which asserts none of the outcome columns appear here.
FEATURES = [
    "additional_units",     # how much is being asked for
    "current_units",        # what the customer already holds
    "request_ratio",        # ask as a share of the resulting footprint
    "tier_rank",            # Free 0 ... Enterprise 3
    "region_utilisation",   # how full the region was that day
    "lead_time_days",       # how slow that region's hardware is to provision
]

TIER_RANK = {"Free": 0, "Standard": 1, "Premium": 2, "Enterprise": 3}

#: Columns that describe the outcome. Present in the source frame, never used.
LEAKAGE = {
    "IsFlagged", "Category", "DelayDays", "DelayHours", "DaysUnavailable",
    "RevenueExposureUSD", "ARRAffectedUSD", "Severity", "TicketStatus",
    "ApprovedDate", "ClosedDate", "UnmetUnits", "BlockedUnits", "CapacityShare",
}

MIN_ROWS = 20

#: CV repeats. One split on 60 rows is a lottery.
REPEATS = 10


@dataclass
class PropensityModel:
    pipeline: Pipeline
    features: list[str]
    coefficients: dict[str, float]
    metrics: dict = field(default_factory=dict)
    n_train: int = 0
    n_positive: int = 0

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        """Probability of failure for each row."""
        return self.pipeline.predict_proba(frame[self.features])[:, 1]

    def explain(self, row: pd.Series) -> list[dict]:
        """Per-feature contribution for one request, largest push first.

        Standardised inputs times coefficients, so the numbers are comparable
        across features with wildly different units.
        """
        scaler = self.pipeline.named_steps["scale"]
        z = (row[self.features].astype(float).to_numpy() - scaler.mean_) / scaler.scale_
        contributions = z * np.asarray(list(self.coefficients.values()))
        out = [
            {
                "feature": f,
                "value": float(row[f]),
                "contribution": float(c),
                "direction": "raises risk" if c > 0 else "lowers risk",
            }
            for f, c in zip(self.features, contributions, strict=True)
        ]
        return sorted(out, key=lambda d: -abs(d["contribution"]))

    def summary(self) -> str:
        m = self.metrics
        if not m.get("trained"):
            return f"Not trained: {m.get('reason', 'unknown')}"
        lo, hi = m["cv_auc_range"]
        return (
            f"Logistic regression on {self.n_train} requests "
            f"({self.n_positive} failures). Cross-validated AUC "
            f"{m['cv_auc']:.2f} ± {m['cv_auc_sd']:.2f} (range {lo:.2f}–{hi:.2f} "
            f"over {m['repeats']} splits) against 0.50 for chance. "
            f"Verdict: {m['verdict']}."
        )


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------


def _utilisation_at(usage: pd.DataFrame, region: str, when) -> float:
    """Region utilisation on the request date, or the nearest earlier day.

    Nearest *earlier* matters: taking the closest day in either direction would
    let tomorrow's usage inform today's prediction, which is leakage wearing a
    convenience's clothes.
    """
    if pd.isna(when):
        return float("nan")
    sub = usage[usage["Region"] == region]
    if sub.empty:
        return float("nan")
    day = pd.to_datetime(when).tz_localize(None).normalize()
    dates = pd.to_datetime(sub["Date"])
    earlier = sub[dates <= day]
    if earlier.empty:
        return float(sub.sort_values("Date").iloc[0]["UtilisationPct"])
    return float(earlier.sort_values("Date").iloc[-1]["UtilisationPct"])


def build_training_frame(entities, fact: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per request, with only what was knowable at request time.

    `fact` replaces the dimensional model's request table -- used to score a simulated
    history through exactly the same feature code as the real one, so a result
    on simulation says something about the real path.
    """
    fact = (entities["fact_capacity_request"] if fact is None else fact).copy()
    regions = entities["dim_region"].set_index("Region")
    usage = entities["fact_usage_daily"]

    fact["raised_at"] = fact["DeniedDate"].fillna(fact["ApprovedDate"])
    fact["additional_units"] = fact["AdditionalLimitCapacity"].astype(float)
    fact["current_units"] = fact["CurrentLimitCapacity"].astype(float)
    total = fact["current_units"] + fact["additional_units"]
    fact["request_ratio"] = np.where(total > 0, fact["additional_units"] / total, 0.0)
    fact["tier_rank"] = fact["SubscriptionTier"].map(TIER_RANK).fillna(0).astype(int)
    fact["lead_time_days"] = fact["Region"].map(regions["LeadTimeDays"]).astype(float)
    fact["region_utilisation"] = [
        _utilisation_at(usage, r, w)
        for r, w in zip(fact["Region"], fact["raised_at"], strict=True)
    ]

    # The label comes from Module 5's classifier rather than being redefined
    # here. Two definitions of "failed" would drift, and the propensity model
    # would quietly stop predicting the thing the rest of the platform reports.
    if "IsFlagged" not in fact.columns:
        from module5.classifier import classify
        from module5.config import Config

        fact = classify(fact, Config())
    fact["failed"] = fact["IsFlagged"].astype(int)

    keep = ["IncidentId", "Region", "SubscriptionId", "SubscriptionTier",
            "raised_at", *FEATURES, "failed"]
    out = fact[[c for c in keep if c in fact.columns]].dropna(subset=FEATURES)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# train / evaluate
# --------------------------------------------------------------------------


def _make_pipeline() -> Pipeline:
    return Pipeline([
        ("scale", StandardScaler()),
        # Strong L2. With six features and thirty events, an unpenalised fit
        # would chase noise and produce coefficients nobody should trust.
        ("clf", LogisticRegression(C=0.5, max_iter=2000, solver="lbfgs")),
    ])


def evaluate(frame: pd.DataFrame, folds: int = 5, seed: int = 20260813) -> dict:
    """Cross-validated performance, against the baseline it has to beat.

    Training-set accuracy on 60 rows is meaningless; this reports out-of-fold
    predictions only, and states the majority-class baseline alongside so a
    reader can see whether the model earned anything.
    """
    X, y = frame[FEATURES], frame["failed"].to_numpy()
    if len(frame) < MIN_ROWS or len(np.unique(y)) < 2:
        return {"trained": False,
                "reason": f"needs at least {MIN_ROWS} rows and both outcomes"}

    # Repeated, because on 60 rows a single split is a lottery: the same model
    # scored 0.49 and 0.61 on two different seeds. Reporting one of those as
    # "the" AUC would be precision the data cannot support.
    aucs, accs = [], []
    last_proba = None
    for r in range(REPEATS):
        cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed + r)
        proba = cross_val_predict(_make_pipeline(), X, y, cv=cv,
                                  method="predict_proba")[:, 1]
        aucs.append(float(roc_auc_score(y, proba)))
        accs.append(float(accuracy_score(y, (proba >= 0.5).astype(int))))
        last_proba = proba

    majority = float(max(y.mean(), 1 - y.mean()))
    auc_mean, auc_sd = float(np.mean(aucs)), float(np.std(aucs))
    # Chance is 0.5. If it sits within a standard deviation of that, the model
    # has not demonstrated anything and the product must not imply it has.
    useful = (auc_mean - auc_sd) > 0.5

    return {
        "trained": True,
        "n": int(len(y)),
        "n_positive": int(y.sum()),
        "cv_auc": auc_mean,
        "cv_auc_sd": auc_sd,
        "cv_auc_range": [float(min(aucs)), float(max(aucs))],
        "cv_accuracy": float(np.mean(accs)),
        "baseline_accuracy": majority,
        "lift_over_baseline": float(np.mean(accs) - majority),
        "folds": folds,
        "repeats": REPEATS,
        "better_than_chance": bool(useful),
        "verdict": (
            "usable" if useful else
            "no demonstrated predictive power on this sample -- do not act on these scores"
        ),
        "out_of_fold_probabilities": last_proba.tolist(),
    }


def train(entities, folds: int = 5) -> PropensityModel:
    """Fit on everything, but report performance from cross-validation."""
    frame = build_training_frame(entities)
    metrics = evaluate(frame, folds=folds)

    pipe = _make_pipeline()
    pipe.fit(frame[FEATURES], frame["failed"])
    coefs = dict(zip(FEATURES, pipe.named_steps["clf"].coef_[0], strict=True))

    return PropensityModel(
        pipeline=pipe,
        features=list(FEATURES),
        coefficients=coefs,
        metrics=metrics,
        n_train=len(frame),
        n_positive=int(frame["failed"].sum()),
    )


def score_requests(model: PropensityModel, frame: pd.DataFrame) -> pd.DataFrame:
    """Attach a probability to each request, highest risk first."""
    out = frame.copy()
    out["failure_probability"] = model.predict(out)
    out["risk_band"] = pd.cut(
        out["failure_probability"],
        bins=[-0.01, 0.35, 0.65, 1.01],
        labels=["low", "medium", "high"],
    ).astype(str)
    return out.sort_values("failure_probability", ascending=False).reset_index(drop=True)
