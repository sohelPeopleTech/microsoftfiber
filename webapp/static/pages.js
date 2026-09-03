/* The six tabs. Each registers itself on PAGES (declared in shell.js).
 *
 * A renderer takes the <main> element, writes it, and wires its own controls.
 * Nothing here holds cross-page state -- switching tabs re-fetches, which at
 * this data size is cheaper than a cache that can go stale mid-review.
 */

/* ==================================================================== 1/6 */
/* Overview                                                                  */

/* Why a region carries revenue loss, which is not the same question as whether
   it is running out of capacity.

   A reader asked it directly: the pill is green, the region is nowhere near its
   threshold, so where is the money coming from. The two columns look in
   opposite directions -- the status forecasts the region's ceiling, the loss is
   history about individual requests -- and across this extract every failure in
   a currently-green region landed on a capacity pool that had room. They failed
   on maintenance windows, quota policy, network faults and tickets nobody
   actioned.

   So the table says which it was. "Add capacity here" is the wrong answer to
   most of them, and nothing on the page said so. */
function failureCause(r) {
  const c = r.failureCause;
  if (!r.failed) return `<span class="t3">—</span>`;
  if (!c || !c.topCause) {
    return `<span class="t3">cause not recorded</span>`;
  }
  const capacity = c.capacityCaused || 0;
  const other = c.otherCaused || 0;
  const onFull = c.landedOnAFullSite || 0;

  /* Two different questions, and an earlier version answered one while
     printing the other. `onFull` is where the failures happened; `overLine` is
     what the region contains. westeurope answers 0 and 2 -- none of its
     failures hit a full building, and yet dc04 sits at 100% with nothing free.
     This said "no capacity pool here is over its line", which the region page
     contradicted one click later, and a reader caught it.

     So the verdict now only ever speaks about the failures, and the count of
     full sites is stated separately as the fact it is. */
  const overLine = c.sitesOverLine || 0;
  const full = overLine
    ? `<span class="t3"> · ${num(overLine)} of ${num(c.sites)} capacity pools here
       ${overLine === 1 ? "is over its own line" : "are over their own lines"}</span>`
    : "";

  let verdict;
  if (capacity > other && onFull) {
    verdict = `<span class="t-bad">capacity was the constraint</span>
      <span class="t3">· ${num(onFull)} landed on a full capacity pool</span>`;
  } else if (capacity > other) {
    verdict = `<span class="t-warn">recorded as a shortage</span>
      <span class="t3">· but they landed where there was room</span>${full}`;
  } else if (capacity) {
    verdict = `<span class="t-warn">${num(other)} of ${num(capacity + other)} were not about capacity</span>${full}`;
  } else {
    verdict = `<span class="t-good">not a capacity problem</span>${full}`;
  }
  return `${esc(c.topCause)}${c.causes > 1 ? `<span class="t3"> +${c.causes - 1} other</span>` : ""}
    <br>${verdict}`;
}

/* Overview moved off `/` when the fleet map took it as the landing page. It is
   a real page with a real URL, not a redirect target -- the only thing that
   changed is which of the two someone arrives on. */
PAGES["/overview"] = async (view) => {
  const d = await get("/api/overview");
  const k = d.kpis;

  const dueNow = d.regions.filter((r) =>
    ["breached", "overdue", "due_now"].includes(r.status));
  const attention = [...d.regions]
    .filter((r) => r.exposure > 0 || dueNow.includes(r))
    .sort((a, b) => (a.daysUntilAction ?? 999) - (b.daysUntilAction ?? 999));

  /* Outcome funnel. The reference tool's conversion funnel asks "how far down
     the pipeline did partners get"; the capacity equivalent asks "how far down
     the pipeline did requests fall". Same shape, opposite polarity -- so the
     wash is applied to the failing steps, not the passing ones.

     Every stage is a subset of the one above it and shares the same
     denominator. That is the whole discipline of a funnel: an earlier version
     put demand spikes in the last row and printed "7 of 60 = 12%", which is
     two unrelated quantities divided by each other. */
  const c = d.categoryCounts || {};
  const L = d.outcomeLabels || {};
  const total = k.total || 1;
  // Approved covers every ticket that eventually got its capacity, however
  // many passes it took -- the split below says how painful each route was.
  // Review: the two in-SLA outcomes are not targets. They are kept only so the
  // 60 reconciles, and are collapsed to one line rather than given the same
  // weight as the failures the tool exists to surface.
  const handled = (c.no_denial || 0) + (c.same_day_approved || 0);
  const step = (n, label, cls) => `<div class="step ${cls}">
      <span class="n">${num(n)}</span><span class="what">${esc(label)}</span></div>`;

  view.innerHTML = howto({
  answers: "<b>Portfolio view across all regions</b> — decisions, request outcomes, and revenue impact.",
  steps: [
    { what: "KPI strip", is: "Shows managed regions, regions needing action, and revenue impact." },
    { what: "Request outcomes", is: "Shows SLA breaches and ungranted requests; successful requests are grouped for reference." },
    { what: "Demand distribution", is: "Ranks request volume by region. Select a row for details." },
    { what: "Denial reasons", is: "Shows why requests failed and the recommended remediation." },
    { what: "Regions requiring action", is: "Prioritises regions by decision urgency and revenue impact." },
  ],
  words: [
    { term: "FTR", means: "First-time resolution — approved on the initial pass." },
    { term: "SLA", means: "Expected resolution time by subscription tier. Missing it counts as a failure." },
    { term: "Revenue loss", means: "<b>Microsoft revenue</b> affected by unfulfilled requests and their duration." },
    { term: "Manual review", means: "Issues requiring engineering or account-team action rather than automation." },
    { term: "Green region with revenue loss", means: "Green means the region has capacity headroom, not that every request succeeds. Failures can come from maintenance, quota, network, or other issues." },
  ],
  next: "Review denial reasons, then action regions with negative 'Days to decide'.",
  sources: "Capacity requests, subscription ARR, and daily Fabric capacity data.",
}) + title("Overview", `Everything across all regions — as of ${String(d.asOf).slice(0, 10)}`) + `

  <div class="kpis">
    ${kpi("Regions monitored", num(d.regions.length), `across ${d.skus.length} Fabric SKUs`, "ink", "Azure regions with capacity requests in this extract. Each region holds Fabric capacities spread over several capacity pools.")}
    ${kpi("Regions needing action", num(dueNow.length), "at or past their safety line", dueNow.length ? "bad" : "good", "Regions already past the threshold their own capacity pools hold, or forecast to cross it before the next review. Scaling an F SKU takes effect immediately, so this is a backlog of decisions rather than of deliveries.")}
    ${kpi("Revenue loss", money(k.exposure), `attributed to ${k.failed} failed requests`, "bad", "Microsoft revenue, not the customer\u2019s own. Each customer\u2019s ARR, apportioned by the share of their request left unfulfilled and how long it stayed unfulfilled. A severity ranking, not money written off.")}
  </div>

  ${panel(`Request outcomes — ${num(k.failed)} of ${num(k.total)} require action`, `
    <div class="funnel">
      <div class="grp">In scope — ${num(k.failed)} failures</div>
      ${step(c.denied_then_approved_late || 0, L.denied_then_approved_late, "wash")}
      ${step(c.denied_unfulfilled || 0, L.denied_unfulfilled, "bad")}
      <div class="grp" style="margin-top:1.1rem">Out of scope — ${num(handled)} handled within SLA</div>
      ${step(handled, `Approved on the first pass or inside the customer's SLA`, "ok")}
    </div>
    <p style="color:var(--ink-2);font-size:.82rem;margin:0 1.15rem 1.15rem">
      Only the two amber and red rows are targets. A request approved on the
      first pass, and one denied then approved inside that customer's SLA, are
      both normal turnaround — they are shown as a single line for reconciliation
      and are not what this tool is for. The
      ${num(c.denied_then_approved_late || 0)} that breached SLA before being
      granted and the ${num(c.denied_unfulfilled || 0)} never granted at all are
      what carry the ${money(k.exposure)} of revenue loss.
      <b>Denied is not the same as refused</b>: of the
      ${num((c.same_day_approved || 0) + (c.denied_then_approved_late || 0)
            + (c.denied_unfulfilled || 0))}
      requests denied at some point,
      ${num((c.same_day_approved || 0) + (c.denied_then_approved_late || 0))}
      were approved in the end and only ${num(c.denied_unfulfilled || 0)} never
      were. The ${num(c.denied_then_approved_late || 0)} above is a <i>delay</i>
      that breached SLA, not a refusal.
    </p>
  `, { flush: true })}

  ${/* Three views of the same period, in one panel rather than stacked down
        the page. Read as three separate cards, the third sat a page-length
        scroll below the first and nothing said they belonged together. */""}
  ${tabPanel("ov-tables", [
    {
      label: "Demand by region",
      count: d.regionDistribution?.length || 0,
      hint: "highest volume first · click a row for detail",
      body: `<div class="scroll-x"><table>
        <thead><tr><th>Region</th><th class="n">Requests</th><th class="n">Share</th>
          <th class="n">Failed</th><th class="n">Customers</th>
          <th class="n">Capacity pools</th><th>Volume</th></tr></thead>
        <tbody>${(d.regionDistribution || []).map((r) => `<tr class="clickable" data-region="${esc(r.region)}">
          <td><b>${esc(r.region)}</b></td>
          <td class="n">${num(r.requests)}</td>
          <td class="n">${pct(r.sharePct, 1)}</td>
          <td class="n">${r.failed ? `<b style="color:var(--bad)">${num(r.failed)}</b>` : "—"}</td>
          <td class="n">${num(r.customers)}</td>
          <td class="n">${num(r.datacentres)}</td>
          <td><span class="bar" style="width:${(r.requests / (d.regionDistribution[0]?.requests || 1)) * 100}%"></span></td>
        </tr>`).join("")}</tbody></table></div>`,
    },
    {
      label: "Denial reasons",
      count: d.reasons?.length || 0,
      hint: "the fix depends on the cause",
      body: `<div class="scroll-x"><table>
        <thead><tr><th>Reason</th><th class="n">Incidents</th><th class="n">Share</th>
          <th>Definition</th><th>Recommended action</th></tr></thead>
        <tbody>${(d.reasons || []).map((r) => `<tr>
          <td><b>${esc(r.reason)}</b>${r.needsHuman
            ? ` <span class="pill warn">manual review</span>` : ""}</td>
          <td class="n">${num(r.count)}</td>
          <td class="n">${pct(r.sharePct, 1)}</td>
          <td class="why">${esc(r.detail)}</td>
          <td class="why">${esc(r.action)}</td>
        </tr>`).join("")}</tbody></table></div>
        <p style="color:var(--ink-2);font-size:.82rem;margin:.75rem 1.15rem 1.15rem">
          Counted across every request that was refused at least once, including
          those later approved. Reasons marked <b>manual review</b> have no automated
          fix — the right next step is a conversation, not a calculation.
        </p>`,
    },
    {
      label: "Regions requiring action",
      count: attention.length,
      hint: "click a row for detail",
      body: attention.length ? `<div class="scroll-x"><table>
        <thead><tr>
          <th>Region</th><th>Status</th><th>Flag rationale</th>
          <th class="n">Days to decide</th><th class="n">Revenue loss</th>
          <th>Why those requests failed</th><th class="n">Failed</th>
        </tr></thead>
        <tbody>${attention.map((r) => `<tr class="clickable" data-region="${esc(r.region)}">
          <td><b>${esc(r.region)}</b></td>
          <td>${statusPill(r.status)}<br>${refusingNow(r.throttling)}</td>
          <td class="why">${esc(r.reason)}</td>
          <td class="n">${r.daysUntilAction == null ? "—" :
            `<b style="color:${r.daysUntilAction < 0 ? "var(--bad)" : "inherit"}">${r.daysUntilAction}</b>`}</td>
          <td class="n">${money(r.exposure)}</td>
          <td class="why">${failureCause(r)}</td>
          <td class="n">${num(r.failed)}</td>
        </tr>`).join("")}</tbody></table></div>`
        : `<p class="empty">No region is currently flagged.</p>`,
    },
  ], { label: "Overview breakdowns" })}
  `;

  view.querySelectorAll("tr[data-region]").forEach((tr) =>
    (tr.onclick = () => navigate(`/region/${encodeURIComponent(tr.dataset.region)}`)));
};

/* ==================================================================== 2/6 */
/* Regions                                                                   */

/* The region recommendation, opened from the Regions table.

   Review drew the boundary and it is worth restating here: a region
   recommendation is about the safety threshold and where the region's own spare
   capacity is. Anything about swapping processors belongs to a facility, so this
   ends with a link into the capacity pools rather than guessing at hardware. */
async function showRecommendation(region) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal"><div class="modal-head">
      <h3>Recommendation — ${esc(region)}</h3>
      <button class="x" aria-label="Close">&times;</button></div>
    <div class="modal-body">Loading…</div></div>`;
  document.body.appendChild(back);
  const close = () => back.remove();
  back.querySelector(".x").onclick = close;
  back.onclick = (e) => { if (e.target === back) close(); };
  document.addEventListener("keydown", function esc0(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc0); }
  });

  let d;
  try {
    d = await get(`/api/region-recommendation/${encodeURIComponent(region)}`);
  } catch (err) {
    back.querySelector(".modal-body").innerHTML =
      `<p class="error">Could not load the recommendation for ${esc(region)}.</p>`;
    return;
  }

  back.querySelector(".modal-body").innerHTML = `
    <p style="margin:0 0 .6rem"><b>${esc(d.headline)}</b></p>
    <p style="background:var(--page);border-left:3px solid var(--brand);
       padding:.7rem .9rem;margin:0 0 1rem">${esc(d.action)}</p>

    <div class="kpis" style="margin:0 0 1rem">
      ${kpi("Utilisation", pct(d.utilisationPct, 1),
            `${num(Math.round(d.usedUnits))} of ${num(Math.round(d.deployedUnits))} CU`,
            d.thresholdUsedPct > 0 ? "bad" : "")}
      ${kpi("Safety threshold", pct(d.thresholdPct, 0),
            d.thresholdUsedPct > 0
              ? `threshold utilised by ${d.thresholdUsedPct.toFixed(1)}%`
              : `${num(Math.round(d.freeUnits))} CU free`,
            d.thresholdUsedPct > 0 ? "bad" : "good")}
      ${kpi("CU pending", num(Math.round(d.coresPending)),
            `owed to ${d.customersWaiting} customer(s)`, d.coresPending ? "bad" : "good")}
    </div>

    ${d.options.length ? `<h4 style="margin:0 0 .3rem;font-size:.85rem">If the safety line moved</h4>
    <div class="scroll-x"><table>
      <thead><tr><th>New line</th><th class="n">CU released</th>
        <th class="n">Headroom after</th><th>Covers what is owed?</th></tr></thead>
      <tbody>${d.options.map((o) => `<tr>
        <td class="n">${pct(o.thresholdPct, 0)}</td>
        <td class="n">${num(Math.round(o.releasesCores))}</td>
        <td class="n">${num(Math.round(o.headroomAfter))}</td>
        <td>${o.coversPending ? `<span class="pill good">yes</span>`
                              : `<span class="pill bad">no</span>`}</td>
      </tr>`).join("")}</tbody></table></div>` : ""}

    <p style="margin:1rem 0 0;font-size:.85rem">
      Hardware changes are decided per facility, not per region.
      ${d.sitesWithActivity} of ${d.siteCount} sites here carry a denial —
      <a href="/datacentres" data-nav style="color:var(--brand)">detailed analysis, click here</a>.
    </p>`;

  back.querySelectorAll("a[data-nav]").forEach((a) =>
    (a.onclick = (e) => { e.preventDefault(); close(); navigate(a.getAttribute("href")); }));
}

/* ------------------------------------------------------------ demand charts

   Two questions, and on a region they are now drawn in one frame. Demand is how
   much capacity was asked for each month, with the months a signed deal drove
   picked out. Position is how full the place actually ran against its own
   safety threshold. Review first asked for these as two charts and then asked
   for them merged, on the grounds that reading a spike in requests against the
   headroom available to absorb it means holding both pictures at once.

   They are merged, not averaged: CU and percent-full are different units and
   keep an axis each, so nothing is rescaled into a number that was never
   measured. `combinedRegionChart` draws that; `demandChart` below still serves
   customers and capacity pools, where no utilisation series exists to pair with. */

let CHART_SEQ = 0;
const CHART_DATA = {};

/* Hover that follows the cursor rather than waiting on the browser's own
   tooltip. `<title>` needed a second of stillness and could not show more than
   one line, which is no use on a chart whose whole point is "this month, this
   many CU, because of this deal". */
function wireCharts(root) {
  (root || document).querySelectorAll("svg[data-chart]").forEach((svg) => {
    const spec = CHART_DATA[svg.dataset.chart];
    if (!spec || svg.dataset.wired) return;
    svg.dataset.wired = "1";

    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    svg.parentElement.style.position = "relative";
    svg.parentElement.appendChild(tip);
    const guide = svg.querySelector(".guide");
    const dot = svg.querySelector(".hover-dot");

    svg.addEventListener("mousemove", (ev) => {
      const box = svg.getBoundingClientRect();
      // Chart coordinates are in the viewBox, the pointer is in screen pixels.
      const vx = ((ev.clientX - box.left) / box.width) * spec.W;
      let best = 0, bd = Infinity;
      spec.points.forEach((pt, i) => {
        const d = Math.abs(pt.x - vx);
        if (d < bd) { bd = d; best = i; }
      });
      const pt = spec.points[best];
      if (guide) { guide.setAttribute("x1", pt.x); guide.setAttribute("x2", pt.x); guide.style.opacity = "1"; }
      if (dot) { dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y); dot.style.opacity = "1"; }
      tip.hidden = false;
      tip.innerHTML = pt.html;
      const left = (pt.x / spec.W) * box.width;
      tip.style.left = `${Math.min(Math.max(left, 8), box.width - 8)}px`;
      tip.style.top = `${(pt.y / spec.H) * box.height}px`;
    });
    svg.addEventListener("mouseleave", () => {
      tip.hidden = true;
      if (guide) guide.style.opacity = "0";
      if (dot) dot.style.opacity = "0";
    });
  });
}

/* Demand as a line: history solid, forecast dashed, deal months marked.
   Bars were the wrong shape for this -- demand over time is a series, and a
   reader comparing this month with last month wants a slope, not two towers. */
function demandChart(d) {
  const hist = d.demand || [];
  if (!hist.length) return `<p class="hint">No capacity requests recorded here.</p>`;
  const proj = d.projection || [];
  const all = hist.concat(proj.map((p) => ({ ...p, isProjection: true })));
  const W = 760, H = 250, L = 56, R = 18, T = 20, B = 44;
  const max = Math.max(...all.map((m) => m.cores), d.baselineCores, 1) * 1.18;
  const step = (W - L - R) / Math.max(all.length - 1, 1);
  const x = (i) => L + i * step;
  const y = (v) => H - B - (v / max) * (H - T - B);

  const id = `chart${++CHART_SEQ}`;
  CHART_DATA[id] = {
    W, H,
    points: all.map((m, i) => ({
      x: x(i), y: y(m.cores),
      html: `<b>${esc(m.month)}</b><br>${num(Math.round(m.cores))} CU`
        + (m.isProjection
            ? `<br><span class="t-mute">forecast — ${esc(d.model || "")}</span>`
            : `<br><span class="${m.isReal === false ? "t-warn" : "t-mute"}">${
                m.isReal === false
                  // Cores with no requests behind them is the first thing anyone
                  // asks about, and it had no answer. The generated months now
                  // carry a request count derived from the recorded
                  // cores-per-request, so both halves of the chart describe the
                  // same kind of place -- and the point says it is modelled.
                  ? `${m.tickets} request(s) · modelled — our records start ${esc(d.firstRecordedMonth || "later")}`
                  : `${m.tickets} recorded request(s)`}</span>`)
        + (m.events && m.events.length
            ? `<br><span class="t-warn">${m.isReal === false
                  ? "unusually large month"
                  : esc(m.events.map((e) => e.type).join(", "))}</span>` : ""),
    })),
  };

  const line = (pts) => pts.map((pt, i) => `${i ? "L" : "M"}${pt[0].toFixed(1)},${pt[1].toFixed(1)}`).join("");
  const histPts = hist.map((m, i) => [x(i), y(m.cores)]);
  const projPts = proj.length
    ? [[x(hist.length - 1), y(hist[hist.length - 1].cores)]]
        .concat(proj.map((m, i) => [x(hist.length + i), y(m.cores)]))
    : [];
  const everyNth = Math.ceil(all.length / 10);

  return `<svg data-chart="${id}" viewBox="0 0 ${W} ${H}" style="width:100%;height:auto"
      role="img" aria-label="Capacity requested per month">
    <text x="12" y="${(T + H - B) / 2}" font-size="10" fill="var(--ink-3)"
      transform="rotate(-90 12 ${(T + H - B) / 2})" text-anchor="middle">CU requested</text>
    <line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="var(--rule-strong)"/>
    ${d.baselineCores > 0 ? `
      <line x1="${L}" x2="${W - R}" y1="${y(d.baselineCores)}" y2="${y(d.baselineCores)}"
        stroke="var(--ink-3)" stroke-dasharray="4 3" opacity=".7"/>
      <text x="${W - R}" y="${y(d.baselineCores) - 4}" font-size="9.5" text-anchor="end"
        fill="var(--ink-3)">ordinary month ≈ ${num(Math.round(d.baselineCores))} CU</text>` : ""}
    ${d.realMonths != null && d.realMonths < hist.length ? `
      <line x1="${x(hist.length - d.realMonths)}" x2="${x(hist.length - d.realMonths)}"
        y1="${T}" y2="${H - B}" stroke="var(--rule-strong)" stroke-dasharray="2 3"/>
      <text x="${x(hist.length - d.realMonths) + 4}" y="${T + 10}" font-size="9"
        fill="var(--ink-3)">recorded from here</text>` : ""}
    ${projPts.length ? `
      <line x1="${x(hist.length - 1)}" x2="${x(hist.length - 1)}" y1="${T}" y2="${H - B}"
        stroke="var(--rule-strong)"/>
      <text x="${x(hist.length - 1) + 4}" y="${H - B + 14}" font-size="9.5"
        fill="var(--ink-3)">forecast</text>` : ""}
    <path d="${line(histPts)}" fill="none" stroke="var(--brand)" stroke-width="1.9"/>
    ${projPts.length ? `<path d="${line(projPts)}" fill="none" stroke="var(--brand)"
      stroke-width="1.9" stroke-dasharray="6 3"/>` : ""}
    ${hist.map((m, i) => m.eventDriven ? `
      <circle cx="${x(i)}" cy="${y(m.cores)}" r="4.5" fill="var(--warn)"/>` : "").join("")}
    ${all.map((m, i) => (i % everyNth === 0 || i === all.length - 1) ? `
      <text x="${x(i)}" y="${H - B + 26}" font-size="9" text-anchor="middle"
        fill="var(--ink-3)">${esc(m.month.slice(2))}</text>` : "").join("")}
    <line class="guide" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--ink-3)"
      stroke-dasharray="3 3" style="opacity:0;pointer-events:none"/>
    <circle class="hover-dot" r="4" fill="var(--brand)" style="opacity:0;pointer-events:none"/>
  </svg>`;
}

/* Both region graphs in one frame, drawn the way the Forecast tab draws its own.

   Review asked for the two region charts merged, then asked for the result to
   look like Forecast. Those are compatible: what Forecast does better is not
   styling but resolution. Utilisation is recorded once a day, and drawing it as
   five monthly averages turned a jagged series into a straight diagonal that
   implied a steadiness the readings do not have. It is now drawn per day, with
   the fitted projection, the error band the model actually earned on held-out
   data, and the crossing marker -- the same furniture as Forecast.

   Two units, so two axes: CU on the left, how-full on the right. Nothing is
   rescaled into a number nobody measured. Colour carries the series and dash
   carries the tense -- blue is CU, grey is utilisation, dashed is projected
   in both cases -- because a reader who learns the rule once should not have to
   relearn it halfway across the chart.

   The x axis is real dates rather than an index, which is what lets a daily
   series and a monthly one share it honestly: a monthly point sits at the
   middle of its month, where the aggregate belongs, not at its edge. */
function combinedRegionChart(d, f, place = "region") {
  const hist = d.demand || [];
  if (!hist.length) return `<p class="hint">No capacity requests recorded here.</p>`;
  const proj = d.projection || [];
  const fHist = (f && f.history) || [];
  const fProj = (f && f.projection) || [];
  const hasUtil = fHist.length > 1;

  const day = (s) => { const [y, m, dd] = s.split("-").map(Number); return Date.UTC(y, m - 1, dd); };
  const midMonth = (s) => { const [y, m] = s.split("-").map(Number); return Date.UTC(y, m - 1, 15); };

  const W = 920, H = 330, L = 66, R = 66, T = 42, B = 50;
  const stamps = [
    ...hist.map((m) => midMonth(m.month)), ...proj.map((m) => midMonth(m.month)),
    ...fHist.map((p) => day(p.date)), ...fProj.map((p) => day(p.date)),
  ];
  const t0 = Math.min(...stamps), t1 = Math.max(...stamps);
  const x = (t) => L + ((t - t0) / Math.max(t1 - t0, 1)) * (W - L - R);

  // Left: cores, from zero. A demand series that does not start at zero
  // overstates the gap between an ordinary month and a deal.
  const coreMax = Math.max(...hist.map((m) => m.cores), ...proj.map((m) => m.cores),
                           d.baselineCores, 1) * 1.18;
  const yC = (v) => (H - B) - (v / coreMax) * (H - T - B);

  // Right: utilisation, wide enough to hold the band and the safety line.
  const uVals = hasUtil
    ? [...fHist.map((p) => p.value), ...fProj.map((p) => p.upper), ...fProj.map((p) => p.lower),
       f.thresholdPct]
    : [d.thresholdPct || 85];
  const uLo = Math.floor(Math.min(...uVals) - 2), uHi = Math.ceil(Math.max(...uVals) + 2);
  const yU = (v) => (H - B) - ((v - uLo) / Math.max(uHi - uLo, 1)) * (H - T - B);

  const line = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const cHist = hist.map((m) => [x(midMonth(m.month)), yC(m.cores)]);
  const cProj = proj.length
    ? [[x(midMonth(hist[hist.length - 1].month)), yC(hist[hist.length - 1].cores)]]
        .concat(proj.map((m) => [x(midMonth(m.month)), yC(m.cores)]))
    : [];
  const uHist = fHist.map((p) => [x(day(p.date)), yU(p.value)]);
  const uProj = fProj.length && fHist.length
    ? [[x(day(fHist[fHist.length - 1].date)), yU(fHist[fHist.length - 1].value)]]
        .concat(fProj.map((p) => [x(day(p.date)), yU(p.value)]))
    : [];
  const band = fProj.length
    ? "M" + fProj.map((p) => `${x(day(p.date)).toFixed(1)},${yU(p.upper).toFixed(1)}`).join("L")
      + "L" + fProj.slice().reverse().map((p) =>
          `${x(day(p.date)).toFixed(1)},${yU(p.lower).toFixed(1)}`).join("L") + "Z"
    : "";

  /* Hover. One entry per recorded day, plus the months that predate the
     utilisation record so the left of the chart is not dead to the cursor.
     Each carries whatever is true at that date: a reading, a projection with
     its range, and the CU asked for in the month it falls in. */
  const coresBy = Object.fromEntries(
    hist.map((m) => [m.month, m]).concat(proj.map((m) => [m.month, { ...m, isProjection: true }])));
  const monthOf = (iso) => iso.slice(0, 7);
  const coresLine = (month) => {
    const m = coresBy[month];
    if (!m) return `<span class="t-mute">no request record in ${esc(month)}</span>`;
    return `${num(Math.round(m.cores))} CU requested in ${esc(month)}`
      + (m.isProjection
          ? `<br><span class="t-mute">forecast — ${esc(d.model || "")}</span>`
          : `<br><span class="${m.isReal === false ? "t-warn" : "t-mute"}">${
              m.isReal === false
                ? `${m.tickets} request(s) · modelled`
                : `${m.tickets} recorded request(s)`}</span>`)
      + (m.events && m.events.length && m.isReal !== false
          ? `<br><span class="t-warn">${esc(m.events.map((e) => e.type).join(", "))}</span>` : "");
  };

  const id = `chart${++CHART_SEQ}`;
  const pts = [];
  fHist.forEach((p) => pts.push({
    x: x(day(p.date)), y: yU(p.value),
    html: `<b>${esc(p.date)}</b><br>${p.value.toFixed(1)}% full`
      + `<br><span class="t-mute">measured</span><br>${coresLine(monthOf(p.date))}`,
  }));
  fProj.forEach((p) => pts.push({
    x: x(day(p.date)), y: yU(p.value),
    html: `<b>${esc(p.date)}</b><br>${p.value.toFixed(1)}% full`
      + `<br><span class="t-mute">projected — could be ${p.lower.toFixed(1)}% to ${p.upper.toFixed(1)}%</span>`
      + `<br>${coresLine(monthOf(p.date))}`,
  }));
  // Months with no utilisation record yet: keep the cursor useful over there.
  const covered = new Set([...fHist, ...fProj].map((p) => monthOf(p.date)));
  hist.concat(proj).forEach((m) => {
    if (covered.has(m.month)) return;
    pts.push({ x: x(midMonth(m.month)), y: yC(m.cores),
               html: `<b>${esc(m.month)}</b><br>${coresLine(m.month)}`
                 + `<br><span class="t-mute">utilisation not recorded this month</span>` });
  });
  pts.sort((a, b) => a.x - b.x);
  CHART_DATA[id] = { W, H, points: pts };

  // Gridlines are evenly spaced and labelled on both scales, the only honest
  // way to rule a frame carrying two units.
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((r) => ({
    y: (H - B) - r * (H - T - B),
    cores: Math.round(r * coreMax),
    util: uLo + r * (uHi - uLo),
  }));

  // Roughly eight date labels, snapped to month starts.
  const marks = [];
  for (let t = t0; t <= t1; ) {
    const dt = new Date(t);
    marks.push(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), 1));
    t = Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth() + 1, 1);
  }
  const everyNth = Math.max(1, Math.ceil(marks.length / 8));
  const label = (t) => {
    const dt = new Date(t);
    return `${String(dt.getUTCFullYear()).slice(2)}-${String(dt.getUTCMonth() + 1).padStart(2, "0")}`;
  };

  const cut = hasUtil && fProj.length ? x(day(fProj[0].date)) : null;
  const crossX = hasUtil && f.crossingDate ? x(day(f.crossingDate)) : null;
  const utilStart = hasUtil ? x(day(fHist[0].date)) : null;

  return `<div class="legend">
    <span><i class="ln demand"></i>CU requested — what customers asked for (left axis)</span>
    ${proj.length ? `<span><i class="ln demand-proj"></i>Projected demand</span>` : ""}
    ${hasUtil ? `<span><i class="ln util"></i>How full the ${esc(place)} ran — one reading per day (right axis)</span>` : ""}
    ${hasUtil && fProj.length ? `<span><i class="ln util-proj"></i>Projected utilisation${f.model ? ` — ${esc(f.model)}` : ""}</span>` : ""}
    ${band ? `<span><i class="ln util-band"></i>Range the forecast could be out by</span>` : ""}
    ${hasUtil ? `<span><i class="ln limit"></i>Safety threshold ${pct(f.thresholdPct, 1)}</span>` : ""}
    ${d.baselineCores > 0 ? `<span><i class="ln baseline"></i>Ordinary month ≈ ${num(Math.round(d.baselineCores))} CU</span>` : ""}
    <span><i class="dot event"></i>Month containing a deal-sized request</span>
  </div>
  <svg data-chart="${id}" class="chart" viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Capacity requested per month and daily utilisation for ${esc(d.id || "")}">
    ${ticks.map((t) => `
      <line x1="${L}" x2="${W - R}" y1="${t.y.toFixed(1)}" y2="${t.y.toFixed(1)}" stroke="var(--rule)"/>
      <text x="${L - 8}" y="${(t.y + 4).toFixed(1)}" font-size="9.5" text-anchor="end"
        fill="var(--brand)">${num(t.cores)}</text>
      ${hasUtil ? `<text x="${W - R + 8}" y="${(t.y + 4).toFixed(1)}" font-size="9.5"
        text-anchor="start" fill="var(--ink-2)">${t.util.toFixed(0)}%</text>` : ""}`).join("")}

    <text x="14" y="${T + (H - T - B) / 2}" font-size="10" fill="var(--brand)"
      transform="rotate(-90 14 ${T + (H - T - B) / 2})" text-anchor="middle">CU requested</text>
    ${hasUtil ? `<text x="${W - 12}" y="${T + (H - T - B) / 2}" font-size="10" fill="var(--ink-2)"
      transform="rotate(90 ${W - 12} ${T + (H - T - B) / 2})" text-anchor="middle">how full (%)</text>` : ""}

    ${d.baselineCores > 0 ? `
      <line x1="${L}" x2="${W - R}" y1="${yC(d.baselineCores).toFixed(1)}"
        y2="${yC(d.baselineCores).toFixed(1)}" stroke="var(--brand)"
        stroke-dasharray="2 4" opacity=".45"/>` : ""}

    ${hasUtil ? `
      <line x1="${L}" x2="${W - R}" y1="${yU(f.thresholdPct).toFixed(1)}"
        y2="${yU(f.thresholdPct).toFixed(1)}" stroke="var(--bad)" stroke-dasharray="5 4"/>
      <text x="${W - R - 2}" y="${(yU(f.thresholdPct) - 5).toFixed(1)}" font-size="9.5"
        text-anchor="end" fill="var(--bad)">safety threshold ${pct(f.thresholdPct, 1)}</text>` : ""}

    ${band ? `<path d="${band}" fill="var(--ink-2)" opacity=".15"/>` : ""}
    ${uHist.length > 1 ? `<path d="${line(uHist)}" fill="none" stroke="var(--ink-2)" stroke-width="1.3"/>` : ""}
    ${uProj.length > 1 ? `<path d="${line(uProj)}" fill="none" stroke="var(--ink-2)"
      stroke-width="1.5" stroke-dasharray="6 3"/>` : ""}

    <path d="${line(cHist)}" fill="none" stroke="var(--brand)" stroke-width="1.9"/>
    ${cProj.length ? `<path d="${line(cProj)}" fill="none" stroke="var(--brand)"
      stroke-width="1.9" stroke-dasharray="6 3"/>` : ""}
    ${hist.map((m) => m.eventDriven && m.isReal !== false ? `
      <circle cx="${x(midMonth(m.month))}" cy="${yC(m.cores).toFixed(1)}" r="4.5"
        fill="var(--warn)"/>` : "").join("")}

    ${utilStart != null && utilStart > L + 2 ? `
      <line x1="${utilStart.toFixed(1)}" x2="${utilStart.toFixed(1)}" y1="${T}" y2="${H - B}"
        stroke="var(--ink-3)" stroke-dasharray="1 4" opacity=".8"/>
      <text x="${(utilStart - 4).toFixed(1)}" y="${T + 10}" font-size="9" text-anchor="end"
        fill="var(--ink-2)" stroke="var(--card)" stroke-width="3" paint-order="stroke"
      >utilisation recorded from here</text>` : ""}
    ${cut != null ? `
      <line x1="${cut.toFixed(1)}" x2="${cut.toFixed(1)}" y1="${T}" y2="${H - B}"
        stroke="var(--rule-strong)"/>
      ${/* A region well up its range puts the daily line right under this
            caption, so it carries a halo like the crossing marker. */ ""}
      <text x="${(cut + 4).toFixed(1)}" y="${T + 10}" font-size="9.5"
        fill="var(--ink-3)" stroke="var(--card)" stroke-width="3" paint-order="stroke"
      >forecast from here</text>` : ""}
    ${crossX != null ? `
      <circle cx="${crossX.toFixed(1)}" cy="${yU(f.thresholdPct).toFixed(1)}" r="4" fill="var(--bad)"/>
      ${/* Above the dot, not below: below is where the rising line is. The halo
            keeps it legible where the band passes behind it. */ ""}
      <text x="${crossX.toFixed(1)}" y="${(yU(f.thresholdPct) - 10).toFixed(1)}" font-size="9"
        text-anchor="middle" fill="var(--bad)"
        stroke="var(--card)" stroke-width="3" paint-order="stroke"
      >crosses ${esc(f.crossingDate)}</text>` : ""}

    ${/* Every Nth month, plus the last -- but drop the last if the tick before it
          is close enough that the two labels would print over each other. */ ""}
    ${marks.map((t, i) => {
      const last = i === marks.length - 1;
      if (i % everyNth !== 0 && !last) return "";
      if (last && i % everyNth !== 0) {
        const prev = marks[Math.floor((marks.length - 1) / everyNth) * everyNth];
        if (x(t) - x(prev) < 34) return "";
      }
      return `<text x="${x(t).toFixed(1)}" y="${H - B + 30}" font-size="9" text-anchor="middle"
        fill="var(--ink-3)">${label(t)}</text>`;
    }).join("")}

    <line class="guide" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--ink-3)"
      stroke-dasharray="3 3" style="opacity:0;pointer-events:none"/>
    <circle class="hover-dot" r="4" fill="var(--ink-2)" style="opacity:0;pointer-events:none"/>
  </svg>`;
}

async function customerDemandPanel(subscriptionId) {
  let d;
  try {
    d = await get(`/api/demand/customer/${encodeURIComponent(subscriptionId)}`);
  } catch (err) {
    return "";
  }
  const proj = d.projection || [];
  return panel("Demand — capacity this customer asks for, per month", `
    ${d.historyIsMostlySynthetic ? `<p class="error" style="margin:0 0 1rem">
      <b>Most of this history is generated.</b> The extract holds
      ${d.realMonths} real month(s) for this customer out of ${d.demand.length} shown —
      two or three tickets cannot carry a forecast, so the remainder was
      synthesised to give the series a shape. Months drawn from the extract are
      marked <b>real</b> below. Treat the pattern as illustrative and the
      projection as a demonstration of method, not a prediction about this
      account.</p>` : ""}
    <div style="position:relative">${demandChart(d)}</div>
    ${proj.length ? `<p style="margin:.75rem 0 0;font-size:.88rem">
      <b>Next three months</b> on this series:
      ${proj.map((x) => `${esc(x.month)} ≈ ${num(Math.round(x.cores))} CU`).join(" · ")}
      <span style="color:var(--ink-3)"> — model: ${esc(d.model)}</span></p>` : ""}
    ${d.note ? `<p style="color:var(--ink-3);font-size:.8rem;margin:.4rem 0 0">${esc(d.note)}</p>` : ""}
    <p style="color:var(--ink-3);font-size:.78rem;margin:.5rem 0 0">
      An ordinary month for this account is about
      ${num(Math.round(d.baselineCores))} CU. This series is held in its own
      table and is never counted into exposure, failure counts or CU pending —
      those come only from the recorded incidents.
    </p>`);
}

/* The capacities inside a facility: what is actually there, and how each one is.

   The question review kept returning to and the product could not answer -- "hey,
   these are the SKUs there in this capacity pool, this is the capacity available,
   this is what we don't have". Until the capacity tables existed a site was its
   region's units divided by ten, so there was nothing to list.

   Two columns here are not about fullness, which is the point. Incidents per node
   says whether the hardware under a capacity is behaving; free viewers says
   whether the SKU is above the licence cliff. A capacity can be comfortable on
   utilisation and wrong on both. */

function skuBar(mix, total) {
  const order = ["F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256",
                 "F512", "F1024", "F2048"];
  const present = order.filter((s) => mix[s]);
  if (!present.length) return "";
  return `<div class="sku-mix">${present.map((s) => `
    <span class="sku-chip${s === "F64" || order.indexOf(s) > order.indexOf("F64")
      ? " free-ok" : ""}" title="${mix[s]} × ${s}">${s}<b>×${mix[s]}</b></span>`).join("")}
    <span class="t3">${num(total)} units in ${present.length} SKU size(s)</span></div>`;
}


/* Throttling stage as the reader should see it. Fabric's own ladder, so the
   colour follows the policy rather than a judgement made here: a delay is a
   nuisance, a rejection is users being refused. */
const STAGE_TONE = {
  none: "", interactive_delay: "warn",
  interactive_rejection: "bad", background_rejection: "bad",
};
function stageCell(stage, label) {
  if (!stage || stage === "none") return `<span class="t3">none</span>`;
  return `<span class="pill ${STAGE_TONE[stage] || ""}">${esc(label || stage)}</span>`;
}

function capacityRows(d) {
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>Size</th><th class="n">Wants</th><th class="n">Workspaces</th>
      <th>What is happening</th><th class="n">How often</th><th class="n">Queries refused</th>
      <th>Free viewers</th>
    </tr></thead>
    <tbody>${d.capacities.map((c) => {
      const bad = c.throttledDays > 0;
      const rejected = c.interactiveRejected + c.backgroundRejected;
      const wants = c.capacityUnits * c.meanUtilisationPct / 100;
      const p = bad ? (PROBLEM[c.worstStage] || PROBLEM.none)
                    : { text: "Fine", tone: "", why: "not delaying or refusing anything" };
      return `<tr class="${bad ? "row-danger" : ""}">
        <td><a href="/capacity/${encodeURIComponent(c.capacityId)}">${esc(c.capacityId)}</a></td>
        <td><b>${esc(c.fabricSku)}</b><span class="t3">${num(c.capacityUnits)} CU</span></td>
        <td class="n" title="averaged over ${c.windowDays} days; peaked at ${Math.round(c.peakUtilisationPct)}% of its CU">
          <b class="${bad ? "t-bad" : ""}">${wants.toFixed(1)} CU</b>
          <span class="t3">has ${num(c.capacityUnits)}</span></td>
        <td class="n">${num(c.workspaces)}</td>
        <td data-info="${esc(p.why)}" title="${esc(p.why)}">${bad
          ? `<span class="pill ${p.tone}">${p.text}</span>`
          : `<span class="t3">fine</span>`}</td>
        <td class="n ${bad ? "t-bad" : ""}">${c.throttledDays
          ? `${c.throttledDays} of ${c.windowDays} days` : "—"}</td>
        <td class="n ${rejected ? "t-bad" : ""}">${rejected ? num(rejected) : "—"}</td>
        <td>${c.supportsFreeViewers
          ? `<span class="pill good">F64+</span>`
          : `<span class="pill wash" title="Below F64: each Power BI viewer needs Pro or PPU">Pro needed</span>`}</td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
}

