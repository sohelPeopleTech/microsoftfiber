"""Shared fixtures. `src` on the path so tests run without an install step."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKBOOK = ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx"

from module5 import aggregate, ingest, revenue  # noqa: E402
from module5.classifier import classify  # noqa: E402
from module5.config import Config  # noqa: E402


@pytest.fixture(scope="session")
def config() -> Config:
    return Config()


@pytest.fixture(scope="session")
def gold():
    gold, dq = ingest.load_gold(WORKBOOK)
    return gold, dq


@pytest.fixture(scope="session")
def classified(gold, config):
    return classify(gold[0], config)


@pytest.fixture(scope="session")
def priced(classified, config):
    return revenue.estimate_impact(classified, config)


@pytest.fixture(scope="session")
def regions(priced, config):
    return aggregate.by_region(priced, config)


@pytest.fixture(scope="session")
def expected_labels():
    return ingest.load_expected_classifications(WORKBOOK)


def make_ticket(**overrides) -> pd.DataFrame:
    """A single synthetic Gold row, for arithmetic tests with known answers."""
    row = {
        "IncidentId": "1",
        "SubscriptionId": "sub-a",
        "TenantId": "tenant-a",
        "Region": "eastus2",
        "CurrentLimitCapacity": 100.0,
        "AdditionalLimitCapacity": 100.0,
        "NewLimitCapacity": 200.0,
        "DeniedDate": pd.Timestamp("2025-01-01", tz="UTC"),
        "ApprovedDate": pd.Timestamp("2025-01-11", tz="UTC"),
        "SubscriptionTier": "Enterprise",
        "ARR_USD": 365_000.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])
