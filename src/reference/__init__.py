"""Reference data that is real, not generated.

Everything under `data/synthetic/` is invented and says so. The two tables here
are the opposite: they come from Microsoft's own published sources and are
checked in so a Fabric run does not need outbound network access to draw a map
or answer "is this workload available there".

    region_geography            where each Azure region physically is
    fabric_region_availability  which Fabric workloads each region supports

Both carry `IsSynthetic = False` and a Provenance naming the source and the date
it was captured, because the whole point of the provenance column in this
project is that a reader can tell invented figures from recorded ones. Mixing a
real table into `data/synthetic/` would defeat that, so they live apart.

Refresh them with `python -m src.reference.refresh`, which re-reads both sources
and rewrites the CSVs. Neither changes often -- region coordinates essentially
never, availability when Microsoft ships a workload to a new region -- so the
checked-in copies are the normal path and the refresh is the exception.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

#: Captured 2026-08-25 from `az account list-locations`, which returns the
#: coordinates Microsoft publishes for each Azure region.
GEOGRAPHY_SOURCE = "az account list-locations (Azure Resource Manager), captured 2026-08-25"

#: Captured 2026-08-25. The page is generated from a markdown table in
#: MicrosoftDocs/fabric-docs-pr, so it is versioned and diffable upstream.
AVAILABILITY_SOURCE = (
    "https://learn.microsoft.com/en-us/fabric/admin/region-availability, captured 2026-08-25"
)

_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"


def _read(name: str) -> pd.DataFrame:
    path = _DIR / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. It is checked in; if it has been deleted, "
            "regenerate it with `python -m src.reference.refresh`."
        )
    return pd.read_csv(path)


def region_geography() -> pd.DataFrame:
    """One row per Azure region: coordinates, city, and which map it belongs on.

    `OnNorthAmericaMap` is the flag the landing map filters on. It is derived
    from Azure's own GeographyGroup rather than parsed out of the region name,
    so a region added later lands in the right place without a code change.
    """
    return _read("region_geography")


def fabric_region_availability() -> pd.DataFrame:
    """One row per Azure region: Power BI, all-Fabric-workloads, and the gaps.

    `UnavailableFeatures` is a semicolon-separated list, empty when the region
    has everything. `PowerBIOnly` marks the regions where Fabric workloads do
    not run at all -- a capacity decision that has nothing to do with how full
    the region is, and which nothing else in this project would otherwise catch.
    """
    return _read("fabric_region_availability")


def unavailable_features(region: str) -> list[str]:
    """The named gaps for one region, or [] when it has the full set."""
    df = fabric_region_availability()
    row = df[df["Region"] == region]
    if row.empty:
        return []
    raw = row.iloc[0]["UnavailableFeatures"]
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [f.strip() for f in raw.split(";") if f.strip()]
