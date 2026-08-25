"""Regenerate every synthetic table from the real ICM extract.

    python scripts/build_synthetic.py [--out data/synthetic]

There was no entry point for this: the tables under `data/synthetic/` were
checked in and `generate_all` was only ever called from the tests, which meant
the committed CSVs and the generator that claims to produce them could drift
apart without anything noticing. This closes that -- and `--check` asserts they
have not drifted, which is the form the test uses.

Output is seeded, so a clean run reproduces the committed files byte for byte.
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.synthdata import generate  # noqa: E402
from src.synthdata.generate import table_path  # noqa: E402

WORKBOOK = ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx"


def load_tickets() -> pd.DataFrame:
    return pd.read_excel(WORKBOOK, sheet_name="ICM_Data")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "data" / "synthetic"))
    ap.add_argument("--check", action="store_true",
                    help="compare against what is on disk instead of overwriting")
    args = ap.parse_args()
    out = Path(args.out)

    tables = generate.generate_all(load_tickets())

    if args.check:
        stale = []
        for name, df in sorted(tables.items()):
            path = table_path(out, name, len(df))
            if not path.exists():
                stale.append(f"{name}: missing on disk")
                continue
            # Compare text, not parsed values. A column holding blanks -- an
            # unlinked deal event has no incident -- comes back from read_csv as
            # float, so an id written as 692794676 reads as 692794676.0 and a
            # value comparison reports drift that is not there.
            #
            # Large tables are gzipped; decompress rather than comparing the
            # container, since the rows are what is under test.
            current = (gzip.decompress(path.read_bytes()).decode()
                       if path.suffix == ".gz" else path.read_text())
            if current != df.to_csv(index=False):
                stale.append(f"{name}: differs from the generator")
        if stale:
            print("STALE:\n  " + "\n  ".join(stale), file=sys.stderr)
            return 1
        print(f"all {len(tables)} tables match the generator")
        return 0

    written = generate.write_all(tables, out)
    for path in sorted(written):
        print(f"{path.name:28} {len(pd.read_csv(path)):>7} rows")
    print(f"\n{len(written)} tables written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
