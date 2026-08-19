#!/usr/bin/env python3
"""Print what the platform currently knows. One command, no notebook.

    python3 scripts/show.py                 everything
    python3 scripts/show.py --region westeurope
    python3 scripts/show.py --module 1      just the lead-time engine
    python3 scripts/show.py --data          what is real and what is generated
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ontology  # noqa: E402
import module1, module2, module3, module4, module6  # noqa: E402
from module5 import pipeline  # noqa: E402
from module5.config import Config  # noqa: E402


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")
    print("─" * min(len(title), 76))


def show_data(onto) -> None:
    rule("DATA — what is real and what is generated")
    src = ontology.sources(onto.tables)
    for r in src.itertuples():
        mark = "generated" if r.FullySynthetic else "mixed/real"
        print(f"  {r.Entity:24} {r.Rows:>5} rows   {mark:10} {r.Grain}")
    print(f"\n  referential integrity: {'clean' if onto.is_valid else 'ISSUES'}")
    for i in onto.issues:
        print(f"    ! {i}")


def show_module1(onto) -> None:
    rule("MODULE 1 — lead-time-aware thresholds")
    df = module1.project_all(onto)
    cols = ["region", "current_utilisation_pct", "sku_class", "lead_time_days",
            "days_until_order", "status"]
    print(df[cols].to_string(index=False))
    due = module1.due_requests(onto)
    print(f"\n  {len(due)} request(s) due or overdue:")
    for r in due.itertuples():
        print(f"    • {r.region}: {r.reason}")


def show_module2(onto) -> None:
    rule("MODULE 2 — SKU migration calculator")
    for region, target in [("southcentralus", "GPU-class"), ("westeurope", "AMD-highmem")]:
        m = module2.migrate_region(onto, region, target)
        print(f"  {region} → {target}")
        print(f"    {m['summary']}")
        print(f"    covers current load: {m['conversion']['covers_requirement']}")


def show_module3(onto) -> None:
    rule("MODULE 3 — regional demand forecast")
    demand = module3.demand_by_period(onto, "M")
    g = module3.growth_ranking(demand)
    print(g[["Rank", "Region", "EarlyMean", "RecentMean", "AbsoluteChange",
             "GrowthPct", "Confident"]].to_string(index=False))
    f = module3.forecast_demand(demand)
    top = g.head(2)["Region"].tolist()
    print("\n  next three months (top two regions):")
    for r in f[f["Region"].isin(top)].itertuples():
        trend = "trend applied" if r.TrendApplied else "flat — too sparse for a trend"
        print(f"    {r.Region:16} {r.Period}  {r.Forecast:>8.1f}   ({trend})")


def show_module4(onto) -> None:
    rule("MODULE 4 — demand spikes and their causes")
    demand = module3.demand_by_period(onto, "M")
    for a in module4.explain_anomalies(demand, onto["fact_event"]):
        tag = {"strong": "EXPLAINED", "weak": "WEAK MATCH"}.get(a.match_strength, "UNEXPLAINED")
        print(f"  [{tag:11}] {a.region:15} {a.period}  {a.value:>6,.0f} units "
              f"(baseline {a.baseline:,.0f})")
        print(f"                {a.recommendation}")


def show_module5(onto) -> None:
    rule("MODULE 5 — capacity-denial revenue impact")
    result = pipeline.run(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                          config=Config.load(ROOT / "config.json"),
                          write_outputs=False)
    s = result.finding["summary"]
    print(f"  {s['tickets_flagged']} of {s['tickets_total']} requests failed "
          f"({s['delayed_count']} late, {s['unfulfilled_count']} unfulfilled)")
    print(f"  {s['customers_affected']} customers · ${s['arr_affected_usd']:,.0f} ARR affected "
          f"· ${s['revenue_exposure_usd']:,.0f} exposure")
    print(f"  classifier gate: {result.finding['classifier_evaluation']['n_correct']}/60\n")
    for r in result.finding["regions"][:5]:
        print(f"    #{r['Rank']} {r['Region']:16} ${r['RevenueExposureUSD']:>10,.0f}   "
              f"{r['TicketsFlagged']} failed")


def show_module6(onto) -> None:
    rule("MODULE 6 — feature availability")
    print(module6.region_summary(onto)[
        ["Region", "Live", "Preview", "Planned", "Unavailable", "CoveragePct"]
    ].to_string(index=False))


def show_region(onto, region: str) -> None:
    rule(f"{region.upper()} — every module on one region")

    f = module1.project_region(onto, region)
    print(f"  M1 threshold   {f.status.upper()}")
    print(f"                 {f.reason}")

    demand = module3.demand_by_period(onto, "M")
    g = module3.growth_ranking(demand)
    row = g[g["Region"] == region].iloc[0]
    print(f"  M3 growth      rank #{row.Rank} of {len(g)}, {row.AbsoluteChange:+,.0f} units "
          f"({'confident' if row.Confident else 'low confidence'})")

    spikes = [a for a in module4.explain_anomalies(demand, onto["fact_event"])
              if a.region == region]
    print(f"  M4 anomalies   {len(spikes)} spike(s)")
    for a in spikes:
        print(f"                 {a.recommendation}")

    check = module6.check_expansion(onto, region)
    print(f"  M6 features    {check['summary']}")

    result = pipeline.run(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                          config=Config.load(ROOT / "config.json"), write_outputs=False)
    match = [r for r in result.finding["regions"] if r["Region"] == region]
    if match:
        r = match[0]
        print(f"  M5 exposure    ${r['RevenueExposureUSD']:,.0f} across "
              f"{r['TicketsFlagged']} failed request(s), rank #{r['Rank']}")


def main() -> int:
    p = argparse.ArgumentParser(description="Show what the platform knows.")
    p.add_argument("--region", help="one region, every module")
    p.add_argument("--module", choices=list("123456"), help="just one module")
    p.add_argument("--data", action="store_true", help="provenance of every entity")
    args = p.parse_args()

    onto = ontology.build(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                          ROOT / "data" / "synthetic")

    if args.region:
        show_region(onto, args.region)
        return 0
    if args.data:
        show_data(onto)
        return 0

    shows = {"1": show_module1, "2": show_module2, "3": show_module3,
             "4": show_module4, "5": show_module5, "6": show_module6}
    if args.module:
        shows[args.module](onto)
        return 0

    show_data(onto)
    for key in "123456":
        shows[key](onto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