async function capacityPanel(scope, id) {
  let d;
  try {
    d = await get(`/api/capacities?${scope}=${encodeURIComponent(id)}`);
  } catch (err) {
    return "";
  }
  if (!d.count) return "";

  return panel(`Fabric capacities here — ${d.count} on ${Object.keys(d.skuMix).length} SKU size(s)`, `
    ${skuBar(d.skuMix, d.totalCapacityUnits)}
    ${capacityRows(d)}
    <p style="background:var(--page);border-left:3px solid var(--brand);
       padding:.7rem .9rem;margin:.9rem 0 0;font-size:.88rem">
      <b>In plain terms:</b> this holds <b>${num(d.totalCapacityUnits)} Capacity Units</b>
      across <b>${d.count} Fabric capacit${d.count === 1 ? "y" : "ies"}</b>, using
      <b>${pct(d.meanUtilisationPct, 0)}</b> of them on average.
      ${d.throttling
        ? `<b class="t-bad">${num(d.throttling)}</b> of them throttled at some point —
           Fabric absorbs ten minutes of overage, then delays interactive jobs by
           20 seconds, then rejects them at an hour, then rejects everything at 24.`
        : `None of them throttled. Peaks above 100% are <i>bursting</i>, which Fabric
           smooths over future timepoints and is not by itself a fault.`}
      ${d.freeViewerCapable
        ? `${d.freeViewerCapable} ${d.freeViewerCapable === 1 ? "is" : "are"} F64 or larger,
           so Power BI content on ${d.freeViewerCapable === 1 ? "it" : "them"} reads on a free licence.`
        : `None is F64 or larger, so every user viewing Power BI content here needs a
           Pro or PPU licence — a cost no utilisation figure shows.`}
    </p>
    <p style="color:var(--ink-3);font-size:.78rem;margin:.5rem 0 0">${esc(d.note)}</p>`);
}

/* One capacity pool's forecast, as its own panel.

   The Forecast tab left the sidebar and the forecasting moved to the thing
   being forecast. That is not a relocation of a chart: a region-level crossing
   date says a geography is filling up, which nobody can act on, while this says
   which building fills first and by when -- and scaling an F SKU is a per-site
   decision made immediately.

   The evidence that made the old tab trustworthy comes with it rather than
   being left behind. The headline is the crossing date; how the model was
   chosen, what it scored and what it was beaten by sits in a fold underneath,
   so the page stays readable for someone who wants the date and complete for
   someone who wants to challenge it. */
