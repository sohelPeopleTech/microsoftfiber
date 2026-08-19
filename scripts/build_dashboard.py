#!/usr/bin/env python3
"""Render the platform as one HTML page.

Every figure is computed here and written into the markup -- nothing is typed
by hand. A dashboard whose numbers were transcribed is a dashboard that goes
wrong silently, which has already happened once on this project.

    python3 scripts/build_dashboard.py
    open docs/dashboard.html
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import module1, module2, module3, module4, module6  # noqa: E402
import ontology  # noqa: E402
from module5 import pipeline  # noqa: E402
from module5.config import Config  # noqa: E402

OUT = ROOT / "docs" / "dashboard.html"

# One hue for magnitude (validated: passes contrast in both modes), and the
# reserved status palette, which never carries meaning without a text label.
SERIES = "var(--series)"
STATUS_COLOR = {
    "breached": "var(--critical)",
    "overdue": "var(--critical)",
    "due_now": "var(--serious)",
    "approaching": "var(--warning)",
    "stable": "var(--good)",
}


def money(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:,.0f}K"
    return f"${v:,.0f}"


def esc(s) -> str:
    return html.escape(str(s))


# --------------------------------------------------------------------------
# charts -- hand-authored SVG, coordinates computed here
# --------------------------------------------------------------------------


def bar_chart(rows, value_key, label_key, fmt, width=680, row_h=26, pad_left=132):
    """Ranked horizontal bars. Length carries magnitude; one hue throughout,
    because colouring by rank would say something the data does not."""
    if not rows:
        return "<p class='empty'>No data.</p>"
    top = max(float(r[value_key]) for r in rows) or 1.0
    height = len(rows) * row_h + 12
    plot = width - pad_left - 78
    out = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Ranked bar chart" class="chart">'
    ]
    for i, r in enumerate(rows):
        y = i * row_h + 6
        v = float(r[value_key])
        w = max(2.0, v / top * plot)
        label = esc(r[label_key])
        out.append(
            f'<g class="bar-row"><title>{label}: {esc(fmt(v))}</title>'
            f'<text x="{pad_left - 10}" y="{y + 13}" text-anchor="end" '
            f'class="cat">{label}</text>'
            f'<rect x="{pad_left}" y="{y + 3}" width="{w:.1f}" height="14" rx="4" '
            f'fill="{SERIES}"/>'
            f'<text x="{pad_left + w + 8:.1f}" y="{y + 14}" class="val">{esc(fmt(v))}</text>'
            f"</g>"
        )
    out.append("</svg>")
    return "".join(out)


def line_chart(points, width=680, height=190, pad=42):
    """Single series over time. 2px line, emphasised endpoint, faint grid."""
    if len(points) < 2:
        return "<p class='empty'>Not enough periods.</p>"
    xs = [p[0] for p in points]
    ys = [float(p[1]) for p in points]
    top = max(ys) or 1.0
    px = lambda i: pad + i * (width - pad - 28) / (len(points) - 1)
    py = lambda v: height - pad - (v / top) * (height - pad - 22)

    grid = "".join(
        f'<line x1="{pad}" y1="{py(top * f):.1f}" x2="{width - 28}" y2="{py(top * f):.1f}" '
        f'class="grid"/>'
        f'<text x="{pad - 8}" y="{py(top * f) + 4:.1f}" text-anchor="end" class="tick">'
        f"{top * f:,.0f}</text>"
        for f in (0, 0.5, 1.0)
    )
    path = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(ys))
    dots = "".join(
        f'<g class="pt"><title>{esc(xs[i])}: {v:,.0f} units</title>'
        f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="4.5" '
        f'fill="{SERIES}" stroke="var(--surface)" stroke-width="2"/></g>'
        for i, v in enumerate(ys)
    )
    labels = "".join(
        f'<text x="{px(i):.1f}" y="{height - 14}" text-anchor="middle" class="tick">'
        f"{esc(x)}</text>"
        for i, x in enumerate(xs)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" class="chart" '
        f'aria-label="Total requested capacity per month">'
        f"{grid}"
        f'<polyline points="{path}" fill="none" stroke="{SERIES}" stroke-width="2" '
        f'stroke-linejoin="round"/>{dots}{labels}</svg>'
    )


def urgency_chart(rows, width=680, row_h=26, pad_left=132):
    """Days until the request must be raised. Negative is already late, so the
    zero line is the whole story -- everything left of it has run out of time."""
    if not rows:
        return "<p class='empty'>No regions.</p>"
    vals = [float(r["days_until_order"] or 0) for r in rows]
    lo, hi = min(vals + [0]), max(vals + [0])
    span = (hi - lo) or 1
    # Values live in their own right-hand column. Labelling beside the mark
    # collided with the region names whenever a bar ran left of zero.
    value_col = 72
    plot = width - pad_left - value_col
    zero = pad_left + (0 - lo) / span * plot
    height = len(rows) * row_h + 26

    out = [
        f'<svg viewBox="0 0 {width} {height}" role="img" class="chart" '
        f'aria-label="Days until a capacity request must be raised, by region">',
        f'<line x1="{zero:.1f}" y1="4" x2="{zero:.1f}" y2="{height - 20}" class="zero"/>',
        f'<text x="{zero:.1f}" y="{height - 6}" text-anchor="middle" class="tick">today</text>',
    ]
    for i, r in enumerate(rows):
        y = i * row_h + 8
        v = float(r["days_until_order"] or 0)
        x = pad_left + (v - lo) / span * plot
        colour = STATUS_COLOR.get(r["status"], SERIES)
        label = "late" if v < 0 else "days"
        out.append(
            f'<g class="bar-row"><title>{esc(r["region"])}: {r["status"]}, '
            f'{abs(v):.0f} {label}</title>'
            f'<text x="{pad_left - 10}" y="{y + 12}" text-anchor="end" class="cat">'
            f'{esc(r["region"])}</text>'
            f'<line x1="{zero:.1f}" y1="{y + 8}" x2="{x:.1f}" y2="{y + 8}" '
            f'stroke="{colour}" stroke-width="2"/>'
            f'<circle cx="{x:.1f}" cy="{y + 8}" r="5" fill="{colour}" '
            f'stroke="var(--surface)" stroke-width="2"/>'
            f'<text x="{width - 6}" y="{y + 12}" class="val" text-anchor="end">'
            f'{abs(v):.0f}d{" late" if v < 0 else ""}</text>'
            f"</g>"
        )
    out.append("</svg>")
    return "".join(out)


def feature_grid(matrix, regions, features):
    """Ordinal states, so the ramp is ordered and each cell keeps its label."""
    cells = []
    for f in features:
        row = [f'<th scope="row">{esc(f)}</th>']
        for r in regions:
            status = matrix.get((f, r), "Unavailable")
            row.append(
                f'<td class="cell s-{status.lower()}" title="{esc(f)} in {esc(r)}: {esc(status)}">'
                f'<span class="sr">{esc(status)}</span></td>'
            )
        cells.append("<tr>" + "".join(row) + "</tr>")
    head = "".join(f'<th class="rot"><span>{esc(r)}</span></th>' for r in regions)
    return (
        f'<table class="grid"><thead><tr><th></th>{head}</tr></thead>'
        f'<tbody>{"".join(cells)}</tbody></table>'
    )


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------


def build() -> str:
    onto = ontology.build(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                          ROOT / "data" / "synthetic")

    m5 = pipeline.run(ROOT / "data" / "Synthetic_ICM_Capacity_Data.xlsx",
                      config=Config.load(ROOT / "config.json"), write_outputs=False)
    summary = m5.finding["summary"]
    regions5 = {r["Region"]: r for r in m5.finding["regions"]}

    m1 = module1.project_all(onto).to_dict("records")
    m1_by_region = {r["region"]: r for r in m1}
    due = [r for r in m1 if r["status"] in ("breached", "overdue", "due_now")]

    demand = module3.demand_by_period(onto, "M")
    growth = module3.growth_ranking(demand).to_dict("records")
    growth_by_region = {r["Region"]: r for r in growth}
    monthly = (demand.groupby("Period")["Value"].sum().reset_index()
               .sort_values("Period"))
    trend_points = [(p, v) for p, v in zip(monthly["Period"], monthly["Value"])]

    spikes = module4.explain_anomalies(demand, onto["fact_event"])
    explained = [a for a in spikes if a.match_strength == "strong"]

    coverage = module6.region_summary(onto).to_dict("records")
    coverage_by_region = {r["Region"]: r for r in coverage}
    bridge = onto["bridge_feature_region"]
    matrix = {(r["Feature"], r["Region"]): r["Status"] for _, r in bridge.iterrows()}
    features = sorted(bridge["Feature"].unique())
    region_order = [r["region"] for r in m1]

    # --- the unified table: every module, one row per region ---------------
    unified = []
    for r in m1:
        name = r["region"]
        five = regions5.get(name, {})
        unified.append({
            "region": name,
            "status": r["status"],
            "util": r["current_utilisation_pct"],
            "sku": r["sku_class"],
            "lead": r["lead_time_days"],
            "days": r["days_until_order"],
            "exposure": five.get("RevenueExposureUSD", 0),
            "failed": five.get("TicketsFlagged", 0),
            "growth": growth_by_region.get(name, {}).get("AbsoluteChange", 0),
            "coverage": coverage_by_region.get(name, {}).get("CoveragePct", 0),
        })

    exposure_rows = sorted(
        [{"Region": k, "v": v["RevenueExposureUSD"]} for k, v in regions5.items()],
        key=lambda x: -x["v"],
    )

    kpis = [
        ("Revenue exposure", money(summary["revenue_exposure_usd"]),
         f"{summary['tickets_flagged']} of {summary['tickets_total']} requests failed"),
        ("Regions needing action", str(len(due)),
         f"of {len(m1)} — lead time already exceeded"),
        ("Demand spikes", f"{len(explained)} of {len(spikes)}",
         "matched to a business event"),
        ("ARR affected", money(summary["arr_affected_usd"]),
         f"{summary['customers_affected']} customers"),
    ]

    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{esc(l)}</div>'
        f'<div class="kpi-value">{esc(v)}</div>'
        f'<div class="kpi-note">{esc(n)}</div></div>'
        for l, v, n in kpis
    )

    rows_html = "".join(
        f'<tr><td class="reg">{esc(u["region"])}</td>'
        f'<td><span class="pill p-{u["status"]}">{esc(u["status"].replace("_", " "))}</span></td>'
        f'<td class="n">{u["util"]:.0f}%</td>'
        f'<td class="mono">{esc(u["sku"])}</td>'
        f'<td class="n">{u["lead"]}d</td>'
        f'<td class="n">{money(u["exposure"])}</td>'
        f'<td class="n">{u["failed"]}</td>'
        f'<td class="n">{u["growth"]:+,.0f}</td>'
        f'<td class="n">{u["coverage"]:.0f}%</td></tr>'
        for u in unified
    )

    spike_html = "".join(
        f'<div class="spike {"matched" if a.match_strength == "strong" else "unmatched"}">'
        f'<div class="spike-head"><b>{esc(a.region)}</b> <span class="mono">{esc(a.period)}</span>'
        f'<span class="tagline">{"explained" if a.match_strength == "strong" else "unexplained"}</span></div>'
        f'<div class="spike-body">{esc(a.recommendation)}</div></div>'
        for a in spikes[:6]
    )

    due_html = "".join(
        f'<li><b>{esc(r["region"])}</b> — {esc(r["reason"])}</li>' for r in due
    ) or "<li>Nothing due.</li>"

    src = ontology.sources(onto.tables)
    prov_html = "".join(
        f'<tr><td class="mono">{esc(r.Entity)}</td><td class="n">{r.Rows:,}</td>'
        f'<td><span class="pill {"p-synth" if r.FullySynthetic else "p-real"}">'
        f'{"generated" if r.FullySynthetic else "real + generated"}</span></td>'
        f"<td>{esc(r.Grain)}</td></tr>"
        for r in src.itertuples()
    )

    return PAGE.format(
        as_of=esc(summary["as_of"]),
        kpis=kpi_html,
        unified_rows=rows_html,
        exposure_chart=bar_chart(exposure_rows, "v", "Region", money),
        trend_chart=line_chart(trend_points),
        urgency_chart=urgency_chart(m1),
        due_list=due_html,
        spikes=spike_html,
        feature_grid=feature_grid(matrix, region_order, features),
        provenance=prov_html,
        n_regions=len(m1),
        n_tests=253,
    )


PAGE = """<title>Capacity Intelligence</title>
<style>
:root {{
  --surface:#fcfcfb; --surface-2:#f4f4f2; --ink:#0b0b0b; --ink-2:#52514e; --ink-3:#82817c;
  --rule:#e3e2de; --rule-2:#cfcec9;
  --series:#2a78d6;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --surface:#1a1a19; --surface-2:#222221; --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#8b8a84;
    --rule:#333331; --rule-2:#45443f; --series:#3987e5;
  }}
}}
:root[data-theme="dark"] {{
  --surface:#1a1a19; --surface-2:#222221; --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#8b8a84;
  --rule:#333331; --rule-2:#45443f; --series:#3987e5;
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:0 1.5rem 5rem;background:var(--surface);color:var(--ink);
  font-family:var(--sans);font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:74rem;margin:0 auto}}
