/* The six tabs. Each registers itself on PAGES (declared in shell.js).
 *
 * A renderer takes the <main> element, writes it, and wires its own controls.
 * Nothing here holds cross-page state -- switching tabs re-fetches, which at
 * this data size is cheaper than a cache that can go stale mid-review.
 */

/* ==================================================================== 1/6 */
/* Overview                                                                  */

PAGES["/"] = async (view) => {
  const d = await get("/api/overview");
  const k = d.kpis;

  const dueNow = d.regions.filter((r) =>
    ["breached", "overdue", "due_now"].includes(r.status));
  const attention = [...d.regions]
    .filter((r) => r.exposure > 0 || dueNow.includes(r))
    .sort((a, b) => (a.daysUntilOrder ?? 999) - (b.daysUntilOrder ?? 999));

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
    answers: "<b>Portfolio position across all monitored regions</b> \u2014 procurement urgency, request outcomes, and the revenue attributed to failures.",
    steps: [
      { what: "KPI strip", is: "portfolio totals \u2014 regions under management, procurement actions now due, and the revenue impact of failed requests." },
      { what: "Request outcomes", is: "the two failure types the tool targets — SLA breached before being granted, and never granted at all. Everything handled inside SLA is collapsed into one line below them, for reconciliation only." },
      { what: "Demand distribution", is: "request volume by region, highest first \u2014 the link between the region count and the incident count. Select a row to open that region." },
      { what: "Denial reason analysis", is: "root cause for every denial with the corresponding remediation path. This determines which team owns the fix." },
      { what: "Regions requiring action", is: "prioritised by procurement urgency, with the rationale for each flag." },
    ],
    words: [
      { term: "FTR", means: "First-time resolution \u2014 approved on the initial pass with no denial recorded." },
      { term: "SLA", means: "Committed turnaround by subscription tier \u2014 Enterprise 48h, Premium 72h, Standard and Free 96h. Resolution inside the window is normal handling; beyond it is a breach and is classified as a failure." },
      { term: "Revenue loss", means: "<b>Microsoft revenue, not the customer&rsquo;s own.</b> The subscription\u2019s ARR, apportioned by the share of the request left unfulfilled and the duration of the shortfall." },
      { term: "Manual review", means: "Denial causes with no automated remediation \u2014 quota policy, network faults and unrecorded causes require engineering or account-team engagement rather than a platform action." },
    ],
    next: "Review the denial reason analysis to identify the applicable remediation, then action any region showing a negative value in 'Days to order-by' \u2014 those procurement windows have elapsed.",
    sources: "ICM capacity incident extract, subscription ARR reference, daily regional utilisation, and hardware provisioning lead times.",
  }) + title("Overview", `Everything across all regions — as of ${String(d.asOf).slice(0, 10)}`) + `

  <div class="kpis">
    ${kpi("Regions monitored", num(d.regions.length), `across ${d.skus.length} hardware classes`, "ink", "Azure regions with capacity requests in this extract. Each region contains several data centres.")}
    ${kpi("Procurement action due", num(dueNow.length), "order-by date elapsed", dueNow.length ? "bad" : "good", "Regions where the latest date to place a hardware order has already passed. Replacement hardware takes 10 to 45 days to arrive depending on class.")}
    ${kpi("Revenue loss", money(k.exposure), `attributed to ${k.failed} failed requests`, "bad", "Microsoft revenue, not the customer\u2019s own. Each customer\u2019s ARR, apportioned by the share of their request left unfulfilled and how long it stayed unfulfilled. A severity ranking, not money written off.")}
    ${kpi("ARR impacted", money(k.arrAffected), `combined ARR of ${k.customers} affected customers`, "", "Total annual revenue of every customer who hit at least one failure, counted once each. Blast radius rather than loss \u2014 always larger than revenue loss.")}
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
    ${d.partial && d.partial.count ? `
    <p style="background:var(--warn-wash);border-left:3px solid var(--warn);
       padding:.6rem .9rem;margin:0 1.15rem 1.15rem;font-size:.82rem">
      <b>A third outcome the extract cannot express.</b>
      ${num(d.partial.count)} request(s) were met <b>in part</b> rather than
      granted or refused — ${num(d.partial.units)} units given against
      ${num(d.partial.units + d.partial.shortfallUnits)} asked for, a median of
      ${pct(d.partial.medianGrantedPct, 0)} of the ask, across
      ${esc(d.partial.regions.join(", "))}.
      These are not in the five counts above and move no published figure.
      <span style="color:var(--ink-3)">${esc(d.partial.note)}</span>
    </p>` : ""}
  `, { flush: true })}

  ${panel("Demand distribution by region", `<div class="scroll-x"><table>
    <thead><tr><th>Region</th><th class="n">Requests</th><th class="n">Share</th>
      <th class="n">Failed</th><th class="n">Customers</th>
      <th class="n">Data centres</th><th>Volume</th></tr></thead>
    <tbody>${(d.regionDistribution || []).map((r) => `<tr class="clickable" data-region="${esc(r.region)}">
      <td><b>${esc(r.region)}</b></td>
      <td class="n">${num(r.requests)}</td>
      <td class="n">${pct(r.sharePct, 1)}</td>
      <td class="n">${r.failed ? `<b style="color:var(--bad)">${num(r.failed)}</b>` : "—"}</td>
      <td class="n">${num(r.customers)}</td>
      <td class="n">${num(r.datacentres)}</td>
      <td><span class="bar" style="width:${(r.requests / (d.regionDistribution[0]?.requests || 1)) * 100}%"></span></td>
    </tr>`).join("")}</tbody></table></div>`,
    { hint: `${d.regionDistribution?.length || 0} regions · highest volume first`, flush: true })}

  ${panel("Denial reason analysis", `<div class="scroll-x"><table>
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
    { hint: "the fix depends on the cause", flush: true })}

  ${panel("Regions requiring action", attention.length ? `<div class="scroll-x"><table>
    <thead><tr>
      <th>Region</th><th>Status</th><th>Flag rationale</th>
      <th class="n">Days to order-by</th><th class="n">Revenue loss</th><th class="n">Failed</th>
    </tr></thead>
    <tbody>${attention.map((r) => `<tr class="clickable" data-region="${esc(r.region)}">
      <td><b>${esc(r.region)}</b><br><span class="pill mute">${esc(r.sku)} · ${r.leadTime}d lead</span></td>
      <td>${statusPill(r.status)}</td>
      <td class="why">${esc(r.reason)}</td>
      <td class="n">${r.daysUntilOrder == null ? "—" :
        `<b style="color:${r.daysUntilOrder < 0 ? "var(--bad)" : "inherit"}">${r.daysUntilOrder}</b>`}</td>
      <td class="n">${money(r.exposure)}</td>
      <td class="n">${num(r.failed)}</td>
    </tr>`).join("")}</tbody></table></div>`
    : `<p class="empty">No region is currently flagged.</p>`,
    { hint: "click a row for detail", flush: true })}
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
   ends with a link into the data centres rather than guessing at hardware. */
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
            `${num(Math.round(d.usedUnits))} of ${num(Math.round(d.deployedUnits))} cores`,
            d.thresholdUsedPct > 0 ? "bad" : "")}
      ${kpi("Safety threshold", pct(d.thresholdPct, 0),
            d.thresholdUsedPct > 0
              ? `threshold utilised by ${d.thresholdUsedPct.toFixed(1)}%`
              : `${num(Math.round(d.freeUnits))} cores free`,
            d.thresholdUsedPct > 0 ? "bad" : "good")}
      ${kpi("Cores pending", num(Math.round(d.coresPending)),
            `owed to ${d.customersWaiting} customer(s)`, d.coresPending ? "bad" : "good")}
    </div>

    ${d.options.length ? `<h4 style="margin:0 0 .3rem;font-size:.85rem">If the safety line moved</h4>
    <div class="scroll-x"><table>
      <thead><tr><th>New line</th><th class="n">Cores released</th>
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

   They are merged, not averaged: cores and percent-full are different units and
   keep an axis each, so nothing is rescaled into a number that was never
   measured. `combinedRegionChart` draws that; `demandChart` below still serves
   customers and data centres, where no utilisation series exists to pair with. */

let CHART_SEQ = 0;
const CHART_DATA = {};

/* Hover that follows the cursor rather than waiting on the browser's own
   tooltip. `<title>` needed a second of stillness and could not show more than
   one line, which is no use on a chart whose whole point is "this month, this
   many cores, because of this deal". */
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
      html: `<b>${esc(m.month)}</b><br>${num(Math.round(m.cores))} cores`
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
      transform="rotate(-90 12 ${(T + H - B) / 2})" text-anchor="middle">cores requested</text>
    <line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="var(--rule-strong)"/>
    ${d.baselineCores > 0 ? `
      <line x1="${L}" x2="${W - R}" y1="${y(d.baselineCores)}" y2="${y(d.baselineCores)}"
        stroke="var(--ink-3)" stroke-dasharray="4 3" opacity=".7"/>
      <text x="${W - R}" y="${y(d.baselineCores) - 4}" font-size="9.5" text-anchor="end"
        fill="var(--ink-3)">ordinary month ≈ ${num(Math.round(d.baselineCores))} cores</text>` : ""}
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

   Two units, so two axes: cores on the left, how-full on the right. Nothing is
   rescaled into a number nobody measured. Colour carries the series and dash
   carries the tense -- blue is cores, grey is utilisation, dashed is projected
   in both cases -- because a reader who learns the rule once should not have to
   relearn it halfway across the chart.

   The x axis is real dates rather than an index, which is what lets a daily
   series and a monthly one share it honestly: a monthly point sits at the
   middle of its month, where the aggregate belongs, not at its edge. */
function combinedRegionChart(d, f) {
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
     its range, and the cores asked for in the month it falls in. */
  const coresBy = Object.fromEntries(
    hist.map((m) => [m.month, m]).concat(proj.map((m) => [m.month, { ...m, isProjection: true }])));
  const monthOf = (iso) => iso.slice(0, 7);
  const coresLine = (month) => {
    const m = coresBy[month];
    if (!m) return `<span class="t-mute">no request record in ${esc(month)}</span>`;
    return `${num(Math.round(m.cores))} cores requested in ${esc(month)}`
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
    <span><i class="ln demand"></i>Cores requested — what customers asked for (left axis)</span>
    ${proj.length ? `<span><i class="ln demand-proj"></i>Projected demand</span>` : ""}
    ${hasUtil ? `<span><i class="ln util"></i>How full the region ran — one reading per day (right axis)</span>` : ""}
    ${hasUtil && fProj.length ? `<span><i class="ln util-proj"></i>Projected utilisation${f.model ? ` — ${esc(f.model)}` : ""}</span>` : ""}
    ${band ? `<span><i class="ln util-band"></i>Range the forecast could be out by</span>` : ""}
    ${hasUtil ? `<span><i class="ln limit"></i>Safety threshold ${pct(f.thresholdPct, 1)}</span>` : ""}
    ${d.baselineCores > 0 ? `<span><i class="ln baseline"></i>Ordinary month ≈ ${num(Math.round(d.baselineCores))} cores</span>` : ""}
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
      transform="rotate(-90 14 ${T + (H - T - B) / 2})" text-anchor="middle">cores requested</text>
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
      ${proj.map((x) => `${esc(x.month)} ≈ ${num(Math.round(x.cores))} cores`).join(" · ")}
      <span style="color:var(--ink-3)"> — model: ${esc(d.model)}</span></p>` : ""}
    ${d.note ? `<p style="color:var(--ink-3);font-size:.8rem;margin:.4rem 0 0">${esc(d.note)}</p>` : ""}
    <p style="color:var(--ink-3);font-size:.78rem;margin:.5rem 0 0">
      An ordinary month for this account is about
      ${num(Math.round(d.baselineCores))} cores. This series is held in its own
      table and is never counted into exposure, failure counts or cores pending —
      those come only from the recorded incidents.
    </p>`);
}

/* The capacities inside a facility: what is actually there, and how each one is.

   The question review kept returning to and the product could not answer -- "hey,
   these are the SKUs there in this data centre, this is the capacity available,
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

function capacityRows(d, region) {
  const fleet = d.capacities.length ? d.capacities[0].fleetIncidentsPerNode : 0;
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>SKU</th><th class="n">CU</th><th class="n">Units</th>
      <th class="n">Used</th><th>Hardware</th><th class="n">Nodes</th>
      ${/* Two numbers, so two columns. These were stacked under one heading
            reading "Incidents per node", which labelled a count as a rate: the
            large figure was the raw count and the small one the per-node rate,
            and a reader had no way to know which was which. The fleet
            comparison also only appeared on capacities that crossed the
            unhealthy line, so a healthy one showed a bare rate with nothing to
            judge it against. Both are now always stated. */ ""}
      <th class="n">Incidents<br><span class="t3">total</span></th>
      <th class="n" title="The fleet is every capacity in the estate, across all regions — not only the ones on this page. It stays the whole estate when this page is filtered, so a region of uniformly poor hardware cannot compare itself against itself and call it normal.">Per node<br><span class="t3">vs fleet ${fleet.toFixed(1)}</span></th>
      <th>Free viewers</th>
    </tr></thead>
    <tbody>${d.capacities.map((c) => {
      const hot = c.utilisationPct >= 90;
      const sick = fleet > 0 && c.rateVsFleet >= 1.4;
      return `<tr>
      <td><a href="/capacity/${encodeURIComponent(c.capacityId)}">${esc(c.capacityId)}</a></td>
      <td><b>${esc(c.fabricSku)}</b></td>
      <td class="n">${num(c.capacityUnits)}</td>
      <td class="n">${num(c.deployedUnits)}</td>
      <td class="n ${hot ? "t-bad" : ""}">${pct(c.utilisationPct, 1)}</td>
      <td class="hw">${esc(c.vendor)} ${esc(c.model)}
        <span class="t3">${esc(c.skuClass)} · ${esc(c.cpu)} · ${num(c.memoryGB)}GB</span></td>
      <td class="n">${c.nodes}</td>
      <td class="n" title="${c.incidents} incident(s) recorded, ${c.seriousIncidents} of them Sev1 or Sev2, ${c.downtimeHours}h lost">
        ${c.incidents}
        <span class="t3">${c.seriousIncidents} sev1/2</span></td>
      <td class="n ${sick ? "t-bad" : ""}" title="${c.incidents} incident(s) over ${c.nodes} node(s), shrunk toward the fleet rate of ${c.fleetIncidentsPerNode.toFixed(1)}">
        ${c.incidentsPerNode.toFixed(1)}
        <span class="t3">${c.rateVsFleet}×${sick ? " — worse" : ""}</span></td>
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
  const usedPct = d.totalUnits ? (d.usedUnits / d.totalUnits) * 100 : 0;

  return panel(`Capacities here — ${d.count} on ${Object.keys(d.skuMix).length} SKU size(s)`, `
    ${skuBar(d.skuMix, d.totalUnits)}
    ${capacityRows(d, id)}
    <p style="background:var(--page);border-left:3px solid var(--brand);
       padding:.7rem .9rem;margin:.9rem 0 0;font-size:.88rem">
      <b>In plain terms:</b> this holds <b>${num(d.totalUnits)} units</b> across
      <b>${d.count} Fabric capacit${d.count === 1 ? "y" : "ies"}</b>, of which
      <b>${num(Math.round(d.usedUnits))}</b> are in use
      (${pct(usedPct, 1)}) and <b>${num(Math.round(d.freeUnits))}</b> are free.
      ${d.freeViewerCapable
        ? `${d.freeViewerCapable} of them ${d.freeViewerCapable === 1 ? "is" : "are"}
           F64 or larger, so Power BI content on ${d.freeViewerCapable === 1 ? "it" : "them"}
           can be read on a free licence.`
        : `None is F64 or larger, so every user viewing Power BI content on any of
           these needs a Pro or PPU licence — a commercial cost that no utilisation
           figure shows.`}
      <b>Incidents</b> is the raw count; <b>per node</b> divides it by the
      machines underneath, which is the only way to compare a one-node F4 with a
      two-node F64 — the larger one will always see more trouble simply for
      being larger. That rate is then shrunk toward the fleet average in
      proportion to how many nodes stand behind it, so a one-node capacity
      having a bad month does not outrank a whole estate.
      ${d.fleet ? `<b>The fleet</b> means every capacity everywhere —
      ${num(d.fleet.capacities)} of them across ${num(d.fleet.sites)} sites in
      ${num(d.fleet.regions)} regions, ${num(d.fleet.incidents)} incidents over
      ${num(d.fleet.nodes)} nodes, so <b>${d.fleet.incidentsPerNode}</b> per node
      is ordinary. It stays the whole estate even when this page shows one site,
      because a region of uniformly poor hardware compared against itself would
      report that everything was fine.` : ""}
      Anything at 1.4× or worse is where moving the workload starts to beat
      buying more of the same hardware.
    </p>
    <p style="color:var(--ink-3);font-size:.78rem;margin:.5rem 0 0">${esc(d.note)}</p>`);
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
  if (scope === "region") {
    try {
      f = await get(`/api/forecast/${encodeURIComponent(id)}?full=true`);
    } catch (err) {
      f = null;
    }
  }
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
      : "Demand — capacity requested per month", `
    <div style="position:relative">${
      showChart ? combinedRegionChart(d, f) : demandChart(d)}</div>
    <p style="background:var(--page);border-left:3px solid var(--brand);
       padding:.7rem .9rem;margin:.75rem 0 0;font-size:.88rem">
      <b>In plain terms:</b> ${showChart ? `this chart carries two different
      measurements, which is why it has two scales.
      The <b style="color:var(--brand)">blue line</b> is what customers
      <b>asked for</b> — cores per month, read on the left. The
      <b style="color:var(--ink-2)">grey line</b> is how <b>full the region
      actually ran</b> — a percentage, one reading per day, read on the right.
      One is demand arriving, the other is the room left to absorb it.
      Dashed means projected, on either line. ` : ""}An ordinary month here is about
      <b>${num(Math.round(d.baselineCores))} cores</b>.
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
           ${peak ? `The largest was <b>${num(Math.round(peak.cores))} cores</b> in ${esc(peak.month)}.` : ""}
           These months stay in the forecast: a signed deal is part of what this
           place asks for, and removing them would project somewhere that never
           signs anything.`
        : `No month here was driven by a recorded business event.`}
      ${rich ? `<br><br>On the utilisation side, the region was
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
        That threshold is this region's own — derived from the thresholds its data
        centres actually hold, not one figure applied everywhere.` : ""}
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
    </p>`)
  + (!d.thresholdSeries?.length && d.thresholdSeriesNote
      ? `<p class="hint" style="margin:.5rem 0 1rem">${esc(d.thresholdSeriesNote)}</p>`
      : "");
}

/* ==================================================================== 2/11 */
/* Fleet map                                                                 */

/* The landing surface review asked for: "you yourself think you're a capacity
   manager, you are sitting in front of all your data centres, you have your map
   in front". The complaint it answers is not that the numbers were wrong but
   that reaching them took four tabs -- "I have to do so many clicks to get me an
   answer. By looking at it, you should be able to get those insights."

   So a marker carries what would otherwise be four screens: how full the region
   is, whether it crosses its safety line and when, what is waiting to be bought,
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
  if (p.status === "overdue" || p.status === "due") return "warn";
  return "good";
}

const MAP_TONE_FILL = { bad: "var(--bad)", warn: "var(--warn)", good: "var(--good)" };

function fleetMap(d) {
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
  const maxUnits = Math.max(...pts.map((p) => p.units), 1);
  const radius = (u) => 1.6 + 2.9 * Math.sqrt(u / maxUnits);

  return `<svg class="chart fleet-map" viewBox="${W.x} ${W.y - pad} ${W.w} ${W.h + pad * 2}"
      role="img" aria-label="Capacity by region, on a world map">
    <rect x="${W.x}" y="${W.y - pad}" width="${W.w}" height="${W.h + pad * 2}" fill="var(--map-sea)"/>
    <path d="${WORLD_PATH}" fill="var(--map-land)" stroke="var(--map-edge)" stroke-width=".25"/>
    ${pts.map((p) => {
      const tone = mapTone(p);
      const r = radius(p.units);
      return `
      ${p.moved ? `<line x1="${(p.lon + 180).toFixed(2)}" y1="${(90 - p.lat).toFixed(2)}"
        x2="${p.mx.toFixed(2)}" y2="${p.my.toFixed(2)}"
        stroke="var(--ink-3)" stroke-width=".2" opacity=".55"/>` : ""}
      <circle class="mk" data-region="${esc(p.region)}"
        cx="${p.mx.toFixed(2)}" cy="${p.my.toFixed(2)}" r="${r.toFixed(2)}"
        fill="${MAP_TONE_FILL[tone]}" fill-opacity=".85"
        stroke="#fff" stroke-width=".45" tabindex="0"
        role="button" aria-label="${esc(p.region)}, ${p.utilisation}% used">
        <title>${esc(p.region)} — ${p.utilisation}% used</title>
      </circle>`;
    }).join("")}
  </svg>`;
}

/* What a marker says when you land on it. Deliberately the four questions that
   were four tabs, in the order a planner asks them. */
function mapCard(p) {
  const gaps = p.unavailableFeatures || [];
  const recs = p.recommendations || {};
  const tone = mapTone(p);
  return `
  <div class="map-card">
    <header>
      <b>${esc(p.region)}</b>
      <span class="pill ${tone}">${esc(p.status || "—")}</span>
    </header>
    <p class="where">${esc(p.displayName)}${p.city ? ` · ${esc(p.city)}` : ""}</p>

    <div class="rows">
      <div><span>How full</span><b>${p.utilisation != null ? pct(p.utilisation, 1) : "—"}
        <span class="t3">of a ${p.thresholdPct != null ? pct(p.thresholdPct, 1) : "—"} line</span></b></div>
      <div><span>Crosses</span><b>${p.crossingDate ? esc(p.crossingDate)
        : (p.status === "breached" ? "already past it" : "not within the year")}</b></div>
      <div><span>Fleet</span><b>${num(p.capacities)} capacities in ${num(p.sites)} sites
        <span class="t3">· ${num(p.units)} units</span></b></div>
      <div><span>Hardware</span><b>${esc(p.skuClass || "—")}
        <span class="t3">· ${p.leadTimeDays ?? "—"}-day lead time</span></b></div>
      ${p.coresPending ? `<div><span>Owed</span><b>${num(p.coresPending)} cores pending
        <span class="t3">· ${num(p.failed)} failed requests</span></b></div>` : ""}
    </div>

    ${gaps.length ? `<p class="gaps">
      <b>${num((p.workloadsAvailable || []).length)} of 9 workloads fully available.</b>
      ${gaps.length} feature(s) missing across ${(p.workloadsPartlyAffected || []).length} area(s)
      — ${gaps.map(esc).join(", ")}.
      <span class="t3">Microsoft's published availability, not a projection.</span></p>`
      : `<p class="gaps ok">All 9 Fabric workloads available here, with nothing missing.
         <span class="t3">Microsoft's published availability.</span></p>`}

    ${(recs.procurement || recs.workload_change || recs.licensing) ? `
      <p class="acts">
        ${recs.procurement ? `<a href="/recommendations?region=${encodeURIComponent(p.region)}&kind=procurement">${recs.procurement} to buy</a>` : ""}
        ${recs.workload_change ? `<a href="/recommendations?region=${encodeURIComponent(p.region)}&kind=workload_change" class="warn">${recs.workload_change} to move</a>` : ""}
        ${recs.licensing ? `<a href="/recommendations?region=${encodeURIComponent(p.region)}&kind=licensing">${recs.licensing} licensing</a>` : ""}
      </p>` : `<p class="acts t3">Nothing outstanding here.</p>`}

    <p class="links">
      <a href="/region/${encodeURIComponent(p.region)}">Open ${esc(p.region)}</a> ·
      <a href="/datacentres?region=${encodeURIComponent(p.region)}">Its ${num(p.sites)} sites</a>
    </p>
  </div>`;
}

/* What opens under the map when a marker is picked.

   The card beside the map answers "is this a problem". Review asked for what
   follows it -- "how many data centres are there, and how they need to change
   and why, and when are we going to hit the threshold, and what hardware is
   being used in each and every data centre" -- which is four screens' worth and
   does not fit a column. It goes full width below the map instead, in the order
   the questions get asked: when, then what to change and why, then what is
   actually in each building. */

function whenBlock(d) {
  const t = d.threshold || {};
  const late = d.daysUntilOrder != null && d.daysUntilOrder < 0;
  return `
  <div class="when">
    <div class="when-rows">
      <div><span>Runs at</span><b>${pct(d.utilisation, 1)}</b>
        <i>against its own ${pct(d.thresholdPct, 1)} safety line</i></div>
      <div><span>Crosses the line</span>
        <b class="${t.alreadyBreached ? "t-bad" : ""}">${t.alreadyBreached
          ? "already past it"
          : (t.crossingDate ? esc(t.crossingDate) : "not within the year")}</b>
        <i>${t.crossingEarliest && !t.alreadyBreached
          ? `somewhere between ${esc(t.crossingEarliest)} and ${esc(t.crossingLatest)}, on ${esc(t.model || "the fitted model")}`
          : ""}</i></div>
      <div><span>Completely full</span>
        <b>${t.saturationDate ? esc(t.saturationDate) : "not within the year"}</b>
        <i>${t.note ? esc(t.note) : ""}</i></div>
      <div><span>Order by</span>
        <b class="${late ? "t-bad" : ""}">${d.orderByDate ? esc(d.orderByDate) : "—"}</b>
        <i>${d.daysUntilOrder == null ? ""
          : late ? `${Math.abs(d.daysUntilOrder)} days ago — ${esc(d.skuClass)} takes ${d.leadTimeDays} days to arrive, and the crossing is sooner than that`
                 : `in ${d.daysUntilOrder} days, allowing ${d.leadTimeDays} days for ${esc(d.skuClass)} to arrive`}</i></div>
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
   on -- for a purchase that is the hardware class, because the lead time is a
   property of the hardware and not of the capacity. */

function overdueCell(days) {
  if (days == null) return "—";
  if (days < 0) return `<b class="t-bad">${num(Math.abs(days))}d late</b>`;
  if (days === 0) return `<b class="t-warn">today</b>`;
  return `in ${num(days)}d`;
}

function buyTable(list) {
  // The lead-time argument depends on the hardware, so it is stated per class
  // rather than per capacity -- which is what made it repeat.
  const classes = new Map();
  for (const r of list) {
    const e = r.evidence || {};
    if (!classes.has(e.skuClass)) classes.set(e.skuClass, e);
  }
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>Data centre</th><th>SKU</th><th>Hardware</th>
      <th class="n">Used</th><th class="n">Its line</th><th class="n">Raise at</th>
      <th class="n">Order by</th><th class="n">Overdue</th><th class="n">Units</th>
    </tr></thead>
    <tbody>${list.map((r) => {
      const e = r.evidence || {};
      return `<tr>
        <td><a href="/capacity/${encodeURIComponent(r.target)}">${esc(r.target)}</a></td>
        <td>${esc(e.datacentre || "")}</td>
        <td><b>${esc(e.fabricSku || "")}</b></td>
        <td>${esc(e.skuClass || "")}<span class="t3"> · ${e.leadTimeDays}d</span></td>
        <td class="n ${e.utilisationPct >= 90 ? "t-bad" : ""}">${pct(e.utilisationPct, 0)}</td>
        <td class="n">${pct(e.standardTriggerPct, 0)}</td>
        <td class="n ${e.raisedEarly ? "t-warn" : ""}"
            title="${e.raisedEarly ? "below its line, but the wait is longer than the time left" : ""}">
          ${pct(e.adjustedTriggerPct, 1)}</td>
        <td class="n">${esc(e.orderByDate || "—")}</td>
        <td class="n">${overdueCell(e.daysUntilOrder)}</td>
        <td class="n">${num(e.unitsToAdd)}</td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <div class="why-notes">
    ${[...classes.values()].map((e) => `
      <p><b>${esc(e.skuClass)}</b> — ${e.leadTimeDays} days to provision${
        e.supplier ? ` from ${esc(e.supplier)}` : ""}.
      ${e.leadTimeDrifted
        ? `That has gone from <b>${num(e.leadTimeWasDays)} to ${num(e.leadTimeDays)} days</b>
           (${e.leadTimeChangePct > 0 ? "+" : ""}${e.leadTimeChangePct}%), so the raise-at figure
           is several points lower than it used to be and waiting for the old one now lands the
           order about ${num(Math.round(e.leadTimeDays - e.leadTimeWasDays))} days late.`
        : `That has not moved materially, so the raise-at figure is simply the line less
           whatever the region grows in ${e.leadTimeDays} days.`}</p>`).join("")}
    <p class="t3">"Raise at" is the utilisation at which the order must go in: the line, less
      whatever the region grows while the hardware is in transit. A row below its line with an
      order already overdue is the case a threshold alone cannot make.</p>
  </div>`;
}

function moveTable(list) {
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>Data centre</th><th>SKU</th><th>On</th>
      <th class="n">Used</th><th class="n">Incidents</th><th class="n">Sev1/2</th>
      <th class="n">Lost</th><th class="n">Per node<br><span class="t3">vs fleet</span></th>
      <th>Move to</th><th class="n">Fewer<br><span class="t3">incidents</span></th>
    </tr></thead>
    <tbody>${list.map((r) => {
      const e = r.evidence || {};
      const to = e.moveTo || {};
      return `<tr>
        <td><a href="/capacity/${encodeURIComponent(r.target)}">${esc(r.target)}</a></td>
        <td>${esc(e.datacentre || "")}</td>
        <td><b>${esc(e.fabricSku || "")}</b></td>
        <td>${esc(e.skuClass || "")}</td>
        <td class="n">${pct(e.utilisationPct, 0)}</td>
        <td class="n">${num(e.incidents)}</td>
        <td class="n">${num(e.seriousIncidents)}</td>
        <td class="n">${e.downtimeHours}h</td>
        <td class="n t-bad" title="fleet runs ${e.fleetIncidentsPerNode}">
          ${Number(e.incidentsPerNode).toFixed(1)}<span class="t3">${e.rateVsFleet}×</span></td>
        <td>${esc(to.sku_class || "")}
          <span class="t3">${esc(to.vendor || "")} ${esc(to.model || "")} · ${num(to.memoryGB)}GB</span></td>
        <td class="n">${to.expectedReductionPct != null ? `${to.expectedReductionPct}%` : "—"}</td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <div class="why-notes">
    <p>Every row here has <b>room to spare</b> — none is near its line. What is wrong is the
      hardware under it, so adding more of the same would not fix it. "Per node" divides incidents
      by the machines underneath and is shrunk toward the fleet average, so a one-node capacity
      having a bad month cannot outrank an estate.</p>
    <p class="t3">The replacement is never smaller on memory: trading an incident problem for a
      capability problem is not a fix.</p>
  </div>`;
}

function licenceTable(list) {
  const e0 = (list[0] || {}).evidence || {};
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Capacity</th><th>Data centre</th><th>SKU</th><th class="n">CU now</th>
      <th>Step to</th><th class="n">CU after</th><th>What it changes</th>
    </tr></thead>
    <tbody>${list.map((r) => {
      const e = r.evidence || {};
      return `<tr>
        <td><a href="/capacity/${encodeURIComponent(r.target)}">${esc(r.target)}</a></td>
        <td>${esc(e.datacentre || "")}</td>
        <td><b>${esc(e.fabricSku || "")}</b></td>
        <td class="n">${num(e.capacityUnits)}</td>
        <td><b>${esc(e.stepTo || "")}</b></td>
        <td class="n">${num(e.stepToUnits)}</td>
        <td>Power BI viewers stop needing a paid licence</td>
      </tr>`;
    }).join("")}</tbody></table></div>
  <div class="why-notes">
    <p>${esc(e0.rule || "")}
      ${e0.subscriptionsInRegion
        ? ` ${num(e0.subscriptionsInRegion)} subscriptions have raised capacity requests in this
            region, so the viewer population is not nil.`
        : ""}</p>
    <p class="t3">Real and documented — <a href="${esc(e0.source || "")}">Microsoft Fabric
      licensing</a>. This is a commercial decision, not a capacity one: none of these capacities
      is short of compute.</p>
  </div>`;
}

function changeBlock(d) {
  const kinds = [
    ["workload_change", "Move the workload", moveTable],
    ["procurement", "Buy capacity", buyTable],
    ["licensing", "Change the licence", licenceTable],
  ];
  const present = kinds.filter(([k]) => (d.recommendations || {})[k]?.length);
  if (!present.length) return `<p class="empty">Nothing outstanding in this region.</p>`;

  return present.map(([k, label, render]) => {
    const list = d.recommendations[k];
    // Twenty rows is a table; sixty is a report. The rest are one click away
    // and are, by construction, the least urgent.
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
  const fleet = d.fleetIncidentsPerNode || 0;
  return `
  <div class="tablewrap"><table class="grid caps">
    <thead><tr>
      <th>Data centre</th><th>Hardware</th><th class="n">Lead time</th>
      <th class="n">Capacities</th><th>SKUs</th>
      <th class="n">Units</th><th class="n">Used</th><th class="n">Free</th>
      <th class="n">Incidents<br><span class="t3">per node</span></th>
    </tr></thead>
    <tbody>${d.sites.map((s) => `
      <tr>
        <td><a href="/datacentre/${encodeURIComponent(s.datacentre)}">${esc(s.datacentre)}</a></td>
        <td class="hw">${esc(s.vendor)} ${esc(s.model)}
          <span class="t3">${esc(s.skuClass)} · ${esc(s.cpu)} · ${num(s.memoryGB)}GB</span></td>
        <td class="n">${s.leadTimeDays}d</td>
        <td class="n">${s.capacities}</td>
        <td><span class="sku-mix tight">${Object.entries(s.skuMix).map(([sku, n]) =>
          `<span class="sku-chip${["F64", "F128", "F256", "F512", "F1024", "F2048"].includes(sku)
            ? " free-ok" : ""}">${esc(sku)}${n > 1 ? `<b>×${n}</b>` : ""}</span>`).join("")}</span></td>
        <td class="n">${num(s.units)}</td>
        <td class="n ${s.pastThreshold ? "t-bad" : ""}"
            title="${s.pastThreshold ? "past" : "inside"} this site's own ${s.thresholdPct}% line">
          ${pct(s.utilisationPct, 1)}
          <span class="t3">line ${s.thresholdPct}%</span></td>
        <td class="n">${num(Math.round(s.freeUnits))}</td>
        <td class="n ${fleet && s.incidentsPerNode >= fleet * 1.4 ? "t-bad" : ""}"
            title="${s.incidents} incident(s), ${s.seriousIncidents} Sev1/Sev2, over ${s.nodes} node(s)">
          ${s.incidents}
          <span class="t3">${s.incidentsPerNode.toFixed(1)} vs ${fleet.toFixed(1)}</span></td>
      </tr>`).join("")}</tbody></table></div>
  <p class="sites-note">
    Every site here runs its own safety line — ${
      [...new Set(d.sites.map((s) => s.thresholdPct))].sort((a, b) => a - b).map((t) => `${t}%`).join(", ")
    } — derived from the thresholds those facilities actually hold, which is why
    two sites at the same fullness are not equally urgent.
    ${d.totals.hardwareClasses.length > 1
      ? `This region runs <b>${d.totals.hardwareClasses.length} hardware classes</b>
         (${d.totals.hardwareClasses.map(esc).join(", ")}), so a lead time quoted
         at region level would be wrong for some of these buildings.`
      : ""}
  </p>`;
}

/* What Fabric actually runs here.

   This showed only the gaps, which answered half the question: a planner
   deciding where to put a workload needs to know what the region *does* run.
   Microsoft publishes only the exceptions, so the available side is derived --
   the nine workloads they name, less any the exceptions sit inside.

   The distinction that matters and was previously invisible: a region can
   support "all Fabric workloads" and still be missing named features within two
   of them. Affected is not the same as absent, and the block says so rather
   than letting a reader infer the worse reading. */
function workloadBlock(d) {
  const ok = d.workloadsAvailable || [];
  const part = d.workloadsPartlyAffected || [];
  const gaps = d.unavailableFeatures || [];
  if (!ok.length && !gaps.length) return "";

  return `
  <div class="workloads">
    <div class="wl ok">
      <h5>${num(ok.length)} workload(s) fully available</h5>
      <p>${ok.map((w) => `<span class="wl-chip ok">${esc(w)}</span>`).join("")}</p>
    </div>
    ${part.length ? `
    <div class="wl part">
      <h5>${num(part.length)} area(s) with a feature missing</h5>
      <p>${part.map((w) => `<span class="wl-chip part">${esc(w)}</span>`).join("")}</p>
      <p class="wl-detail"><b>Not available:</b> ${gaps.map(esc).join(", ")}.
        These areas still run here — the named features inside them do not.</p>
    </div>` : `
    <div class="wl ok">
      <h5>Nothing missing</h5>
      <p class="wl-detail">Every Fabric workload and feature Microsoft tracks is available in this region.</p>
    </div>`}
  </div>
  <p class="wl-src">Microsoft's published regional availability, refreshed from Fabric
    documentation — not a projection and not generated. A region with plenty of headroom is
    still the wrong home for a workload it cannot run.</p>`;
}

function mapDetail(d) {
  const t = d.totals;
  return `
  <section class="panel detail">
    <header>
      <b>${esc(d.region)}</b>
      <span class="pill ${d.status === "breached" ? "bad"
        : (d.status === "overdue" || d.status === "due") ? "warn" : "good"}">${esc(d.status || "—")}</span>
      <span class="hint">${num(t.sites)} data centres · ${num(t.capacities)} capacities ·
        ${num(t.units)} units · ${num(t.nodes)} nodes</span>
      <a class="closer" href="#" id="detail-close" aria-label="Close">×</a>
    </header>
    <div class="body">
      ${workloadBlock(d)}

      <h3 class="sec">When does it hit the threshold?</h3>
      ${whenBlock(d)}

      <h3 class="sec">What has to change, and why?</h3>
      ${changeBlock(d)}

      <h3 class="sec">What is in each data centre?</h3>
      ${sitesBlock(d)}

      <p class="prov">
        ${num(t.sitesPastThreshold)} of ${num(t.sites)} sites are past their own line.
        ${t.freeViewerCapable} of ${num(t.capacities)} capacities are F64 or larger, so Power BI
        content on the rest needs a Pro or PPU licence per viewer.
        Capacities, hardware, per-capacity utilisation and incidents are generated;
        the Fabric SKU ladder, the F64 rule and the workload availability above are real.
      </p>
    </div>
  </section>`;
}

PAGES["/map"] = async (view) => {
  const d = await get("/api/map");
  const r = await get("/api/recommendations?limit=1");

  const breached = d.points.filter((p) => p.status === "breached").length;
  const gapped = d.points.filter((p) => (p.unavailableFeatures || []).length).length;

  view.innerHTML = howto({
    answers: "<b>Where the fleet stands, on one screen.</b> Every region as a point: how full it is against its own safety line, when it crosses, what is waiting to be bought, and which Fabric workloads will not run there.",
    steps: [
      { what: "Marker colour", is: "the region's state against <i>its own</i> threshold — red is past it, amber has an order due or overdue, green is inside it. The same three states the pills use elsewhere." },
      { what: "Marker size", is: "deployed units, by area rather than radius so a region twice the size looks twice the size." },
      { what: "Selecting one", is: "opens its card beside the map. It stays open while you pick another, because the question is usually which of two regions is worse." },
      { what: "Overlapping points", is: "eastus and eastus2 are both in Virginia, and three European regions sit within four degrees. Markers that would cover each other are nudged apart and joined to their true position by a hairline." },
    ],
    words: [
      { term: "How full", means: "Utilisation against the region's own safety threshold, which is derived from the thresholds its data centres actually hold — not one figure applied to every region." },
      { term: "Unavailable features", means: "Fabric workloads Microsoft does not currently run in that region. This is <b>real published data</b>, refreshed from Microsoft Learn, and it is a capacity decision that has nothing to do with how full a region is: a region with plenty of headroom is still the wrong home for a workload it cannot run." },
      { term: "To buy / to move", means: "Two different answers. <b>To buy</b> means capacity is running out. <b>To move</b> means the capacity is fine but the hardware under it is failing more than the fleet average — adding more of the same would not fix it." },
    ],
    next: "Start with the red markers, then the amber ones carrying a move recommendation — those are the ones no utilisation figure would have surfaced.",
    sources: "Azure region coordinates and Microsoft Fabric regional availability are real. Capacities, hardware, per-capacity utilisation and operational incidents are generated — every generated row carries its provenance.",
  }) + `

  <section class="panel"><div class="body map-summary">
    <span class="t3">${d.points.length} regions · ${num(d.points.reduce((a, p) => a + p.capacities, 0))} capacities · data to ${esc(d.asOf)}</span>
    <span><b class="${breached ? "t-bad" : ""}">${breached}</b> region(s) past their safety line</span>
    <span><b>${r.countsByKind.procurement || 0}</b> purchases outstanding,
      <b class="t-warn">${r.earlyRaises}</b> of them earlier than the usual trigger</span>
    <span><b class="t-warn">${r.countsByKind.workload_change || 0}</b> capacit(y/ies) worth moving despite having room</span>
    <span><b>${gapped}</b> region(s) missing at least one Fabric workload</span>
  </div></section>

  <section class="panel">
    <div class="body map-wrap">
      <div class="map-holder">${fleetMap(d)}
        <div class="legend map-legend">
          <span><i class="dot bad"></i>Past its safety line</span>
          <span><i class="dot warn"></i>Order due or overdue</span>
          <span><i class="dot good"></i>Inside its line</span>
          <span class="t3">Marker area = deployed units</span>
        </div>
      </div>
      <aside id="map-side" class="map-side">
        <p class="empty">Select a region on the map.</p>
      </aside>
    </div>
  </section>

  <div id="map-detail"></div>`;

  const byRegion = Object.fromEntries(d.points.map((p) => [p.region, p]));
  const side = $("map-side");

  const detail = $("map-detail");
  let pending = null;

  async function select(region) {
    const p = byRegion[region];
    if (!p) return;
    side.innerHTML = mapCard(p);
    view.querySelectorAll("circle.mk").forEach((c) =>
      c.classList.toggle("on", c.dataset.region === region));

    // The drill-down is a second request, so a slow one must not paint over a
    // marker picked after it. Only the newest selection is allowed to render.
    const token = region;
    pending = token;
    detail.innerHTML = `<p class="loading">Loading ${esc(region)}…</p>`;
    try {
      const d = await get(`/api/map/${encodeURIComponent(region)}`);
      if (pending !== token) return;
      detail.innerHTML = mapDetail(d);
      const close = $("detail-close");
      if (close) close.onclick = (ev) => { ev.preventDefault(); detail.innerHTML = ""; };
    } catch (err) {
      if (pending !== token) return;
      detail.innerHTML = `<section class="panel"><div class="body">
        <p class="error">Could not load the detail for ${esc(region)}.</p></div></section>`;
    }
  }

  view.querySelectorAll("circle.mk").forEach((c) => {
    c.addEventListener("click", () => select(c.dataset.region));
    // Keyboard: the markers are focusable, so they have to answer to a key.
    c.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(c.dataset.region); }
    });
  });

  // Open on whatever is worst, so the page is useful before anyone clicks.
  const worst = [...d.points].sort((a, b) =>
    (b.utilisation ?? 0) - (a.utilisation ?? 0))[0];
  if (worst) select(worst.region);
};

PAGES["/regions"] = async (view) => {
  const d = await get("/api/overview");

  view.innerHTML = howto({
    answers: "<b>Capacity position by region</b> \u2014 how full each region is against its own safety threshold, what it still owes its customers, and what to do about it.",
    steps: [
      { what: "Safety threshold control", is: "the utilisation ceiling. Adjusting it recomputes every region — this is a recalculation, not a filter. Each region's threshold is also shown as its own column." },
      { what: "Region table", is: "every monitored region: capacity held, capacity used, how much of the safety threshold has been consumed, and how many cores are still owed. Any column is sortable." },
      { what: "Recommendation", is: "opens the region-level advice — what moving the safety threshold would release and whether it covers what is owed. Hardware decisions are per facility, so it links through to the data centres." },
      { what: "Region drill-down", is: "selecting a row opens that region: data centres in scope, denial causes, all incidents, feature availability and demand anomalies." },
    ],
    words: [
      { term: "Threshold status", means: "Whether the region is <b>in risk</b> — utilisation has moved past its safety threshold — or not. It is a state, not a fault: a region using the capacity it holds has done nothing wrong." },
      { term: "Threshold utilised by", means: "How far past the safety threshold utilisation has reached, in percentage points. At an 85% threshold and 97.2% utilisation, the threshold has been utilised by 12.2%." },
      { term: "Cores pending", means: "Capacity requested by customers and not yet delivered. This is what the region owes, not how many tickets failed \u2014 one ticket can be worth hundreds of cores." },
      { term: "Why hardware is not shown here", means: "A region holds ten data centres that may run Intel, AMD or GPU-class. Naming one hardware class at region level would be wrong, so hardware and provisioning lead time live on the facility \u2014 which is what you actually take offline." },
    ],
    next: "Work the regions in risk first, largest cores-pending first. Open the recommendation to see whether moving the safety threshold covers what is owed, or whether the region needs capacity added.",
    sources: "daily regional utilisation, hardware class and provisioning lead time per region, and the ICM incident history.",
  }) + title("Regions", "How full each region is, and what it still owes") + `

  <section class="panel"><div class="body" style="display:flex;gap:2rem;align-items:center;flex-wrap:wrap">
    <label class="ctl" title="Each region is judged against its own threshold. Tick this to force them all to one figure and see what changes.">
      <input type="checkbox" id="thr-override"> Force one threshold on every region
    </label>
    <label class="ctl" id="thr-wrap" style="display:none">
      <input type="range" id="thr" min="50" max="99" value="85" step="1">
      <b id="thr-val" style="min-width:3ch">85%</b>
    </label>
    <label class="ctl">Trend window
      <select id="win">
        <option value="30">30 days</option>
        <option value="45" selected>45 days</option>
        <option value="90">90 days</option>
      </select>
    </label>
    <span class="hint" id="thr-note" style="color:var(--ink-3);font-size:.82rem"></span>
  </div></section>

  <div id="region-table"></div>`;

  const exposureByRegion = Object.fromEntries(d.regions.map((r) => [r.region, r]));

  // Default: fullest region first. The order-by column it used to sort on was
  // removed with the procurement framing, and a default pointing at a column
  // that no longer exists silently falls back to whatever is first.
  const sort = { key: "util", asc: false };

  async function draw() {
    // No pct means every region is judged on its own threshold, which is the
    // default. The control forces one line on all of them as a what-if.
    const override = $("thr-override").checked;
    const p = await get(`/api/threshold?trend_days=${$("win").value}`
      + (override ? `&pct=${$("thr").value}` : ""));
    const inRisk = p.regions.filter((r) => r.at_risk).length;
    $("thr-note").textContent = p.thresholdIsPerRegion
      ? `${inRisk} of ${p.regions.length} regions are in risk, each against its own threshold `
        + `(${Math.min(...p.regions.map((r) => r.threshold_pct))}% to `
        + `${Math.max(...p.regions.map((r) => r.threshold_pct))}%).`
      : `${inRisk} of ${p.regions.length} regions are in risk with every region forced to `
        + `${p.thresholdPct}%.`;

    /* Sorting the whole table, not just the default order -- the explainer
       above promises it, and a promised control that does nothing is worse
       than not offering it. Exposure lives on a different object, so the
       accessor closes over both. */
    /* Columns are the set review asked for. Hardware and lead time were removed
       deliberately: a region holds ten data centres that may run Intel, AMD and
       GPU-class, so naming one at region level is wrong -- both belong on the
       facility, which is the thing you actually take offline. "Days to order-by"
       went with them; it answered a procurement question on a page that is
       supposed to answer "which region is in trouble, and how much does it
       owe". */
    const COLS = [
      { key: "region", label: "Region", get: (r) => r.region, numeric: false },
      { key: "total", label: "Total cores", get: (r) => r.deployed_units, numeric: true },
      { key: "used", label: "Utilised cores", get: (r) => r.used_units, numeric: true },
      { key: "util", label: "Utilisation", get: (r) => r.current_utilisation_pct, numeric: true },
      { key: "thr", label: "Threshold", get: (r) => r.threshold_pct, numeric: true },
      { key: "status", label: "Threshold status", get: (r) => (r.at_risk ? 1 : 0), numeric: true },
      { key: "pending", label: "Cores pending", get: (r) => r.cores_pending, numeric: true },
      { key: "exposure", label: "Revenue loss",
        get: (r) => (exposureByRegion[r.region] || {}).exposure ?? 0, numeric: true },
      { key: "cust", label: "Customers waiting", get: (r) => r.customers_waiting, numeric: true },
    ];
    const col = COLS.find((c) => c.key === sort.key) || COLS[3];
    const rows = [...p.regions].sort((a, b) => {
      const x = col.get(a), y = col.get(b);
      const cmp = col.numeric ? (x - y) : String(x).localeCompare(String(y));
      return sort.asc ? cmp : -cmp;
    });
    const arrow = (c) => (c.key === sort.key ? (sort.asc ? " ▲" : " ▼") : "");

    $("region-table").innerHTML = panel("Regions by capacity position", `<div class="scroll-x"><table>
      <thead><tr>
        ${COLS.map((c) => `<th class="${c.numeric ? "n " : ""}sortable" data-sort="${c.key}"
           style="cursor:pointer;user-select:none">${esc(c.label)}${arrow(c)}</th>`).join("")}
        <th>Reason</th><th>Recommendation</th>
      </tr></thead>
      <tbody>${rows.map((r) => {
        const e = exposureByRegion[r.region] || {};
        return `<tr class="clickable" data-region="${esc(r.region)}">
          <td><b>${esc(r.region)}</b></td>
          <td class="n">${num(Math.round(r.deployed_units))}</td>
          <td class="n">${num(Math.round(r.used_units))}</td>
          <td class="n"><b style="color:${r.at_risk ? "var(--bad)" : "inherit"}">${pct(r.current_utilisation_pct, 1)}</b></td>
          <td class="n">${pct(r.threshold_pct, 0)}</td>
          <td>${thresholdPill(r)}</td>
          <td class="n">${r.cores_pending ? `<b>${num(Math.round(r.cores_pending))}</b>` : "—"}</td>
          <td class="n">${money(e.exposure)}</td>
          <td class="n">${r.customers_waiting || "—"}</td>
          <td class="why">${esc(r.reason)}</td>
          <td><button class="rec-btn" data-rec="${esc(r.region)}"
                style="cursor:pointer;border:1px solid var(--rule);background:var(--page);
                       border-radius:3px;padding:.25rem .6rem;font-size:.78rem;color:var(--brand)">
                View</button></td>
        </tr>`;
      }).join("")}</tbody></table></div>`, { hint: "click a row for detail", flush: true });

    $("region-table").querySelectorAll("button[data-rec]").forEach((b) =>
      (b.onclick = (ev) => { ev.stopPropagation(); showRecommendation(b.dataset.rec); }));

    $("region-table").querySelectorAll("th[data-sort]").forEach((th) =>
      (th.onclick = () => {
        // Re-clicking the active column flips it; a new column starts ascending,
        // except the numeric ones where "worst first" is what anyone wants.
        const key = th.dataset.sort;
        if (sort.key === key) sort.asc = !sort.asc;
        else { sort.key = key; sort.asc = !["exposure", "util", "lead"].includes(key); }
        draw();
      }));

    $("region-table").querySelectorAll("tr[data-region]").forEach((tr) =>
      (tr.onclick = () => navigate(`/region/${encodeURIComponent(tr.dataset.region)}`)));
  }

  $("thr-override").onchange = () => {
    $("thr-wrap").style.display = $("thr-override").checked ? "" : "none";
    draw();
  };
  $("thr").oninput = () => { $("thr-val").textContent = `${$("thr").value}%`; draw(); };
  $("win").onchange = draw;
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
    const used = Math.round(r.cores - r.coresFree);
    view.innerHTML = title(`Region: ${name}`, `${r.siteCount} data centres`) + panel(`Region: ${name}`, `
      <p style="margin-top:0"><b>${atRisk ? "In risk." : "Not in risk."}</b> ${esc(t.reason)}</p>
      <div class="kpis" style="margin:1rem 0">
        ${kpi("Total cores", num(Math.round(r.cores)),
              `${num(Math.round(r.coresFree))} free across ${r.siteCount} sites`, "ink",
              "Compute deployed across every facility in this region.")}
        ${kpi("Utilised cores", num(used),
              `of ${num(Math.round(r.cores))} deployed`, atRisk ? "bad" : "ink")}
        ${kpi("Utilisation", pct(t.current_utilisation_pct, 1),
              `against a ${pct(t.threshold_pct, 0)} safety threshold`,
              atRisk ? "bad" : "good")}
        ${kpi("Safety threshold", pct(t.threshold_pct, 0),
              atRisk
                ? `threshold utilised by ${pct(t.current_utilisation_pct - t.threshold_pct, 1)}`
                : `${pct(t.threshold_pct - t.current_utilisation_pct, 1)} still available`,
              atRisk ? "bad" : "good",
              `The share of this region's ${num(Math.round(r.cores))} cores at which it is treated as at risk. `
              + `${pct(t.threshold_pct, 0)} of ${num(Math.round(r.cores))} is `
              + `${num(Math.round(r.cores * t.threshold_pct / 100))} cores. Utilisation past that point `
              + `is consuming the margin the threshold exists to protect.`)}
      </div>
      <div class="kpis" style="margin:0 0 1rem">
        ${kpi("Threshold status", atRisk ? "In risk" : "Not in risk",
              atRisk ? `utilisation is past the ${pct(t.threshold_pct, 0)} threshold`
                     : `utilisation is inside the ${pct(t.threshold_pct, 0)} threshold`,
              atRisk ? "bad" : "good")}
        ${kpi("Cores pending", num(Math.round(t.cores_pending || 0)),
              `owed to ${t.customers_waiting || 0} customer(s)`,
              t.cores_pending ? "bad" : "good",
              "Capacity requested and not yet delivered. This is what the region owes, not how many tickets failed.")}
        ${kpi("Revenue loss", r.revenueLoss ? money(r.revenueLoss) : "—",
              "attributed to failures in this region", r.revenueLoss ? "bad" : "good")}
        ${kpi("Failed requests", num(r.failedCount), `of ${r.tickets.length} raised in this region`,
              r.failedCount ? "bad" : "good",
              "Requests that breached their SLA before being granted, or were never granted at all. Requests denied and then approved inside SLA are normal turnaround and are not counted.")}
      </div>

      <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Every data centre in this region</h4>
      <div class="scroll-x"><table>
        <thead><tr><th>Data centre</th><th class="n">Cores</th><th class="n">Free</th>
          <th class="n">Threshold</th><th class="n">Utilisation</th>
          <th>Threshold status</th><th class="n">Requests</th>
          <th class="n">Failed</th>
          <th class="n">Oldest open</th><th class="n">Revenue loss</th>
          <th>Denial cause and recommended action</th></tr></thead>
        <tbody>${(r.datacentres || []).map((x) => `<tr class="clickable" data-dc="${esc(x.datacentre)}">
          <td class="mono"><b>${esc(x.datacentre)}</b></td>
          <td class="n">${num(Math.round(x.cores))}</td>
          <td class="n">${x.coresFree <= 0 ? `<b style="color:var(--bad)">0</b>` : num(Math.round(x.coresFree))}</td>
          <td class="n">${pct(x.thresholdPct)}</td>
          <td class="n"><b style="color:${x.overThreshold ? "var(--bad)" : "inherit"}">${pct(x.utilisationPct, 1)}</b></td>
          <td>${x.overThreshold
            ? `<span class="pill bad">In risk</span>`
            : `<span class="pill good">Not in risk</span>`}</td>
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
        All ${r.siteCount} data centres are listed. ${r.sitesOverThreshold} of them
        ${r.sitesOverThreshold === 1 ? "is" : "are"} past its own safety threshold,
        and ${r.sitesWithActivity} ${r.sitesWithActivity === 1 ? "has" : "have"}
        had a request raised against
        ${r.sitesWithActivity === 1 ? "it" : "them"}. Those are different things:
        a data centre can be over its line with nothing having failed there yet,
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
        <thead><tr><th>Incident</th><th>Customer</th><th>Data centre</th>
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
  // the data centre page -- it is supposed to show in both".
  view.insertAdjacentHTML("beforeend", await capacityPanel("region", name));
  view.insertAdjacentHTML("beforeend", await demandPanels("region", name));
  wireCharts(view);

  view.insertAdjacentHTML("beforeend", `<p style="margin:1.5rem 0 0">
    <a href="/regions">← All regions</a> &nbsp;·&nbsp;
    <a href="/datacentres">Data centres</a> &nbsp;·&nbsp;
    <a href="/forecast">Forecast</a></p>`);

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
    <tbody>${d.customers.map((c) => `<tr class="clickable${
      c.risk && c.risk.band === "high" ? " at-risk" : ""}" data-sub="${esc(c.subscriptionId)}">
      <td class="n">${c.rank}</td>
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
      { what: "Recommendation", is: "strategic rather than per-ticket — where this customer already has headroom, or whether they need hardware." },
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
    <thead><tr><th>Incident</th><th>Region</th><th>Data centre</th>
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
    answers: "<b>Recommended remediation per ranked region</b>, with a migration calculator for modelling a hardware change before committing to it.",
    steps: [
      { what: "Each card", is: "one region \u2014 problem, cause, impact and expected effect, followed by the incidents the recommendation was derived from." },
      { what: "Accept / Reject", is: "at the bottom of each recommendation. Nothing is actioned automatically \u2014 a named person decides. A rejection requires a justification, which is what tells the next run whether to suppress the region or re-raise it." },
      { what: "SKU migration calculator", is: "select a region, the specific facility to take offline, and the target hardware class. Returns the post-migration position and whether the remaining region capacity can absorb the load during the work." },
    ],
    words: [
      { term: "Failure mode", means: "Whether the constraint is approval latency or genuine capacity shortage. The two require opposite remediations, so each card states which applies." },
      { term: "Justification requirement", means: "Every decision is appended to a decision log with owner, timestamp and rationale. The next run suppresses a rejected region \u2014 without a justification it cannot determine whether to suppress or re-raise." },
    ],
    next: "Record an accept or reject decision against each recommendation. The decision is logged and governs what the next run reports.",
    sources: "the classified incident history, and relative cost and performance indices per hardware class.",
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

  ${panel("SKU migration calculator", `
    <p style="margin:0 0 1rem;color:var(--ink-2)">
      Work happens in a building, not a country — so pick the site you would
      actually take offline. The target list leaves out the hardware it already
      runs, because that is not a migration.
    </p>
    <div style="display:flex;gap:1.5rem;flex-wrap:wrap;align-items:center;margin-bottom:1rem">
      <label class="ctl">Region <select id="sw-region"></select></label>
      <label class="ctl">Data centre <select id="sw-dc"></select></label>
      <label class="ctl">Swap to <select id="sw-sku"></select></label>
      <label class="ctl">Mode <select id="sw-mode">
        <option value="same_footprint">Same number of units</option>
        <option value="like_for_like">Same amount of work</option>
      </select></label>
    </div>
    <div id="sw-out"></div>`)}`;

  /* --- swap calculator ------------------------------------------------- */
  const opt = await get("/api/swap-options");

  function fillRegions() {
    $("sw-region").innerHTML = opt.regions
      .map((r) => `<option>${esc(r)}</option>`).join("");
  }
  function fillSites() {
    const inRegion = opt.sites.filter((s) => s.region === $("sw-region").value);
    $("sw-dc").innerHTML = inRegion.map((s) =>
      `<option value="${esc(s.datacentre)}">${esc(s.datacentre)} — ${esc(s.currentHardware)}${
        s.hasActivity ? "" : " (no requests yet)"}</option>`).join("");
  }
  function fillTargets() {
    // Never offer the hardware the site already runs.
    const site = opt.sites.find((s) => s.datacentre === $("sw-dc").value);
    const current = site ? site.currentHardware : "";
    $("sw-sku").innerHTML = opt.hardware
      .filter((h) => h !== current)
      .map((h) => `<option>${esc(h)}</option>`).join("");
  }

  async function runSwap() {
    const out = $("sw-out");
    if (!$("sw-dc").value || !$("sw-sku").value) { out.innerHTML = ""; return; }
    out.innerHTML = `<p class="loading" style="padding:0">Calculating\u2026</p>`;
    try {
      const q = new URLSearchParams({
        datacentre: $("sw-dc").value, to_sku: $("sw-sku").value, mode: $("sw-mode").value,
      });
      const s = await get(`/api/swap?${q}`);
      const c = s.conversion, f = s.feasibility;
      const up = c.capacity_delta >= 0;

      out.innerHTML = `
        <p style="margin:0 0 1rem">
          <b>${esc(s.datacentre)}</b> currently runs <b>${esc(s.currentHardware)}</b>
          across ${num(s.units)} units. Swapping it to <b>${esc(s.targetHardware)}</b>:
        </p>
        <div class="kpis">
          ${kpi("Work it could handle", num(Math.round(c.capacity_after)),
                `from ${num(Math.round(c.capacity_before))} (${up ? "+" : ""}${num(Math.round(c.capacity_delta))})`,
                up ? "good" : "bad")}
          ${kpi("Cost change", `${c.cost_delta_pct > 0 ? "+" : ""}${c.cost_delta_pct.toFixed(0)}%`,
                "against what this site costs now", c.cost_delta_pct > 0 ? "bad" : "good")}
          ${kpi("Units after the swap", num(Math.round(c.to_units)),
                `from ${num(Math.round(c.from_units))}`, "ink")}
          ${kpi("Wait for the hardware", `${c.lead_time_days} days`,
                `before ${esc(s.targetHardware)} arrives`, "ink")}
        </div>

        <h4 style="margin:1.25rem 0 .4rem;font-size:.9rem">Migration feasibility</h4>
        <p style="background:${f.can_convert_a_whole_datacentre ? "var(--good-wash)" : "var(--bad-wash)"};
           padding:.8rem 1rem;border-radius:3px;margin:0 0 .75rem">
          <b>${f.can_convert_a_whole_datacentre ? "Yes." : "No."}</b>
          ${f.can_convert_a_whole_datacentre
            ? `${esc(s.region)} has ${num(Math.round(f.max_offline_units))} units of spare capacity
               after a safety margin, and this site holds ${num(Math.round(f.units_per_datacentre))}.`
            : esc(f.blocker || "Not enough spare capacity in the region during the work.")}
        </p>
        <p style="color:var(--ink-2);font-size:.82rem;margin:0">
          Feasibility is a region question, not a site one: while this building is
          down, the rest of ${esc(s.region)} has to carry what customers are
          running. Region has ${num(Math.round(f.deployed_units))} units deployed,
          ${num(Math.round(f.used_units))} in use.
        </p>`;
    } catch (e) {
      out.innerHTML = `<p class="empty" style="padding:0">${esc(e.message)}</p>`;
    }
  }

  $("sw-region").onchange = () => { fillSites(); fillTargets(); runSwap(); };
  $("sw-dc").onchange = () => { fillTargets(); runSwap(); };
  $("sw-sku").onchange = runSwap;
  $("sw-mode").onchange = runSwap;
  fillRegions(); fillSites(); fillTargets(); runSwap();

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

const REC_KIND = {
  procurement: { label: "Buy", tone: "" },
  workload_change: { label: "Move", tone: "warn" },
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
    <p class="ev">
      ${e.region ? `<a href="/region/${encodeURIComponent(e.region)}">${esc(e.region)}</a>` : ""}
      ${e.datacentre ? `· <a href="/datacentre/${encodeURIComponent(e.datacentre)}">${esc(e.datacentre)}</a>` : ""}
      ${e.fabricSku ? `· <b>${esc(e.fabricSku)}</b>` : ""}
      ${e.skuClass ? `· ${esc(e.skuClass)}` : ""}
      ${e.utilisationPct != null ? `· ${pct(e.utilisationPct, 1)} used` : ""}
      ${r.kind === "procurement" && e.orderByDate
        ? `· order by <b>${esc(e.orderByDate)}</b> (${e.daysUntilOrder < 0
            ? `${Math.abs(e.daysUntilOrder)}d overdue` : `in ${e.daysUntilOrder}d`})` : ""}
      ${r.kind === "procurement" && e.unitsToAdd ? `· add ${num(e.unitsToAdd)} units` : ""}
      ${r.kind === "workload_change" && e.moveTo
        ? `· move to <b>${esc(e.moveTo.sku_class)}</b> (${esc(e.moveTo.vendor)} ${esc(e.moveTo.model)})` : ""}
      ${r.kind === "workload_change" && e.downtimeHours != null
        ? `· ${e.downtimeHours}h lost` : ""}
      ${r.kind === "licensing" && e.stepTo ? `· step to <b>${esc(e.stepTo)}</b>` : ""}
    </p>
  </article>`;
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
      { what: "Buy", is: "capacity is running out. An order is raised when the time left before the trigger falls below the time the hardware takes to arrive, so a capacity can need buying while still under its threshold." },
      { what: "Move", is: "capacity is <i>not</i> the problem. These have room to spare but fail more than the fleet does, measured per node. Adding more of the same hardware would not fix them." },
      { what: "Licence", is: "a commercial step, not a capacity one. Below F64 every Power BI viewer needs a paid licence; at F64 a free licence and a viewer role are enough." },
      { what: "Ordering", is: "kinds are interleaved rather than grouped, because the most pressing thing is the most pressing thing regardless of which engine produced it." },
    ],
    words: [
      { term: "Raised early", means: "The order is going in before the capacity reaches its usual trigger. That happens when the lead time is long enough that waiting would land the order after the capacity was needed — and it is the case a threshold alone can never make." },
      { term: "Incidents per node", means: "Operational incidents divided by physical nodes, then shrunk toward the fleet average in proportion to how many nodes stand behind the record. Raw counts would just rank capacities by size; unshrunk rates would let a one-node capacity with a bad month outrank an estate." },
      { term: "Why these are separate", means: "A single blended risk score would let a buying case and a hardware case average each other into something calm. A site at 40% with a dozen outages is not calm; it is two different problems, one of which is invisible to utilisation." },
    ],
    next: "Work the moves and the early raises first — everything else is already visible on the Regions and Forecast tabs.",
    sources: "Capacities, hardware, per-capacity utilisation, operational incidents and lead-time history are generated. The Fabric SKU ladder and the F64 licensing rule are real and cited on each licensing recommendation.",
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
      <b class="t-warn">${d.earlyRaises}</b> purchase(s) raised earlier than the usual trigger ·
      <b class="t-warn">${num(counts.workload_change || 0)}</b> hardware move(s)</span>
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
   phone you keep, and a capacity with plenty of headroom that drops nodes every
   week is not capacity you keep either. Utilisation cannot tell those apart. */
PAGES["/capacity"] = async (view, id) => {
  const d = await get(`/api/capacity/${encodeURIComponent(id)}`);
  const h = d.health, hw = d.hardware;
  const sick = h.rateVsFleet >= 1.4;
  const last = d.utilisation.length ? d.utilisation[d.utilisation.length - 1] : null;

  view.innerHTML = title(d.capacityId,
    `${d.fabricSku} · ${d.capacityUnits} CU · ${hw.vendor} ${hw.model} · in ` +
    `${d.datacentre}, ${d.region}`) + `

  <div class="kpis">
    <div class="kpi"><div class="label">How full</div>
      <div class="value${last && last.UtilisationPct >= 90 ? " bad" : ""}">${last ? pct(last.UtilisationPct, 1) : "—"}</div>
      <div class="sub">${num(d.deployedUnits)} units across ${d.nodes} node(s)</div></div>
    <div class="kpi"><div class="label">Incidents</div>
      <div class="value${sick ? " bad" : ""}">${h.incidents}</div>
      <div class="sub">${h.seriousIncidents} Sev1/Sev2 · ${h.downtimeHours}h lost</div></div>
    <div class="kpi"><div class="label">Per node</div>
      <div class="value${sick ? " bad" : ""}">${h.incidentsPerNode.toFixed(1)}</div>
      <div class="sub">fleet runs ${h.fleetIncidentsPerNode.toFixed(1)} · this is ${h.rateVsFleet}\u00d7</div></div>
    <div class="kpi"><div class="label">Free viewers</div>
      <div class="value${d.supportsFreeViewers ? " good" : ""}">${d.supportsFreeViewers ? "Yes" : "No"}</div>
      <div class="sub">${d.supportsFreeViewers
        ? "F64 or larger — a free licence can read Power BI here"
        : "below F64 — every viewer needs Pro or PPU"}</div></div>
  </div>

  ${d.recommendations.length ? panel("What to do about it",
    d.recommendations.map(recCard).join(""), { flush: true }) : ""}

  ${panel("Hardware", `
    <div class="tablewrap"><table class="grid"><tbody>
      <tr><th>Class</th><td>${esc(hw.skuClass)}</td></tr>
      <tr><th>Vendor / model</th><td>${esc(hw.vendor)} ${esc(hw.model)}</td></tr>
      <tr><th>CPU</th><td>${esc(hw.cpu)}</td></tr>
      <tr><th>Per node</th><td>${num(hw.coresPerNode)} cores · ${num(hw.memoryGB)}GB · ${hw.storageTB}TB</td></tr>
      <tr><th>Nodes</th><td>${d.nodes}</td></tr>
    </tbody></table></div>
    <p style="color:var(--ink-3);font-size:.78rem;margin:.6rem 0 0">
      Server models are real products; which model sits in which facility is generated.</p>`)}

  ${panel(`Incidents — ${h.incidents} in the window`, d.incidents.length ? `
    <div class="tablewrap"><table class="grid">
      <thead><tr><th>Opened</th><th>Severity</th><th>Type</th>
        <th class="n">Downtime</th><th class="n">Customers</th></tr></thead>
      <tbody>${d.incidents.map((i) => `<tr>
        <td>${esc(i.OpenedDate)}</td>
        <td><span class="pill ${i.Severity === "Sev1" ? "bad" : i.Severity === "Sev2" ? "wash" : ""}">${esc(i.Severity)}</span></td>
        <td>${esc(i.IncidentType)}</td>
        <td class="n">${i.DowntimeMinutes} min</td>
        <td class="n">${i.ImpactedCustomers}</td></tr>`).join("")}</tbody></table></div>
    <p style="color:var(--ink-3);font-size:.78rem;margin:.6rem 0 0">${esc(h.shrunkTowardFleet)}</p>`
    : `<p class="empty">No incidents recorded on this capacity.</p>`)}

  <p style="margin:1.5rem 0 0">
    <a href="/datacentre/${encodeURIComponent(d.datacentre)}">\u2190 ${esc(d.datacentre)}</a>
    &nbsp;\u00b7&nbsp; <a href="/region/${encodeURIComponent(d.region)}">${esc(d.region)}</a>
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
    sources: "the configuration file, the classifier validation result, and per-field provenance from the ontology.",
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
    <p style="color:var(--ink-2);margin:0">A region at 71% can outrank one at 94% when its hardware takes
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
/* Data centres                                                              */

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

PAGES["/datacentres"] = async (view) => {
  const d = await get("/api/datacentres");
  const solid = d.datacentres.filter((x) => !x.lowEvidence);

  view.innerHTML = howto({
    answers: "<b>Site-level risk ranking.</b> A region identifies the geography; a data centre identifies the facility requiring intervention.",
    steps: [
      { what: "Site table", is: `all facilities with recorded activity, ranked by risk score. ${d.withActivity} of ${d.totalSites} sites have recorded incidents; the remainder are dormant and excluded.` },
      { what: "Risk score", is: "computed from that facility\u2019s own incidents, with the highest-weighted component named beneath." },
      { what: "Clicking a row", is: "opens that site underneath \u2014 how its score was built line by line, why its requests were refused, and every ticket. The region name in there is a link if you want the wider picture." },
      { what: "Evidence base", is: "stated under every score, because nearly every facility here has one or two incidents. Where a facility has too few to judge on its own, its failure rate is pulled toward the fleet average and both figures are shown \u2014 a single denial is a 100% failure rate arithmetically, but it is one observation, not a finding." },
    ],
    words: [
      { term: "How the score is built", is: "", means: `${Object.entries(d.weights).map(([k, w]) => `${Math.round(w * 100)}% ${esc(words(k))}`).join(", ")}. Each site is scored from its own rows — this is not a region total divided up.` },
      { term: "Primary denial reason", means: "The most frequent denial cause at that facility \u2014 the fastest indicator of whether the constraint is capacity, hardware or policy." },
    ],
    next: `Rank by score, but read the evidence line beneath it before acting: ${solid.length} of ${d.datacentres.length} sites here have three or more requests, so for most of them the ranking is driven by utilisation and lead time — which are measured continuously — rather than by a failure rate drawn from one or two tickets.`,
    sources: "ICM incidents attributed to a facility, regional utilisation, and hardware provisioning lead times.",
  }) + title("Data centres", `${d.withActivity} sites with activity, of ${d.totalSites} across all regions`) + `

  ${panel("Data centres by risk score", `<div class="scroll-x"><table>
    <thead><tr><th>Data centre</th><th>Region</th><th>Hardware</th>
      <th class="n">Requests</th><th class="n">Failed</th><th class="n">Customers</th>
      <th class="n">Revenue loss</th><th>Primary denial reason</th><th>Risk score</th></tr></thead>
    <tbody>${d.datacentres.map((x) => `<tr class="clickable" data-dc="${esc(x.datacentre)}">
      <td class="mono"><b>${esc(x.datacentre)}</b></td>
      <td>${esc(x.region)}</td>
      <td>${esc(x.hardware)} <span class="pill mute">${x.leadTime}d</span></td>
      <td class="n">${num(x.requests)}</td>
      <td class="n">${x.failed ? `<b style="color:var(--bad)">${num(x.failed)}</b>` : "—"}</td>
      <td class="n">${num(x.customers)}</td>
      <td class="n">${x.revenueLoss ? money(x.revenueLoss) : "—"}</td>
      <td>${x.topReason ? esc(x.topReason) : "—"}</td>
      <td>${riskCell(x.risk)}</td>
    </tr>`).join("")}</tbody></table></div>`,
    { hint: "click a row for detail", flush: true })}
  <div id="dc-detail"></div>`;

  view.querySelectorAll("tr[data-dc]").forEach((tr) =>
    (tr.onclick = () => navigate(`/datacentre/${encodeURIComponent(tr.dataset.dc)}`)));
};

/* ==================================================================== deep */
/* One data centre — its own page, reachable from Regions or Data centres.    */

PAGES["/datacentre"] = async (view, id, showAll = false) => {
  if (!id) { view.innerHTML = `<p class="error">No data centre selected.</p>`; return; }
  const x = await get(`/api/datacentre/${encodeURIComponent(id)}`);
  const labels = x.componentLabels || {};
  const over = x.headroom != null && x.headroom < 0;

  view.innerHTML = howto({
    answers: `<b>Everything recorded at ${esc(x.datacentre)}</b> — capacity position, denial causes, recommended remediation and the full incident list.`,
    steps: [
      { what: "Capacity position", is: "cores deployed, cores free, and this facility's own safety threshold." },
      { what: "Recommended remediation", is: "one entry per denial cause at this site, with the migration arithmetic where a hardware change is the fix." },
      { what: "Risk index breakdown", is: "each component of the score, with its measured value and contribution." },
      { what: "Incident register", is: "every request raised here, with the derivation behind each revenue-loss figure." },
    ],
    next: "Action the remediation for the highest-cost cause. Causes marked manual review require engineering or account-team engagement.",
    sources: "ICM incidents attributed to this facility, regional utilisation, and hardware provisioning lead times.",
  }) + title(`Data centre: ${x.datacentre}`,
             `In ${x.region} · ${x.hardware} · ${x.leadTimeDays}-day provisioning lead time`) + `

  <div class="kpis">
    ${kpi("Risk score", x.risk.score.toFixed(1), `${x.risk.band} risk`,
          x.risk.band === "high" ? "bad" : x.risk.band === "medium" ? "" : "good")}
    ${kpi("Capacity", `${num(Math.round(x.cores ?? 0))} cores`,
          `${num(Math.round(x.coresFree ?? 0))} free · threshold ${pct(x.thresholdPct ?? 0)}`,
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
    therefore driven mainly by utilisation and lead time, which are measured
    continuously, until this facility has more history behind it.</p>` : ""}

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
            <thead><tr><th>Raise safety line to</th><th class="n">Cores released</th>
              <th class="n">Headroom after</th></tr></thead>
            <tbody>${rec.threshold.map((o) => `<tr>
              <td><b>${pct(o.thresholdPct)}</b></td>
              <td class="n">+${num(Math.round(o.releasesCores))}</td>
              <td class="n" style="color:${o.headroomAfter < 0 ? "var(--bad)" : "var(--good)"}">
                ${num(Math.round(o.headroomAfter))}</td>
            </tr>`).join("")}</tbody></table></div>
        </div>` : ""}
        ${(rec.migration || []).length ? `<div style="margin-top:.6rem">
          <p style="margin:0 0 .4rem;font-size:.85rem;color:var(--ink-2)">
            Migration arithmetic for this facility:</p>
          <div class="scroll-x"><table style="font-size:.85rem">
            <thead><tr><th>Option</th><th class="n">Cores after</th>
              <th class="n">Work capacity</th><th class="n">Cost</th>
              <th class="n">Lead time</th><th>Feasible now</th></tr></thead>
            <tbody>${rec.migration.map((m) => `<tr>
              <td><b>${esc(m.toSku)}</b></td>
              <td class="n">${num(Math.round(m.coresAfter))}</td>
              <td class="n">${num(Math.round(m.capacityAfter))}
                <span style="color:${m.capacityDelta >= 0 ? "var(--good)" : "var(--bad)"}">
                (${m.capacityDelta >= 0 ? "+" : ""}${num(Math.round(m.capacityDelta))})</span></td>
              <td class="n">${m.costDeltaPct > 0 ? "+" : ""}${m.costDeltaPct.toFixed(0)}%</td>
              <td class="n">${m.leadTimeDays}d</td>
              <td>${m.feasible ? `<span class="pill good">yes</span>`
                                : `<span class="pill bad">no</span>`}</td>
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

  <div id="dc-caps"></div>
  <div id="dc-demand"></div>

  <p style="margin:1.5rem 0 0">
    <a href="/region/${encodeURIComponent(x.region)}">← Back to ${esc(x.region)}</a>
    &nbsp;·&nbsp; <a href="/datacentres">All data centres</a>
  </p>`;

  // This facility's own demand. Utilisation over time is only recorded per
  // region, so that chart stays on the region page and this says where to find it.
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
      { term: "Why this view exists", means: "\u201cwesteurope has 4 failures\u201d does not identify an owner. \u201cThree capacity-ceiling denials and one hardware fault\u201d identifies two remediations with two different owners." },
    ],
    next: "Work the causes with the largest revenue loss that are not marked manual review — those have a fix the platform can already model.",
    sources: `the ${d.totalFailed} failed requests, grouped by cause.`,
  }) + title("Reasons", `${d.reasons.length} causes behind ${d.totalFailed} failed requests`) + `

  ${d.reasons.map((r) => panel(r.reason, `
    <div class="kpis" style="margin-bottom:1rem">
      ${kpi("Incidents", num(r.count), `${pct(r.sharePct, 1)} of all failures`, "ink")}
      ${kpi("Revenue loss", r.revenueLoss ? money(r.revenueLoss) : "—", "attributed to this cause", "bad")}
      ${kpi("Customers affected", num(r.customers), `across ${num(r.datacentres)} data centres`, "ink")}
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
    next: "Filter to a cause you are working on, then open the region or data centre it points at.",
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
      <thead><tr><th>Incident</th><th>Customer</th><th>Region</th><th>Data centre</th>
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
function forecastChart(f) {
  const hist = f.history || [], proj = f.projection || [];
  if (hist.length < 2) return `<p class="empty">Not enough history to plot.</p>`;

  const W = 900, H = 260, L = 42, R = 12, T = 14, B = 26;
  const all = [...hist.map((p) => p.value),
               ...proj.map((p) => p.upper), ...proj.map((p) => p.lower),
               f.thresholdPct];
  const lo = Math.floor(Math.min(...all) - 2), hi = Math.ceil(Math.max(...all) + 2);
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
    <span><i class="ln measured"></i>Measured — how full the region actually was, one reading per day</span>
    <span><i class="ln projected"></i>Projected — where the trend takes it if nothing changes</span>
    <span><i class="ln band"></i>Range the forecast could be out by</span>
    <span><i class="ln limit"></i>Safety line</span>
  </div>
  <svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Utilisation history and projection for ${esc(f.region)}">
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
  }) + title("Forecast", `${d.forecasts.length} regions projected against a ${d.thresholdPct}% safety line`) + `

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
      ${kpi("Forecast error", f.scores.length ? `${f.scores[0].mape.toFixed(2)}%` : "—",
            f.scores.length && f.history.length
              ? `typically ±${(f.scores[0].mape / 100 * f.history[f.history.length - 1].value).toFixed(1)} points out, ${d.horizonDays} days ahead`
              : "", "ink",
            `How wrong this model was when tested. It was fitted on part of the history, `
            + `asked to predict the ${d.horizonDays} days it had not seen, and marked against `
            + `what actually happened — repeated over ${f.scores.length ? f.scores[0].folds : d.folds} `
            + `stretches of the record. Lower is better. Note it is a percentage `
            + `of the reading, not percentage points: ${f.scores.length ? f.scores[0].mape.toFixed(2) : "—"}% `
            + `of a ${f.history.length ? f.history[f.history.length - 1].value.toFixed(0) : "—"}% `
            + `utilisation reading is about `
            + `${f.scores.length && f.history.length ? (f.scores[0].mape / 100 * f.history[f.history.length - 1].value).toFixed(1) : "—"} points.`)}
      ${kpi("Skill vs naive", f.scores.length ? `${f.scores[0].skillVsNaive > 0 ? "+" : ""}${f.scores[0].skillVsNaive.toFixed(0)}%` : "—",
            f.scores.length
              ? (f.scores[0].skillVsNaive > 0
                  ? `${f.scores[0].skillVsNaive.toFixed(0)}% more accurate than assuming nothing changes`
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
      All ${f.scores.length} candidates, scored — including ARIMA and SARIMA</summary>
      <div class="scroll-x" style="margin-top:.5rem"><table>
        <thead><tr><th>Model</th><th class="n">MAPE</th><th class="n">RMSE</th>
          <th class="n">Skill vs naive</th><th class="n">Folds</th></tr></thead>
        <tbody>${f.scores.map((s, i) => `<tr>
          <td>${i === 0 ? "<b>" : ""}${esc(s.model)}${i === 0 ? "</b> <span class=\"pill good\">chosen</span>" : ""}</td>
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
      { what: "Would have prevented", is: "failures that occurred in reality but would have been admitted under the reserve. Where it is zero, the region was genuinely out of capacity — that is a procurement problem, not a policy one." },
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
    <thead><tr><th>Region</th><th>Hardware</th><th class="n">Compute units</th>
      <th class="n">Capacity units</th><th>Equivalent Fabric SKU</th></tr></thead>
    <tbody>${d.pools.map((p) => `<tr>
      <td><b>${esc(p.Region)}</b></td>
      <td>${esc(p.SKUClass)}</td>
      <td class="n">${num(Math.round(p.DeployedUnits))}</td>
      <td class="n">${num(Math.round(p.CapacityUnits))}</td>
      <td><span class="pill info">${esc(p.EquivalentSKU)}</span></td>
    </tr>`).join("")}</tbody></table></div>
    <p style="color:var(--ink-2);font-size:.82rem;margin:.75rem 1.15rem 1.15rem">
      Fabric is sold as an F-SKU rated in Capacity Units, not as raw compute.
      The ladder is real; the ${d.unitsPerCu} units-per-CU conversion is assumed.
    </p>`, { flush: true })}`;
};