async function forecastPanel(datacentreId) {
  let f;
  try {
    f = await get(`/api/forecast/datacentre/${encodeURIComponent(datacentreId)}`);
  } catch (err) {
    return "";
  }
  if (!(f.history || []).length) {
    return panel("Forecast", `<p class="empty" style="padding:0">${
      esc(f.note || "No utilisation history is recorded for this capacity pool.")}</p>`);
  }

  const last = f.history[f.history.length - 1];
  const heading = f.alreadyBreached
    ? (f.saturationDate ? `already over its line, full by ${f.saturationDate}`
                        : "already over its line")
    : (f.crossingDate ? `projected to cross its line on ${f.crossingDate}`
                      : "stays within its safety line");

  return panel(`Forecast — ${heading}`, `
    ${f.note ? `<p class="error" style="margin:0 0 1rem">${esc(f.note)}</p>` : ""}
    <div style="position:relative">${forecastChart(f, "capacity pool")}</div>

    <p style="background:var(--page);border-left:3px solid var(--brand);
       padding:.7rem .9rem;margin:.75rem 0 0;font-size:.88rem">
      <b>In plain terms:</b> ${esc(f.datacentre)} was
      <b>${pct(last.value, 1)} full</b> on ${esc(last.date)},
      ${f.alreadyBreached
        ? `already past its own ${pct(f.thresholdPct)} safety line`
        : `against its own ${pct(f.thresholdPct)} safety line`}.
      ${f.alreadyBreached
        ? (f.saturationDate
            ? `On this trend it has <b>no capacity left at all by ${esc(f.saturationDate)}</b>.`
            : `It is not projected to fill completely within the next ${f.projectionDays} days.`)
        : (f.crossingDate
            ? `On this trend it crosses that line on <b>${esc(f.crossingDate)}</b>${
                f.saturationDate ? ` and is completely full by ${esc(f.saturationDate)}${
                  f.saturationBeyondChart ? " — past the right-hand edge of this chart" : ""}` : ""}.`
            : `It is not projected to cross that line within the next ${f.projectionDays} days.`)}
      The line is this building's own utilisation, not its region's — the two can
      point in different directions, and that is the whole reason for forecasting
      here rather than a level up.
    </p>

    ${f.extrapolatedBeyondHistory ? `<p style="color:var(--warn-ink,var(--ink-2));font-size:.8rem;
       margin:.4rem 0 0;border-left:3px solid var(--warn);padding-left:.7rem">
      This projection runs <b>${f.plottedDays} days</b> forward on
      <b>${f.history.length} days</b> of history. It is extrapolating past the window
      it was fitted on, so treat the date as a planning prompt rather than a
      commitment — the further right it sits, the weaker it is.
    </p>` : ""}

    <p style="color:var(--ink-3);font-size:.78rem;margin:.4rem 0 0">
      This line is CU consumed over CU held, across the capacities in the building.
      <b>Above 100% is bursting, not an error</b> — a capacity can draw more CU than
      it holds and Fabric smooths the overage over future timepoints, which is why a
      site can sit past 100% without anything having failed. Note this is a different
      measurement from the utilisation in the site table above, which is capped at
      the CU deployed; the two agree everywhere a building is not bursting.
    </p>

    <div class="kpis" style="margin-top:1rem">
      ${kpi("Model used", esc(f.model),
            f.forced ? "set for every forecast in the product"
                     : (f.beatsNaive ? "beat the naive baseline" : "nothing beat naive"),
            f.forced ? "warn" : (f.beatsNaive ? "good" : "warn"),
            f.forced
              ? `This model was set rather than chosen by measured accuracy. For `
                + `${esc(f.datacentre)} the backtest ranked `
                + `${esc((f.forced.wouldHaveChosen) || "another model")} first.`
              : "Chosen by backtest on data the model never saw, not selected by hand.")}
      ${f.alreadyBreached
        ? kpi("Full by", f.saturationDate || "—",
              f.saturationDate ? "projected to reach 100% utilisation"
                               : `not projected to fill within ${f.projectionDays} days`,
              f.saturationDate ? "bad" : "",
              "This capacity pool is already past its safety line, so a crossing date is "
              + "history. What matters is when it runs out completely.")
        : kpi("Crossing date", f.crossingDate || "—",
              f.crossingEarliest ? `between ${f.crossingEarliest} and ${f.crossingLatest}`
                                 : `not projected within ${f.projectionDays} days`,
              f.crossingDate ? "bad" : "good",
              "First projected day past this building's own safety line. The range comes "
              + "from the error the model made on data it never saw.")}
      ${/* scoreFor(f) finds the row for the model actually drawing the line.
             Reading the first-ranked row instead would give the backtest winner,
             which is a different model wherever the choice is forced -- and the
             page would then print one model's accuracy under another's name. */ ""}
      ${kpi("Forecast error (MAPE)", scoreFor(f) ? `${scoreFor(f).mape.toFixed(2)}%` : "—",
            scoreFor(f) ? `typically ±${(scoreFor(f).mape / 100 * last.value).toFixed(1)} points out, `
                + `${f.horizonDays} days ahead` : "", "ink",
            `How wrong this model was when tested. It was fitted on part of this `
            + `building's history, asked to predict the ${f.horizonDays} days it had `
            + `not seen, and marked against what actually happened — repeated over `
            + `${scoreFor(f) ? scoreFor(f).folds : f.folds} stretches of the record. `
            + `Lower is better, and it is a percentage of the reading rather than `
            + `percentage points.`)}
      ${kpi("Skill vs naive", scoreFor(f) ? `${scoreFor(f).skillVsNaive > 0 ? "+" : ""}${scoreFor(f).skillVsNaive.toFixed(0)}%` : "—",
            scoreFor(f) ? (scoreFor(f).skillVsNaive > 0
                  ? `${scoreFor(f).skillVsNaive.toFixed(0)}% more accurate than assuming nothing changes`
                  : "worse than assuming nothing changes")
              : "", f.beatsNaive ? "good" : "bad",
            `Whether the modelling was worth doing at all. The benchmark is the simplest `
            + `forecast there is — carry the last reading forward, no model. 0% means the `
            + `modelling added nothing; below 0% it did worse than doing nothing, and that `
            + `candidate is rejected rather than displayed.`)}
    </div>

    <details style="margin-top:1rem"><summary style="cursor:pointer;color:var(--brand);font-size:.88rem">
      How this was worked out — all ${(f.scores || []).length} models scored</summary>
      <p style="color:var(--ink-2);font-size:.83rem;margin:.6rem 0 .2rem">
        Every candidate is backtested on this capacity pool's own series over
        ${f.folds} rolling folds at a ${f.horizonDays}-day horizon, on data it was
        never shown. The lowest RMSE wins; a model that failed to fit on any fold
        is listed but cannot win, because averaging only the stretches it managed
        would rank it against models that sat the whole exam.
      </p>
      <div class="scroll-x" style="margin-top:.5rem"><table>
        <thead><tr><th>Model</th>
          ${th("MAPE", "Mean absolute percentage error — on average, how far each "
             + "prediction landed from what actually happened, as a percentage of the "
             + "reading rather than in percentage points. Lower is better.", "n")}
          ${th("RMSE", "Root mean squared error, in percentage points. It squares each "
             + "miss before averaging, so one large error counts for far more than "
             + "several small ones — which is why the winner is chosen on this and not "
             + "on MAPE. In capacity planning it is the single big miss that causes a "
             + "denial.", "n")}
          ${th("Skill vs naive", "The share of the naive benchmark's error this model "
             + "removed. 0% means the modelling added nothing.", "n")}
          ${th("Folds", "How many separate stretches of the history the model was tested "
             + "on. A model that failed on some is listed with fewer and cannot win.", "n")}
        </tr></thead>
        <tbody>${(f.scores || []).map((sc, i) => `<tr>
          <td>${i === 0 ? `<b>${esc(sc.model)}</b>` : esc(sc.model)}</td>
          <td class="n">${sc.mape.toFixed(2)}%</td>
          <td class="n">${sc.rmse.toFixed(2)}</td>
          <td class="n" style="color:${sc.skillVsNaive > 0 ? "var(--good)" : "var(--bad)"}">
            ${sc.skillVsNaive > 0 ? "+" : ""}${sc.skillVsNaive.toFixed(1)}%</td>
          <td class="n">${sc.folds}</td>
        </tr>`).join("")}</tbody></table></div>
      <p class="prov" style="margin-top:.6rem">${esc(f.provenance || "")}</p>
    </details>`);
}

async function demandPanels(scope, id) {
  let d;
  try {
    d = await get(`/api/demand/${scope}/${encodeURIComponent(id)}`);
  } catch (err) {
    return panel("Demand", `<p class="error">Could not load demand for ${esc(id)}.</p>`);
  }
  /* The daily utilisation series and its projection, for regions only -- the
     monthly averages this endpoint returns are enough to say where the region
     sits but not to draw it, since averaging a day-by-day series into five
     points turns a jagged record into a straight line. `full` asks for the
     whole year rather than the Forecast tab's trim: this chart also carries
     eighteen months of demand, so a year ahead is a minority of its width.
     A failure here costs the utilisation half, not the panel. */
  let f = null;
  if (scope === "region" || scope === "datacentre") {
    // A capacity pool has a series of its own now -- consumed CU seconds over
    // available, summed across the capacities in the building. The guard used
    // to stop at regions because the site endpoint returned nothing to draw.
    const url = scope === "region"
      ? `/api/forecast/${encodeURIComponent(id)}?full=true`
      : `/api/forecast/datacentre/${encodeURIComponent(id)}?full=true`;
    try {
      f = await get(url);
    } catch (err) {
      f = null;
    }
  }
  const place = scope === "datacentre" ? "capacity pool" : "region";
  const spikes = (d.demand || []).filter((m) => m.eventDriven);
  // Two different things wear the same amber. A recorded month is amber because
  // an event names the ticket in it; a generated month is amber because it is
  // several times an ordinary month. Only the first is evidence of a deal.
  const attributed = spikes.filter((m) => m.isReal !== false);
  const flagged = spikes.filter((m) => m.isReal === false);
  const peak = (d.demand || []).reduce((a, b) => (b.cores > (a?.cores ?? -1) ? b : a), null);

  /* Quote the daily record the chart actually draws, not the monthly averages
     from the demand endpoint. Both are true, but a caption reading 72.8% beside
     a line ending at 75% is the kind of two-numbers-one-question split this
     project keeps finding, and there is no reason to introduce another. */
  const fh = (f && f.history) || [];
  const rich = fh.length > 1;
  const util = d.thresholdSeries || [];
  const lastU = rich ? fh[fh.length - 1] : null;
  const firstU = rich ? fh[0] : null;
  const line = rich ? f.thresholdPct : d.thresholdPct;
  const delta = lastU ? lastU.value - line : null;
  // Direction of travel over the recorded window, so the prose can say whether
  // the region is walking towards its line or away from it.
  const drift = rich ? lastU.value - firstU.value : null;
  const showChart = rich || util.length;

  return panel(showChart
      ? "Demand and utilisation — what was asked for, and how full it ran"
      : "Demand — CU requested per month", `
    <div style="position:relative">${
      showChart ? combinedRegionChart(d, f, place) : demandChart(d)}</div>
    <p style="background:var(--page);border-left:3px solid var(--brand);
       padding:.7rem .9rem;margin:.75rem 0 0;font-size:.88rem">
      <b>In plain terms:</b> ${showChart ? `this chart carries two different
      measurements, which is why it has two scales.
      The <b style="color:var(--brand)">blue line</b> is what customers
      <b>asked for</b> — CU per month, read on the left. The
      <b style="color:var(--ink-2)">grey line</b> is how <b>full this ${place}
      actually ran</b> — a percentage, one reading per day, read on the right.
      One is demand arriving, the other is the room left to absorb it.
      Dashed means projected, on either line. ` : ""}An ordinary month here is about
      <b>${num(Math.round(d.baselineCores))} CU</b>.
      ${spikes.length
        ? `${spikes.length} month(s) ran well above that.
           ${attributed.length
             ? `${attributed.length} of them ${attributed.length === 1 ? "is" : "are"}
                tied to a recorded business event — ${esc(attributed.map((m) =>
                  `${m.month} (${(m.events[0] || {}).type || "event"})`).join(", "))}.`
             : ""}
           ${flagged.length
             ? `${flagged.length} ${flagged.length === 1 ? "falls" : "fall"} in the
                generated part of the history and ${flagged.length === 1 ? "is" : "are"}
                marked deal-sized on size alone, not on a recorded event —
                ${esc(flagged.map((m) => m.month).join(", "))}.`
             : ""}
           ${peak ? `The largest was <b>${num(Math.round(peak.cores))} CU</b> in ${esc(peak.month)}.` : ""}
           These months stay in the forecast: a signed deal is part of what this
           place asks for, and removing them would project somewhere that never
           signs anything.`
        : `No month here was driven by a recorded business event.`}
      ${rich ? `<br><br>On the utilisation side, this ${place} was
        <b>${lastU.value.toFixed(1)}% full</b> on ${esc(lastU.date)} against its own
        <b>${pct(line, 1)}</b> safety threshold —
        ${delta >= 0
          ? `<b style="color:var(--bad)">${delta.toFixed(1)} points past it</b>`
          : `<b style="color:var(--good)">${Math.abs(delta).toFixed(1)} points still in hand</b>`}.
        ${Math.abs(drift) >= 0.5
          ? `Across the ${fh.length} days on record it has moved
             ${drift > 0
               ? `<b>up ${drift.toFixed(1)} points</b>, so it is walking towards the line, not sitting still`
               : `<b>down ${Math.abs(drift).toFixed(1)} points</b>, so it is moving away from the line`}`
          : `Across the ${fh.length} days on record it has held roughly level`},
        and on that trend it
        ${f.alreadyBreached
          ? (f.saturationDate
              ? `has <b>no capacity left at all by ${esc(f.saturationDate)}</b>`
              : `is already past the line`)
          : (f.crossingDate
              ? `<b>crosses the line on ${esc(f.crossingDate)}</b>${
                  f.saturationDate ? ` and is full by ${esc(f.saturationDate)}` : ""}`
              : `does not cross the line within the year drawn`)}.
        ${scope === "datacentre"
          ? `That threshold is this building's own, which is where a threshold is
             actually held — the region's is derived from these.`
          : `That threshold is this region's own — derived from the thresholds its data
             centres actually hold, not one figure applied everywhere.`}` : ""}
    </p>
    <p style="color:var(--ink-3);font-size:.78rem;margin:.5rem 0 0">
      The blue line is capacity asked for, not capacity granted. Amber points mark a
      month holding a request linked to a business event, and the link is recorded
      on the event rather than inferred from timing.
      ${rich ? `The two lines do not cover the same span: requests go back to the
      start of the extract, utilisation is only recorded from ${esc(firstU.date)},
      so the grey line starts there. Nothing to the right of the solid divider has
      happened yet — the dashed grey line is ${esc(f.model || "a model")} fitted to
      the readings on its left, the shaded band is the error that model actually
      made on data it was not shown, and the dashed blue line is a separate monthly
      fit${d.model ? ` (${esc(d.model)})` : ""}.
      ${f.extrapolatedBeyondHistory ? `<b>The projection runs ${f.plottedDays} days
      forward on ${fh.length} days of history</b>, so treat dates late in it as a
      prompt to plan rather than a measurement.` : ""}` : ""}
      ${scope === "datacentre" && d.thresholdSeriesProvenance
        ? `<br><b>Where this building's utilisation comes from:</b> ${esc(d.thresholdSeriesProvenance)}`
        : ""}
    </p>`)
  + (!d.thresholdSeries?.length && d.thresholdSeriesNote
      ? `<p class="hint" style="margin:.5rem 0 1rem">${esc(d.thresholdSeriesNote)}</p>`
      : "");
}

/* ==================================================================== 2/11 */
/* Fleet map                                                                 */

/* The landing surface review asked for: "you yourself think you're a capacity
   manager, you are sitting in front of all your capacity pools, you have your map
   in front". The complaint it answers is not that the numbers were wrong but
   that reaching them took four tabs -- "I have to do so many clicks to get me an
   answer. By looking at it, you should be able to get those insights."

   So a marker carries what would otherwise be four screens: how full the region
   is, whether it crosses its safety line and when, what has to be scaled,
   and what Fabric will not run there. Selecting one opens the detail beside the
   map rather than navigating away, because comparing two regions means seeing
   the second without losing the first.

   Coastlines are in world.js. The viewBox is degrees, so a region sits at
   (lon + 180, 90 - lat) with no projection code in between. */

/* Markers are placed by real coordinates, and real coordinates collide: eastus
   and eastus2 are both in Virginia, and three European regions sit inside four
   degrees of each other. Nudging them apart is a lie about geography, but a
   small and legible one -- the alternative is a single blob that cannot be
   clicked, which is a worse lie about the fleet. Spreading is deterministic so
   a region does not move between renders. */
function spreadMarkers(points, minGap) {
  const placed = [];
  for (const p of points) {
    let { mx, my } = p;
    for (let attempt = 0; attempt < 24; attempt++) {
      const hit = placed.find((q) => Math.hypot(q.mx - mx, q.my - my) < minGap);
      if (!hit) break;
      // Walk outward along the line away from whatever it collided with,
      // falling back to a fixed bearing when two points are exactly on top.
      const dx = mx - hit.mx, dy = my - hit.my;
      const len = Math.hypot(dx, dy) || 1;
      const push = (minGap - len) + 0.35;
      mx += (dx / len || 0.7) * push;
      my += (dy / len || -0.7) * push;
    }
    placed.push({ ...p, mx, my, moved: mx !== p.mx || my !== p.my });
  }
  return placed;
}

/* Marker colour is the region's state against its own safety line, matching the
   pills everywhere else so the map does not invent a third vocabulary. */
function mapTone(p) {
  if (p.status === "breached") return "bad";
  // "due_now", not "due": module1 emits due_now and this compared against a
  // string nothing ever sends, so the amber band was carried entirely by
  // "overdue" -- which only fired because hardware lead times outran the
  // days left before a crossing.
  if (p.status === "overdue" || p.status === "due_now") return "warn";
  return "good";
}

const MAP_TONE_FILL = { bad: "var(--bad)", warn: "var(--warn)", good: "var(--good)" };

/* Where a region's capacity pools are drawn, and the honest caveat on it.

   Region coordinates are real -- Azure publishes them and they are marked REAL
   in the model. Individual capacity pool locations are not published, by design,
   so there is nothing to plot. Review asked to see the sites on the map after
   clicking a region, and the truthful way is to anchor them on the region and
   say plainly that the ring is a layout rather than a location. Ten invented
   pins on a real map, at the same fidelity as the region markers, would be a
   figure someone screenshots and quotes. */
const SITE_RING_DEGREES = 3.4;

function siteRing(cx, cy, n) {
  if (n === 1) return [{ x: cx, y: cy - SITE_RING_DEGREES }];
  return Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    return { x: cx + Math.cos(a) * SITE_RING_DEGREES,
             y: cy + Math.sin(a) * SITE_RING_DEGREES * 0.72 };
  });
}

/* The fleet, or one region with its capacity pools.

   `focus` is null for the world view. Given a region and its sites the viewBox
   windows onto that region -- the projection is already in degrees, so zooming
   is arithmetic on the box and needs no projection code -- and the sites are
   drawn around it, each coloured by its own safety line rather than by the
   region's, because the threshold lives on the capacity pool. */
function fleetMap(d, focus) {
  const W = WORLD_VIEWBOX, pad = 2;
  const pts = spreadMarkers(
    d.points
      .filter((p) => p.lat != null && p.lon != null)
      .map((p) => ({ ...p, mx: p.lon + 180, my: 90 - p.lat }))
      .sort((a, b) => a.mx - b.mx),
    3.6);

  // Marker area, not radius, tracks deployed units: doubling the radius of a
  // circle quadruples what the eye reads, so sizing by radius would overstate
  // the big regions by the square.
  const maxUnits = Math.max(...pts.map((p) => p.capacityUnits), 1);
  const radius = (u) => 1.6 + 2.9 * Math.sqrt(u / maxUnits);

  // Zoomed, the box is a window on the region; otherwise it is the world.
  const hit = focus && pts.find((p) => p.region === focus.region);
  const span = 30;
  const box = hit
    ? { x: hit.mx - span, y: hit.my - span * 0.42, w: span * 2, h: span * 0.84 }
    : { x: W.x, y: W.y - pad, w: W.w, h: W.h + pad * 2 };
  const k = box.w / W.w;              // shrink strokes and labels with the box

  return `<svg class="chart fleet-map${hit ? " zoomed" : ""}"
      viewBox="${box.x.toFixed(2)} ${box.y.toFixed(2)} ${box.w.toFixed(2)} ${box.h.toFixed(2)}"
      role="img" aria-label="${hit ? `Capacity pools in ${esc(focus.region)}`
                                  : "Capacity by region, on a world map"}">
    <rect x="${box.x.toFixed(2)}" y="${box.y.toFixed(2)}" width="${box.w.toFixed(2)}"
      height="${box.h.toFixed(2)}" fill="var(--map-sea)"/>
    <path d="${WORLD_PATH}" fill="var(--map-land)" stroke="var(--map-edge)" stroke-width=".25"/>
    ${pts.map((p) => {
      const tone = mapTone(p);
      // Radii are in degrees, and the box shrinks by k when zoomed -- so a
      // marker sized for a 360-degree world is six times too big inside a
      // 60-degree window. It filled the frame.
      const r = radius(p.capacityUnits) * k;
      return `
      ${p.moved ? `<line x1="${(p.lon + 180).toFixed(2)}" y1="${(90 - p.lat).toFixed(2)}"
        x2="${p.mx.toFixed(2)}" y2="${p.my.toFixed(2)}"
        stroke="var(--ink-3)" stroke-width="${(0.2 * k).toFixed(3)}" opacity=".55"/>` : ""}
      <circle class="mk" data-region="${esc(p.region)}"
        cx="${p.mx.toFixed(2)}" cy="${p.my.toFixed(2)}" r="${r.toFixed(2)}"
        fill="${MAP_TONE_FILL[tone]}" fill-opacity=".85"
        stroke="#fff" stroke-width=".45" tabindex="0"
        role="button" aria-label="${esc(p.region)}, ${p.utilisation}% used">
        <title>${esc(p.region)} — ${p.utilisation}% used</title>
      </circle>`;
    }).join("")}
    ${hit && focus.sites && focus.sites.length ? (() => {
      const ring = siteRing(hit.mx, hit.my, focus.sites.length);
      return focus.sites.map((st, i) => {
        const at = ring[i], full = st.overThreshold;
        return `
        <line x1="${hit.mx.toFixed(2)}" y1="${hit.my.toFixed(2)}"
          x2="${at.x.toFixed(2)}" y2="${at.y.toFixed(2)}"
          stroke="var(--ink-3)" stroke-width="${(0.22 * k).toFixed(3)}" opacity=".4"/>
        <circle class="site-mk${full ? " full" : ""}" data-dc="${esc(st.datacentre)}"
          cx="${at.x.toFixed(2)}" cy="${at.y.toFixed(2)}" r="${(2.0 * k).toFixed(2)}"
          fill="${full ? MAP_TONE_FILL.bad : MAP_TONE_FILL.good}" fill-opacity=".9"
          stroke="#fff" stroke-width="${(0.5 * k).toFixed(3)}" tabindex="0" role="button"
          aria-label="${esc(st.datacentre)}, ${st.utilisationPct}% of its own ${st.thresholdPct}% line">
          <title>${esc(st.datacentre)} — ${st.utilisationPct}% of its own ${st.thresholdPct}% line, ${num(st.capacityUnits)} CU</title>
        </circle>
        <text x="${at.x.toFixed(2)}" y="${(at.y + 4.6 * k).toFixed(2)}" text-anchor="middle"
          font-size="${(2.2 * k).toFixed(2)}" fill="var(--ink-2)"
          style="pointer-events:none">${esc(st.datacentre.split("-").pop())}</text>`;
      }).join("");
    })() : ""}
  </svg>`;
}

/* What a marker says when you land on it. Deliberately the four questions that
   were four tabs, in the order a planner asks them. */
function mapCard(p) {
  const tone = mapTone(p);
  return `
  <div class="map-card">
    <header>
      <b>${esc(p.region)}</b>
      ${statusPill(p.status)}
    </header>
    <p class="where">${esc(p.displayName)}${p.city ? ` · ${esc(p.city)}` : ""}</p>

    <div class="rows">
      ${/* Colour is the state against the region's own line, matching the
            markers and the pills. Only the rows that can be alarming carry it:
            a fleet count is never a danger, and colouring it would make the
            colour mean nothing. */ ""}
      <div><span>Current capacity usage</span><b class="${tone === "bad" ? "t-bad" : tone === "warn" ? "t-warn" : ""}">
        ${p.utilisation != null ? pct(p.utilisation, 1) : "—"}
        <span class="t3">of a ${p.thresholdPct != null ? pct(p.thresholdPct, 1) : "—"} line</span></b></div>
      ${/* Crosses, Fleet, Capacity and Workloads used to sit here, and the
            recommendation counts under them. All four are on the panel that
            opens below the map the moment a region is picked, and the region
            page states them again -- so beside the globe they were a third
            copy competing with the thing the reader clicked to see. What is
            left is the one figure the marker's colour is derived from, and the
            two links out. */ ""}
      ${p.coresPending ? `<div><span>Owed</span><b>${num(p.coresPending)} CU pending
        <span class="t3">· ${num(p.failed)} failed requests</span></b></div>` : ""}
    </div>

    <p class="links">
      <a href="/region/${encodeURIComponent(p.region)}">Open ${esc(p.region)}</a> ·
      <a href="/datacentres?region=${encodeURIComponent(p.region)}">Its ${num(p.sites)} sites</a>
    </p>
  </div>`;
}

/* What opens under the map when a marker is picked.

   The card beside the map answers "is this a problem". Review asked for what
   follows it -- "how many capacity pools are there, and how they need to change
   and why, and when are we going to hit the threshold, and what hardware is
   being used in each and every capacity pool" -- which is four screens' worth and
   does not fit a column. It goes full width below the map instead, in the order
   the questions get asked: when, then what to change and why, then what is
   actually in each building. */

function whenBlock(d) {
  const t = d.threshold || {};
  return `
  <div class="when">
    <div class="when-rows">
      <div><span>Current capacity usage</span><b class="${d.utilisation >= d.thresholdPct ? "t-bad" : ""}">${pct(d.utilisation, 1)}</b>
        <i>${d.utilisation >= d.thresholdPct
              ? `${pct(d.utilisation - d.thresholdPct, 1)} past its ${pct(d.thresholdPct, 1)} safety line`
              : `${pct(d.thresholdPct - d.utilisation, 1)} below its ${pct(d.thresholdPct, 1)} safety line`}</i></div>
      <div><span>Crosses the line</span>
        <b class="${t.alreadyBreached ? "t-bad" : ""}">${t.alreadyBreached
          ? "already past it"
          : (t.crossingDate ? esc(t.crossingDate) : "not within the year")}</b>
        <i>${t.crossingEarliest && !t.alreadyBreached
          ? `somewhere between ${esc(t.crossingEarliest)} and ${esc(t.crossingLatest)}, on ${esc(t.model || "the fitted model")}`
          : ""}</i></div>

      ${/* No order-by date. Scaling an F SKU takes effect immediately, so the
            question is not when to raise an order but whether the capacities in
            this region are already throttling. */ ""}
      <div><span>Capacities throttling</span>
        <b class="${d.totals.throttlingCapacities ? "t-bad" : ""}">${num(d.totals.throttlingCapacities)}
          <span class="t3">of ${num(d.totals.capacities)}</span></b>
        <i>${d.totals.throttlingCapacities
          ? `${num(d.totals.interactiveRejected + d.totals.backgroundRejected)} operations refused. Scaling an F SKU is immediate — there is nothing to order.`
          : "None is delaying or refusing operations."}</i></div>
    </div>
    ${d.reason ? `<p class="when-why">${esc(d.reason)}</p>` : ""}
  </div>`;
}

/* What has to change, as three tables.

   This was prose: a paragraph per group of capacities that shared a reason. It
   read badly for a reason worth recording. The paragraphs differed only in a
   trigger percentage, so five of them stacked up looking identical while the
   numbers that actually varied -- which capacity, how full, by when -- were
   buried mid-sentence and wrapped across lines. A reader could not compare two
   rows without reading two paragraphs.

   The variable part belongs in columns and the constant part belongs said once.
   So each kind gets a table of its capacities, and the reasoning that applies
   to all of them sits underneath it, grouped by the thing it actually depends
   on -- for a scale-up that is the throttling stage, because the urgency is a
   property of what the capacity is refusing and not of the capacity itself. */

/* What is happening, in the words someone would use out loud.

   The first version of this table led with "Mean used 110%", which cannot be
   read aloud without first explaining bursting and smoothing -- and review said
   so: "I can not explain what that number is." A percentage over 100 is the
   mechanism, not the finding.

   So the finding leads: this capacity is refusing queries, on this many days,
   and this many were turned away. The CU figures stay, one column over, as the
   evidence for it -- and expressed as "wants 8.8 of 8 CU" rather than "110%",
   because the first is a sentence and the second needs a footnote. */
const PROBLEM = {
  background_rejection: { text: "Refusing everything", tone: "bad",
    why: "past 24 hours of borrowed capacity — every request, interactive and background, is refused" },
  interactive_rejection: { text: "Refusing queries", tone: "bad",
    why: "past an hour of borrowed capacity — users' queries are turned away" },
  interactive_delay: { text: "Delaying queries", tone: "warn",
    why: "past ten minutes of borrowed capacity — every query waits an extra 20 seconds" },
  none: { text: "No room left", tone: "warn",
    why: "not throttling yet, but there is almost nothing spare to absorb a busy day" },
};

function scaleTable(list, up) {
  if (!up) return scaleDownTable(list);
  return `
  <p class="table-lede">Read a row as: <b>this capacity is trying to use more Capacity Units
    than its SKU provides</b>, so Fabric has started delaying or refusing work on it.</p>
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>Size</th><th class="n">Wants</th>
      <th>What is happening</th><th class="n">How often</th><th class="n">Queries refused</th>
      <th>What to do</th>
    </tr></thead>
    <tbody>${list.map((r) => {
      const e = r.evidence || {};
      const p = PROBLEM[e.isThrottling ? e.worstStage : "none"] || PROBLEM.none;
      const wants = e.capacityUnits * e.meanUtilisationPct / 100;
      const rejected = (e.interactiveRejected || 0) + (e.backgroundRejected || 0);
      return `<tr class="${p.tone === "bad" ? "row-danger" : "row-soon"}">
        <td><a href="/capacity/${encodeURIComponent(r.target)}">${esc(r.target)}</a>
          <span class="t3">${esc(e.datacentre || "")}</span></td>
        <td><b>${esc(e.fabricSku)}</b><span class="t3">${num(e.capacityUnits)} CU</span></td>
        <td class="n" title="averaged over the last ${e.windowDays} days; it peaked at ${Math.round(e.peakUtilisationPct)}%">
          <b class="${p.tone === "bad" ? "t-bad" : "t-warn"}">${wants.toFixed(1)} CU</b>
          <span class="t3">has ${num(e.capacityUnits)}</span></td>
        <td data-info="${esc(p.why)}" title="${esc(p.why)}"><span class="pill ${p.tone}">${p.text}</span></td>
        <td class="n">${e.throttledDays
          ? `${e.throttledDays} of ${e.windowDays} days` : `<span class="t3">not yet</span>`}</td>
        <td class="n ${rejected ? "t-bad" : ""}">${rejected ? num(rejected) : "—"}</td>
        <td class="act"><span class="pill ${p.tone === "bad" ? "bad" : "wash"}">Scale to ${esc(e.scaleTo)}</span>
          <span class="t3">${num(e.scaleToUnits)} CU · applies immediately${
            e.crossesSlowBoundary ? " · crosses the F256/F512 boundary" : ""}</span></td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <div class="why-notes">
    <p><b>Wants</b> is what the capacity actually consumed on an average day, in CU. Fabric
      lets a job use more than the SKU provides and spreads the cost over the next few
      hours — so briefly going over is normal. Doing it every day is not: the borrowed
      capacity accumulates until Fabric starts pushing back.</p>
    <p><b>What is happening</b> is Fabric's own ladder. Ten minutes of borrowed capacity is
      free. Past that, queries are delayed 20 seconds; past an hour, queries are refused;
      past 24 hours, everything is refused.</p>
  </div>`;
}

function scaleDownTable(list) {
  return `
  <p class="table-lede">Read a row as: <b>this capacity is being paid for and barely used.</b>
    F SKUs bill per second whether or not anything runs on them.</p>
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>Size</th><th class="n">Wants</th><th class="n">Busiest day</th>
      <th class="n">Idle for</th><th>What to do</th>
    </tr></thead>
    <tbody>${list.map((r) => {
      const e = r.evidence || {};
      const wants = e.capacityUnits * e.meanUtilisationPct / 100;
      return `<tr>
        <td><a href="/capacity/${encodeURIComponent(r.target)}">${esc(r.target)}</a>
          <span class="t3">${esc(e.datacentre || "")}</span></td>
        <td><b>${esc(e.fabricSku)}</b><span class="t3">${num(e.capacityUnits)} CU</span></td>
        <td class="n"><b>${wants.toFixed(1)} CU</b><span class="t3">has ${num(e.capacityUnits)}</span></td>
        <td class="n">${pct(e.peakUtilisationPct, 0)}<span class="t3">of its CU</span></td>
        <td class="n">${e.windowDays} days<span class="t3">never throttled</span></td>
        <td class="act"><span class="pill">Scale down to ${esc(e.scaleTo)}</span>
          <span class="t3">${num(e.scaleToUnits)} CU · busiest day would be
            ${pct(e.peakAfterScaleDownPct, 0)}${
            e.losesFreeViewers ? " · drops below F64, viewers would need Pro" : ""}</span></td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <div class="why-notes">
    <p>Nothing that throttled in the window appears here, however idle its average looks —
      a capacity quiet six days a week and overloaded on the seventh is sized for the
      seventh.</p>
  </div>`;
}

function moveTable(list) {
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>SKU</th><th class="n">CU</th><th class="n">Throttled</th>
      <th>Worst stage</th><th>Workspace to move</th><th class="n">Its share</th><th>Move to</th>
    </tr></thead>
    <tbody>${list.map((r) => {
      const e = r.evidence || {};
      return `<tr class="row-danger">
        <td><a href="/capacity/${encodeURIComponent(r.target)}">${esc(r.target)}</a></td>
        <td><b>${esc(e.fabricSku)}</b></td>
        <td class="n">${num(e.capacityUnits)}</td>
        <td class="n t-bad">${e.throttledDays}/${e.windowDays}d</td>
        <td>${stageCell(e.worstStage, e.worstStageLabel)}</td>
        <td><b>${esc(e.workspace)}</b><span class="t3">${esc(e.workspaceWorkload)}</span></td>
        <td class="n t-bad">${pct(e.workspaceSharePct, 0)}</td>
        <td><a href="/capacity/${encodeURIComponent(e.moveTo)}">${esc(e.moveTo)}</a>
          <span class="t3">${esc(e.moveToSku)} · at ${pct(e.moveToUtilisationPct, 0)}</span></td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <div class="why-notes">
    <p>Where one workspace is most of what a capacity consumes, moving it is the cheaper
      answer: it costs nothing per second, where the next SKU up bills continuously. Every
      destination here is in the same region, is not throttling, and has room for what
      arrives.</p>
    <p class="t3">Capacities hosting a single workspace are excluded — moving it would empty
      the capacity, which is a consolidation decision, not a rebalancing one.</p>
  </div>`;
}

function licenceTable(list) {
  const e0 = (list[0] || {}).evidence || {};
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>Capacity pool</th><th>SKU</th><th class="n">CU now</th>
      <th>Step to</th><th class="n">CU after</th><th class="n">Power BI workspaces</th>
    </tr></thead>
    <tbody>${list.map((r) => {
      const e = r.evidence || {};
      return `<tr>
        <td><a href="/capacity/${encodeURIComponent(r.target)}">${esc(r.target)}</a></td>
        <td>${esc(e.datacentre || "")}</td>
        <td><b>${esc(e.fabricSku)}</b></td>
        <td class="n">${num(e.capacityUnits)}</td>
        <td><b>${esc(e.stepTo)}</b></td>
        <td class="n">${num(e.stepToUnits)}</td>
        <td class="n">${num(e.powerBiWorkspaces)} of ${num(e.workspaces)}</td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <div class="why-notes">
    <p>${esc(e0.rule || "")}</p>
    <p class="t3">Real and documented — <a href="${esc(e0.source || "")}">Microsoft Fabric
      licensing</a>. A commercial decision, not a capacity one: none of these is short of CU.</p>
  </div>`;
}

function changeBlock(d) {
  const kinds = [
    ["scale_up", "Scale up", (l) => scaleTable(l, true)],
    ["load_balance", "Move a workspace", moveTable],
    ["scale_down", "Scale down", (l) => scaleTable(l, false)],
    ["licensing", "Change the licence", licenceTable],
  ];
  const present = kinds.filter(([k]) => (d.recommendations || {})[k]?.length);
  if (!present.length) return `<p class="empty">Nothing outstanding in this region.</p>`;

  return present.map(([k, label, render]) => {
    const list = d.recommendations[k];
    const show = list.slice(0, 20);
    return `
    <div class="change-group">
      <h4>${label} <span class="count">${num(list.length)}</span></h4>
      ${render(show)}
      ${list.length > show.length ? `<p class="hint">
        Showing the ${num(show.length)} most urgent of ${num(list.length)} —
        <a href="/recommendations?kind=${k}&region=${encodeURIComponent(d.region)}">open the rest</a></p>`
        : (list.length > 3 ? `<p class="hint">
        <a href="/recommendations?kind=${k}&region=${encodeURIComponent(d.region)}">Open these with their full evidence</a></p>` : "")}
    </div>`;
  }).join("");
}

function sitesBlock(d) {
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity pool</th><th class="n">Capacities</th><th>SKUs</th>
      <th class="n">CU</th><th class="n">Workspaces</th>
      <th>What is happening</th><th class="n">Queries refused</th>
    </tr></thead>
    <tbody>${d.sites.map((s) => {
      const rejected = s.interactiveRejected + s.backgroundRejected;
      return `<tr class="${s.throttlingCapacities ? "row-danger" : ""}">
        <td><a href="/datacentre/${encodeURIComponent(s.datacentre)}">${esc(s.datacentre)}</a></td>
        <td class="n">${s.capacities}</td>
        <td><span class="sku-mix tight">${Object.entries(s.skuMix).map(([sku, n]) =>
          `<span class="sku-chip${["F64","F128","F256","F512","F1024","F2048"].includes(sku)
            ? " free-ok" : ""}">${esc(sku)}${n > 1 ? `<b>×${n}</b>` : ""}</span>`).join("")}</span></td>
        <td class="n">${num(s.capacityUnits)}</td>
        <td class="n">${num(s.workspaces)}</td>
        ${/* No site-level "wants" figure. Averaging demand across a site's
              capacities produced rows reading "wants 78 CU, has 92" beside
              "refusing queries", which looks like a contradiction and is not:
              CU does not pool. An F4 at 120% throttles while the F64 next to it
              sits idle, and a site average hides exactly that. The count of
              throttling capacities is the honest figure at this grain. */ ""}
        <td>${s.throttlingCapacities
          ? `<span class="pill ${(PROBLEM[s.worstStage] || PROBLEM.none).tone}">${
              (PROBLEM[s.worstStage] || PROBLEM.none).text}</span>
             <span class="t3">${s.throttlingCapacities} of ${s.capacities} capacities</span>`
          : `<span class="t3">nothing throttling</span>`}</td>
        <td class="n ${rejected ? "t-bad" : ""}">${rejected ? num(rejected) : "—"}</td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <p class="sites-note">
    <b>CU does not pool across capacities.</b> Each Fabric capacity is throttled on its own
    consumption, so an F4 running hot is delayed or refused even when the F64 beside it is
    idle — which is why a site can hold plenty of CU in total and still be refusing queries.
    That is also why the fix is usually to scale the one capacity, or move a workspace off
    it, rather than to buy more CU for the site.
  </p>`;
}

/* What Fabric actually runs here. Only the gaps: review asked for the available
   list removed once it was six of nine chips saying nothing was wrong. */
function workloadBlock(d) {
  const part = d.workloadsPartlyAffected || [];
  const gaps = d.unavailableFeatures || [];
  const total = d.workloadCount || 9;

  if (d.powerBIOnly) {
    return `<p class="wl-lede bad"><b>Power BI only.</b> Fabric workloads do not run in
      this region at all.</p>`;
  }
  if (!gaps.length) {
    return `<p class="wl-lede ok"><b>All ${total} Fabric workloads run here with nothing
      missing.</b> <span class="t3">Microsoft's published availability.</span></p>`;
  }
  return `
  <div class="wl part standalone">
    <h5>${num(part.length)} of ${total} workloads are missing a feature</h5>
    <p>${part.map((w) => `<span class="wl-chip part">${esc(w)}</span>`).join("")}</p>
    <p class="wl-detail"><b>Not available here:</b> ${gaps.map(esc).join(", ")}.${
      d.platformAffected ? " One of these is platform-level rather than inside a workload." : ""}
      These workloads still run — the named features inside them do not, so this is a
      constraint on <i>what you can build</i> here, not on whether the region works.
      The other ${num(total - part.length)} have nothing missing.</p>
  </div>
  <p class="wl-src">Microsoft's published regional availability, refreshed from Fabric
    documentation — not a projection and not generated.</p>`;
}

function mapDetail(d) {
  const t = d.totals;
  return `
  <section class="panel detail">
    <header>
      <b>${esc(d.region)}</b>
      ${statusPill(d.status)}
      <!-- capacityUnits/workspaces, not units/nodes: those two were left over
           from the Azure model, are not on this payload, and rendered as a
           confident "0 units - 0 nodes" in the header of every region. -->
      <span class="hint">${num(t.sites)} capacity pools · ${num(t.capacities)} capacities ·
        ${num(t.capacityUnits)} CU · ${num(t.workspaces)} workspaces</span>
      <a class="closer" href="#" id="detail-close" aria-label="Close">×</a>
    </header>
    <div class="body">
      ${workloadBlock(d)}

      <h3 class="sec">When does it hit the threshold?</h3>
      ${whenBlock(d)}

      <h3 class="sec">What has to change, and why?</h3>
      ${changeBlock(d)}

      <h3 class="sec">What is in each capacity pool?</h3>
      ${sitesBlock(d)}

      <!-- Was "N of M sites are past their own line", reading a totals field
           nothing sends, so it read "0 of 10" everywhere. It could not be
           fixed by supplying the field: a site has no line of its own in the
           Fabric model. CU does not pool, so a capacity pool is not a thing that
           fills up -- each capacity in it throttles on its own consumption.
           What is countable, and what an admin acts on, is how many sites hold
           a capacity that is throttling. -->
      <p class="prov">
        ${num((d.sites || []).filter((s) => s.throttlingCapacities > 0).length)}
        of ${num(t.sites)} capacity pools hold at least one throttling capacity.
        ${t.freeViewerCapable} of ${num(t.capacities)} capacities are F64 or larger, so Power BI
        content on the rest needs a Pro or PPU licence per viewer.
        Capacities, their CU consumption and their throttling history are generated;
        the Fabric SKU ladder, the F64 rule and the workload availability above are real.
      </p>
    </div>
  </section>`;
}

PAGES["/map"] = async (view) => {
  const d = await get("/api/map");

  /* The strip that used to sit above the globe -- "11 regions, 2 past their
     safety line, 3 due to cross it" -- has gone. It restated in words what the
     markers say in colour, directly above the thing it was describing, and it
     pushed the globe down the page. The Regions tab carries the same counts in
     a table that can be sorted and read properly. */

  
view.innerHTML = howto({
  answers: "<b>Fleet overview, on one screen.</b> See each region's capacity, threshold status, scaling needs, and unavailable Fabric workloads.",
  steps: [
    { what: "Marker colour", is: "Shows status: <b>red</b> = over threshold, <b>amber</b> = forecast to cross, <b>green</b> = within limits." },
    { what: "Marker size", is: "Represents deployed Capacity Units (CU)." },
    { what: "Globe controls", is: "Drag to rotate and use + / − to zoom. Only the visible hemisphere is shown." },
    { what: "Selecting a region", is: "Centers and zooms to it, showing its capacity pools and detailed information." },
    { what: "Overlapping points", is: "Nearby regions are slightly separated and connected to their actual location." },
  ],
  words: [
    { term: "How full", means: "Utilisation compared with each region's own safety threshold." },
    { term: "Unavailable features", means: "Fabric workloads not currently supported in that region, based on published Microsoft data." },
    { term: "To scale / rebalance", means: "<b>Scale</b> when capacity lacks headroom; <b>rebalance</b> when one workspace is consuming most of a capacity despite available regional capacity." },
  ],
  next: "Start with red markers, then amber regions with move recommendations.",
  sources: "Azure region locations and Fabric availability use real published data. Capacity usage and throttling data are generated and marked with provenance.",
}) + title("Fleet map",
  /* Scale and scope, not status: the markers and the legend below already carry
     which regions are in trouble, and the strip that used to say so in words is
     gone for exactly that reason. Summed from the payload rather than written
     down, so it stays true as the fleet changes. */
  `${num(d.points.length)} regions · ${
     num(d.points.reduce((n, p) => n + (p.sites || 0), 0))} capacity pools · ${
     num(d.points.reduce((n, p) => n + (p.capacityUnits || 0), 0))} CU deployed`) + `


  <section class="panel">
    <div class="body map-wrap">
      <div class="map-holder">
        <div id="map-svg">${globeMap(d, { lon0: -30, lat0: 22, zoom: 1 }, null)}</div>
        <div class="map-zoom">
          <button type="button" id="zoom-in" aria-label="Zoom in">+</button>
          <button type="button" id="zoom-out" aria-label="Zoom out">−</button>
        </div>
        <div class="legend map-legend" id="map-legend">
          <span><i class="dot bad"></i>Past its safety line</span>
          <span><i class="dot warn"></i>Due to cross its line</span>
          <span><i class="dot good"></i>Inside its line</span>
          <span class="t3">Marker area = Capacity Units</span>
        </div>
      </div>
      <aside id="map-side" class="map-side">
        ${filterBar("map-filter", {
          placeholder: "Search regions",
          label: "Jump to",
          allLabel: "All regions",
          // Eleven markers on a globe, some of them behind it at any given
          // rotation. Picking a region here spins the globe to it and opens it,
          // which is the same outcome as finding and clicking the marker --
          // without having to drag the world round to see whether it is there.
          options: [...new Set(d.points.map((p) => p.region))].sort(),
        })}
        <div id="map-side-body"><p class="empty">Select a region on the map,
          or find one with the box above.</p></div>
      </aside>
    </div>
  </section>

  <div id="map-detail"></div>`;

  const byRegion = Object.fromEntries(d.points.map((p) => [p.region, p]));
  // The panel body, not the whole aside: selecting a region rewrites this, and
  // the filter bar above it has to survive that or it disappears on first use.
  const side = $("map-side-body");

  /* Redraw the map, zoomed on a region or back out to the world.

     The SVG is replaced rather than mutated, so every redraw rewires its own
     markers -- a handler bound once at page load would be attached to nodes
     that no longer exist the moment the box changes. */
  let focus = null;
  // Where the viewer stands. The globe is rendered from this, and spinTo
  // animates it -- so a region is reached by turning the world to it rather
  // than by cutting to a new frame.
  let camera = { lon0: -30, lat0: 22, zoom: 1 };
  function drawMap(next, cam) {
    focus = next;
    if (cam) camera = cam;
    // The markers are about to be replaced; a tooltip left open by a marker
    // that no longer exists would hang there with stale text.
    const openTip = view.querySelector(".map-holder .map-tip");
    if (openTip) openTip.hidden = true;
    $("map-svg").innerHTML = globeMap(d, camera, focus);
    $("map-legend").innerHTML = focus
      ? `<span><i class="dot bad"></i>Site past its own line</span>
         <span><i class="dot good"></i>Site with room</span>
         <span class="t3">Each site is its region's real point plus a generated
           offset \u2014 Microsoft does not publish where individual capacity pools
           are, so the spread within a region is approximate and scaled up to read</span>
         <a href="#" id="map-back">\u2190 all regions</a>`
      : `<span><i class="dot bad"></i>Past its safety line</span>
         <span><i class="dot warn"></i>Due to cross its line</span>
         <span><i class="dot good"></i>Inside its line</span>
         <span class="t3">Marker area = Capacity Units</span>`;
    wireMarkers();
    const back = $("map-back");
    if (back) back.onclick = (ev) => {
      ev.preventDefault();
      spinTo(camera, { lon0: -30, lat0: 22, zoom: 1 }, 620, (cam) => drawMap(null, cam));
    };
  }

  /* One dark tooltip, reused. It lives on `.map-holder` (which is positioned)
     and outlives every redraw -- only `#map-svg` is replaced -- so it is made
     once and refilled. The markers carry the same facts in their aria-label for
     assistive tech; this is the sighted reader's version, without the browser's
     one-second delay, and it is where the coordinates go because they do not
     read well appended to a label. */
  function mapTip() {
    const mh = view.querySelector(".map-holder");
    let tip = mh.querySelector(".chart-tip.map-tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip map-tip";
      tip.hidden = true;
      mh.appendChild(tip);
    }
    return tip;
  }

  function coordLine(lat, lon) {
    return (lat == null || lon == null) ? ""
      : `<br><span class="t-mute">${lat.toFixed(3)}, ${lon.toFixed(3)}</span>`;
  }

  function hoverable(c, html) {
    const tip = mapTip();
    const mh = view.querySelector(".map-holder");
    const move = (ev) => {
      const box = mh.getBoundingClientRect();
      const x = Math.min(Math.max(ev.clientX - box.left, 70), box.width - 70);
      // Below the cursor near the top edge, above it everywhere else, so the
      // readout is not clipped by the panel when a marker sits high on the globe.
      const y = ev.clientY - box.top;
      tip.style.left = `${x}px`;
      tip.style.top = `${y < 90 ? y + 26 : y}px`;
      tip.style.transform = y < 90 ? "translate(-50%, 0)" : "translate(-50%, -125%)";
    };
    c.addEventListener("mouseenter", (ev) => {
      if (drag || dragged) return;   // not while the globe is being spun
      tip.innerHTML = html();
      tip.hidden = false;
      move(ev);
    });
    c.addEventListener("mousemove", move);
    c.addEventListener("mouseleave", () => { tip.hidden = true; });
  }

  function wireMarkers() {
    const bySite = Object.fromEntries(
      ((focus && focus.sites) || []).map((s) => [s.datacentre, s]));

    view.querySelectorAll("circle.mk").forEach((c) => {
      c.classList.toggle("on", !!focus && c.dataset.region === focus.region);
      // Not after a drag: spinning the globe with a marker under the cursor
      // would otherwise open that region as soon as the pointer is lifted.
      c.addEventListener("click", () => { if (!dragged) select(c.dataset.region); });
      c.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(c.dataset.region); }
      });
      const p = byRegion[c.dataset.region];
      if (p) hoverable(c, () => `<b>${esc(p.region)}</b>${
        p.city ? ` <span class="t-mute">${esc(p.city)}</span>` : ""}${
        coordLine(p.lat, p.lon)}<br>${
        p.utilisation == null ? "—" : `${pct(p.utilisation, 1)} used`}${
        p.thresholdPct != null ? ` <span class="t-mute">of a ${pct(p.thresholdPct, 1)} line</span>` : ""}<br>${
        `<span class="t-mute">${num(p.capacities)} capacities · ${num(p.capacityUnits)} CU · ${num(p.sites)} sites</span>`}`);
    });
    // A site opens the capacity pool, which is where its own KPIs already live.
    view.querySelectorAll("circle.site-mk").forEach((c) => {
      const go = () => {
        if (dragged) return;
        navigate(`/datacentre/${encodeURIComponent(c.dataset.dc)}`);
      };
      c.addEventListener("click", go);
      c.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); }
      });
      const s = bySite[c.dataset.dc];
      if (s) hoverable(c, () => `<b>${esc(s.datacentre)}</b>${
        coordLine(s.lat, s.lon)}<br>${s.utilisationPct}% <span class="t-mute">of its own ${s.thresholdPct}% line</span><br>${
        `<span class="t-mute">${num(s.capacities)} capacit${s.capacities === 1 ? "y" : "ies"} · ${num(s.capacityUnits)} CU</span>`}${
        s.throttlingCapacities ? `<br><span class="t-bad">${num(s.throttlingCapacities)} throttling</span>` : ""}`);
    });
  }

  const detail = $("map-detail");
  let pending = null;

  /* `zoom` is false for the selection made on arrival. The page opens on
     whatever is worst so it says something before anyone clicks, but opening
     zoomed into that region would throw away the fleet view -- which is the
     whole point of the landing screen. Zooming is a thing a reader asks for. */
  async function select(region, { zoom = true } = {}) {
    const p = byRegion[region];
    if (!p) return;
    side.innerHTML = mapCard(p);

    // The drill-down is a second request, so a slow one must not paint over a
    // marker picked after it. Only the newest selection is allowed to render.
    const token = region;
    pending = token;
    detail.innerHTML = `<p class="loading">Loading ${esc(region)}…</p>`;
    try {
      const d = await get(`/api/map/${encodeURIComponent(region)}`);
      if (pending !== token) return;
      if (zoom) {
        const p = byRegion[region];
        /* Zoom in far enough that the region and its sites fill the view.
           2.2 left most of a hemisphere on screen, so selecting a region looked
           much like not selecting one -- the sites were a cluster of dots among
           ten other regions' markers. 3.6 was better but still showed a
           continent; 6.8 drops to roughly a country/state frame so the data
           centres separate and their labels are readable. Inside ZOOM_MAX. */
        const target = { lon0: p.lon, lat0: p.lat, zoom: 6.8 };
        const next = { region, sites: d.sites || [] };
        spinTo(camera, target, 620, (cam) => drawMap(next, cam));
      }
      detail.innerHTML = mapDetail(d);
      const close = $("detail-close");
      if (close) close.onclick = (ev) => { ev.preventDefault(); detail.innerHTML = ""; };
    } catch (err) {
      if (pending !== token) return;
      detail.innerHTML = `<section class="panel"><div class="body">
        <p class="error">Could not load the detail for ${esc(region)}.</p></div></section>`;
    }
  }

  /* Turning the globe by hand, and the two zoom buttons.

     ZOOM
         Bounded at both ends. Below about 0.85 the globe is a dot with eleven
         markers stacked on it; the top end is 9 -- far enough in to read one
         region's capacity pools at roughly a state frame, which is as close as a
         country-resolution coastline is worth showing. Each press animates
         through spinTo, the same easing a region selection uses, so the two do
         not feel like different controls.

     DRAG
         Every handler is on `.map-holder` with the pointer captured, not on
         `window`. The obvious version listens on window so the drag survives
         the pointer leaving the element -- but route() replaces the view and
         those listeners stay bound to a detached globe, so every visit to this
         tab leaves another one running. Pointer capture gives the same
         behaviour and dies with the DOM. The holder is also safe to bind to
         because only `#map-svg`'s contents are replaced on redraw, never the
         holder itself. */
  const ZOOM_MIN = 0.85, ZOOM_MAX = 9;

  function zoomBy(factor) {
    const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, camera.zoom * factor));
    if (Math.abs(z - camera.zoom) < 0.001) return;
    spinTo(camera, { ...camera, zoom: z }, 260, (cam) => drawMap(focus, cam));
  }
  $("zoom-in").onclick = () => zoomBy(1.4);
  $("zoom-out").onclick = () => zoomBy(1 / 1.4);

  const holder = view.querySelector(".map-holder");
  let drag = null;
  // Set while a drag is ending, so the click that follows a spin of the globe
  // does not also open whichever region happened to be under the finger.
  let dragged = false;

  /* Degrees of rotation per pixel dragged.

     Read off the globe as drawn rather than fixed: the SVG is a 560-unit
     viewBox scaled to whatever width the panel has, and the sphere's radius
     inside it grows with zoom. A constant would send the globe spinning when
     zoomed in and feel stuck when zoomed out. */
  function degPerPixel() {
    const svg = holder.querySelector("svg.globe");
    const units = 560 / (svg && svg.clientWidth ? svg.clientWidth : 560);
    return units / (560 * 0.44 * camera.zoom) * (180 / Math.PI);
  }

  /* How far the pointer has to travel before this counts as a spin and not a
     click. Below it, nothing is captured and nothing is redrawn. */
  const DRAG_SLOP = 4;

  holder.addEventListener("pointerdown", (ev) => {
    if (ev.button) return;                       // primary button only
    if (ev.target.closest(".map-zoom")) return;  // the zoom buttons are not the globe
    // Deliberately no setPointerCapture here. Capturing on pointerdown
    // retargets the click that follows to the holder, so every marker and both
    // zoom buttons stopped responding -- they rendered, hovered, and did
    // nothing. The pointer is captured in pointermove instead, once the
    // movement is far enough that it cannot have been a click.
    drag = { x: ev.clientX, y: ev.clientY, k: degPerPixel(),
             lon0: camera.lon0, lat0: camera.lat0, moved: 0, held: false };
  });

  holder.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    drag.moved = Math.max(drag.moved, Math.abs(dx) + Math.abs(dy));
    if (drag.moved <= DRAG_SLOP) return;
    if (!drag.held) {
      drag.held = true;
      holder.setPointerCapture(ev.pointerId);
      holder.classList.add("dragging");
    }
    drawMap(focus, {
      lon0: drag.lon0 - dx * drag.k,
      // Clamped short of the poles: at exactly +/-90 the projection's cos(lat0)
      // is zero, the graticule collapses and the land stops drawing.
      lat0: Math.max(-85, Math.min(85, drag.lat0 + dy * drag.k)),
      zoom: camera.zoom,
    });
  });

  function endDrag(ev) {
    if (!drag) return;
    dragged = drag.moved > DRAG_SLOP;
    drag = null;
    holder.classList.remove("dragging");
    if (holder.hasPointerCapture(ev.pointerId)) holder.releasePointerCapture(ev.pointerId);
    // Cleared after the click that this pointerup is about to produce.
    setTimeout(() => { dragged = false; }, 0);
  }
  holder.addEventListener("pointerup", endDrag);
  holder.addEventListener("pointercancel", endDrag);

  wireMarkers();

  /* The map's filter is not the table filter: there are no rows to hide, so
     hiding is not the useful verb. Choosing a region spins the globe to it and
     opens it -- the same outcome as finding its marker, minus the dragging.
     Clearing the box goes back to the whole fleet rather than leaving the
     camera wherever the last pick left it. */
  const names = [...new Set(d.points.map((p) => p.region))].sort();
  const mq = $("map-filter-q"), mf = $("map-filter-f"), mc = $("map-filter-count");

  function jump(region) {
    if (!region || !byRegion[region]) return;
    if (mf) mf.value = region;
    select(region);
  }

  if (mf) mf.onchange = () => {
    if (mf.value) { mq.value = ""; mc.textContent = ""; jump(mf.value); }
    else resetView();
  };

  if (mq) mq.oninput = () => {
    const term = mq.value.trim().toLowerCase();
    if (!term) { mc.textContent = ""; if (mf) mf.value = ""; return; }
    const hits = names.filter((n) => n.toLowerCase().includes(term));
    // One match is an answer, so act on it. Several is a narrowing, so say how
    // many rather than picking one of them arbitrarily.
    if (hits.length === 1) { mc.textContent = ""; jump(hits[0]); }
    else mc.textContent = hits.length
      ? `${hits.length} regions match — keep typing, or use the dropdown`
      : "No region matches";
  };

  function resetView() {
    spinTo(camera, { lon0: -30, lat0: 22, zoom: 1 }, 620, (cam) => drawMap(null, cam));
    detail.innerHTML = "";
    side.innerHTML = `<p class="empty">Select a region on the map,
      or find one with the box above.</p>`;
  }

  // Open on whatever is worst, so the page is useful before anyone clicks.
  const worst = [...d.points].sort((a, b) =>
    (b.utilisation ?? 0) - (a.utilisation ?? 0))[0];
  if (worst) select(worst.region, { zoom: false });
};