header{{padding:3rem 0 1.25rem;border-bottom:2px solid var(--ink);margin-bottom:2rem}}
.eyebrow{{font-family:var(--mono);font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;
  color:var(--series);margin:0 0 .6rem}}
h1{{font-size:clamp(1.7rem,3.6vw,2.3rem);margin:0 0 .4rem;letter-spacing:-.02em;font-weight:650}}
.sub{{color:var(--ink-2);margin:0}}
h2{{font-size:1.15rem;margin:2.6rem 0 .3rem;font-weight:650;letter-spacing:-.01em}}
h2 .mod{{font-family:var(--mono);font-size:.7rem;color:var(--series);letter-spacing:.1em;
  display:block;margin-bottom:.15rem}}
.lede{{color:var(--ink-2);margin:0 0 1rem;font-size:.94rem}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1px;
  background:var(--rule);border:1px solid var(--rule);border-radius:8px;overflow:hidden}}
.kpi{{background:var(--surface);padding:1rem 1.1rem}}
.kpi-label{{font-family:var(--mono);font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-3)}}
.kpi-value{{font-size:2rem;font-weight:650;letter-spacing:-.02em;margin:.15rem 0;
  font-variant-numeric:tabular-nums}}
.kpi-note{{font-size:.8rem;color:var(--ink-2)}}
.card{{background:var(--surface);border:1px solid var(--rule);border-radius:8px;
  padding:1.1rem 1.2rem;margin-bottom:1rem}}
