"""Re-capture the real reference tables from their upstream sources.

    python -m src.reference.refresh [--regions eastus,westus2,...]

Not part of the pipeline. The CSVs are checked in precisely so a Fabric run
needs no outbound network, and this script exists for the day Microsoft ships a
workload into a new region and the availability table goes stale.

Geography comes from the Azure CLI, which must be installed and logged in.
Availability comes from the Learn page, which is rendered from a markdown table
in MicrosoftDocs/fabric-docs-pr -- the raw markdown is fetched rather than the
rendered HTML, because the markdown is the thing under version control and the
HTML wraps every cell in links that would have to be stripped back off.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import sys
import urllib.request

import certifi
import pandas as pd

from . import _DIR, AVAILABILITY_SOURCE, GEOGRAPHY_SOURCE

#: The public mirror. The Learn page's own metadata points at fabric-docs-pr,
#: which is Microsoft's private authoring repo and 404s for everyone else; the
#: published content lands in MicrosoftDocs/fabric-docs on `main`.
RAW_MARKDOWN = (
    "https://raw.githubusercontent.com/MicrosoftDocs/fabric-docs/main/"
    "docs/admin/region-availability.md"
)

#: Regions the ICM extract contains. Passed explicitly rather than read from the
#: extract so this script stays runnable without the workbook present.
DEFAULT_REGIONS = [
    "canadacentral", "canadaeast", "centralindia", "eastus", "eastus2",
    "northcentralus", "northeurope", "southcentralus", "uksouth",
    "westeurope", "westus2",
]

NORTH_AMERICA = {"US", "Canada", "Mexico"}


def _az_locations() -> dict:
    out = subprocess.run(
        ["az", "account", "list-locations", "-o", "json"],
        capture_output=True, text=True, check=True).stdout
    return {r["name"]: r for r in json.loads(out)
            if (r.get("metadata") or {}).get("regionType") == "Physical"}


def geography(regions: list[str]) -> pd.DataFrame:
    loc = _az_locations()
    rows = []
    for region in regions:
        m = loc.get(region)
        if m is None:
            print(f"  ! {region}: not returned by az, skipped", file=sys.stderr)
            continue
        meta = m["metadata"]
        paired = (meta.get("pairedRegion") or [{}])[0].get("name", "")
        rows.append({
            "Region": region,
            "DisplayName": m["displayName"],
            "City": meta.get("physicalLocation") or "",
            "GeographyGroup": meta.get("geographyGroup") or "",
            "Latitude": meta.get("latitude"),
            "Longitude": meta.get("longitude"),
            "PairedRegion": paired,
            "OnNorthAmericaMap": meta.get("geographyGroup") in NORTH_AMERICA,
            "IsSynthetic": False,
            "Provenance": f"REAL - {GEOGRAPHY_SOURCE}",
        })
    return pd.DataFrame(rows)


def _slug(name: str) -> str:
    """"US - South Central US" -> "southcentralus", to join on the ICM region."""
    tail = name.split(" - ")[-1]
    return re.sub(r"[^a-z0-9]", "", tail.lower())


#: The nine workloads Microsoft names under "Components of Microsoft Fabric".
#: https://learn.microsoft.com/en-us/fabric/fundamentals/microsoft-fabric-overview
FABRIC_WORKLOADS = [
    "Power BI",
    "Databases",
    "Data Factory",
    "Industry Solutions",
    "Real-Time Intelligence",
    "Data Engineering",
    "Data Science",
    "Data Warehouse",
    "Fabric IQ (preview)",
]

#: Docs path fragment -> the workload it belongs to. The availability table
#: links every feature it names, and the link target says which workload the
#: feature sits in -- so "which workload is affected" is read out of Microsoft's
#: own URLs rather than guessed from the feature name.
_AREA_BY_PATH = {
    "real-time-intelligence": "Real-Time Intelligence",
    "iq/": "Fabric IQ (preview)",
    "industry": "Industry Solutions",
    "data-factory": "Data Factory",
    "data-engineering": "Data Engineering",
    "data-science": "Data Science",
    "data-warehouse": "Data Warehouse",
    "database": "Databases",
    "power-bi": "Power BI",
    "powerbi": "Power BI",
    "apps/": "Fabric platform",
}


def _area_for(path: str) -> str:
    for fragment, workload in _AREA_BY_PATH.items():
        if fragment in path:
            return workload
    return "Fabric platform"


def availability(regions: list[str]) -> pd.DataFrame:
    # This Python build ships no CA store of its own; certifi is already a
    # declared dependency for exactly this reason.
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(RAW_MARKDOWN, timeout=30, context=ctx) as fh:
        md = fh.read().decode("utf-8")

    wanted = set(regions)
    rows = []
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("Geography", "**Geography**", "---"):
            continue
        _geo, name, pbi, fabric, gaps = cells[:5]
        slug = _slug(name)
        if slug not in wanted:
            continue
        # Cells carry markdown links; the link text is the feature name and the
        # link target says which workload it belongs to. Runs of whitespace are
        # collapsed because the upstream table contains the odd double space,
        # and a refresh should not rewrite the file with a purely cosmetic diff.
        linked = [(re.sub(r"\s+", " ", text).strip(), href)
                  for text, href in re.findall(r"\[([^\]]+)\]\(([^)]*)\)", gaps)]
        named = [text for text, _ in linked]
        if not named and gaps and not gaps.lower().startswith("power bi only"):
            named = [re.sub(r"\s+", " ", gaps).strip()]
        # Which of the nine workloads the missing features sit in. A region can
        # have "all Fabric workloads" and still be missing pieces of two of
        # them, which is the distinction the product needs to draw.
        affected = sorted({_area_for(href) for _, href in linked})
        full_workloads = "✅" in fabric
        available = ([w for w in FABRIC_WORKLOADS if w not in affected]
                     if full_workloads else ["Power BI"])
        rows.append({
            "Region": slug,
            "FabricRegionName": name,
            "PowerBI": "✅" in pbi,
            "AllFabricWorkloads": full_workloads,
            "PowerBIOnly": "✅" in pbi and not full_workloads,
            "WorkloadsAvailable": ";".join(available),
            "WorkloadsAvailableCount": len(available),
            "WorkloadsPartlyAffected": ";".join(affected),
            "UnavailableFeatureCount": len(named),
            "UnavailableFeatures": ";".join(named),
            "IsSynthetic": False,
            "Provenance": f"REAL - {AVAILABILITY_SOURCE}",
        })

    found = {r["Region"] for r in rows}
    for missing in sorted(wanted - found):
        print(f"  ! {missing}: no row in the Microsoft table", file=sys.stderr)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regions", default=",".join(DEFAULT_REGIONS))
    args = ap.parse_args()
    regions = [r.strip() for r in args.regions.split(",") if r.strip()]

    _DIR.mkdir(parents=True, exist_ok=True)
    for name, frame in (("region_geography", geography(regions)),
                        ("fabric_region_availability", availability(regions))):
        if frame.empty:
            print(f"{name}: nothing captured, leaving the existing file alone",
                  file=sys.stderr)
            continue
        frame.to_csv(_DIR / f"{name}.csv", index=False)
        print(f"{name}.csv: {len(frame)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