/* The map is the landing page. `/map` is kept as an alias rather than
   redirected: it is the address the page had for its whole life, it appears in
   bookmarks and in the docs, and the router's unknown-path fallback also lands
   here -- so both spellings render the same thing instead of one of them
   bouncing. */
PAGES["/"] = PAGES["/map"];

PAGES["/regions"] = async (view) => {
  view.innerHTML = howto({
  answers: "<b>How full each region is.</b> See capacity, utilisation, and risk at a glance.",

  steps: [
    {
      what: "Region table",
      is: "Shows capacity and utilisation for every region. Sort by any column."
    },
    {
      what: "Utilisation in red",
      is: "Means the region is above its own safety threshold."
    },
    {
      what: "Region drill-down",
      is: "Select a region to see its capacity pools, thresholds, forecasts, and scaling actions."
    }
  ],

  words: [
    {
      term: "Total CU",
      means: "Capacity Units deployed across all capacity pools in the region."
    },
    {
      term: "Utilisation",
      means: "CU in use versus deployed CU. A regional average can hide an overloaded capacity pool."
    },
    {
      term: "Why this table is short",
      means: "Region-level details can hide site-level issues, so thresholds, forecasts, and actions are shown in the data-centre drill-down."
    }
  ],

  next: "Start with the fullest regions, then open them to identify which capacity pool needs action.",

  sources: "Daily regional utilisation and Fabric capacity data."
}) +

  title(
    "Regions",
    "How full each region is"
  ) +

  `

  <section class="panel">
    <div class="body">
      <span
        class="hint"
        id="thr-note"
        style="color:var(--ink-3);font-size:.82rem"
      ></span>
    </div>
  </section>

  <div id="region-table"></div>
  `;

  /*
   * Default sorting:
   * fullest region first.
   */
  const sort = {
    key: "util",
    asc: false
  };

  async function draw() {
    /*
     * Get current threshold information.
     */
    const p = await get("/api/threshold");

    /*
     * Safety check in case the API returns an unexpected response.
     */
    if (!p || !Array.isArray(p.regions)) {
      $("region-table").innerHTML =
        `<p class="error">Could not load region data.</p>`;
      return;
    }

    /*
     * Number of regions currently in risk.
     */
    const inRisk = p.regions.filter(
      (r) => r.at_risk
    ).length;

    /*
     * Update threshold note.
     */
    const thresholdValues = p.regions
      .map((r) => r.threshold_pct)
      .filter((v) => v != null && !Number.isNaN(Number(v)));

    const thrNote = $("thr-note");

    if (thrNote) {
      if (p.regions.length && thresholdValues.length) {
        thrNote.textContent =
          `${inRisk} of ${p.regions.length} regions are past their own safety threshold `
          + `(${Math.min(...thresholdValues)}% to `
          + `${Math.max(...thresholdValues)}%). `
          + `Open a region to see which of its capacity pools is responsible.`;
      } else {
        thrNote.textContent =
          "No region threshold information is currently available.";
      }
    }

    /*
     * Columns displayed in the region table.
     */
    const COLS = [
      {
        key: "region",
        label: "Region",
        get: (r) => r.region,
        numeric: false
      },
      {
        key: "sites",
        label: "Capacity pools",
        get: (r) => r.datacentre_count,
        numeric: true
      },
      {
        key: "total",
        label: "Total CU",
        get: (r) => r.deployed_units,
        numeric: true
      },
      {
        key: "used",
        label: "Utilised CU",
        get: (r) => r.used_units,
        numeric: true
      },
      {
        key: "util",
        label: "Utilisation",
        get: (r) => r.current_utilisation_pct,
        numeric: true
      }
    ];

    /*
     * Determine which column is currently being sorted.
     */
    const col =
      COLS.find((c) => c.key === sort.key) ||
      COLS[COLS.length - 1];

    /*
     * Sort a copy so the API response itself is not mutated.
     */
    const rows = [...p.regions].sort((a, b) => {
      const x = col.get(a);
      const y = col.get(b);

      const cmp = col.numeric
        ? (Number(x) || 0) - (Number(y) || 0)
        : String(x ?? "").localeCompare(String(y ?? ""));

      return sort.asc ? cmp : -cmp;
    });

    /*
     * Sorting arrow.
     */
    const arrow = (c) =>
      c.key === sort.key
        ? (sort.asc ? " ▲" : " ▼")
        : "";

    /*
     * Preserve filter state before rebuilding the table.
     *
     * IMPORTANT:
     * The old code correctly used ?. while reading these values,
     * but later unconditionally did:
     *
     * $("rg-filter-q").value = keep.q;
     * $("rg-filter-f").value = keep.f;
     *
     * If either element was missing, that caused:
     *
     * Cannot set properties of null (setting 'value')
     */
    const qElement = $("rg-filter-q");
    const fElement = $("rg-filter-f");

    const keep = {
      q: qElement?.value || "",
      f: fElement?.value || ""
    };

    /*
     * Render the complete region table.
     */
    $("region-table").innerHTML =
      panel(
        "Regions by capacity position",

        filterBar("rg-filter", {
          placeholder: "Search regions",
          label: "Show",
          allLabel: "All regions",

          options: [
            {
              value: "risk",
              label: "Past its safety threshold"
            },
            {
              value: "ok",
              label: "Within its safety threshold"
            }
          ]
        })

        +

        `<div class="scroll-x">
          <table>
            <thead>
              <tr>
                ${COLS.map((c) => `
                  <th
                    class="${c.numeric ? "n " : ""}sortable"
                    data-sort="${c.key}"
                    style="cursor:pointer;user-select:none"
                  >
                    ${esc(c.label)}${arrow(c)}
                  </th>
                `).join("")}
              </tr>
            </thead>

            <tbody>
              ${
                rows.map((r) => `
                  <tr
                    class="clickable"
                    data-region="${esc(r.region)}"
                    data-search="${esc(r.region)}"
                    data-filter="${r.at_risk ? "risk" : "ok"}"
                  >
                    <td>
                      <b>${esc(r.region)}</b>
                    </td>

                    <td class="n">
                      ${num(r.datacentre_count)}
                    </td>

                    <td class="n">
                      ${num(Math.round(r.deployed_units))}
                    </td>

                    <td class="n">
                      ${num(Math.round(r.used_units))}
                    </td>

                    <td class="n">
                      <b
                        style="color:${r.at_risk ? "var(--bad)" : "inherit"}"
                        ${
                          r.at_risk
                            ? `
                              data-info="Past this region's own ${r.threshold_pct}% safety threshold. Which capacity pool is responsible is on the region page."
                              title="Past its own ${r.threshold_pct}% safety threshold"
                            `
                            : ""
                        }
                      >
                        ${pct(r.current_utilisation_pct, 1)}
                      </b>
                    </td>
                  </tr>
                `).join("")
              }
            </tbody>
          </table>
        </div>`,

        {
          hint: "click a row for detail",
          flush: true
        }
      );

    /*
     * Wire table column sorting.
     *
     * Sorting rebuilds the table, so these handlers have to be
     * attached every time draw() runs.
     */
    $("region-table")
      .querySelectorAll("th[data-sort]")
      .forEach((th) => {
        th.onclick = () => {
          const key = th.dataset.sort;

          if (sort.key === key) {
            /*
             * Clicking the same column reverses the order.
             */
            sort.asc = !sort.asc;
          } else {
            /*
             * New columns start ascending.
             *
             * Utilisation is the exception:
             * highest utilisation first is more useful.
             */
            sort.key = key;
            sort.asc = key !== "util";
          }

          draw();
        };
      });

    /*
     * Wire region row navigation.
     */
    $("region-table")
      .querySelectorAll("tr[data-region]")
      .forEach((tr) => {
        tr.onclick = () => {
          navigate(
            `/region/${encodeURIComponent(tr.dataset.region)}`
          );
        };
      });

    /*
     * Restore the filter values safely.
     *
     * THIS IS THE MAIN FIX.
     *
     * We never do:
     *
     * $("some-id").value = ...
     *
     * without first confirming the element exists.
     */
    const restoredQ = $("rg-filter-q");
    const restoredF = $("rg-filter-f");

    if (restoredQ) {
      restoredQ.value = keep.q;
    }

    if (restoredF) {
      restoredF.value = keep.f;
    }

    /*
     * Wire search/filter behavior.
     */
    wireFilter(
      "rg-filter",
      "#region-table tr[data-region]",
      {
        noun: "regions"
      }
    );
  }

  /*
   * Initial render.
   */
  await draw();
};



/* ==================================================================== deep */
/* One region — its own page.                                                */