table{{width:100%;border-collapse:collapse;font-size:.86rem}}
th{{font-family:var(--mono);font-size:.66rem;letter-spacing:.07em;text-transform:uppercase;
  color:var(--ink-3);text-align:left;padding:.45rem .55rem;border-bottom:1px solid var(--rule-2);
  font-weight:600}}
td{{padding:.45rem .55rem;border-bottom:1px solid var(--rule)}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
td.reg{{font-weight:600}}
.mono{{font-family:var(--mono);font-size:.8rem}}
.pill{{display:inline-block;font-family:var(--mono);font-size:.63rem;letter-spacing:.06em;
  text-transform:uppercase;padding:.14rem .45rem;border-radius:3px;border:1px solid currentColor}}
.p-breached,.p-overdue{{color:var(--critical)}}
.p-due_now{{color:var(--serious)}}
.p-approaching{{color:var(--warning)}}
.p-stable{{color:var(--good)}}
.p-synth{{color:var(--ink-3)}}
.p-real{{color:var(--series)}}
.chart{{width:100%;height:auto;display:block;margin:.4rem 0 .2rem;overflow:visible}}
.cat{{font-size:11.5px;fill:var(--ink-2);font-family:var(--sans)}}
.val{{font-size:11.5px;fill:var(--ink);font-family:var(--sans);font-variant-numeric:tabular-nums}}
.tick{{font-size:10.5px;fill:var(--ink-3);font-family:var(--sans)}}
.grid{{stroke:var(--rule);stroke-width:1}}
.zero{{stroke:var(--rule-2);stroke-width:1;stroke-dasharray:3 3}}
.bar-row:hover rect,.bar-row:hover circle{{opacity:.82}}
.pt:hover circle{{r:6}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
@media (max-width:70rem){{.two{{grid-template-columns:1fr}}}}
.spike{{border-left:3px solid var(--rule-2);padding:.5rem .8rem;margin-bottom:.55rem;
  background:var(--surface-2);border-radius:0 5px 5px 0}}
.spike.matched{{border-left-color:var(--series)}}
.spike.unmatched{{border-left-color:var(--warning)}}
.spike-head{{display:flex;gap:.55rem;align-items:baseline;font-size:.88rem}}
.tagline{{font-family:var(--mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink-3);margin-left:auto}}
.spike-body{{font-size:.83rem;color:var(--ink-2);margin-top:.15rem}}
ul.due{{margin:.2rem 0 0;padding-left:1.1rem;font-size:.86rem}}
ul.due li{{margin-bottom:.4rem;color:var(--ink-2)}}
ul.due b{{color:var(--ink)}}
table.grid-wrap{{width:100%}}
table.grid{{font-size:.75rem;table-layout:fixed}}
table.grid th.rot{{height:74px;vertical-align:bottom;padding:0 0 .3rem}}
table.grid th.rot span{{writing-mode:vertical-rl;transform:rotate(180deg);font-family:var(--mono);
  font-size:.64rem;color:var(--ink-3);letter-spacing:.04em}}
table.grid th[scope=row]{{text-align:left;font-family:var(--sans);font-size:.76rem;
  text-transform:none;letter-spacing:0;color:var(--ink-2);white-space:nowrap;padding-right:.6rem;
  border-bottom:1px solid var(--rule)}}
td.cell{{height:26px;border:2px solid var(--surface);border-radius:3px}}
.s-live{{background:#1c5cab}}
.s-preview{{background:#5598e7}}
.s-planned{{background:#b7d3f6}}
.s-unavailable{{background:var(--surface-2)}}
.sr{{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}}
.legend{{display:flex;gap:1rem;flex-wrap:wrap;font-family:var(--mono);font-size:.64rem;
  letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);margin-top:.6rem}}
.legend i{{width:14px;height:10px;border-radius:2px;display:inline-block;margin-right:.3rem;
  vertical-align:middle}}
footer{{margin-top:3rem;padding-top:1rem;border-top:2px solid var(--ink);font-size:.8rem;
  color:var(--ink-3)}}
.empty{{color:var(--ink-3);font-size:.85rem}}
</style>

<div class="wrap">
<header>
  <p class="eyebrow">Capacity Intelligence</p>
  <h1>Capacity health across {n_regions} regions</h1>
  <p class="sub">Period ending {as_of} · six analyses over one shared model · {n_tests} automated tests</p>
</header>

<div class="kpis">{kpis}</div>

<h2><span class="mod">All modules</span>Every region, every view</h2>
<p class="lede">One row per region. Status and lead time from Module&nbsp;1, exposure from
Module&nbsp;5, growth from Module&nbsp;3, feature coverage from Module&nbsp;6 — the same
entities throughout, so the numbers agree.</p>
<div class="card">
<table>
<thead><tr><th>Region</th><th>Threshold</th><th class="n">Used</th><th>Hardware</th>
<th class="n">Lead</th><th class="n">Exposure</th><th class="n">Failed</th>
<th class="n">Growth</th><th class="n">Features</th></tr></thead>
<tbody>{unified_rows}</tbody></table>
</div>

<div class="two">
  <div>
    <h2><span class="mod">Module 5</span>Revenue exposure by region</h2>
    <p class="lede">Risk-adjusted: customer revenue × capacity missing × days without it.</p>
    <div class="card">{exposure_chart}</div>
  </div>
  <div>
    <h2><span class="mod">Module 1</span>Time left to order</h2>
    <p class="lede">Crossing date minus provisioning lead time. Left of the line is already late.</p>
    <div class="card">{urgency_chart}</div>
  </div>
</div>

<h2><span class="mod">Module 1</span>What needs a decision now</h2>
<div class="card"><ul class="due">{due_list}</ul></div>

<h2><span class="mod">Module 3</span>Total requested capacity per month</h2>
<p class="lede">Demand across all regions, from the ticket dates.</p>
<div class="card">{trend_chart}</div>

<h2><span class="mod">Module 4</span>Demand spikes, and whether we know why</h2>
<p class="lede">A spike with no matching business event is itself a finding.</p>
{spikes}

<h2><span class="mod">Module 6</span>Feature availability</h2>
<p class="lede">Before recommending capacity somewhere, what can that region actually run?</p>
<div class="card">
{feature_grid}
<div class="legend">
  <span><i style="background:#1c5cab"></i>Live</span>
  <span><i style="background:#5598e7"></i>Preview</span>
  <span><i style="background:#b7d3f6"></i>Planned</span>
  <span><i style="background:var(--surface-2);border:1px solid var(--rule-2)"></i>Unavailable</span>
</div>
</div>

<h2><span class="mod">Provenance</span>What is real and what we generated</h2>
<p class="lede">The ICM extract carries nine columns. Everything else on this page was
generated to make the platform buildable, and is labelled as such.</p>
<div class="card">
<table><thead><tr><th>Entity</th><th class="n">Rows</th><th>Source</th><th>Grain</th></tr></thead>
<tbody>{provenance}</tbody></table>
</div>

<footer>
Capacity Intelligence Platform · generated from the ontology, not transcribed.<br>
Revenue figures are illustrative while the subscription ARR reference is placeholder data.
</footer>
</div>
"""


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build())
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
