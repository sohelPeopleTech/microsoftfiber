"""The propensity model — mostly tests that it cannot cheat or overclaim.

With 60 rows the danger is not a bad algorithm, it is a model that looks
brilliant because it was shown the answer, or one whose single lucky CV split
gets quoted as fact.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ontology
import propensity
from propensity.model import FEATURES, LEAKAGE, build_training_frame, evaluate
from tests.conftest import WORKBOOK


@pytest.fixture(scope="module")
def onto():
    return ontology.build(WORKBOOK, "data/synthetic")


@pytest.fixture(scope="module")
def frame(onto):
    return build_training_frame(onto)


@pytest.fixture(scope="module")
def model(onto):
    return propensity.train(onto)


# --- leakage --------------------------------------------------------------


def test_no_feature_is_an_outcome():
    """The whole model is worthless if it can see what happened."""
    assert not (set(FEATURES) & LEAKAGE)


def test_training_frame_carries_no_outcome_columns(frame):
    leaked = set(frame.columns) & LEAKAGE
    assert not leaked, f"outcome columns present: {leaked}"


def test_utilisation_never_comes_from_the_future(onto):
    """Region utilisation must be read as-of the request, not after it."""
    from propensity.model import _utilisation_at

    usage = onto["fact_usage_daily"]
    region = usage["Region"].iloc[0]
    days = sorted(usage[usage["Region"] == region]["Date"])
    mid = pd.Timestamp(days[len(days) // 2])

    got = _utilisation_at(usage, region, mid)
    on_or_before = usage[(usage["Region"] == region)
                         & (pd.to_datetime(usage["Date"]) <= mid)]
    expected = float(on_or_before.sort_values("Date").iloc[-1]["UtilisationPct"])
    assert got == expected


def test_a_leaked_feature_would_be_obvious(frame):
    """Sanity check on the test itself: had we leaked, AUC would be ~1.0."""
    cheat = frame.copy()
    cheat["cheating_feature"] = cheat["failed"] * 100.0
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict, StratifiedKFold
    from sklearn.metrics import roc_auc_score

    y = cheat["failed"].to_numpy()
    p = cross_val_predict(LogisticRegression(max_iter=1000), cheat[["cheating_feature"]], y,
                          cv=StratifiedKFold(5, shuffle=True, random_state=1),
                          method="predict_proba")[:, 1]
    assert roc_auc_score(y, p) > 0.95, "a leak should be detectable"


# --- the frame ------------------------------------------------------------


def test_every_request_is_scored(onto, frame):
    assert len(frame) == len(onto["fact_capacity_request"])
    assert frame[FEATURES].notna().all().all()


def test_label_matches_module5(onto, frame):
    """One definition of failure across the platform, not two."""
    from module5.classifier import classify
    from module5.config import Config

    classified = classify(onto["fact_capacity_request"], Config())
    assert frame["failed"].sum() == int(classified["IsFlagged"].sum())


def test_request_ratio_is_bounded(frame):
    assert frame["request_ratio"].between(0, 1).all()


# --- honesty --------------------------------------------------------------


def test_performance_is_reported_with_its_spread(model):
    m = model.metrics
    assert m["trained"]
    assert "cv_auc_sd" in m and "cv_auc_range" in m
    assert m["repeats"] >= 5
    lo, hi = m["cv_auc_range"]
    assert lo <= m["cv_auc"] <= hi


def test_the_model_declares_itself_useless_on_this_sample(model):
    """It does not predict. The product must say so rather than imply skill."""
    m = model.metrics
    assert m["better_than_chance"] is False
    assert "no demonstrated predictive power" in m["verdict"]
    assert "do not act" in m["verdict"]


def test_summary_states_sample_size_and_verdict(model):
    s = model.summary()
    assert "60 requests" in s and "30 failures" in s
    assert "chance" in s and "Verdict" in s


def test_a_model_with_real_signal_is_recognised_as_useful():
    """The verdict must not be hard-coded pessimism."""
    rng = np.random.default_rng(3)
    n = 200
    signal = rng.normal(size=n)
    y = (signal + rng.normal(scale=0.4, size=n) > 0).astype(int)
    frame = pd.DataFrame({f: rng.normal(size=n) for f in FEATURES})
    frame["additional_units"] = signal
    frame["failed"] = y
    m = evaluate(frame)
    assert m["better_than_chance"] is True
    assert m["verdict"] == "usable"
    assert m["cv_auc"] > 0.75


def test_too_little_data_refuses_to_train():
    frame = pd.DataFrame({f: [1.0, 2.0] for f in FEATURES})
    frame["failed"] = [0, 1]
    assert evaluate(frame)["trained"] is False


def test_one_class_refuses_to_train(frame):
    only_fails = frame[frame["failed"] == 1]
    assert evaluate(only_fails)["trained"] is False


# --- explanation ----------------------------------------------------------


def test_every_prediction_can_be_explained(model, frame):
    contributions = model.explain(frame.iloc[0])
    assert len(contributions) == len(FEATURES)
    assert abs(contributions[0]["contribution"]) >= abs(contributions[-1]["contribution"])
    assert all(c["direction"] in ("raises risk", "lowers risk") for c in contributions)


def test_probabilities_are_probabilities(model, frame):
    p = model.predict(frame)
    assert ((p >= 0) & (p <= 1)).all()


# --- against a simulation with known ground truth -------------------------


@pytest.fixture(scope="module")
def simulated(onto):
    from synthdata import simulate
    sim = simulate.simulate_requests(onto, n=600)
    return sim, simulate.as_fact_table(sim, onto)


def test_simulation_matches_the_real_marginals(simulated):
    """A simulation that denies 25% when reality denies 75% teaches nothing."""
    from synthdata import simulate

    sim, _ = simulated
    s = simulate.summarise(sim)
    assert 0.68 <= s["denial_rate"] <= 0.82
    assert 0.15 <= s["never_fulfilled"] / s["rows"] <= 0.27


def test_simulation_is_deterministic(onto):
    from synthdata import simulate

    a = simulate.simulate_requests(onto, n=100)
    b = simulate.simulate_requests(onto, n=100)
    pd.testing.assert_frame_equal(a, b)


def test_simulated_rows_are_tagged(simulated):
    sim, _ = simulated
    assert sim["IsSimulated"].all()
    assert sim["Provenance"].str.startswith("SIMULATED").all()


def test_the_model_finds_signal_when_signal_exists(onto, simulated):
    """The point of the simulation: prove the machinery works, given signal."""
    _, fact = simulated
    frame = build_training_frame(onto, fact=fact)
    m = evaluate(frame)
    assert m["better_than_chance"] is True
    assert m["verdict"] == "usable"
    assert m["cv_auc"] > 0.6


def test_the_estimate_tracks_the_true_probability(onto, simulated):
    """Only a simulation can check this -- real data has no answer key."""
    from propensity.model import _make_pipeline

    sim, fact = simulated
    frame = build_training_frame(onto, fact=fact)
    pipe = _make_pipeline()
    pipe.fit(frame[FEATURES], frame["failed"])
    predicted = pipe.predict_proba(frame[FEATURES])[:, 1]
    truth = (fact.set_index(fact["IncidentId"].astype(str))
             .loc[frame["IncidentId"].astype(str), "TrueFailureProb"].to_numpy())
    assert np.corrcoef(predicted, truth)[0, 1] > 0.3


def test_the_ground_truth_never_becomes_a_feature(onto, simulated):
    """TrueFailureProb is the answer key. It must not reach the model."""
    _, fact = simulated
    frame = build_training_frame(onto, fact=fact)
    assert "TrueFailureProb" not in frame.columns
    assert "SimPressure" not in frame.columns
    assert not (set(frame.columns) & LEAKAGE)


def test_simulated_and_real_go_through_the_same_feature_code(onto, simulated):
    _, fact = simulated
    a = build_training_frame(onto)
    b = build_training_frame(onto, fact=fact)
    assert list(a.columns) == list(b.columns)