PAGES["/region"] = async (view, name, showAll = false) => {
  if (!name) { view.innerHTML = `<p class="error">No region selected.</p>`; return; }
        const r = await get(`/api/region/${encodeURIComponent(name)}`);
    const t = r.threshold;
    /* check_expansion() returns a summary object, not a list of cells -- the
       blocked ones are already named for us. Treating it as an array threw a
       TypeError here, which left the panel showing "Loading…" forever with the
       real error only visible in the console. */
    const blocked = r.features?.blocked_features || [];

    /* Subtitle carries the site count only. Hardware was removed on review --
       a region holds many facilities that may run different classes, so naming
       one here is wrong -- and the incident count went with it because the
       figure that matters is capacity owed, not tickets raised. */
    const atRisk = t.current_utilisation_pct > t.threshold_pct;
    const used = Math.round(r.capacityUnits - r.capacityUnitsFree);
    view.innerHTML = title(`Region: ${name}`, `${r.siteCount} capacity pools`) + panel(`Region: ${name}`, `
      <p style="margin-top:0"><b>${atRisk ? "In risk." : "Not in risk."}</b> ${esc(t.reason)}</p>
      <div class="kpis" style="margin:1rem 0">
        ${kpi("Total CU", num(Math.round(r.capacityUnits)),
              `${num(Math.round(r.capacityUnitsFree))} free across ${r.siteCount} sites`, "ink",
              "Capacity Units deployed across every capacity pool in this region.")}
        ${/* Utilised CU and Utilisation were two cards saying one thing: review
              asked for them consolidated, so the count leads and the share of
              deployed capacity sits under it. */ ""}
        ${kpi("Utilised CU", `${num(used)} <span class="kpi-of">of ${num(Math.round(r.capacityUnits))}</span>`,
              `${pct(t.current_utilisation_pct, 1)} of deployed capacity`,
              atRisk ? "bad" : "ink",
              "Capacity Units currently in use, and what share of the region's deployed "
              + "total that is.")}
        ${/* Safety threshold and Threshold status were both removed on review.
              The threshold is a fixed policy figure that does not move, and its
              status is already stated in words at the top of this panel -- three
              cards for one unchanging number. The line each capacity pool holds is
              in the table below, which is where review said the threshold
              belongs. */ ""}
        ${/* Review: "the threshold should be part of a capacity pool, not at a
              region level. First look at a capacity pool, then roll it up." A
              region with ten sites where one is full is not constrained -- the
              work goes to one of the other nine. What is constrained is a
              region with nowhere left to put it, so this is the room remaining
              in the sites that still have any, set against what has been
              asked for. */ ""}
        ${(() => { const sr = r.sitesRollup || {};
          if (!sr.sites) return "";
          return kpi("Placeable capacity", num(Math.round(sr.placeableCu)),
            sr.canAbsorbPipeline
              ? `across ${num(sr.sitesWithRoom)} of ${num(sr.sites)} sites with room`
              : `${num(Math.round(sr.shortBy))} CU short of the ${num(Math.round(sr.pendingCu))} requested`,
            sr.canAbsorbPipeline ? "good" : "bad",
            `Capacity Units that can still be handed out in this region: the room left `
            + `under each capacity pool's own safety line, added up across the `
            + `${num(sr.sitesWithRoom)} sites that still have some. Capacity in a site `
            + `already over its line cannot be given away, which is why this is lower `
            + `than the region's free total. ${sr.sitesOverLine
                ? `${num(sr.sitesOverLine)} of ${num(sr.sites)} sites here are over their own line.`
                : "No site here is over its own line."} `
            + `A region is only short when there is nowhere left in it to place the `
            + `work \u2014 a customer picks a region, not a building.`);
        })()}
        ${kpi("Requested new capacity", num(Math.round(t.cores_pending || 0)),
              `in pipeline from ${t.customers_waiting || 0} customer(s)`,
              t.cores_pending ? "bad" : "good",
              "Capacity Units asked for and not yet delivered \u2014 the pipeline for this "
              + "region, not a count of tickets. Requests are raised against a specific "
              + "SKU; this total does not yet say which, so it cannot tell you whether the "
              + "region holds the SKUs the requests actually need.")}
      </div>

      ${/* This table gained five columns when the Regions tab lost them. They
             were always site questions: when does it cross its line, what would
             it take to stay under, what does it owe, who is waiting. Asked of a
             region they produce an average that hides the building actually in
             trouble; asked of a building they are answerable. */ ""}
      <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Every capacity pool in this region</h4>
      <p style="color:var(--ink-2);font-size:.82rem;margin:0 0 .5rem">
        The capacity position of each building. The Regions tab deliberately carries
        only how full a region is — everything that can be acted on is here, one
        capacity pool at a time.
      </p>
      <div class="scroll-x"><table>
        <thead><tr><th>Capacity pool</th><th class="n">CU</th><th class="n">Free</th>
          <th class="n">Threshold</th><th class="n">Utilisation</th>
          <th>Threshold status</th>
          ${th("Hits threshold in", "How long until this capacity pool reaches its own "
             + "safety threshold on its current trend, from a model backtested on this "
             + "building's own daily CU record. The same forecast the site page draws — "
             + "not a second, cheaper fit that would give a different answer to the same "
             + "question.", "n")}
          ${th("To stay under", "The Capacity Units this capacity pool would have to add "
             + "for its current usage to sit under its own threshold. Utilisation is "
             + "usage over deployed capacity, so it needs usage \u00d7 100 \u00f7 threshold "
             + "deployed, and this is the gap. A floor, not a purchase order: Capacity "
             + "Units are not bought loose, so the SKU beneath is the smallest single "
             + "one that covers it.", "n")}
          ${th("CU pending", "Capacity requested against this capacity pool and not yet "
             + "delivered. What this building owes, not how many tickets failed — one "
             + "ticket can be worth hundreds of CU.", "n")}
          ${th("Waiting", "Distinct customers with an unmet request raised against this "
             + "capacity pool.", "n")}
          <th class="n">Requests</th>
          <th class="n">Failed</th>
          <th class="n">Oldest open</th><th class="n">Revenue loss</th>
          <th>Denial cause and recommended action</th></tr></thead>
        <tbody>${(r.datacentres || []).map((x) => `<tr class="clickable" data-dc="${esc(x.datacentre)}">
          <td class="mono"><b>${esc(x.datacentre)}</b></td>
          <td class="n">${num(Math.round(x.capacityUnits))}</td>
          <td class="n">${x.capacityUnitsFree <= 0 ? `<b style="color:var(--bad)">0</b>` : num(Math.round(x.capacityUnitsFree))}</td>
          <td class="n">${pct(x.thresholdPct)}</td>
          <td class="n"><b style="color:${x.overThreshold ? "var(--bad)" : "inherit"}">${pct(x.utilisationPct, 1)}</b></td>
          <td>${x.overThreshold
            ? `<span class="pill bad">In risk</span>`
            : `<span class="pill good">Not in risk</span>`}</td>
          <td class="n">${x.hitsThresholdIn == null ? `<span class="t3">—</span>`
            : x.hitsThresholdIn <= 0 ? `<b class="t-bad">already there</b>`
            : `<b>${num(x.hitsThresholdIn)}</b> <span class="t3">days</span>`}</td>
          <td class="n">${x.cuToStayUnder
            ? `<b class="t-warn">${num(Math.round(x.cuToStayUnder))}</b>
               <br><span class="t3">add an ${esc(x.smallestSkuStep)}</span>`
            : `<span class="t3">nothing</span>`}</td>
          <td class="n">${x.cuPending ? `<b>${num(Math.round(x.cuPending))}</b>` : "—"}</td>
          <td class="n">${x.customersWaiting || "—"}</td>
          <td class="n">${num(x.requests)}</td>
          <td class="n">${x.failed ? `<b style="color:var(--bad)">${num(x.failed)}</b>` : "—"}</td>
          <td class="n">${x.oldestOpenDays != null
            ? `<b style="color:${x.oldestOpenDays > 30 ? "var(--bad)" : "inherit"}">${x.oldestOpenDays}d</b>` : "—"}</td>
          <td class="n">${x.revenueLoss ? money(x.revenueLoss) : "—"}</td>
          <td class="why">${(x.recommendations || []).length
            ? x.recommendations.map((r) => `<div style="margin:0 0 .45rem">
                <b>${esc(r.reason)}</b>${r.count > 1 ? ` <span class="pill mute">x${r.count}</span>` : ""}
                ${r.needsHuman ? ` <span class="pill warn">manual review</span>` : ""}
                <br><span style="color:var(--ink-2)">${esc(r.action)}</span>
              </div>`).join("")
            : "—"}</td>
        </tr>`).join("")}</tbody></table></div>
      <p style="color:var(--ink-2);font-size:.82rem;margin:.5rem 0 0">
        All ${r.siteCount} capacity pools are listed. ${r.sitesOverThreshold} of them
        ${r.sitesOverThreshold === 1 ? "is" : "are"} past its own safety threshold,
        and ${r.sitesWithActivity} ${r.sitesWithActivity === 1 ? "has" : "have"}
        had a request raised against
        ${r.sitesWithActivity === 1 ? "it" : "them"}. Those are different things:
        a capacity pool can be over its line with nothing having failed there yet,
        which is the case worth seeing before it becomes a denial. Earlier this
        table showed only the sites carrying a denial, which hid the rest.
        Select a row to open that facility and see the arithmetic behind each
        recommendation.
      </p>

      <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Denial reasons at this location</h4>
      ${(r.reasons || []).length ? `<div class="scroll-x"><table>
        <thead><tr><th>Reason</th><th class="n">Incidents</th><th>Recommended action</th></tr></thead>
        <tbody>${r.reasons.map((x) => `<tr>
          <td><b>${esc(x.reason)}</b>${x.needsHuman
            ? ` <span class="pill warn">manual review</span>` : ""}</td>
          <td class="n">${num(x.count)}</td>
          <td class="why">${esc(x.action)}</td>
        </tr>`).join("")}</tbody></table></div>`
        : `<p style="color:var(--ink-2);margin:0;font-size:.82rem">No refusals recorded here.</p>`}

      <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Incidents
        <span style="font-weight:400;color:var(--ink-3);font-size:.82rem">
          — ${r.tickets.filter((x) => x.isFlagged).length} failures</span></h4>
      <div class="scroll-x"><table>
        <thead><tr><th>Incident</th><th>Customer</th><th>Capacity pool</th>
          <th>Outcome</th><th>Reason</th><th class="n">Days</th>
          <th class="n">Revenue loss</th><th>Revenue loss basis</th></tr></thead>
        <tbody>${r.tickets.filter((x) => x.isFlagged || showAll).map((x) => `<tr>
          <td class="mono">${esc(x.incidentId)}</td>
          <td><b>${esc(x.customerName)}</b><br><span class="pill mute">${esc(x.tier)}</span></td>
          <td class="mono">${esc(x.datacentre)}</td>
          <td>${x.isFlagged ? `<span class="pill bad">${esc(x.outcomeLabel)}</span>`
                            : `<span class="pill good">${esc(x.outcomeLabel)}</span>`}</td>
          <td>${x.reason ? esc(x.reason) : "—"}</td>
          <td class="n">${x.days || "—"}</td>
          <td class="n">${x.exposure ? money(x.exposure) : "—"}</td>
          <td class="why">${calcCell(x)}</td>
        </tr>`).join("")}</tbody></table></div>

      <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Feature availability</h4>
      ${blocked.length ? `<p style="margin:0">${blocked.map((f) =>
          `<span class="pill warn" style="margin-right:.3rem">${esc(f)}</span>`).join("")}</p>
        <p style="color:var(--ink-2);margin:.5rem 0 0;font-size:.82rem">
          ${r.features.features_available} of ${r.features.features_checked} features are live here.
          Recommending expansion into ${esc(name)} is only valid for customers who do not need
          the ${blocked.length === 1 ? "one above" : "ones above"}.</p>`
        : `<p style="color:var(--ink-2);margin:0;font-size:.82rem">All
           ${r.features?.features_checked ?? 0} features are live here — no expansion blocker.</p>`}

      <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Demand anomalies</h4>
      ${r.spikes.length ? `<ul style="margin:0;padding-left:1.1rem">${r.spikes.map((s) => `<li>
          <b>${esc(s.period)}</b> — ${num(s.value)} units against a ${num(s.baseline)} baseline
          (${s.pct_above_baseline}% above).
          ${s.matched
            ? /* event_timing is the module's own phrasing and it handles an
                 event inside the period; days_before_spike goes negative there,
                 which rendered as "-6 day(s) before". */
              `Matched to <b>${esc(s.event_type)}</b> on ${esc(s.event_date)},
               ${esc(s.event_timing)} — <span class="pill ${
                 s.match_strength === "strong" ? "good" : "warn"}">${esc(s.match_strength)}</span>`
            : `<span class="pill mute">no business event found in the window</span>`}
        </li>`).join("")}</ul>
        <p style="color:var(--ink-2);margin:.5rem 0 0;font-size:.82rem">
          A spike with no match is left unexplained on purpose — a detector that
          accounted for every jump would be pattern-matching, not attribution.</p>`
        : `<p style="color:var(--ink-2);margin:0;font-size:.82rem">No demand spikes detected here.</p>`}
    `);

  // Review asked for the forecasts to live where the thing being forecast lives,
  // not only on a tab of their own: "either it will show in the region page or
  // the capacity pool page -- it is supposed to show in both".
  view.insertAdjacentHTML("beforeend", await capacityPanel("region", name));
  view.insertAdjacentHTML("beforeend", await demandPanels("region", name));
  wireCharts(view);

  // The Forecast tab this used to link to has left the sidebar. The forecast
  // did not: it is on each capacity pool above, so the link goes there.
  view.insertAdjacentHTML("beforeend", `<p style="margin:1.5rem 0 0">
    <a href="/regions">← All regions</a> &nbsp;·&nbsp;
    <a href="/datacentres">All capacity pools</a></p>`);

  view.querySelectorAll("tr[data-dc]").forEach((tr) =>
    (tr.onclick = () => navigate(`/datacentre/${encodeURIComponent(tr.dataset.dc)}`)));
  wireRegisterToggle(view, "region", () => PAGES["/region"](view, name, true));
};

PAGES["/customers"] = async (view) => {
  const d = await get("/api/customers");

  view.innerHTML = howto({
    answers: "<b>Failure impact by subscription</b> \u2014 the same incidents as the Regions tab, aggregated by customer.",
    steps: [
      { what: "Subscription table", is: "every affected subscription, ranked by attributed revenue loss." },
      { what: "Subscription drill-down", is: "an account-level recommendation, followed by the full incident history with the calculation behind each figure." },
    ],
    words: [
      { term: "ARR", means: "Annual recurring revenue for the subscription. Free-tier subscriptions carry $0 ARR and therefore always rank last regardless of failure severity — the measure has no visibility of Free-tier impact." },
      { term: "Failed / total", means: "Failed incidents against total incidents raised by that subscription." },
      { term: "Risk score", means: "Computed from that subscription\u2019s own incidents rather than apportioned from a regional total. The account recommendation is strategic \u2014 identifying existing headroom \u2014 rather than restating the regional remediation." },
    ],
    next: "Where a subscription shows repeated failures in one region, review that region on the Actions tab \u2014 each recommendation quantifies how many of the region\u2019s failures the proposed change would have prevented.",
    sources: "ICM capacity incidents joined to the subscription ARR reference.",
  }) + title("Customers", `${d.totalAffected} customers were affected by a capacity failure`) + `
  <div id="cust-table"></div><div id="cust-detail"></div>`;

  $("cust-table").innerHTML = panel("Affected subscriptions", d.customers.length ? `<div class="scroll-x"><table>
    <thead><tr><th class="n">#</th><th>Customer</th><th>Tier</th>
      <th class="n">ARR</th><th class="n">Revenue loss</th>
      <th class="n">Failed / total</th><th>Worst region</th>
      <th>Regions</th><th>Risk score</th></tr></thead>
    <tbody>${d.customers.map((c, i) => `<tr class="clickable${
      c.risk && c.risk.band === "high" ? " at-risk" : ""}" data-sub="${esc(c.subscriptionId)}">
      <!-- The position in this list, which is what the "#" column means. This
           read a rank field that /api/customers has never sent, so every row
           in the table printed the word "undefined" in its first cell. -->
      <td class="n">${i + 1}</td>
      <td><b>${esc(c.customerName)}</b>${c.risk && c.risk.band === "high"
        ? ` <span class="pill bad">at risk</span>` : ""}
        <br><span class="mono" style="font-size:.72rem;color:var(--ink-3)">${esc(c.customerShort)}</span></td>
      <td><span class="pill info">${esc(c.tier)}</span></td>
      <td class="n">${money(c.arr)}</td>
      <td class="n"><b>${money(c.exposure)}</b></td>
      <td class="n">${c.failedRequests} / ${c.totalRequests}</td>
      <td>${esc(c.worstRegion)}</td>
      <td>${c.regionBreakdown
        ? c.regionBreakdown.map((r) => `<span class="pill mute">${esc(r.region)} · ${r.requests}</span>`).join(" ")
        : c.regions.map((r) => `<span class="pill mute">${esc(r)}</span>`).join(" ")}</td>
      <td>${c.risk ? riskCell(c.risk) : "—"}</td>
    </tr>`).join("")}</tbody></table></div>`
    : `<p class="empty">No affected customers.</p>`, { hint: "click a row for every request", flush: true });

  $("cust-table").querySelectorAll("tr[data-sub]").forEach((tr) =>
    (tr.onclick = () => navigate(`/customer/${encodeURIComponent(tr.dataset.sub)}`)));
};

/* ==================================================================== deep */
/* One customer — its own page.                                              */

PAGES["/customer"] = async (view, sub, showAll = false) => {
  if (!sub) { view.innerHTML = `<p class="error">No customer selected.</p>`; return; }
  const c = await get(`/api/customer/${encodeURIComponent(sub)}`);
  // The strategic recommendation is computed across the customer list, so it
  // is fetched from there rather than duplicated on the per-customer payload.
  const list = await get("/api/customers");
  const me = list.customers.find((x) => x.subscriptionId === sub) || {};
  const rec = me.recommendation;

  view.innerHTML = howto({
    answers: `<b>Everything recorded for ${esc(c.customerName)}</b> — capacity position, account-level recommendation, and every request they have raised.`,
    steps: [
      { what: "Account position", is: "what they pay us per year, how much of it is exposed, and how many of their requests failed." },
      { what: "Recommendation", is: "strategic rather than per-ticket — where this customer already has headroom, or whether every region they sit in is genuinely full." },
      { what: "Incident register", is: "every request they raised, with the derivation behind each revenue-loss figure." },
    ],
    next: "Where the recommendation points at another region, check that region has the Fabric features this customer needs before proposing a move.",
    sources: "ICM incidents for this subscription joined to the ARR reference.",
  }) + title(c.customerName, `${c.tier} subscription · ${c.subscriptionId}`) + `

  <div class="kpis">
    ${kpi("ARR at risk", money(c.arr), `${esc(c.tier)} subscription`, "ink",
          "What this customer pays Microsoft per year. This is the revenue exposed by their failed requests, not the amount lost.")}
    ${kpi("Failed requests", `${c.failedCount} of ${c.totalCount}`, "of total requests raised",
          c.failedCount ? "bad" : "good")}
    ${kpi("Revenue loss", money(c.exposure), "attributed to this subscription", "bad")}
    ${kpi("Regions", num(c.regions.length), c.regions.join(", "), "ink")}
  </div>

  ${rec ? panel("Account recommendation", `
    <p style="margin:0 0 .5rem"><b>${esc(rec.headline)}</b></p>
    <p style="margin:0;color:var(--ink-2)">${esc(rec.detail)}</p>`) : ""}

  ${panel("Incident register", `<div class="scroll-x"><table>
    <thead><tr><th>Incident</th><th>Region</th><th>Capacity pool</th>
      <th>Outcome</th><th>Reason</th><th class="n">Units requested</th>
      <th class="n">Days</th><th class="n">Revenue loss</th>
      <th>Revenue loss basis</th></tr></thead>
    <tbody>${c.requests.filter((x) => x.isFlagged || showAll).map((x) => `<tr>
      <td class="mono">${esc(x.incidentId)}</td>
      <td><a href="/region/${encodeURIComponent(x.region)}">${esc(x.region)}</a></td>
      <td class="mono"><a href="/datacentre/${encodeURIComponent(x.datacentre)}">${esc(x.datacentre)}</a></td>
      <td>${x.isFlagged ? `<span class="pill bad">${esc(x.outcomeLabel)}</span>`
                        : `<span class="pill good">${esc(x.outcomeLabel)}</span>`}</td>
      <td>${x.reason ? esc(x.reason) : "—"}</td>
      <td class="n">${num(x.askedFor)}</td>
      <td class="n">${x.days || "—"}</td>
      <td class="n">${x.exposure ? money(x.exposure) : "—"}</td>
      <td class="why">${calcCell(x)}</td>
    </tr>`).join("")}</tbody></table></div>
    ${registerToggle("customer", showAll ? 0 : c.requests.filter((x) => !x.isFlagged).length)}`,
    { flush: true })}

  <div id="cust-demand"></div>

  <p style="margin:1.5rem 0 0"><a href="/customers">← All customers</a></p>`;

  // Customer-level forecasting, which review asked for and accepted would need
  // a synthesised history. The panel says so at the top rather than in a footnote.
  $("cust-demand").innerHTML = await customerDemandPanel(sub);
  wireCharts($("cust-demand"));
  wireRegisterToggle(view, "customer", () => PAGES["/customer"](view, sub, true));
};

/* ==================================================================== 5/6 */
/* Actions                                                                   */

/* Render whatever decision is already on record for a region. Read from the
   server rather than remembered in the page: the same recommendation may have
   been decided from the CLI, or by a colleague, or in an earlier session. */
function decided(a, region) {
  const d = a.decisions?.[region];
  if (!d) return "";
  const tone = d.decision === "approve" ? "good" : "bad";
  const why = d.reason ? ` — ${esc(d.reason)}` : "";
  return `<span class="pill ${tone}">${esc(d.decision)}d by ${esc(d.by)}` +
         ` on ${esc(String(d.at).slice(0, 10))}${why}</span>`;
}

PAGES["/actions"] = async (view) => {
  const [a, d] = await Promise.all([get("/api/actions"), get("/api/overview")]);

  view.innerHTML = howto({
    answers: "<b>Recommended remediation per ranked region</b>, with a scale calculator for modelling an F-SKU change before committing to it.",
    steps: [
      { what: "Each card", is: "one region \u2014 problem, cause, impact and expected effect, followed by the incidents the recommendation was derived from." },
      { what: "Accept / Reject", is: "at the bottom of each recommendation. Nothing is actioned automatically \u2014 a named person decides. A rejection requires a justification, which is what tells the next run whether to suppress the region or re-raise it." },
      { what: "Capacity scale calculator", is: "select a region, the capacity to change, and the rung to move it to. Returns what it would be running at afterwards, and whether the move crosses the F64 licensing line or the F256/F512 boundary. There is no feasibility question: scaling an F SKU applies immediately and takes nothing offline." },
    ],
    words: [
      { term: "Failure mode", means: "Whether the constraint is approval latency or genuine capacity shortage. The two require opposite remediations, so each card states which applies." },
      { term: "Justification requirement", means: "Every decision is appended to a decision log with owner, timestamp and rationale. The next run suppresses a rejected region \u2014 without a justification it cannot determine whether to suppress or re-raise." },
    ],
    next: "Record an accept or reject decision against each recommendation. The decision is logged and governs what the next run reports.",
    sources: "the classified capacity-request history, and the Fabric capacities behind each region.",
  }) + title("Actions", `${a.recommendations.length} things to decide on — self-check ${a.gate.passed ? "passed" : "FAILED"}`) + `

  ${a.gate.passed ? "" : `<p class="error"><b>The self-check failed.</b> ${esc(a.gate.detail)}
    The output below is retained for diagnostic purposes only. Do not action it
    and do not distribute it.</p>`}

  ${a.recommendations.map((r, i) => panel(`#${r.rank} ${r.region}`, `
    <p style="margin:0 0 1rem;font-size:.95rem"><b>${esc(r.headline)}</b></p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin-bottom:1rem">
      <div><b style="font-size:.78rem;color:var(--ink-2)">PROBLEM</b><p style="margin:.2rem 0">${esc(r.problem)}</p></div>
      <div><b style="font-size:.78rem;color:var(--ink-2)">CAUSE</b><p style="margin:.2rem 0">${esc(r.cause)}</p></div>
      <div><b style="font-size:.78rem;color:var(--ink-2)">IMPACT</b><p style="margin:.2rem 0">${esc(r.impact)}</p></div>
      <div><b style="font-size:.78rem;color:var(--ink-2)">EFFECT</b><p style="margin:.2rem 0">${esc(r.effect)}</p></div>
    </div>
    <p style="background:var(--brand-wash);padding:.8rem 1rem;border-radius:3px;margin:0 0 1rem">
      <b>Recommended action:</b> ${esc(r.action)}</p>
    <p style="color:var(--ink-2);margin:0 0 1rem">${esc(r.rationale)}</p>
    ${r.evidence?.length ? `<details><summary style="cursor:pointer;font-size:.85rem;color:var(--brand)">
      Evidence (${r.evidence.length})</summary>
      <ul style="margin:.5rem 0 0;padding-left:1.1rem;font-size:.85rem;color:var(--ink-2)">
        ${r.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>` : ""}
    <div style="display:flex;gap:.6rem;margin-top:1.15rem;align-items:center;flex-wrap:wrap">
      <button class="btn" data-region="${esc(r.region)}" data-decision="approve">👍 Accept</button>
      <button class="btn ghost" data-region="${esc(r.region)}" data-decision="reject">👎 Reject</button>
      <input type="text" id="reason-${esc(r.region)}" placeholder="Justification (required to reject)"
             style="flex:1;min-width:220px">
      <span id="decision-${esc(r.region)}" style="font-size:.8rem">${decided(a, r.region)}</span>
    </div>`, { hint: `owner: ${r.owner}` })).join("")}

  ${panel("Capacity scale calculator", `
    <p style="margin:0 0 1rem;color:var(--ink-2)">
      Pick a capacity and a rung on the F-SKU ladder. The change applies
      immediately — there is nothing to order and nothing goes offline, so the
      only questions are what it would be running at afterwards and whether the
      move crosses a line that costs you something else.
    </p>
    <div style="display:flex;gap:1.5rem;flex-wrap:wrap;align-items:center;margin-bottom:1rem">
      <label class="ctl">Region <select id="sc-region"></select></label>
      <label class="ctl">Capacity <select id="sc-cap"></select></label>
      <label class="ctl">Scale to <select id="sc-sku"></select></label>
    </div>
    <div id="sc-out"></div>`)}`;

  /* --- capacity scale calculator ---------------------------------------
     Replaces a calculator that asked which building to take offline and what to
     convert it into. Fabric has no building to empty and nothing to convert; it
     has a ladder, and one capacity at a time moves along it. */
  const opt = await get("/api/scale-options");
  const LADDER = Object.keys(opt.skuLadder);

  function fillRegions() {
    $("sc-region").innerHTML = opt.regions
      .map((r) => `<option>${esc(r)}</option>`).join("");
  }
  function fillCapacities() {
    const inRegion = opt.capacities.filter((c) => c.region === $("sc-region").value);
    $("sc-cap").innerHTML = inRegion.map((c) =>
      `<option value="${esc(c.capacityId)}">${esc(c.capacityId)} — ${esc(c.sku)}${
        c.throttledDays ? ` · throttling ${c.throttledDays}d` : ""}</option>`).join("");
  }
  function fillTargets() {
    // Never offer the SKU it already runs: that is not a change.
    const cap = opt.capacities.find((c) => c.capacityId === $("sc-cap").value);
    const current = cap ? cap.sku : "";
    $("sc-sku").innerHTML = LADDER.filter((k) => k !== current)
      .map((k) => `<option value="${esc(k)}">${esc(k)} — ${num(opt.skuLadder[k])} CU</option>`)
      .join("");
    if (cap) {
      // Default to the rung the product would recommend, so the first thing
      // shown is the answer rather than an arbitrary SKU.
      const up = LADDER.find((k) => opt.skuLadder[k] > cap.capacityUnits);
      if (up) $("sc-sku").value = up;
    }
  }

  async function runScale() {
    const out = $("sc-out");
    if (!$("sc-cap").value || !$("sc-sku").value) { out.innerHTML = ""; return; }
    out.innerHTML = `<p class="loading" style="padding:0">Calculating\u2026</p>`;
    try {
      const q = new URLSearchParams({
        capacity: $("sc-cap").value, to_sku: $("sc-sku").value,
      });
      const v = await get(`/api/scale?${q}`);
      const c = v.current, t = v.selected;
      const relief = t.peakAfterPct < c.peakPct;

      const notes = [];
      if (t.gainsFreeViewers) {
        notes.push(["good", `At ${esc(t.sku)} this capacity reaches F64, so Power BI
          content on it becomes readable on a Free licence. Viewers who need Pro
          or PPU today would not.`]);
      }
      if (t.losesFreeViewers) {
        notes.push(["bad", `This drops below F64. Power BI content here stops being
          readable on a Free licence, so every viewer needs Pro or PPU — which can
          cost more than the smaller SKU saves.`]);
      }
      if (t.crossesSlowBoundary) {
        notes.push(["warn", `This crosses the F256/F512 boundary, which Microsoft
          notes can scale more slowly than moves within either side of it.`]);
      }
      if (t.stillBursts) {
        notes.push(["warn", `At ${esc(t.sku)} the measured peak is still
          ${pct(t.peakAfterPct)} — above the ceiling. Bursting is not a fault on its
          own, but this capacity would keep generating overage.`]);
      }

      out.innerHTML = `
        <p style="margin:0 0 1rem">
          <b>${esc(v.capacityId)}</b> runs <b>${esc(c.sku)}</b> on
          ${num(c.capacityUnits)} CU, averaging ${pct(c.meanPct)} and peaking at
          ${pct(c.peakPct)} over ${num(c.windowDays)} days. Moving it to
          <b>${esc(t.sku)}</b>:
        </p>
        <div class="kpis">
          ${kpi("Capacity Units", num(t.capacityUnits),
                `from ${num(c.capacityUnits)} (${t.cuDeltaPct > 0 ? "+" : ""}${t.cuDeltaPct.toFixed(0)}%)`,
                t.direction === "up" ? "good" : "ink")}
          ${kpi("Peak after the move", pct(t.peakAfterPct),
                `from ${pct(c.peakPct)}`, relief ? "good" : "bad")}
          ${kpi("Headroom against peak", pct(t.headroomPct),
                t.stillBursts ? "still bursting past the ceiling" : "below the ceiling",
                t.stillBursts ? "bad" : "good")}
          ${kpi("When it takes effect", "Immediately",
                "an F SKU is scaled in Azure", "good")}
        </div>

        <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Is this the right rung?</h4>
        <p style="background:${t.comfortable ? "var(--good-wash)" : "var(--bad-wash)"};
           padding:.8rem 1rem;border-radius:3px;margin:0 0 .75rem">
          <b>${t.comfortable ? "Yes." : "Not on its own."}</b>
          ${t.comfortable
            ? `It leaves the mean at ${pct(t.meanAfterPct)} and the peak at
               ${pct(t.peakAfterPct)}, both inside the ceiling.`
            : `It leaves the mean at ${pct(t.meanAfterPct)} and the peak at
               ${pct(t.peakAfterPct)}.${v.recommended
                 ? ` ${esc(v.recommended)} is the smallest rung that clears both.`
                 : ""}`}
        </p>
        ${notes.map(([tone, text]) => `
          <p style="background:var(--${tone}-wash);padding:.7rem 1rem;border-radius:3px;
             margin:0 0 .55rem;font-size:.86rem">${text}</p>`).join("")}
        <p class="t3" style="font-size:.82rem;margin:.4rem 0 0">${esc(v.why)}</p>`;
    } catch (e) {
      out.innerHTML = `<p class="empty" style="padding:0">${esc(e.message)}</p>`;
    }
  }

  $("sc-region").onchange = () => { fillCapacities(); fillTargets(); runScale(); };
  $("sc-cap").onchange = () => { fillTargets(); runScale(); };
  $("sc-sku").onchange = runScale;
  fillRegions(); fillCapacities(); fillTargets(); runScale();

  view.querySelectorAll("button[data-decision]").forEach((b) => (b.onclick = async () => {
    const { region, decision } = b.dataset;
    const out = $(`decision-${region}`);
    const reason = $(`reason-${region}`).value.trim();

    /* A rejection with no reason cannot drive the difference between
       "suppress this next week" and "re-open it" -- so it is required here,
       not merely encouraged. */
    if (decision === "reject" && !reason) {
      out.innerHTML = `<span class="pill warn">A reason is required to reject.</span>`;
      $(`reason-${region}`).focus();
      return;
    }

    out.innerHTML = `<span class="pill mute">Recording…</span>`;
    try {
      const r = await post("/api/decision", { region, decision, reason });
      const dec = r.decision;
      out.innerHTML = `<span class="pill ${decision === "approve" ? "good" : "bad"}">
        ${esc(dec.decision)}d by ${esc(dec.by)} — recorded</span>`;
      $(`reason-${region}`).value = "";
    } catch (e) {
      out.innerHTML = `<span class="pill bad">Not recorded: ${esc(e.message)}</span>`;
    }
  }));

};

/* ==================================================================== 6/6 */
/* Methodology                                                               */

/* ==================================================================== 11/12 */
/* Recommendations                                                            */

/* Three engines, one list. Review asked for a product that says what to do
   rather than what happened, and the three cases here are the ones a utilisation
   figure cannot make on its own:

     procurement      buy now, though it is below the trigger, because the wait grew
     workload change  move it, though it has room, because the hardware keeps failing
     licensing        step up, though it is not full, because of who can read it

   The routine overdue purchases are the bulk of the list and the least
   interesting part of it: a region past its threshold needs buying and every
   other screen already says so. The two counts that lead the page are the ones
   nothing else would have surfaced. */

/* Whether anything in a region is refusing work right now.

   The threshold status beside this answers a different question -- is there
   room here to grant more capacity -- and it cannot answer this one, because
   Capacity Units do not pool. westeurope reads 83.1% against a 90% line and is
   "not in risk", and inside that average one F8 runs at 182.5%, has throttled
   every day for thirty days and has refused 1,481 operations. It cannot borrow
   a single CU from the F32 sitting at 33% beside it.

   An executive reading the region tables had no way to see that: the throttling
   was computed, shown on the capacity pages, and absent from every screen above
   them. So it is stated here, next to the status rather than instead of it,
   because both are true and they answer to different people. */
function refusingNow(t) {
  if (!t || !t.capacities) return "";
  if (!t.throttling) {
    return `<span class="t3" style="font-size:.72rem">nothing refusing work</span>`;
  }
  const severe = t.worstStage === "background_rejection"
    || t.worstStage === "interactive_rejection";
  return `<span class="refusing ${severe ? "t-bad" : "t-warn"}">
      ${num(t.throttling)} of ${num(t.capacities)} refusing work</span>
    ${t.operationsRefused
      ? `<span class="t3" style="font-size:.72rem">${num(t.operationsRefused)} operations turned away</span>`
      : ""}`;
}

const REC_KIND = {
  scale_up: { label: "Scale up", tone: "bad" },
  reclaim: { label: "Reclaim idle capacity", tone: "warn" },
  load_balance: { label: "Move a workspace", tone: "warn" },
  scale_down: { label: "Scale down", tone: "" },
  licensing: { label: "Licence", tone: "" },
};

function recCard(r) {
  const e = r.evidence || {};
  const k = REC_KIND[r.kind] || { label: r.kind, tone: "" };
  return `
  <article class="rec">
    <header>
      <span class="pill ${k.tone}">${k.label}</span>
      <b>${esc(r.headline)}</b>
    </header>
    <p class="rec-why">${esc(r.detail)}</p>
    ${r.kind === "reclaim" ? reclaimEvidence(e) : `
    <p class="ev">
      ${e.region ? `<a href="/region/${encodeURIComponent(e.region)}">${esc(e.region)}</a>` : ""}
      ${e.datacentre ? `· <a href="/datacentre/${encodeURIComponent(e.datacentre)}">${esc(e.datacentre)}</a>` : ""}
      ${e.fabricSku ? `· <b>${esc(e.fabricSku)}</b>` : ""}
      ${e.capacityUnits != null ? `· ${num(e.capacityUnits)} CU` : ""}
      ${e.meanUtilisationPct != null ? `· ${pct(e.meanUtilisationPct, 0)} used` : ""}
      ${e.throttledDays ? `· throttled ${e.throttledDays}/${e.windowDays}d` : ""}
      ${e.worstStageLabel && e.worstStage !== "none" ? `· ${esc(e.worstStageLabel)}` : ""}
      ${e.scaleTo ? `· to <b>${esc(e.scaleTo)}</b> (${num(e.scaleToUnits)} CU)` : ""}
      ${e.workspace ? `· move <b>${esc(e.workspace)}</b> (${pct(e.workspaceSharePct, 0)}) to ${esc(e.moveTo)}` : ""}
      ${e.stepTo ? `· step to <b>${esc(e.stepTo)}</b>` : ""}
    </p>`}
  </article>`;
}

/* A reclaim is the only recommendation about two parties rather than one
   capacity, and the reader is an executive deciding whether a call is worth
   making. So it leads with what the region refused and what that cost, names
   the account holding the idle capacity, and states plainly that nobody can
   action it on the customer's behalf -- a transfer is the obvious wrong reading
   and the screen has to close it off. */
/* The scored row for the model actually in use.

   The KPIs beside the chart read scores[0], which is the backtest winner.
   Where the model is forced -- it is set to sarima for every region here --
   that is a different model, so northcentralus printed holt_winters' 1.06%
   error under "Model used: sarima", whose own error is 1.11%. westeurope was
   worse: it showed theil_sen's positive skill while sarima, the model actually
   drawing the line on the chart, scores -0.2% and is beaten by assuming
   nothing changes at all.

   Falls back to the winner only if the model in use was not scored, which
   should not happen and would be visible in the table if it did. */
function scoreFor(f) {
  return (f.scores || []).find((s) => s.model === f.model) || (f.scores || [])[0];
}

function reclaimEvidence(e) {
  return `
    <div class="reclaim-ev">
      <div class="reclaim-cols">
        <div>
          <span class="t3">Refused in ${esc(e.region || "")}</span>
          <b class="t-bad">${money(e.exposureUnblocked)}</b>
          <span class="t3">${num(e.refusedRequests)} request(s) ·
            ${num(e.refusedAccounts)} account(s) · short by ${num(e.shortfallUnits)} CU</span>
        </div>
        <div>
          <span class="t3">Idle, and held by</span>
          <b>${esc(e.heldByName || "unidentified")}</b>
          <span class="t3"><a href="/capacity/${encodeURIComponent(e.capacityId || "")}">${esc(e.capacityId || "")}</a>
            · ${esc(e.fabricSku || "")} at ${pct(e.meanUtilisationPct, 0)} for ${num(e.windowDays)}d</span>
        </div>
        <div>
          <span class="t3">Returns to the region</span>
          <b class="t-good">${num(e.releasesUnits)} CU</b>
          <span class="t3">${esc(e.fabricSku || "")} → ${esc(e.stepTo || "")} ·
            covers ${pct(e.coversPct, 0)} of the shortfall</span>
        </div>
        <div>
          <span class="t3">Next step</span>
          <b>Account conversation</b>
          <span class="t3">${esc(e.owner || "")}</span>
        </div>
      </div>
      <p class="reclaim-note">
        <b>This is a recommendation, not an action.</b> A Fabric capacity belongs to
        the tenant that owns it — it cannot be moved to another customer, and
        Microsoft cannot resize it on their behalf. What is being proposed is a
        conversation with ${esc(e.heldByName || "the account")}: if they step down to
        ${esc(e.stepTo || "")}, ${num(e.releasesUnits)} CU returns to
        ${esc(e.region || "")} and the next request there can be granted from it.
        ${e.losesFreeViewers
          ? `<b class="t-warn">Weigh first:</b> ${esc(e.stepTo || "")} is below F64, so
             every Power BI viewer on that capacity would need a Pro or PPU licence.`
          : ""}
      </p>
    </div>`;
}

PAGES["/recommendations"] = async (view, _unused, query) => {
  const params = new URLSearchParams(query || location.search);
  const kind = params.get("kind") || "";
  const region = params.get("region") || "";
  const qs = [kind && `kind=${encodeURIComponent(kind)}`,
              region && `region=${encodeURIComponent(region)}`,
              "limit=200"].filter(Boolean).join("&");
  const d = await get(`/api/recommendations?${qs}`);
  const counts = d.countsByKind || {};

  const scope = region ? ` in ${region}` : "";
  view.innerHTML = howto({
    answers: `<b>What to do next${scope}</b>, most urgent first — with the reasoning under each one rather than a score.`,
    steps: [
      { what: "Scale up", is: "the capacity is throttling, or has no headroom left for the next surge. Fabric absorbs ten minutes of overage, then delays interactive jobs by 20 seconds, then rejects them at an hour, then rejects everything at 24 hours. Scaling an F SKU takes effect immediately \u2014 there is nothing to order." },
      { what: "Move a workspace", is: "one workspace is most of what a throttling capacity consumes. Moving it costs nothing per second, where the next SKU up bills continuously." },
      { what: "Scale down", is: "the capacity is idle. F SKUs bill per second whether or not anything runs on them, so unused CUs are a standing cost. Nothing that throttled in the window appears here." },
      { what: "Reclaim idle capacity", is: "the region refused somebody while capacity sat idle in it. Names the account holding it, what the region refused, and how much of that shortfall stepping down would cover. It is a conversation to have, not a change anyone can make for the customer \u2014 a Fabric capacity belongs to its tenant." },
      { what: "Licence", is: "a commercial step, not a capacity one. Below F64 every Power BI viewer needs Pro or PPU; at F64 a Free licence and a viewer role are enough." },
    ],
    words: [
      { term: "Capacity Units (CU)", means: "What a Fabric SKU provides. An F64 gives 64 CUs, so a day of it is 64 \u00d7 86,400 CU seconds. Consumption is measured against that." },
      { term: "Bursting and smoothing", means: "Fabric lets an operation use more compute than the SKU provides, then spreads the cost over future 30-second timepoints \u2014 interactive over 5 to 64 minutes, background over 24 hours. So <b>utilisation above 100% is normal</b> and is not by itself a fault." },
      { term: "Throttling stages", means: "Only when smoothed consumption eats into future capacity does throttling begin. Ten minutes is free (overage protection); past that, interactive delay at 20 seconds, interactive rejection at an hour, background rejection at 24 hours." },
      { term: "Why these are separate", means: "A throttling capacity and an idle one are opposite problems, and an average of them describes neither. Blending them into one score is how a capacity refusing user queries reads as calm." },
      { term: "Why so few reclaims exist", means: "Capacity is not divisible and the ladder doubles, so slack is only recoverable when the <b>whole next rung down</b> still fits. An F64 running 40 CU cannot give up 24 \u2014 there is no F24, and F32 would take eight CU away from what it is using. That is why a fleet of hundreds yields a handful, and the value is naming the region, the account and the amount rather than the volume." },
    ],
    next: "Work the throttling capacities first — those are refusing user operations now. Scale-downs are money rather than risk and can wait.",
    sources: "Capacities, their CU consumption and their throttling history are generated. The Fabric SKU ladder, the CU arithmetic, the published throttling thresholds and the F64 licensing rule are real.",
  }) + title(`Recommendations${scope}`,
             `${num(d.total)} outstanding${kind ? ` · ${kind.replace("_", " ")}` : ""}`) + `

  <section class="panel"><div class="body rec-filters">
    <a class="chip${!kind ? " on" : ""}" href="/recommendations${region ? `?region=${encodeURIComponent(region)}` : ""}">All ${num(Object.values(counts).reduce((a, b) => a + b, 0))}</a>
    ${Object.entries(REC_KIND).map(([k, meta]) => `
      <a class="chip${kind === k ? " on" : ""}"
         href="/recommendations?kind=${k}${region ? `&region=${encodeURIComponent(region)}` : ""}">
        ${meta.label} ${num(counts[k] || 0)}</a>`).join("")}
    ${region ? `<a class="chip" href="/recommendations?kind=${encodeURIComponent(kind)}">All regions</a>` : ""}
    <span class="t3" style="margin-left:auto">
      <b class="t-bad">${num(counts.scale_up || 0)}</b> capacit(y/ies) to scale up ·
      <b class="t-warn">${num(counts.load_balance || 0)}</b> workspace move(s)</span>
  </div></section>

  ${d.recommendations.length
    ? `<div class="recs">${d.recommendations.map(recCard).join("")}</div>`
    : `<section class="panel"><div class="body"><p class="empty">Nothing outstanding here.</p></div></section>`}

  ${d.shown < d.total ? `<p class="hint" style="margin:1rem 0 0">
    Showing the ${num(d.shown)} most urgent of ${num(d.total)}. The remainder are
    routine overdue purchases in regions already past their threshold.</p>` : ""}`;
};


/* One capacity: what it is, how it has run, and what has gone wrong on it.

   The deepest level the product now reaches. Review's analogy for why it exists:
   a phone with plenty of storage that switches off every five minutes is not a
   phone you keep, and a capacity averaging 60% that spends a week refusing
   queries at peak is not capacity you keep either. A mean cannot tell those
   apart, which is why the throttling history sits beside it. */
PAGES["/capacity"] = async (view, id) => {
  const d = await get(`/api/capacity/${encodeURIComponent(id)}`);
  const h = d.health;
  const bad = h.throttledDays > 0;

  view.innerHTML = title(d.capacityId,
    `${d.fabricSku} · ${num(d.capacityUnits)} Capacity Units · in ${d.datacentre}, ${d.region}`) + `

  <div class="kpis">
    <div class="kpi"><div class="label">Wants, on an average day</div>
      <div class="value${h.meanUtilisationPct >= 85 ? " bad" : ""}">${
        (d.capacityUnits * h.meanUtilisationPct / 100).toFixed(1)}<span style="font-size:1.2rem"> CU</span></div>
      <div class="sub">it has ${num(d.capacityUnits)} · busiest day wanted
        ${(d.capacityUnits * h.peakUtilisationPct / 100).toFixed(1)} CU</div></div>
    <div class="kpi"><div class="label">Throttled</div>
      <div class="value${bad ? " bad" : ""}">${h.throttledDays}</div>
      <div class="sub">of the last ${h.windowDays} days</div></div>
    <div class="kpi"><div class="label">What is happening</div>
      <div class="value${bad ? " bad" : " good"}" style="font-size:1.5rem;line-height:1.5">
        ${esc((PROBLEM[bad ? h.worstStage : "none"] || PROBLEM.none).text)}</div>
      <div class="sub">${bad
        ? `borrowed ${num(Math.round(h.peakFutureMinutes))} minutes of future capacity at its worst`
        : "never delayed or refused anything"}</div></div>
    <div class="kpi"><div class="label">Queries refused</div>
      <div class="value${(h.interactiveRejected + h.backgroundRejected) ? " bad" : ""}">
        ${num(h.interactiveRejected + h.backgroundRejected)}</div>
      <div class="sub">${num(h.interactiveRejected)} interactive · ${num(h.backgroundRejected)} background</div></div>
  </div>

  ${d.recommendations.length ? panel("What to do about it",
    changeBlock({ region: d.region, recommendations: d.recommendations.reduce((a, r) => {
      (a[r.kind] = a[r.kind] || []).push(r); return a; }, {}) }), { flush: true }) : ""}

  ${panel("How it consumes capacity", `
    <div class="tablewrap"><table class="grid"><tbody>
      <tr><th>SKU</th><td><b>${esc(d.fabricSku)}</b> — ${num(d.capacityUnits)} Capacity Units</td></tr>
      <tr><th>A day of it</th><td>${num(d.cuSecondsPerDay)} CU seconds
        <span class="t3">(${num(d.capacityUnits)} CU × 86,400 seconds)</span></td></tr>
      <tr><th>Free viewers</th><td>${d.supportsFreeViewers
        ? `<span class="pill good">F64+</span> a Free licence can read Power BI here`
        : `<span class="pill wash">Pro needed</span> below F64, every Power BI viewer needs Pro or PPU`}</td></tr>
    </tbody></table></div>
    <p style="color:var(--ink-3);font-size:.78rem;margin:.6rem 0 0">${esc(h.policy)}</p>`)}

  ${panel(`Workspaces — ${d.workspaces.length}`, d.workspaces.length ? `
    <div class="tablewrap"><table class="grid">
      <thead><tr><th>Workspace</th><th>Primary workload</th><th class="n">Share of this capacity</th></tr></thead>
      <tbody>${d.workspaces.map((w) => `<tr>
        <td><b>${esc(w.WorkspaceName)}</b><span class="t3">${esc(w.WorkspaceId)}</span></td>
        <td>${esc(w.PrimaryWorkload)}</td>
        <td class="n ${w.ShareOfCapacityPct >= 55 ? "t-warn" : ""}">${pct(w.ShareOfCapacityPct, 0)}</td>
      </tr>`).join("")}</tbody></table></div>`
    : `<p class="empty">No workspaces assigned.</p>`)}

  ${panel(`Throttling events — ${d.throttlingEvents.length}`, d.throttlingEvents.length ? `
    <div class="tablewrap"><table class="grid">
      <thead><tr><th>Date</th><th>Stage</th><th class="n">Into future capacity</th>
        <th class="n">Interactive refused</th><th class="n">Background refused</th><th>Effect</th></tr></thead>
      <tbody>${d.throttlingEvents.map((e) => `<tr>
        <td>${esc(e.Date)}</td>
        <td>${stageCell(e.Stage, (e.Stage || "").replace(/_/g, " "))}</td>
        <td class="n">${num(Math.round(e.FutureCapacityMinutes))} min</td>
        <td class="n">${num(e.InteractiveRejected)}</td>
        <td class="n">${num(e.BackgroundRejected)}</td>
        <td class="t3">${esc(e.Effect)}</td>
      </tr>`).join("")}</tbody></table></div>`
    : `<p class="empty">This capacity has never throttled.</p>`)}

  <p style="margin:1.5rem 0 0">
    <a href="/datacentre/${encodeURIComponent(d.datacentre)}">← ${esc(d.datacentre)}</a>
    &nbsp;·&nbsp; <a href="/region/${encodeURIComponent(d.region)}">${esc(d.region)}</a>
  </p>`;
};

PAGES["/methodology"] = async (view) => {
  const m = await get("/api/methodology");

  view.innerHTML = howto({
    answers: "<b>Derivation of every figure in the application</b> \u2014 so the method can be challenged rather than the output taken on trust.",
    steps: [
      { what: "Calculation methodology", is: "the derivations for revenue loss and the order-by date, written out rather than left in code." },
      { what: "SLA by tier", is: "the committed turnaround per subscription tier before an incident is classified as a failure." },
      { what: "Risk index weighting", is: "the contribution of each component to a risk score. A configured starting position rather than a derived relationship, and adjustable without code change." },
      { what: "Classifier validation gate", is: "the result of re-classifying 60 incidents with known outcomes. Any mismatch blocks publication." },
      { what: "Controls and provenance", is: "the data quality controls applied on ingest, and the origin of each field." },
    ],
    next: "Any of these settings can be changed without a code change, and every run records the configuration it executed with.",
    sources: "the configuration file, the classifier validation result, and per-field provenance from the dimensional model.",
  }) + title("Methodology", "Where every number on this site comes from") + `

  ${panel("Calculation methodology", `
    <h4 style="margin:0 0 .3rem;font-size:.9rem">Revenue exposure</h4>
    <p class="mono" style="background:var(--page);padding:.7rem 1rem;border-radius:3px;margin:0 0 .5rem">
      revenue loss = what the customer pays us per year
      × how much of their request was missing
      × how many days it was missing
      ÷ ${m.config.annualisation_days}</p>
    <p style="color:var(--ink-2);margin:0 0 1.25rem">Risk-adjusted, not a booked loss.
      <code>capacity_share</code> is the requested delta as a share of the resulting footprint;
      <code>days_unavailable</code> is the delay for a late approval, or denial-date to today for an
      unfulfilled one, capped at ${m.config.unfulfilled_cap_days} days.</p>

    <h4 style="margin:0 0 .3rem;font-size:.9rem">Customer revenue touched</h4>
    <p style="color:var(--ink-2);margin:0 0 1.25rem">The full yearly payment of every customer with at least one
      failed request, de-duplicated per subscription. Blast radius, not risk. Conflating the two is how a
      credible estimate becomes a number nobody trusts.</p>

    <h4 style="margin:0 0 .3rem;font-size:.9rem">Order-by date</h4>
    <p class="mono" style="background:var(--page);padding:.7rem 1rem;border-radius:3px;margin:0 0 .5rem">
      order hardware by = the day the region is forecast to fill up
      − how long that hardware takes to arrive</p>
    <p style="color:var(--ink-2);margin:0">A region at 71% can outrank one at 94% when its capacities are throttling and the other's are not. Fullness alone does not decide it, because Fabric throttles on borrowed future capacity rather than on how full a capacity looks, and that depends on
      45 days rather than 10. Getting that the right way round is the whole point of this page.</p>`)}

  ${panel("SLA by subscription tier", `<div class="scroll-x"><table>
    <thead><tr><th>Subscription tier</th><th class="n">SLA</th><th>Definition</th></tr></thead>
    <tbody>${Object.entries(m.config.tier_delay_hours || {}).map(([t, h]) => `<tr>
      <td><b>${esc(t)}</b></td><td class="n">${h}h</td>
      <td class="why">A request answered within ${h} hours is normal turnaround, not a failure.</td>
    </tr>`).join("")}</tbody></table></div>
    <p style="color:var(--ink-2);margin:1rem 1.15rem 0;font-size:.85rem">
      A bigger customer is owed a faster answer, so the allowance tightens as tier rises.
      <b>These are calibrated from the labelled sample, not a contractual SLA</b> — confirming them
      against the real capacity SLA is an open item.</p>`, { flush: true })}

  ${panel("Risk index weighting", `<div class="scroll-x"><table>
    <thead><tr><th>Component</th><th class="n">Weight</th>
      <th class="n">Max contribution</th></tr></thead>
    <tbody>${(m.riskWeights || []).map((w) => `<tr>
      <td>${esc(w.label)}</td>
      <td class="n">${pct(w.weight * 100)}</td>
      <td class="n">+${(w.weight * 100).toFixed(0)}</td>
    </tr>`).join("")}</tbody>
    <tfoot><tr><td style="text-align:right"><b>Total</b></td>
      <td class="n"><b>100%</b></td><td class="n"><b>100</b></td></tr></tfoot>
  </table></div>
  <p style="color:var(--ink-2);margin:1rem 1.15rem 0;font-size:.85rem">
    <b>These weights are a starting position, not a measured relationship.</b>
    There is nothing in a sample this size to fit them against \u2014 the risk
    model on the same data reports no predictive power at all. They say what we
    currently believe matters most, applied consistently so places can be ranked
    against each other. The ranking is worth more than any single score.
  </p>
  <p style="color:var(--ink-2);margin:.5rem 1.15rem 1.15rem;font-size:.85rem">
    Disagree with the split? It is a setting, not code \u2014 change it and every
    score moves with it. Every score on the site shows all four parts, so you can
    see exactly what a change would do before making it.
  </p>`, { hint: "a setting you can argue with", flush: true })}

  ${panel("Classifier validation gate", `
    <p style="margin:0 0 .75rem">${m.gate.passed
      ? `<span class="pill good">PASSED</span>` : `<span class="pill bad">FAILED</span>`}
      ${esc(m.gate.detail)}</p>
    <p style="color:var(--ink-2);margin:0">Sorting a ticket is a comparison of two dates, not a prediction — so it is
      testable, and it is tested. If it fails to reproduce a known answer, artefacts are still written for
      debugging but nothing reaches a leadership channel.</p>`)}

  ${panel("Data quality controls", `<div class="scroll-x"><table>
    <thead><tr><th>Control</th><th class="n">Result</th></tr></thead>
    <tbody>${Object.entries(m.dataQuality || {}).map(([k, v]) => `<tr>
      <td>${esc(words(k))}</td><td class="n">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td>
    </tr>`).join("")}</tbody></table></div>`, { flush: true })}

  ${panel("Data provenance", `<div class="scroll-x"><table>
    <thead><tr><th>Entity</th><th class="n">Rows</th><th>Provenance</th></tr></thead>
    <tbody>${(m.provenance || []).map((p) => `<tr>
      <td><b>${esc(p.Entity ?? p.entity ?? "")}</b></td>
      <td class="n">${num(p.Rows ?? p.rows ?? 0)}</td>
      <td class="why">${esc(p.Provenance ?? p.provenance ?? "")}</td>
    </tr>`).join("")}</tbody></table></div>`,
    { hint: "which numbers are real and which are generated", flush: true })}`;
};

/* ==================================================================== 7/9 */
/* Capacity pools                                                              */

/* Shared: a risk score with its band, and the component that drove it. Used
   by the datacentre and customer views so one number means one thing. */
/* Review: the in-SLA outcomes are not targets, so a register defaults to the
   failures. The rest are not deleted -- the count is stated and one click
   reveals them -- because hiding rows without saying so is how a total stops
   reconciling. */
function registerToggle(id, hidden) {
  if (!hidden) return "";
  return `<p style="margin:.6rem 1.15rem 1.15rem;font-size:.82rem;color:var(--ink-2)">
    ${hidden} request(s) handled within SLA are not listed \u2014 they carry no
    revenue loss and are not targets.
    <button class="btn ghost" style="padding:.15rem .6rem;font-size:.78rem;margin-left:.4rem"
      data-showall="${id}">Show all</button></p>`;
}

function wireRegisterToggle(view, id, render) {
  const btn = view.querySelector(`[data-showall="${id}"]`);
  if (btn) btn.onclick = () => render(true);
}

function calcCell(x) {
  // One basis on screen: the ARR apportionment that produces the headline.
  //
  // A second, rate-card basis is computed in src/ratecard and was shown here
  // alongside it. Removed on review: on rows where ARR already gives a figure
  // the two sit within a few percent of each other and add nothing except the
  // question of which one counts, and the rates behind it are placeholders
  // rather than the published Fabric price. It earns its place again when
  // Finance supplies real rates -- and the free-tier blind spot it covered is
  // still stated in words on every $0 row.
  return esc(x.workingOut || "");
}

function riskCell(risk) {
  const top = risk.drivers?.[0];
  const e = risk.evidence || {};
  // The old "low sample confidence" pill fired on 43 of 45 rows, and a warning
  // on almost every row is decoration. The evidence base is stated on every row
  // instead, which is the thing the reader actually needs to weigh the number.
  const basis = e.requests
    ? `<br><span style="font-size:.72rem;color:var(--ink-3)" title="${
        e.shrunk
          ? `Measured ${Math.round(e.rawFailureRate * 100)}% failure on ${e.requests} request(s) — too few to stand alone, so it is pulled toward the ${Math.round(e.priorRate * 100)}% fleet rate, giving ${Math.round(e.usedFailureRate * 100)}%.`
          : `Failure rate used as measured: ${Math.round(e.usedFailureRate * 100)}%.`
      }">from ${num(e.requests)} request${e.requests === 1 ? "" : "s"}${
        e.shrunk ? ` · rate ${Math.round(e.rawFailureRate * 100)}%→${Math.round(e.usedFailureRate * 100)}%` : ""
      }</span>`
    : "";
  return `<b style="font-size:1.05rem">${risk.score.toFixed(1)}</b>
    ${riskPill(risk.band)}
    ${top ? `<br><span style="font-size:.75rem;color:var(--ink-3)">mostly ${esc(words(top.component))}</span>` : ""}
    ${basis}`;
}

/* The per-SKU rows that sit under one capacity pool on /datacentres.

   A building is not one thing -- it runs several Fabric capacities, each on its
   own F SKU, and CU does not pool between them. These rows break the building
   into its SKUs: how many capacities on each, the CU they hold and use, the
   utilisation and its weekly trend, and the rung the short ones should move to.
   The demand and risk columns belong to the building, not to a SKU, so they are
   left blank here rather than repeated. Hidden until the row is opened. */
function dcSkuRows(x) {
  if (!x.skus || !x.skus.length) return "";
  const growth = (g) => g == null ? `<span class="t3">—</span>`
    : g > 0 ? `+${g.toFixed(2)}<span class="t3"> pp/wk</span>`
    : `<span class="t3">${g.toFixed(2)} pp/wk</span>`;
  return x.skus.map((s) => `<tr class="sku-sub" data-sub="${esc(x.datacentre)}" hidden>
    <td class="sku-sub-lead"></td>
    <td></td>
    <td><b>${esc(s.sku)}</b></td>
    <td class="n">${num(s.capacityCount)}<br><span class="t3">${num(s.capacityUnits)} CU</span></td>
    <td class="n">${num(Math.round(s.totalCU))}</td>
    <td class="n">${cu1(s.utilisedCU)}</td>
    <td class="n"><b${s.utilisationPct >= s.planningThresholdPct ? ` class="t-bad"` : ""}>${
      pct(s.utilisationPct, 1)}</b>${s.peakPct > 100
        ? `<br><span class="t3">peak ${pct(s.peakPct, 0)}</span>` : ""}</td>
    ${/* The threshold used to sit on the site row and be left blank here. The
          site row no longer carries capacity figures, so it is stated per SKU
          -- otherwise the column is empty in every row of the table. */""}
    <td class="n t3">${s.planningThresholdPct == null ? "" : pct(s.planningThresholdPct, 0)}</td>
    <td class="n">${growth(s.growthPctPerWeek)}</td>
    <td class="n t3">${s.leadTimeWeeks}w</td>
    <td class="n">${s.weeksToDecide == null ? `<span class="t3">—</span>`
      : s.weeksToDecide <= 0 ? `<b class="t-bad">now</b>`
      : `<b${s.planningStatus === "overdue" ? ` class="t-bad"` : ""}>${
           s.weeksToDecide.toFixed(1)}</b><span class="t3"> wks</span>`}</td>
    <td>${s.planningStatus === "overdue"
      ? `<span class="pill bad">Overdue</span>`
      : `<span class="pill good">OK</span>`}</td>
    ${/* The Procurement column is gone from the header, so it goes from here
          too -- a stray cell here shunts every SKU row one column right of the
          site row above it. */""}
    <td class="n"></td><td class="n"></td><td class="n"></td><td class="n"></td><td></td><td></td>
  </tr>`).join("");
}

// PAGES["/datacentres"] = async (view) => {
//   const d = await get("/api/datacentres");
//   const solid = d.datacentres.filter((x) => !x.lowEvidence);

//   view.innerHTML = howto({
//     answers: "<b>Site-level risk ranking.</b> A region identifies the geography; a capacity pool identifies the facility requiring intervention.",
//     steps: [
//       { what: "Site table", is: `all facilities with recorded activity, ranked by risk score. ${d.withActivity} of ${d.totalSites} sites have recorded incidents; the remainder are dormant and excluded.` },
//       { what: "Risk score", is: "computed from that facility\u2019s own incidents, with the highest-weighted component named beneath." },
//       { what: "Clicking a row", is: "opens that site \u2014 how its score was built line by line, why its requests were refused, every ticket, and <b>its own forecast</b>: when this building crosses its safety line, fitted to its own daily CU record. The region name in there is a link if you want the wider picture." },
//       { what: "Evidence base", is: "stated under every score, because nearly every facility here has one or two incidents. Where a facility has too few to judge on its own, its failure rate is pulled toward the fleet average and both figures are shown \u2014 a single denial is a 100% failure rate arithmetically, but it is one observation, not a finding." },
//     ],
//     words: [
//       { term: "How the score is built", is: "", means: `${Object.entries(d.weights).map(([k, w]) => `${Math.round(w * 100)}% ${esc(words(k))}`).join(", ")}. Each site is scored from its own rows — this is not a region total divided up.` },
//       { term: "Primary denial reason", means: "The most frequent denial cause at that facility \u2014 the fastest indicator of whether the constraint is capacity, licensing or policy." },
//     ],
//     next: `Rank by score, but read the evidence line beneath it before acting: ${solid.length} of ${d.datacentres.length} sites here have three or more requests, so for most of them the ranking is driven by utilisation and throttling — which are measured continuously — rather than by a failure rate drawn from one or two tickets.`,
//     sources: "ICM capacity requests attributed to a facility, and the Fabric capacities in it.",
//   }) + title("Capacity pools", `${d.withActivity} sites with activity, of ${d.totalSites} across all regions`) + `

//   ${panel("Capacity pools by risk score",
//     filterBar("dc-filter", {
//       placeholder: "Search capacity pools — name or region",
//       label: "Region",
//       allLabel: "All regions",
//       options: [...new Set(d.datacentres.map((x) => x.region))].sort(),
//     })
//     + `<div class="scroll-x"><table>
//     <thead><tr><th>Capacity pool</th><th>Region</th>
//       <th class="n">Total CU</th><th class="n">Utilised CU</th>
//       ${th("Utilisation", "Capacity Units in use over Capacity Units deployed at this "
//          + "site. Unlike the region average above it, this is the figure a decision "
//          + "is actually taken on.", "n")}
//       ${th("Threshold", "The planning line the buy decision is taken against, fixed at "
//          + "80% for every site so that \u2018overdue\u2019 means the same thing in every "
//          + "row. Each building also has its own safety threshold, between 82.5% and 90%, "
//          + "which is what its forecast is judged against on its own page.", "n")}
//       ${th("Growth", "Percentage points of utilisation added per week, as the "
//          + "least-squares trend of this building\u2019s own daily record. There is no "
//          + "growth column in the source data \u2014 this is derived from the 150 days of "
//          + "CU consumption behind it, not read from a field.", "n")}
//       ${th("Lead time", "How long capacity takes to arrive once ordered. Fixed at 12 "
//          + "weeks across the estate.", "n")}
//       ${th("Weeks to decide", "How long until this site reaches the 80% planning line at "
//          + "its current growth rate. A dash means it is flat or shrinking, so there is no "
//          + "date to work back from.", "n")}
//       ${th("Status", "Overdue when the runway is shorter than the 12-week lead time \u2014 "
//          + "capacity ordered today would arrive after the line is crossed, so the "
//          + "decision is already late.")}
//       <th class="n">Requests</th><th class="n">Failed</th><th class="n">Customers</th>
//       <th class="n">Revenue loss</th><th>Primary denial reason</th><th>Risk score</th></tr></thead>
//     <tbody>${d.datacentres.map((x) => `<tr class="clickable" data-dc="${esc(x.datacentre)}"
//       data-region="${esc(x.region)}" data-filter="${esc(x.region)}"
//       data-search="${esc(`${x.datacentre} ${x.region} ${x.topReason || ""} ${x.planningStatus}`)}">
//       <td class="mono"><b>${esc(x.datacentre)}</b></td>
//       <td>${esc(x.region)}</td>
//       <td class="n">${x.capacityCount == null ? "—"
//         : `${num(x.capacityCount)}<br><span class="t3">${num(x.capacityUnits)} CU${
//              x.throttling ? ` · <b class="t-bad">${num(x.throttling)} throttling</b>` : ""}</span>`}</td>
//       <td class="n">${num(Math.round(x.totalCU))}</td>
//       <td class="n">${num(Math.round(x.utilisedCU))}</td>
//       <td class="n"><b${x.siteUtilisationPct >= x.planningThresholdPct
//         ? ` class="t-bad"` : ""}>${pct(x.siteUtilisationPct, 1)}</b></td>
//       <td class="n t3">${pct(x.planningThresholdPct, 0)}</td>
//       ${/* Derived, not read: no growth column exists in the extract. */ ""}
//       <td class="n">${x.growthPctPerWeek == null ? `<span class="t3">—</span>`
//         : x.growthPctPerWeek > 0
//           ? `+${x.growthPctPerWeek.toFixed(2)}<span class="t3"> pp/wk</span>`
//           : `<span class="t3">${x.growthPctPerWeek.toFixed(2)} pp/wk</span>`}</td>
//       <td class="n t3">${x.leadTimeWeeks}w</td>
//       <td class="n">${x.weeksToDecide == null ? `<span class="t3">—</span>`
//         : x.weeksToDecide <= 0 ? `<b class="t-bad">now</b>`
//         : `<b${x.planningStatus === "overdue" ? ` class="t-bad"` : ""}>${
//              x.weeksToDecide.toFixed(1)}</b><span class="t3"> wks</span>`}</td>
//       <td>${x.planningStatus === "overdue"
//         ? `<span class="pill bad">Overdue</span>`
//         : `<span class="pill good">OK</span>`}</td>
//       <td class="n">${num(x.requests)}</td>
//       <td class="n">${x.failed ? `<b style="color:var(--bad)">${num(x.failed)}</b>` : "—"}</td>
//       <td class="n">${num(x.customers)}</td>
//       <td class="n">${x.revenueLoss ? money(x.revenueLoss) : "—"}</td>
//       <td>${x.topReason ? esc(x.topReason) : "—"}</td>
//       <td>${riskCell(x.risk)}</td>
//     </tr>`).join("")}</tbody></table></div>`,
//     { hint: "click a row for detail", flush: true })}
//   <div id="dc-detail"></div>`;

//   view.querySelectorAll("tr[data-dc]").forEach((tr) =>
//     (tr.onclick = () => navigate(`/datacentre/${encodeURIComponent(tr.dataset.dc)}`)));

//   /* 110 sites is past the point where scrolling is a way of finding one. The
//      search matches the site name, its region and its denial reason together, so
//      "westeurope" and "throttl" both narrow the list; the dropdown cuts to a
//      single region, which is the question this page is most often opened with. */
//   wireFilter("dc-filter", "#view tr[data-dc]", { noun: "capacity pools" });
// };
PAGES["/datacentres"] = async (view) => {
  const d = await get("/api/datacentres");
  const solid = d.datacentres.filter((x) => !x.lowEvidence);

  view.innerHTML = howto({
    answers: "<b>Site-level risk ranking.</b> A region identifies the geography; a capacity pool identifies the facility requiring intervention.",
    steps: [
      { what: "Site table", is: `all facilities with recorded activity, ranked by risk score. ${d.withActivity} of ${d.totalSites} sites have recorded incidents; the remainder are dormant and excluded.` },
      { what: "Risk score", is: "computed from that facility\u2019s own incidents, with the highest-weighted component named beneath." },
      { what: "Clicking a row", is: "opens that site \u2014 how its score was built line by line, why its requests were refused, every ticket, and <b>its own forecast</b>: when this building crosses its safety line, fitted to its own daily CU record. The region name in there is a link if you want the wider picture." },
      { what: "Evidence base", is: "stated under every score, because nearly every facility here has one or two incidents. Where a facility has too few to judge on its own, its failure rate is pulled toward the fleet average and both figures are shown \u2014 a single denial is a 100% failure rate arithmetically, but it is one observation, not a finding." },
    ],
    words: [
      { term: "How the score is built", is: "", means: `${Object.entries(d.weights).map(([k, w]) => `${Math.round(w * 100)}% ${esc(words(k))}`).join(", ")}. Each site is scored from its own rows — this is not a region total divided up.` },
      { term: "Primary denial reason", means: "The most frequent denial cause at that facility \u2014 the fastest indicator of whether the constraint is capacity, licensing or policy." },
      { term: "What to do about a full site", means: "Open the row. Each F SKU in the building is listed on its own line with how full it is, and scaling the SKU on a capacity is the change that applies \u2014 there is nothing to order for a building as a whole." },
    ],
    next: `Rank by score, but read the evidence line beneath it before acting: ${solid.length} of ${d.datacentres.length} sites here have three or more requests, so for most of them the ranking is driven by utilisation and throttling — which are measured continuously — rather than by a failure rate drawn from one or two tickets.`,
    sources: "ICM capacity requests attributed to a facility, and the Fabric capacities in it.",
  }) + title("Capacity pools", `${d.withActivity} sites with activity, of ${d.totalSites} across all regions`) + `

  ${panel("Capacity pools by risk score",
    filterBar("dc-filter", {
      placeholder: "Search capacity pools — name or region",
      label: "Region",
      allLabel: "All regions",
      options: [...new Set(d.datacentres.map((x) => x.region))].sort(),
    })
    + `<div class="scroll-x"><table>
    <thead><tr>
      <th>Region</th>
      <th>Capacity pool</th>
      ${th("SKU", "The Fabric SKUs deployed in this building. Open a row to see "
         + "each one on its own line — CU does not pool between capacities, so "
         + "an F64 at 40% does not lend headroom to an F8 that is throttling.")}
      <th class="n">Capacities</th>
      <th class="n">Total CU</th>
      <th class="n">Utilised CU</th>
      ${th("Utilisation", "Capacity Units in use over Capacity Units deployed, stated per "
         + "SKU \u2014 open a row to see them. There is no site figure here on purpose: "
         + "CU does not pool between capacities, so a building average hides the one "
         + "that is throttling.", "n")}
      ${th("Threshold", "The planning line the buy decision is taken against, fixed at "
         + "80% so that \u2018overdue\u2019 means the same thing in every row. Each "
         + "building also has its own safety threshold, between 82.5% and 90%, which is "
         + "what its forecast is judged against on its own page.", "n")}
      ${th("Growth", "Percentage points of utilisation added per week per SKU, as the "
         + "least-squares trend of that SKU\u2019s own daily record. There is no growth "
         + "column in the source data \u2014 this is derived from the 150 days of CU "
         + "consumption behind it, not read from a field.", "n")}
      ${th("Lead time", "How long capacity takes to arrive once ordered. Fixed at 12 "
         + "weeks across the estate.", "n")}
      ${th("Weeks to decide", "How long until that SKU reaches the 80% planning line at its "
         + "current growth rate. A dash means it is flat or shrinking, so there is no "
         + "date to work back from.", "n")}
      ${th("Status", "Overdue when a SKU\u2019s runway is shorter than the 12-week lead "
         + "time \u2014 capacity ordered today would arrive after the line is crossed, so "
         + "the decision is already late.")}
      ${/* No Procurement column. It named a purchase -- "buy an F8" -- that
            Fabric does not have: you do not order capacity for a building, you
            scale the F SKU on a capacity, which is what the per-SKU rows
            underneath each site already say and what the site's own page
            recommends. The endpoint still computes `procureSku` and its
            shortfall; nothing on this table reads them. */""}
      <th class="n">Requests</th><th class="n">Failed</th><th class="n">Customers</th>
      <th class="n">Revenue loss</th><th>Primary denial reason</th><th>Risk score</th></tr></thead>
    <tbody>${d.datacentres.map((x) => `<tr class="clickable" data-dc="${esc(x.datacentre)}"
      data-region="${esc(x.region)}" data-filter="${esc(x.region)}"
      ${/* No `procureSku` here either: matching on a column the reader cannot
            see means typing "F8" pulls up a site with no F8 anywhere on its
            row. The SKUs it actually holds are still matched. */""}
      ${/* The site status is no longer a column, so it is no longer matched here.
            Each SKU carries its own, and those are what the row now shows when
            it is opened -- typing "overdue" should find the sites that have an
            overdue SKU in them. */""}
      data-search="${esc(`${x.datacentre} ${x.region} ${x.topReason || ""} ${(x.skus || []).map((s) => s.sku + " " + s.planningStatus).join(" ")}`)}">
      <td>${esc(x.region)}</td>
      <td class="mono"><b>${esc(x.datacentre)}</b></td>
      <td class="sku-cell">${(x.skus && x.skus.length)
        ? `<button type="button" class="sku-expand" aria-expanded="false"
             aria-label="Show the SKU breakdown for ${esc(x.datacentre)}"><svg viewBox="0 0 16 16"
             aria-hidden="true"><path d="M6 4l4 4-4 4" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
           <span class="sku-mix">${x.skus.map((s) => s.capacityCount > 1
             ? `${num(s.capacityCount)}&times;${esc(s.sku)}` : esc(s.sku)).join(" ")}</span>`
        : `<span class="t3">—</span>`}</td>
      ${/* The capacity columns are deliberately empty on the site row. A building
            runs several F SKUs and CU does not pool between them, so a site
            total is an average of things that cannot lend each other headroom:
            an F64 at 40% next to an F8 at 95% reads as comfortable, and the
            capacity that is actually throttling disappears into the mean. The
            figures live on the per-SKU rows underneath, where the decision is
            taken; open the row to see them. Requests, failures, customers,
            revenue loss and risk stay here — those are the building’s own
            incident record, not a sum over its SKUs. */ ""}
      <td class="n"></td>
      <td class="n"></td>
      <td class="n"></td>
      <td class="n"></td>
      <td class="n"></td>
      <td class="n"></td>
      <td class="n"></td>
      <td class="n"></td>
      <td></td>
      <td class="n">${num(x.requests)}</td>
      <td class="n">${x.failed ? `<b style="color:var(--bad)">${num(x.failed)}</b>` : "—"}</td>
      <td class="n">${num(x.customers)}</td>
      <td class="n">${x.revenueLoss ? money(x.revenueLoss) : "—"}</td>
      <td>${x.topReason ? esc(x.topReason) : "—"}</td>
      <td class="risk-cell">${riskCell(x.risk)}</td>
    </tr>${dcSkuRows(x)}`).join("")}</tbody></table></div>`,
    { hint: "click a row for detail · open a row for its SKUs and their figures", flush: true })}
  <div id="dc-detail"></div>`;

  view.querySelectorAll("tr[data-dc]").forEach((tr) =>
    (tr.onclick = () => navigate(`/datacentre/${encodeURIComponent(tr.dataset.dc)}`)));

  /* The per-SKU rows sit in the DOM right after their capacity pool. They are
     shown only when that row is open, and hidden the moment the filter takes
     the parent out -- otherwise a search would leave orphaned SKU rows behind. */
  function syncSkuRows() {
    view.querySelectorAll("tr[data-dc]").forEach((tr) => {
      const open = tr.classList.contains("open");
      view.querySelectorAll(`tr[data-sub="${tr.dataset.dc}"]`).forEach((s) => {
        s.hidden = tr.hidden || !open;
      });
    });
  }
  view.querySelectorAll(".sku-expand").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();               // the row itself navigates; this does not
      const tr = btn.closest("tr[data-dc]");
      const open = tr.classList.toggle("open");
      btn.setAttribute("aria-expanded", String(open));
      syncSkuRows();
    });
  });

  /* 110 sites is past the point where scrolling is a way of finding one. The
     search matches the site name, its region, its denial reason and its SKUs
     together, so "westeurope", "throttl" and "F64" all narrow the list; the
     dropdown cuts to a single region, which is the question this page is most
     often opened with. */
  wireFilter("dc-filter", "#view tr[data-dc]", {
    noun: "capacity pools", onApply: syncSkuRows,
  });
};
/* ==================================================================== deep */
/* One capacity pool — its own page, reachable from Regions or Capacity pools.    */

PAGES["/datacentre"] = async (view, id, showAll = false) => {
  if (!id) { view.innerHTML = `<p class="error">No capacity pool selected.</p>`; return; }
  const x = await get(`/api/datacentre/${encodeURIComponent(id)}`);
  const labels = x.componentLabels || {};
  const over = x.headroom != null && x.headroom < 0;

  view.innerHTML = howto({
    answers: `<b>Everything recorded at ${esc(x.datacentre)}</b> — capacity position, denial causes, recommended remediation and the full incident list.`,
    steps: [
      { what: "Capacity position", is: "CU deployed, CU free, and this site's own safety threshold. Below it, every Fabric capacity in the building with what it is running at." },
      { what: "Recommended remediation", is: "one entry per denial cause at this site, with the capacities to scale where a larger F SKU is the fix." },
      { what: "Risk index breakdown", is: "each component of the score, with its measured value and contribution." },
      { what: "Incident register", is: "every request raised here, with the derivation behind each revenue-loss figure." },
      { what: "Forecast", is: "when <i>this building</i> crosses its own safety line, fitted to its own daily CU record. There is no separate forecasting tab: a region-level crossing date says a geography is filling up, which nobody can act on, whereas this names the building and the date \\u2014 and scaling an F SKU is a per-site decision that applies immediately." },
      { what: "Demand and utilisation", is: "what customers asked for here each month, drawn against how full the site actually ran." },
    ],
    words: [
      { term: "Why the forecast is here", means: "It used to be a tab of its own covering regions only. A region is a geography and cannot be scaled; a capacity in a building can. The forecast now sits beside the capacities that would be changed to move the date." },
      { term: "How this site's utilisation is measured", means: "Consumed CU seconds over available CU seconds, summed across the capacities in the building. Summing the seconds matters: an F2 at 90% and an F512 at 20% do not average to 55% of the building." },
    ],
    next: "Read the crossing date, then the capacity list beneath it — that is what would be scaled to move the date. Action the remediation for the highest-cost cause; causes marked manual review require engineering or account-team engagement.",
    sources: "ICM capacity requests attributed to this facility, the Fabric capacities in it, and this facility's own daily CU consumption record.",
  }) + title(`Capacity pool: ${x.datacentre}`,
             `In ${x.region} · ${num(x.fabric.capacityCount)} Fabric capacities · ${num(x.fabric.capacityUnits)} CU`
             + (x.fabric.throttling ? ` · ${num(x.fabric.throttling)} throttling` : "")) + `

  <div class="kpis">
    ${kpi("Risk score", x.risk.score.toFixed(1), `${x.risk.band} risk`,
          x.risk.band === "high" ? "bad" : x.risk.band === "medium" ? "" : "good")}
    ${kpi("Capacity", `${num(Math.round(x.capacityUnits ?? 0))} CU`,
          `${num(Math.round(x.capacityUnitsFree ?? 0))} free · threshold ${pct(x.thresholdPct ?? 0)}`,
          over ? "bad" : "ink")}
    ${kpi("Failed requests", num(x.failedCount ?? 0), `of ${num(x.requests)} raised here`,
          x.failedCount ? "bad" : "good",
          "Requests that breached SLA before being granted, or were never granted at all.")}
    ${kpi("Revenue loss", x.revenueLoss ? money(x.revenueLoss) : "—",
          "attributed to this facility", "bad")}
  </div>
  ${x.risk?.evidence?.shrunk ? `<p class="error" style="margin-top:1rem">
    <b>Thin evidence, and the score accounts for it.</b> ${x.requests} incident(s)
    recorded here, of which ${x.risk.evidence.denied} failed — a measured failure
    rate of ${Math.round(x.risk.evidence.rawFailureRate * 100)}%. That is too few
    observations to stand on its own, so it is pulled toward the
    ${Math.round(x.risk.evidence.priorRate * 100)}% fleet average and scored as
    <b>${Math.round(x.risk.evidence.usedFailureRate * 100)}%</b>. The score is
    therefore driven mainly by utilisation and throttling, which are measured
    continuously, until this facility has more history behind it.</p>` : ""}

  ${panel(`What is in this capacity pool — ${num(x.fabric.capacityCount)} Fabric capacities`,
    x.fabric.capacityCount ? `
    <p style="margin:0 0 .9rem;color:var(--ink-2);font-size:.88rem">
      There is no hardware to list. Fabric is a SaaS platform, so what a building
      holds is capacities, each with an F SKU and a number of Capacity Units.
      <b>CU does not pool</b> — a capacity throttles on its own consumption, so a
      site can hold plenty of CU and still be refusing queries.
    </p>
    <div class="tablewrap"><table>
      <thead><tr><th>Capacity</th><th class="n">SKU</th><th class="n">CU</th>
        <th class="n">Mean</th><th class="n">Peak</th><th>What is happening</th>
        <th class="n">Queries refused</th><th>Free viewers</th><th>What to do</th></tr></thead>
      <tbody>${x.fabric.capacities.map((c) => `<tr class="${c.throttledDays ? "row-danger" : ""}">
        <td><a href="/capacity/${encodeURIComponent(c.capacityId)}">${esc(c.capacityId)}</a></td>
        <td class="n"><b>${esc(c.sku)}</b></td>
        <td class="n">${num(c.capacityUnits)}</td>
        <td class="n">${pct(c.meanPct)}</td>
        <td class="n ${c.peakPct > 100 ? "t-bad" : ""}">${pct(c.peakPct)}</td>
        <td>${c.throttledDays
              ? `<span class="pill ${(PROBLEM[c.worstStage] || PROBLEM.none).tone}">${
                   esc((PROBLEM[c.worstStage] || PROBLEM.none).text)}</span>
                 <span class="t3">${num(c.throttledDays)} of ${num(c.windowDays)} days</span>`
              : `<span class="t3">Not throttling</span>`}</td>
        <td class="n">${c.interactiveRejected ? `<b class="t-bad">${num(c.interactiveRejected)}</b>` : "—"}</td>
        <td>${c.freeViewers
              ? `<span class="t-good">Free</span>`
              : `<span class="t3">Pro / PPU</span>`}</td>
        <td>${c.recommended && c.direction === "up"
              ? `<b class="t-warn">Scale to ${esc(c.recommended)}</b>
                 <span class="t3">${num(c.recommendedUnits)} CU · immediately</span>`
              : c.recommended
                ? `<span class="t3">Could shrink to ${esc(c.recommended)}</span>`
                : `<span class="t3">Nothing</span>`}</td>
      </tr>`).join("")}</tbody></table></div>
    <p class="prov">
      ${num(x.fabric.needingScale)} of ${num(x.fabric.capacityCount)} need a larger SKU ·
      ${num(x.fabric.freeViewerCapable)} are F64 or larger, so Power BI content on the
      rest needs a Pro or PPU licence per viewer. Scaling an F SKU applies immediately;
      there is nothing to order and nothing goes offline.
    </p>`
    : `<p class="empty" style="padding:0">No Fabric capacities are recorded in this capacity pool.</p>`)}

  ${panel("Recommended remediation", (x.recommendations || []).length
    ? x.recommendations.map((rec) => `
      <div style="border-left:3px solid ${rec.needsHuman ? "var(--warn)" : "var(--brand)"};
        padding:.1rem 0 .1rem 1rem;margin:0 0 1.25rem">
        <p style="margin:0 0 .4rem"><b>${esc(rec.reason)}</b>
          <span class="pill mute">${rec.count} incident(s)</span>
          ${rec.needsHuman ? `<span class="pill warn">manual review</span>` : ""}
          ${rec.revenueLoss ? `<span class="pill bad">${money(rec.revenueLoss)}</span>` : ""}</p>
        <p style="background:${rec.needsHuman ? "var(--warn-wash)" : "var(--brand-wash)"};
           padding:.8rem 1rem;border-radius:3px;margin:0">${esc(rec.action)}</p>
        ${(rec.threshold || []).length ? `<div style="margin-top:.6rem">
          <div class="scroll-x"><table style="font-size:.85rem">
            <thead><tr><th>Raise safety line to</th><th class="n">CU released</th>
              <th class="n">Headroom after</th></tr></thead>
            <tbody>${rec.threshold.map((o) => `<tr>
              <td><b>${pct(o.thresholdPct)}</b></td>
              <td class="n">+${num(Math.round(o.releasesCores))}</td>
              <td class="n" style="color:${o.headroomAfter < 0 ? "var(--bad)" : "var(--good)"}">
                ${num(Math.round(o.headroomAfter))}</td>
            </tr>`).join("")}</tbody></table></div>
        </div>` : ""}
        ${(rec.scale || []).length ? `<div style="margin-top:.6rem">
          <p style="margin:0 0 .4rem;font-size:.85rem;color:var(--ink-2)">
            The capacities here that are short, worst first:</p>
          <div class="scroll-x"><table style="font-size:.85rem">
            <thead><tr><th>Capacity</th><th class="n">Now</th><th class="n">Peak</th>
              <th>What is happening</th><th class="n">Queries refused</th>
              <th>Scale to</th></tr></thead>
            <tbody>${rec.scale.map((m) => `<tr>
              <td><a href="/capacity/${encodeURIComponent(m.capacityId)}">${esc(m.capacityId)}</a></td>
              <td class="n"><b>${esc(m.fromSku)}</b><br><span class="t3">${num(m.cuBefore)} CU</span></td>
              <td class="n ${m.peakPct > 100 ? "t-bad" : ""}">${pct(m.peakPct)}</td>
              <td>${m.throttledDays
                    ? `<span class="pill ${(PROBLEM[m.worstStage] || PROBLEM.none).tone}">${
                         esc((PROBLEM[m.worstStage] || PROBLEM.none).text)}</span>
                       <br><span class="t3">${num(m.throttledDays)} of ${num(m.windowDays)} days</span>`
                    : `<span class="pill warn">No room left</span>`}</td>
              <td class="n">${m.interactiveRejected ? num(m.interactiveRejected) : "—"}</td>
              <td><b class="t-good">${esc(m.toSku)}</b>
                <br><span class="t3">${num(m.cuAfter)} CU · applies immediately</span></td>
            </tr>`).join("")}</tbody></table></div></div>` : ""}
      </div>`).join("")
    : `<p class="empty" style="padding:0">No denials recorded at this facility.</p>`)}

  ${panel("Risk index breakdown", `<div class="scroll-x"><table>
    <thead><tr><th>Component</th><th class="n">Measured value</th>
      <th class="n">Contribution</th></tr></thead>
    <tbody>${x.risk.drivers.map((dr) => `<tr>
      <td>${esc(labels[dr.component] || words(dr.component))}</td>
      <td class="n">${(dr.raw * 100).toFixed(0)}%</td>
      <td class="n"><b>+${dr.contribution.toFixed(1)}</b></td>
    </tr>`).join("")}</tbody>
    <tfoot><tr><td colspan="2" style="text-align:right"><b>Total</b></td>
      <td class="n"><b>${x.risk.score.toFixed(1)}</b></td></tr></tfoot>
  </table></div>`, { flush: true })}

  ${panel("Incident register", `<div class="scroll-x"><table>
    <thead><tr><th>Incident</th><th>Customer</th><th>Outcome</th><th>Reason</th>
      <th class="n">Days</th><th class="n">Revenue loss</th>
      <th>Revenue loss basis</th></tr></thead>
    <tbody>${x.tickets.filter((k) => k.isFlagged || showAll).map((k) => `<tr>
      <td class="mono">${esc(k.incidentId)}</td>
      <td><b>${esc(k.customerName)}</b><br><span class="pill mute">${esc(k.tier)}</span></td>
      <td>${k.isFlagged ? `<span class="pill bad">${esc(k.outcomeLabel)}</span>`
                        : `<span class="pill good">${esc(k.outcomeLabel)}</span>`}</td>
      <td>${k.reason ? esc(k.reason) : "—"}</td>
      <td class="n">${k.days || "—"}</td>
      <td class="n">${k.exposure ? money(k.exposure) : "—"}</td>
      <td class="why">${calcCell(k)}</td>
    </tr>`).join("")}</tbody></table></div>
    ${registerToggle("dc", showAll ? 0 : x.tickets.filter((k) => !k.isFlagged).length)}`,
    { flush: true })}

  <div id="dc-forecast"></div>
  <div id="dc-caps"></div>
  <div id="dc-demand"></div>

  <p style="margin:1.5rem 0 0">
    <a href="/region/${encodeURIComponent(x.region)}">← Back to ${esc(x.region)}</a>
    &nbsp;·&nbsp; <a href="/datacentres">All capacity pools</a>
  </p>`;

  /* This facility's own forecast, then its capacities, then its demand.

     The comment that used to sit here said utilisation over time was recorded
     per region only, so the chart lived on the region page. That was wrong: the
     CU record is per capacity per day and every capacity names its building, so
     this site has a series of its own and now gets a forecast fitted to it.

     Forecast first, because it is the reason someone opened the page. The
     capacity list beneath it is what would be scaled to change the date. */
  $("dc-forecast").innerHTML = await forecastPanel(id);
  $("dc-caps").innerHTML = await capacityPanel("datacentre", id);
  $("dc-demand").innerHTML = await demandPanels("datacentre", id);
  wireCharts($("dc-demand"));

  wireRegisterToggle(view, "dc", () => PAGES["/datacentre"](view, id, true));
};

/* ==================================================================== 8/9 */
/* Reasons                                                                   */

PAGES["/reasons"] = async (view) => {
  const d = await get("/api/reasons");

  view.innerHTML = howto({
    answers: "<b>Root cause analysis.</b> The same incidents as the other tabs, aggregated by denial reason rather than by location.",
    steps: [
      { what: "Each card", is: "one denial cause \u2014 incident count, attributed revenue loss, geographic concentration and the applicable remediation." },
      { what: "Manual review", is: "marks causes with no automated remediation. These require engineering or account-team engagement, and the card states so rather than proposing a platform action." },
      { what: "The regions listed", is: "where that cause is worst. If one cause is concentrated in one region, that is usually a single fix rather than several." },
    ],
    words: [
      { term: "Why this view exists", means: "\u201cwesteurope has 4 failures\u201d does not identify an owner. \u201cThree capacity-ceiling denials and one licensing block\u201d identifies two remediations with two different owners." },
    ],
    next: "Work the causes with the largest revenue loss that are not marked manual review — those have a fix the platform can already model.",
    sources: `the ${d.totalFailed} failed requests, grouped by cause.`,
  }) + title("Reasons", `${d.reasons.length} causes behind ${d.totalFailed} failed requests`) + `

  ${d.reasons.map((r) => panel(r.reason, `
    <div class="kpis" style="margin-bottom:1rem">
      ${kpi("Incidents", num(r.count), `${pct(r.sharePct, 1)} of all failures`, "ink")}
      ${kpi("Revenue loss", r.revenueLoss ? money(r.revenueLoss) : "—", "attributed to this cause", "bad")}
      ${kpi("Customers affected", num(r.customers), `across ${num(r.datacentres)} capacity pools`, "ink")}
      ${kpi("Remediation", r.needsHuman ? "Manual" : "Automated", r.needsHuman ? "no automated fix available" : esc(r.handledBy), r.needsHuman ? "warn" : "good")}
    </div>
    <p style="margin:0 0 .75rem">${esc(r.detail)}</p>
    <p style="background:${r.needsHuman ? "var(--warn-wash)" : "var(--brand-wash)"};
       padding:.8rem 1rem;border-radius:3px;margin:0 0 1rem">
      <b>What to do:</b> ${esc(r.action)}</p>
    ${r.regions.length ? `<p style="margin:0;font-size:.85rem;color:var(--ink-2)">Worst in:
      ${r.regions.map((x) => `<span class="pill mute" style="margin-right:.3rem">${esc(x.region)} · ${x.count}</span>`).join("")}</p>` : ""}
  `, { hint: r.needsHuman ? "manual review" : `handled by ${r.handledBy}` })).join("")}`;
};

/* ==================================================================== 9/9 */
/* Incidents                                                                 */

PAGES["/incidents"] = async (view) => {
  const d = await get("/api/incidents");

  view.innerHTML = howto({
    answers: "<b>Full incident register.</b> The lowest level of the drill-down, for targeted lookup.",
    steps: [
      { what: "Filters", is: "narrow by region, denial cause or outcome. Filters combine, so a single cause can be isolated within a single region." },
      { what: "Search", is: "matches on incident number or subscription identifier." },
      { what: "Calculation column", is: "the derivation behind each revenue-loss figure, so no value requires trust." },
    ],
    next: "Filter to a cause you are working on, then open the region or capacity pool it points at.",
    sources: "the full ticket history, with the site and cause attributed to each.",
  }) + title("Incidents", `${d.incidents.length} tickets`) + `

  <section class="panel"><div class="body" style="display:flex;gap:1rem;flex-wrap:wrap;align-items:center">
    <input type="search" id="q" placeholder="Incident number or customer name\u2026" style="width:240px">
    <label class="ctl">Region <select id="f-region"><option value="">All</option>
      ${d.regions.map((r) => `<option>${esc(r)}</option>`).join("")}</select></label>
    <label class="ctl">Reason <select id="f-reason"><option value="">All</option>
      ${d.reasons.map((r) => `<option>${esc(r)}</option>`).join("")}</select></label>
    <label class="ctl">Outcome <select id="f-outcome"><option value="">All</option>
      ${d.outcomes.map((r) => `<option>${esc(r)}</option>`).join("")}</select></label>
    <span id="count" style="color:var(--ink-3);font-size:.82rem"></span>
  </div></section>
  <div id="inc-table"></div>`;

  function draw() {
    const q = $("q").value.trim().toLowerCase();
    const rows = d.incidents.filter((x) =>
      (!q || x.incidentId.toLowerCase().includes(q)
         || (x.customerName || "").toLowerCase().includes(q)
         || x.customerShort.toLowerCase().includes(q)) &&
      (!$("f-region").value || x.region === $("f-region").value) &&
      (!$("f-reason").value || x.reason === $("f-reason").value) &&
      (!$("f-outcome").value || x.outcomeLabel === $("f-outcome").value));

    $("count").textContent = `${rows.length} of ${d.incidents.length} shown`;
    $("inc-table").innerHTML = panel("Tickets", rows.length ? `<div class="scroll-x"><table>
      <thead><tr><th>Incident</th><th>Customer</th><th>Region</th><th>Capacity pool</th>
        <th>Outcome</th><th>Reason</th><th class="n">Days</th>
        <th class="n">Revenue loss</th><th>Revenue loss basis</th></tr></thead>
      <tbody>${rows.map((x) => `<tr>
        <td class="mono">${esc(x.incidentId)}</td>
        <td><b>${esc(x.customerName)}</b><br><span class="pill mute">${esc(x.tier)}</span></td>
        <td>${esc(x.region)}</td>
        <td class="mono">${esc(x.datacentre)}</td>
        <td>${x.isFlagged ? `<span class="pill bad">${esc(x.outcomeLabel)}</span>`
                          : `<span class="pill good">${esc(x.outcomeLabel)}</span>`}</td>
        <td>${x.reason ? esc(x.reason) : "—"}</td>
        <td class="n">${x.days || "—"}</td>
        <td class="n">${x.exposure ? money(x.exposure) : "—"}</td>
        <td class="why">${calcCell(x)}</td>
      </tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">Nothing matches those filters.</p>`, { flush: true });
  }

  ["q", "f-region", "f-reason", "f-outcome"].forEach((id) => {
    $(id).oninput = draw; $(id).onchange = draw;
  });
  draw();
};

/* ==================================================================== 9/10 */
/* Forecast                                                                  */

/* History, projection, uncertainty band and the safety line in one inline SVG.
   No chart library, for the same reason there is no build step. */
function forecastChart(f, place = "region") {
  const hist = f.history || [], proj = f.projection || [];
  if (hist.length < 2) return `<p class="empty">Not enough history to plot.</p>`;

  const W = 900, H = 260, L = 42, R = 12, T = 14, B = 26;
  const all = [...hist.map((p) => p.value),
               ...proj.map((p) => p.upper), ...proj.map((p) => p.lower),
               f.thresholdPct];
  // Utilisation is a share of deployed capacity, so 100% is the ceiling and
  // there is nothing above it to draw. The axis added two points of headroom
  // unconditionally and topped out at 102%, on a chart whose own caption says
  // the projection is capped at 100% because a line past that forecasts
  // nothing. Five of the eleven regions showed it. The floor is clamped for
  // the same reason -- a region cannot be less than empty.
  const lo = Math.max(0, Math.floor(Math.min(...all) - 2));
  /* 100% is the ceiling for a *region*: utilisation there is used over deployed
     capacity and a line past it forecasts nothing. A single Fabric capacity is
     not bound by that. It can consume more CU than it holds -- that is bursting,
     which Fabric smooths over future timepoints -- so a building whose
     capacities are bursting genuinely runs above 100%, and westeurope-dc04 sits
     at 185%. Clamping the axis unconditionally drew that line off the top of the
     chart, out of the viewBox, where it was simply invisible. So the ceiling
     applies only when the data stays under it. */
  const top = Math.max(...all);
  const hi = top > 100 ? Math.ceil(top + 2) : Math.min(100, Math.ceil(top + 2));
  const n = hist.length + proj.length;
  const x = (i) => L + (i / (n - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);

  const line = (pts, off = 0) =>
    pts.map((p, i) => `${i ? "L" : "M"}${x(i + off).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const band = proj.length
    ? `M${proj.map((p, i) => `${x(i + hist.length).toFixed(1)},${y(p.upper).toFixed(1)}`).join("L")}
       L${proj.slice().reverse().map((p, i) =>
         `${x(proj.length - 1 - i + hist.length).toFixed(1)},${y(p.lower).toFixed(1)}`).join("L")}Z`
    : "";

  const cross = f.crossingDate
    ? proj.findIndex((p) => p.date === f.crossingDate) : -1;

  return `<div class="legend">
    <span><i class="ln measured"></i>Measured — how full the ${esc(place)} actually was, one reading per day</span>
    <span><i class="ln projected"></i>Projected — where the trend takes it if nothing changes</span>
    <span><i class="ln band"></i>Range the forecast could be out by</span>
    <span><i class="ln limit"></i>Safety line</span>
  </div>
  <svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Utilisation history and projection for ${esc(f.datacentre || f.region)}">
    <text x="10" y="${T + 4}" font-size="10" fill="var(--ink-3)"
      transform="rotate(-90 10 ${T + 4})" text-anchor="end">how full (%)</text>
    ${[lo, Math.round((lo + hi) / 2), hi].map((v) =>
      `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--rule)"/>
       <text x="4" y="${y(v) + 4}" font-size="10" fill="var(--ink-3)">${v}%</text>`).join("")}
    <line x1="${L}" x2="${W - R}" y1="${y(f.thresholdPct)}" y2="${y(f.thresholdPct)}"
      stroke="var(--bad)" stroke-dasharray="5 4"/>
    <text x="${W - R}" y="${y(f.thresholdPct) - 5}" font-size="10" text-anchor="end"
      fill="var(--bad)">safety line ${f.thresholdPct}%</text>
    ${band ? `<path d="${band}" fill="var(--brand)" opacity=".13"/>` : ""}
    <path d="${line(hist)}" fill="none" stroke="var(--ink-2)" stroke-width="1.6"/>
    ${proj.length ? `<path d="${line(proj, hist.length)}" fill="none"
      stroke="var(--brand)" stroke-width="1.8" stroke-dasharray="6 3"/>` : ""}
    <line x1="${x(hist.length - 1)}" x2="${x(hist.length - 1)}" y1="${T}" y2="${H - B}"
      stroke="var(--rule-strong)"/>
    <text x="${x(hist.length - 1) + 4}" y="${H - B + 14}" font-size="10"
      fill="var(--ink-3)">forecast from here</text>
    ${cross >= 0 ? `<circle cx="${x(hist.length + cross)}" cy="${y(f.thresholdPct)}" r="4"
      fill="var(--bad)"/>` : ""}
    <text x="${L}" y="${H - B + 14}" font-size="10" fill="var(--ink-3)">${esc(hist[0].date)}</text>
  </svg>`;
}

PAGES["/forecast"] = async (view) => {
  const d = await get("/api/forecast");
  const a = await get("/api/anomalies");

  view.innerHTML = howto({
    answers: d.forecasts.some((f) => f.forced)
      ? `<b>When each region is projected to cross its safety line</b>, using ${esc(d.forecasts.find((f) => f.forced).model)} for every region — a set choice, not the most accurate one per region. Each panel names what the backtest would have picked and what the difference costs.`
      : "<b>When each region is projected to cross its safety line</b>, using whichever model actually forecast best for that region.",
    steps: [
      { what: "Model selection", is: `${d.candidates.length} candidate models are backtested per region on data they never saw. The one with the lowest error is used; a region where nothing beats the naive baseline says so. A model that failed to fit on any fold is still listed but cannot win \u2014 otherwise it would post an average from an easier exam than the models it is judged against.` },
      { what: "The chart", is: "solid line is measured history, dashed is the projection, and the shaded band is the error the chosen model actually made on held-out data. The red dashed line is the safety threshold." },
      { what: "Crossing date", is: "the first projected day past the safety threshold, with an earliest and latest bound from the same error band. A region already past its line shows the date it fills completely instead, because a crossing date it passed months ago is not a decision." },
      { what: "Anomalies", is: `${a.total} outliers were detected and removed before fitting, so a deal-driven spike does not become the trend.` },
    ],
    words: [
      { term: "Forecast error", means: `How far off the model was when it was tested, averaged. Each model is fitted on part of the record, asked to predict the next ${d.horizonDays} days it was not shown, and marked against what actually happened — repeated over ${d.folds} stretches, so the figure is accuracy on unseen data rather than how neatly it fits the past. Lower is better. It is a percentage of the reading, not percentage points: 1.6% of a 75% utilisation reading is about 1.2 points.` },
      { term: "Skill vs naive", means: "Whether the modelling was worth doing. The benchmark is the simplest forecast there is — \u2018tomorrow looks like today\u2019, carry the last reading forward, no model at all. Skill is how much of that benchmark\u2019s error the chosen model removed. 0% means the modelling added nothing; below 0% it did worse than doing nothing, and that candidate is rejected rather than displayed." },
      { term: "Why both are shown", means: "Neither means much alone. A low error can simply mean the region is easy to predict \u2014 if utilisation barely moves, doing nothing scores well too. High skill on its own says a model beat the benchmark without saying whether either is accurate enough to act on. Together they say the error is small and it was earned." },
      { term: "MAPE / RMSE", means: "The two error measures behind those figures. MAPE is the average percentage miss; RMSE is a measure that penalises one large miss far more than several small ones, which is why the winning model is chosen on it \u2014 in capacity planning a single big miss is what causes a denial." },
      { term: "ARIMA / SARIMA", means: d.arimaAvailable
          ? "Both are in the candidate set and compete like any other model. They differ in one respect that matters here: ARIMA has no seasonal term, so on demand that follows the working week it has to absorb that cycle into its error, while SARIMA models the week explicitly."
          : "Not in the candidate set here \u2014 both need statsmodels, which is not installed. They activate automatically if that package is added." },
    ],
    next: "Work the regions with the soonest crossing date. A region already past its line is a present condition, not a forecast.",
    sources: "daily regional utilisation with detected anomalies excluded, backtested over " + d.folds + " rolling folds at a " + d.horizonDays + "-day horizon.",
  }) + title("Forecast", d.thresholdPct == null
       // Null is the normal case: every region is judged against the line its
       // own capacity pools hold, and only the what-if control forces one figure
       // on all of them. Interpolated raw, it printed "a null% safety line".
       ? `${d.forecasts.length} regions, each projected against its own safety line`
       : `${d.forecasts.length} regions projected against a ${d.thresholdPct}% safety line`) + `

  ${d.forecasts.map((f) => panel(`${f.region} — ${f.alreadyBreached
      ? (f.saturationDate ? `already over the line, full by ${f.saturationDate}`
                          : "already over the line")
      : (f.crossingDate ? `projected to cross on ${f.crossingDate}`
                        : "stays within the safety line")}`, `
    ${f.note ? `<p class="error" style="margin:0 0 1rem">${esc(f.note)}</p>` : ""}
    <div style="position:relative">${forecastChart(f)}</div>
    <p style="background:var(--page);border-left:3px solid var(--brand);
       padding:.7rem .9rem;margin:.75rem 0 0;font-size:.88rem">
      <b>In plain terms:</b> ${esc(f.region)} was
      <b>${f.history.length ? pct(f.history[f.history.length - 1].value, 1) : "—"} full</b>
      on ${esc(f.history.length ? f.history[f.history.length - 1].date : "")},
      ${f.alreadyBreached
        ? `already past its ${pct(f.thresholdPct)} safety line`
        : `against a ${pct(f.thresholdPct)} safety line`}.
      ${f.alreadyBreached
        ? (f.saturationDate
            ? `On this trend it has <b>no capacity left at all by ${esc(f.saturationDate)}</b>.`
            : `It is not projected to fill completely within the next ${d.projectionDays} days.`)
        : (f.crossingDate
            ? `On this trend it crosses that line on <b>${esc(f.crossingDate)}</b>${
                f.saturationDate ? ` and is completely full by ${esc(f.saturationDate)}${
                  f.saturationBeyondChart ? " — past the right-hand edge of this chart" : ""}` : ""}.`
            : `It is not projected to cross that line within the next ${d.projectionDays} days.`)}
      The blue line is a forecast — nothing to the right of the divider has happened yet.
    </p>
    ${f.extrapolatedBeyondHistory ? `<p style="color:var(--warn-ink,var(--ink-2));font-size:.8rem;
       margin:.4rem 0 0;border-left:3px solid var(--warn);padding-left:.7rem">
      This projection runs <b>${f.plottedDays} days</b> forward on
      <b>${f.history.length} days</b> of history. It is extrapolating past the
      window it was fitted on, so treat the date as a planning prompt rather
      than a commitment — the further right it sits, the weaker it is.
    </p>` : ""}
    <p style="color:var(--ink-3);font-size:.78rem;margin:.4rem 0 0">
      Both lines show utilisation, not ticket counts. Utilisation is a share of
      deployed capacity, so the projection is capped at 100% — a trend line
      running past that is not a forecast of anything.
    </p>
    <div class="kpis" style="margin-top:1rem">
      ${kpi("Model used", esc(f.model),
            f.forced
              ? `set for all regions${f.forced.wouldHaveChosen && f.forced.wouldHaveChosen !== f.model
                   ? ` — backtest picked ${esc(f.forced.wouldHaveChosen)}` : ""}`
              : (f.beatsNaive ? "beat the naive baseline" : "nothing beat naive"),
            f.forced ? "warn" : (f.beatsNaive ? "good" : "warn"),
            f.forced
              ? `This model was set for every region rather than chosen by measured accuracy. `
                + `For ${esc(f.region)} the backtest ranked `
                + `${esc(f.forced.wouldHaveChosen || "another model")} first; using `
                + `${esc(f.model)} instead carries ${f.forced.costPct > 0 ? "" : "no"} `
                + `${f.forced.costPct > 0 ? `${f.forced.costPct.toFixed(1)}% more error` : "penalty"}. `
                + `The full ranking below is unchanged.`
              : "Chosen by backtest on held-out data, not selected by hand.")}
      ${f.alreadyBreached
        ? kpi("Full by", f.saturationDate || "—",
              f.saturationDate ? "projected to reach 100% utilisation"
                               : `not projected to fill within ${d.projectionDays} days`,
              f.saturationDate ? "bad" : "", 
              "This region is already past its safety line, so a crossing date is history. What matters is when it runs out completely.")
        : kpi("Crossing date", f.crossingDate || "—",
              f.crossingEarliest ? `between ${f.crossingEarliest} and ${f.crossingLatest}`
                                 : `not projected within ${d.projectionDays} days`,
              f.crossingDate ? "bad" : "good",
              "First projected day past the safety line. The range comes from the error the model made on data it never saw.")}
      ${kpi("Forecast error (MAPE)", f.scores.length ? `${scoreFor(f).mape.toFixed(2)}%` : "—",
            f.scores.length && f.history.length
              ? `typically ±${(scoreFor(f).mape / 100 * f.history[f.history.length - 1].value).toFixed(1)} points out, ${d.horizonDays} days ahead`
              : "", "ink",
            `How wrong this model was when tested. It was fitted on part of the history, `
            + `asked to predict the ${d.horizonDays} days it had not seen, and marked against `
            + `what actually happened — repeated over ${f.scores.length ? scoreFor(f).folds : d.folds} `
            + `stretches of the record. Lower is better. Note it is a percentage `
            + `of the reading, not percentage points: ${f.scores.length ? scoreFor(f).mape.toFixed(2) : "—"}% `
            + `of a ${f.history.length ? f.history[f.history.length - 1].value.toFixed(0) : "—"}% `
            + `utilisation reading is about `
            + `${f.scores.length && f.history.length ? (scoreFor(f).mape / 100 * f.history[f.history.length - 1].value).toFixed(1) : "—"} points.`)}
      ${kpi("Skill vs naive", f.scores.length ? `${scoreFor(f).skillVsNaive > 0 ? "+" : ""}${scoreFor(f).skillVsNaive.toFixed(0)}%` : "—",
            f.scores.length
              ? (scoreFor(f).skillVsNaive > 0
                  ? `${scoreFor(f).skillVsNaive.toFixed(0)}% more accurate than assuming nothing changes`
                  : `worse than assuming nothing changes`)
              : "", f.beatsNaive ? "good" : "bad",
            `Whether the modelling was worth doing at all. The comparison is against `
            + `the simplest possible forecast — "tomorrow looks like today", carry the `
            + `last reading forward, no model. This is how much of that baseline's error `
            + `the chosen model removed. 0% means the modelling added nothing; a negative `
            + `number means it did worse than doing nothing, and that model is rejected `
            + `rather than shown.`)}
    </div>
    <details open style="margin-top:1rem"><summary style="cursor:pointer;color:var(--brand);font-size:.88rem">
      All ${f.scores.length} models scored</summary>
      <div class="scroll-x" style="margin-top:.5rem"><table>
        <thead><tr><th>Model</th>
          ${th("MAPE", "Mean absolute percentage error \u2014 on average, how far each "
             + "prediction landed from what actually happened, as a percentage of the "
             + "reading rather than in percentage points. A MAPE of 1% against a 90% "
             + "utilisation reading is about 0.9 points. Lower is better.", "n")}
          ${th("RMSE", "Root mean squared error, in percentage points. It squares each "
             + "miss before averaging, so one large error counts for far more than "
             + "several small ones \u2014 which is why the winner is chosen on this and "
             + "not on MAPE. In capacity planning it is the single big miss that causes "
             + "a denial, not a steady small drift.", "n")}
          ${th("Skill vs naive", "How much better than doing nothing at all. The "
             + "benchmark is the simplest forecast there is \u2014 carry the last "
             + "reading forward, no model. This is the share of that benchmark's error "
             + "the model removed. 0% means the modelling added nothing; below 0% it "
             + "did worse than assuming no change.", "n")}
          ${th("Folds", "How many separate stretches of the history the model was "
             + "tested on. Each fold hides a different final period, fits on what came "
             + "before it and marks the prediction against what happened. A model that "
             + "failed to fit on some folds is listed with fewer, and cannot win \u2014 "
             + "averaging only the stretches it managed would rank it against models "
             + "that sat the whole exam.", "n")}
        </tr></thead>
        <tbody>${f.scores.map((s, i) => `<tr>
          <td>${i === 0 ? `<b>${esc(s.model)}</b>` : esc(s.model)}</td>
          <td class="n">${s.mape.toFixed(2)}%</td>
          <td class="n">${s.rmse.toFixed(2)}</td>
          <td class="n" style="color:${s.skillVsNaive > 0 ? "var(--good)" : "var(--bad)"}">
            ${s.skillVsNaive > 0 ? "+" : ""}${s.skillVsNaive.toFixed(1)}%</td>
          <td class="n">${s.folds}</td>
        </tr>`).join("")}</tbody></table></div></details>
  `)).join("")}

  ${panel("Anomalies removed before fitting", `<div class="scroll-x"><table>
    <thead><tr><th>Region</th><th>Date</th><th class="n">Utilisation</th>
      <th>Direction</th><th>Attributed cause</th></tr></thead>
    <tbody>${Object.values(a.regions).flatMap((r) => r.outliers.map((o) => `<tr>
      <td>${esc(o.region)}</td><td>${esc(o.date)}</td>
      <td class="n">${pct(o.value, 1)}</td>
      <td>${o.direction === "above" ? `<span class="pill warn">spike</span>`
                                    : `<span class="pill mute">dip</span>`}</td>
      <td>${o.explained
        ? `${esc(o.eventType)} <span class="pill good">${o.daysBefore}d before</span>`
        : `<span class="pill mute">no event found in window</span>`}</td>
    </tr>`)).join("")}</tbody></table></div>
    <p style="color:var(--ink-2);font-size:.82rem;margin:.75rem 1.15rem 1.15rem">
      ${a.total} outliers by ${esc(a.method.fence)}, after removing a
      ${a.method.detrendWindow}-day trend and a ${a.method.season}-day cycle.
      ${a.explained} matched a business event inside ${a.method.eventWindowDays} days;
      ${a.unexplained} did not and are reported unattributed rather than given a
      cause. All of them are excluded from training — an unexplained spike is
      still not the trend.
    </p>`, { flush: true })}`;
};

/* =================================================================== 10/10 */
/* Capacity policy                                                           */

PAGES["/policy"] = async (view) => {
  const d = await get("/api/capacity-policy");
  const t = d.totals;

  view.innerHTML = howto({
    answers: "<b>What a tier reserve would have changed.</b> Today capacity is first-come-first-served, so a request arriving later can find a region already emptied.",
    steps: [
      { what: "The reserve", is: "the share of each region held for each subscription tier. Higher tiers may borrow unused lower-tier reserve; lower tiers may never borrow upward." },
      { what: "The simulation", is: "every request replayed in arrival order under that reserve, then compared with what actually happened." },
      { what: "Would have prevented", is: "failures that occurred in reality but would have been admitted under the reserve. Where it is zero, the region was genuinely out of capacity — no admission policy can conjure Capacity Units, so that one needs scaling, not rationing." },
      { what: "Capacity pools", is: "each region expressed as the Fabric SKU ladder, so a denial can be discussed in the units a customer actually buys." },
    ],
    words: [
      { term: "Capacity Unit (CU)", means: `The unit Fabric is billed and throttled in. This model converts raw compute at ${d.unitsPerCu} units per CU — an assumption, and every pool figure scales with it.` },
      { term: "Borrowing", means: "Downward only. An Enterprise request may consume unspent Free-tier reserve; the reverse would defeat the purpose of holding any back." },
    ],
    next: "Compare 'would have prevented' against 'actual failures' per region. A high number means the capacity existed and was allocated badly; zero means it did not exist.",
    sources: "the incident history replayed in arrival order against each region's deployed capacity.",
  }) + title("Capacity policy", `A tier reserve would have prevented ${t.wouldHavePrevented} of ${t.actualFailures} failures`) + `

  <div class="kpis">
    ${kpi("Would have prevented", num(t.wouldHavePrevented), `of ${num(t.actualFailures)} actual failures`, "good", "Requests that failed in reality but would have been admitted under the reserve.")}
    ${kpi("Reserve would still deny", num(t.denied), "genuinely out of capacity", "bad", "Requests the reserve would refuse because the region did not have the units. No allocation policy fixes this \u2014 it is a procurement problem.")}
    ${kpi("Admitted", num(t.admitted), "under the simulated reserve", "ink")}
    ${kpi("Reserve in force", Object.entries(d.reserve).map(([k, v]) => `${Math.round(v * 100)}`).join(" / "),
          Object.keys(d.reserve).join(" / "), "ink", "Share of each region held for each tier. Must sum to 100%.")}
  </div>

  ${panel("By region", `<div class="scroll-x"><table>
    <thead><tr><th>Region</th><th class="n">Capacity</th><th class="n">Admitted</th>
      <th class="n">Would deny</th><th class="n">Actual failures</th>
      <th class="n">Would have prevented</th><th>Verdict</th></tr></thead>
    <tbody>${d.regions.map((r) => `<tr>
      <td><b>${esc(r.region)}</b></td>
      <td class="n">${num(Math.round(r.capacity))}</td>
      <td class="n">${num(r.admitted)}</td>
      <td class="n">${r.denied ? `<b style="color:var(--bad)">${num(r.denied)}</b>` : "—"}</td>
      <td class="n">${num(r.actualFailures)}</td>
      <td class="n">${r.wouldHavePrevented
        ? `<b style="color:var(--good)">${num(r.wouldHavePrevented)}</b>` : "—"}</td>
      <td>${r.wouldHavePrevented
        ? `<span class="pill good">allocation problem</span>`
        : (r.denied ? `<span class="pill bad">out of capacity</span>`
                    : `<span class="pill mute">no failures</span>`)}</td>
    </tr>`).join("")}</tbody></table></div>`, { flush: true })}

  ${panel("Capacity pools", `<div class="scroll-x"><table>
    <thead><tr><th>Region</th><th class="n">Compute units</th>
      <th class="n">Capacity units</th><th>Equivalent Fabric SKU</th></tr></thead>
    <tbody>${d.pools.map((p) => `<tr>
      <td><b>${esc(p.Region)}</b></td>
      <td class="n">${num(Math.round(p.DeployedUnits))}</td>
      <td class="n">${num(Math.round(p.CapacityUnits))}</td>
      <td><span class="pill info">${esc(p.EquivalentSKU)}</span></td>
    </tr>`).join("")}</tbody></table></div>
    <p style="color:var(--ink-2);font-size:.82rem;margin:.75rem 1.15rem 1.15rem">
      Fabric is sold as an F-SKU rated in Capacity Units, not as raw compute.
      The ladder is real; the ${d.unitsPerCu} units-per-CU conversion is assumed.
    </p>`, { flush: true })}`;
};






