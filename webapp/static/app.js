/* Capacity Intelligence — client.
 *
 * Filtering and sorting stay here because they are views of data already
 * loaded. The threshold slider and the migration calculator go to the server,
 * because those genuinely recompute Module 1 and Module 2 — which is the whole
 * difference between this and the generated page it replaces.
 */

const $ = (id) => document.getElementById(id);

const state = {
  regions: [],        // as served
  view: [],           // after search + sort
  selected: null,
  customer: null,
  sort: { key: "daysUntilOrder", asc: true },
  skus: [],
};

const money = (v) =>
  Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(2)}M`
  : Math.abs(v) >= 1e3 ? `$${Math.round(v / 1e3)}K`
  : `$${Math.round(v).toLocaleString()}`;

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

/* ---------------------------------------------------------------- KPIs */

function drawKpis(k, asOf) {
  $("kpis").innerHTML = [
    ["Customer revenue at risk", money(k.exposure), `${k.failed} of ${k.total} requests failed`],
    ["Annual revenue touched", money(k.arrAffected), `${k.customers} customers affected`],
    ["Demand spikes explained", `${k.spikesExplained} of ${k.spikesTotal}`, "matched to a known deal"],
    ["Data up to", asOf, "latest date in ICM"],
  ].map(([l, v, n]) =>
    `<div class="kpi"><i>${esc(l)}</i><b>${esc(v)}</b><em>${esc(n)}</em></div>`
  ).join("");
}

/* --------------------------------------------------------------- table */

const COLUMNS = [
  { key: "region", label: "Region", cls: "reg" },
  { key: "status", label: "Threshold", fmt: (v) =>
      `<span class="pill s-${v}">${esc(v.replace("_", " "))}</span>` },
  { key: "utilisation", label: "Capacity used", n: true, fmt: (v) => `${v.toFixed(0)}%` },
  { key: "sku", label: "Hardware", cls: "mono" },
  { key: "leadTime", label: "Lead", n: true, fmt: (v) => `${v}d` },
  { key: "daysUntilOrder", label: "Order in", n: true,
    fmt: (v) => v === null ? "—" : v < 0 ? `${Math.abs(v)}d late` : `${v}d` },
  { key: "exposure", label: "Revenue at risk", n: true, fmt: money },
  { key: "failed", label: "Failed reqs", n: true },
  { key: "growth", label: "Growth", n: true, fmt: (v) => (v > 0 ? "+" : "") + Math.round(v) },
  { key: "coverage", label: "Features live", n: true, fmt: (v) => `${v.toFixed(0)}%` },
];

function applyView() {
  const q = $("q").value.trim().toLowerCase();
  let rows = state.regions.filter((r) => !q || r.region.toLowerCase().includes(q));
  const { key, asc } = state.sort;
  rows.sort((a, b) => {
    let x = a[key], y = b[key];
    if (x === null) x = asc ? Infinity : -Infinity;
    if (y === null) y = asc ? Infinity : -Infinity;
    if (typeof x === "string") return asc ? x.localeCompare(y) : y.localeCompare(x);
    return asc ? x - y : y - x;
  });
  state.view = rows;
  drawTable();
  drawUrgency();
}

function drawTable() {
  const head = COLUMNS.map((c) => {
    const sorted = state.sort.key === c.key;
    return `<th data-k="${c.key}" class="${c.n ? "n " : ""}${sorted ? "sorted " + (state.sort.asc ? "asc" : "") : ""}">${esc(c.label)}</th>`;
  }).join("");

  const body = state.view.map((r) => {
    const cells = COLUMNS.map((c) => {
      const v = r[c.key];
      const shown = c.fmt ? c.fmt(v) : esc(v);
      return `<td class="${c.n ? "n " : ""}${c.cls || ""}">${shown}</td>`;
    }).join("");
    return `<tr data-r="${esc(r.region)}" class="${state.selected === r.region ? "sel" : ""}">${cells}</tr>`;
  }).join("");

  $("table").innerHTML =
    `<table><thead><tr>${head}</tr></thead><tbody>${body ||
      '<tr><td colspan="10" class="empty">No region matches.</td></tr>'}</tbody></table>`;

  $("table").querySelectorAll("th").forEach((th) =>
    th.onclick = () => {
      const k = th.dataset.k;
      state.sort = { key: k, asc: state.sort.key === k ? !state.sort.asc : true };
      applyView();
    });
  $("table").querySelectorAll("tbody tr[data-r]").forEach((tr) =>
    tr.onclick = () => select(tr.dataset.r));
}

/* ------------------------------------------------------------- urgency */

function drawUrgency() {
  const rows = state.view.filter((r) => r.daysUntilOrder !== null);
  if (!rows.length) { $("urgency").innerHTML = '<p class="empty">Nothing to plot.</p>'; return; }
  const W = 640, RH = 24, padL = 122, valCol = 74;
  const vals = rows.map((r) => r.daysUntilOrder);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), span = (hi - lo) || 1;
  const plot = W - padL - valCol;
  const zero = padL + ((0 - lo) / span) * plot;
  const H = rows.length * RH + 24;
  const colour = { breached: "var(--critical)", overdue: "var(--critical)",
    due_now: "var(--serious)", approaching: "var(--warning)", stable: "var(--good)" };

  const marks = rows.map((r, i) => {
    const y = i * RH + 8, v = r.daysUntilOrder;
    const x = padL + ((v - lo) / span) * plot, c = colour[r.status] || "var(--series)";
    return `<g class="row" data-r="${esc(r.region)}">
      <title>${esc(r.region)}: ${r.status}, ${Math.abs(v)} days</title>
      <text x="${padL - 8}" y="${y + 12}" text-anchor="end" class="cat">${esc(r.region)}</text>
      <line x1="${zero.toFixed(1)}" y1="${y + 8}" x2="${x.toFixed(1)}" y2="${y + 8}" stroke="${c}" stroke-width="2"/>
      <circle cx="${x.toFixed(1)}" cy="${y + 8}" r="4.5" fill="${c}" stroke="var(--surface)" stroke-width="2"/>
      <text x="${W - 4}" y="${y + 12}" text-anchor="end" class="val">${Math.abs(v)}d${v < 0 ? " late" : ""}</text>
    </g>`;
  }).join("");

  $("urgency").innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img"
      aria-label="Days until each region must raise a capacity request">
    <line x1="${zero.toFixed(1)}" y1="2" x2="${zero.toFixed(1)}" y2="${H - 18}" class="zero"/>
    <text x="${zero.toFixed(1)}" y="${H - 5}" text-anchor="middle" class="tick">today</text>
    ${marks}</svg>`;
  $("urgency").querySelectorAll("g.row").forEach((g) =>
    g.onclick = () => select(g.dataset.r));
}

/* --------------------------------------------------------------- trend */

async function drawTrend(region) {
  const { points } = await get("/api/trend" + (region ? `?region=${encodeURIComponent(region)}` : ""));
  $("trend-scope").textContent = region || "all regions";
  if (points.length < 2) { $("trend").innerHTML = '<p class="empty">Not enough periods.</p>'; return; }
  const W = 380, H = 150, pad = 40;
  const ys = points.map((p) => p.value), top = Math.max(...ys) || 1;
  const px = (i) => pad + (i * (W - pad - 16)) / (points.length - 1);
  const py = (v) => H - 30 - (v / top) * (H - 52);

  const grid = [0, 0.5, 1].map((f) =>
    `<line x1="${pad}" y1="${py(top * f).toFixed(1)}" x2="${W - 16}" y2="${py(top * f).toFixed(1)}" class="grid"/>
     <text x="${pad - 6}" y="${(py(top * f) + 3).toFixed(1)}" text-anchor="end" class="tick">${Math.round(top * f).toLocaleString()}</text>`
  ).join("");
  const line = points.map((p, i) => `${px(i).toFixed(1)},${py(p.value).toFixed(1)}`).join(" ");
  const dots = points.map((p, i) =>
    `<g><title>${esc(p.period)}: ${Math.round(p.value).toLocaleString()} units</title>
     <circle cx="${px(i).toFixed(1)}" cy="${py(p.value).toFixed(1)}" r="4"
       fill="var(--series)" stroke="var(--surface)" stroke-width="2"/></g>`).join("");
  const labs = points.map((p, i) =>
    `<text x="${px(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="tick">${esc(p.period.slice(2))}</text>`).join("");

  $("trend").innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img"
      aria-label="Requested capacity per month">${grid}
    <polyline points="${line}" fill="none" stroke="var(--series)" stroke-width="2" stroke-linejoin="round"/>
    ${dots}${labs}</svg>`;
}

/* -------------------------------------------------------------- spikes */

async function drawSpikes(region) {
  const { spikes } = await get("/api/spikes" + (region ? `?region=${encodeURIComponent(region)}` : ""));
  $("spike-scope").textContent = region ? region : "all regions";
  $("spikes").innerHTML = spikes.length
    ? spikes.map((a) => `<div class="spike ${a.match_strength || "none"}">
        <b>${esc(a.region)}</b> <span class="mono">${esc(a.period)}</span>
        <p>${esc(a.recommendation)}</p></div>`).join("")
    : '<p class="empty">No spikes detected.</p>';
}

/* ------------------------------------------------------------ features */

async function drawFeatures(region) {
  const d = await get("/api/features" + (region ? `?region=${encodeURIComponent(region)}` : ""));
  const regions = region ? [region] : d.regions;
  const map = {};
  d.cells.forEach((c) => (map[`${c.Feature}|${c.Region}`] = c.Status));
  const cols = `160px repeat(${regions.length}, minmax(0,1fr))`;
  const header = region ? "" :
    `<div class="fgrid" style="grid-template-columns:${cols};margin-bottom:2px">
      <div></div>${regions.map((r) => `<div class="tick" style="font-size:.52rem;writing-mode:vertical-rl;transform:rotate(180deg);height:56px">${esc(r)}</div>`).join("")}</div>`;
  const rows = d.features.map((f) =>
    `<div class="fgrid" style="grid-template-columns:${cols}">
      <div class="frow">${esc(f)}</div>
      ${regions.map((r) => {
        const s = map[`${f}|${r}`] || "Unavailable";
        return `<div class="fcell f-${s.toLowerCase()}" title="${esc(f)} in ${esc(r)}: ${esc(s)}">${region ? esc(s) : ""}</div>`;
      }).join("")}</div>`).join("");
  $("features").innerHTML = header + rows +
    `<div class="legend"><span><i style="background:#1c5cab"></i>Live</span>
     <span><i style="background:#5598e7"></i>Preview</span>
     <span><i style="background:#b7d3f6"></i>Planned</span>
     <span><i style="background:var(--surface-3)"></i>Unavailable</span></div>`;
}

/* -------------------------------------------------------------- detail */

async function drawDetail(region) {
  $("detail-title").textContent = region;
  $("detail").innerHTML = '<p class="loading">Loading…</p>';
  const d = await get(`/api/region/${encodeURIComponent(region)}`);
  const t = d.threshold;
  const flagged = d.tickets.filter((x) => x.isFlagged);

  $("detail").innerHTML = `
    <div class="detail-head"><b>${esc(region)}</b>
      <span class="pill s-${t.status}">${esc(t.status.replace("_", " "))}</span>
      <span class="muted">${esc(t.sku_class)} · ${t.lead_time_days}d lead</span></div>
    <p class="reason">${esc(t.reason)}</p>
    <p class="reason">${esc(d.features.summary)}</p>
    <h3 style="font-size:.72rem;font-family:var(--mono);text-transform:uppercase;
      letter-spacing:.08em;color:var(--ink-3);margin:.9rem 0 .3rem">
      Failed requests (${flagged.length}) — one row per customer</h3>
    ${ticketTable(flagged)}`;
}

/* ---------------------------------------------------- migration calc */

async function runCalc() {
  if (!state.selected) { $("calc-result").innerHTML = '<p class="empty">Select a region to model a hardware change.</p>'; return; }
  const to = $("calc-to").value, mode = $("calc-mode").value;
  $("calc-result").innerHTML = '<p class="loading">Calculating…</p>';
  try {
    const d = await get(`/api/convert?region=${encodeURIComponent(state.selected)}&to_sku=${encodeURIComponent(to)}&mode=${mode}`);
    const c = d.conversion;
    const covers = c.covers_requirement;
    $("calc-result").innerHTML = `<div class="result">
      <div class="big" style="color:${c.capacity_delta >= 0 ? "var(--good)" : "var(--critical)"}">
        ${c.capacity_delta >= 0 ? "+" : ""}${Math.round(c.capacity_delta).toLocaleString()} work units</div>
      <div class="muted">${esc(d.from_sku)} → ${esc(d.to_sku)}, ${Math.round(d.deployed_units).toLocaleString()} units deployed</div>
      <dl>
        <dt>Cost</dt><dd>${c.cost_delta_pct >= 0 ? "+" : ""}${c.cost_delta_pct.toFixed(0)}%</dd>
        <dt>Units after</dt><dd>${Math.round(c.to_units).toLocaleString()}</dd>
        <dt>Lead time</dt><dd>${d.lead_time_days} days</dd>
        <dt>Covers load</dt><dd style="color:${covers ? "var(--good)" : "var(--critical)"}">
          ${covers ? "yes" : "no — would not cover current usage"}</dd>
      </dl></div>`;
  } catch (e) {
    $("calc-result").innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
}

/* ------------------------------------------------- conversion planner */

/* The one picture that answers the question: does a datacentre fit in the
 * space that is free? Total width is everything deployed. The bar fills with
 * what customers are running, then the margin held back, then what is genuinely
 * free. Underneath, the block being taken offline is drawn to the same scale and
 * pushed up against the right-hand end — if it reaches back into the part that
 * is in use, that overlap is the shortfall, and it is drawn in red. */
function headroomBar(p) {
  const W = 380, H = 82, pad = 2;
  const total = p.deployedUnits || 1;
  const w = (u) => Math.max(0, (u / total) * (W - pad * 2));
  const track = W - pad * 2;
  const used = Math.min(track, w(p.usedUnits));
  // A region can be running inside its own safety margin — then the margin has
  // no room left to draw, and that is the finding, so it is coloured rather
  // than silently truncated.
  const margin = Math.min(w(p.safetyMarginUnits), track - used);
  const marginBreached = w(p.safetyMarginUnits) > margin + 0.5;
  const free = Math.max(0, track - used - margin);
  const block = w(p.trancheUnits);
  const blockX = pad + track - block;
  const freeStart = pad + used + margin;
  const overlap = Math.max(0, freeStart - blockX);

  const seg = (x, wd, fill, label) => wd < 0.5 ? "" :
    `<g><title>${esc(label)}</title>
       <rect x="${x.toFixed(1)}" y="10" width="${(wd - 2).toFixed(1)}" height="26"
         rx="3" fill="${fill}"/></g>`;

  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img"
      aria-label="Capacity in use, held back, and free, against the size of the hardware being taken offline">
    ${seg(pad, used, "var(--series)", `In use now: ${Math.round(p.usedUnits).toLocaleString()} units`)}
    ${seg(pad + used, margin,
          marginBreached ? "var(--warning)" : "var(--rule-2)",
          marginBreached
            ? `Already inside the safety margin — only ${Math.round(p.deployedUnits - p.usedUnits).toLocaleString()} of the ${Math.round(p.safetyMarginUnits).toLocaleString()}-unit margin is left`
            : `Safety margin held back: ${Math.round(p.safetyMarginUnits).toLocaleString()} units`)}
    ${seg(freeStart, free, "var(--good)", `Free to take offline: ${Math.round(p.maxOfflineUnits).toLocaleString()} units`)}
    <text x="${pad}" y="${8}" class="tick">in use ${Math.round(p.usedUnits).toLocaleString()}</text>
    <text x="${W - pad}" y="${8}" text-anchor="end" class="tick">${Math.round(p.deployedUnits).toLocaleString()} deployed</text>

    <line x1="${freeStart.toFixed(1)}" y1="6" x2="${freeStart.toFixed(1)}" y2="${H - 20}" class="zero"/>
    <g><title>${esc(p.blockLabel)}</title>
      <rect x="${blockX.toFixed(1)}" y="44" width="${Math.max(2, block - 2).toFixed(1)}" height="18"
        rx="3" fill="${overlap > 0.5 ? "var(--critical)" : "var(--good)"}" opacity=".85"/></g>
    <text x="${W - pad}" y="${H - 6}" text-anchor="end" class="tick">${esc(p.blockLabel)}</text>
    ${overlap > 0.5
      ? `<text x="${pad}" y="${H - 6}" class="tick" fill="var(--critical)">overlaps capacity in use</text>`
      : ""}
  </svg>`;
}

async function runPlan() {
  const out = $("plan-result");
  if (!state.selected) { out.innerHTML = '<p class="empty">Select a region to test a conversion.</p>'; return; }
  const to = $("calc-to").value;
  const n = +$("plan-dc").value, total = +$("plan-total").value;
  out.innerHTML = '<p class="loading">Checking…</p>';
  try {
    const d = await get(`/api/conversion-plan?region=${encodeURIComponent(state.selected)}` +
      `&to_sku=${encodeURIComponent(to)}&datacentres=${total}&convert_datacentres=${n}`);

    const perDc = d.units_per_datacentre;
    const bar = headroomBar({
      deployedUnits: d.deployed_units,
      usedUnits: d.used_units,
      safetyMarginUnits: d.safety_margin_units,
      maxOfflineUnits: d.max_offline_units,
      trancheUnits: perDc,
      blockLabel: `one datacentre = ${Math.round(perDc).toLocaleString()} units`,
    });

    const steps = d.tranches.length ? `<h4>Schedule — ${d.tranche_count} pass${d.tranche_count > 1 ? "es" : ""}</h4>
      <table class="steps"><thead><tr>
        <th>Pass</th><th>Offline</th><th>Still available</th><th>Needed</th><th>Short by</th>
      </tr></thead><tbody>${d.tranches.map((t) => `<tr>
        <td>${t.number}</td>
        <td>${Math.round(t.units_out).toLocaleString()}</td>
        <td>${Math.round(t.available_during).toLocaleString()}</td>
        <td>${Math.round(t.required).toLocaleString()}</td>
        <td style="color:${t.shortfall > 0 ? "var(--critical)" : "var(--ink-3)"}">${t.shortfall > 0 ? Math.round(t.shortfall).toLocaleString() : "—"}</td>
      </tr>`).join("")}</tbody></table>` : "";

    const opts = d.options.length ? `<h4>What would make it possible</h4>
      <ul class="opts">${d.options.map((o) =>
        `<li><b>${esc(o.option)}</b><em>${esc(o.detail)}</em></li>`).join("")}</ul>` : "";

    out.innerHTML = `<div class="plan">
      <div class="verdict ${d.feasible ? "go" : "nogo"}">
        <b>${d.feasible ? "Can convert" : "Cannot convert"}</b>
        <span>${n} of ${total} datacentres → ${esc(d.to_sku)}</span></div>
      <p class="reason">${esc(d.summary)}</p>
      ${bar}
      <div class="legend">
        <span><i style="background:var(--series)"></i>in use by customers</span>
        <span><i style="background:${d.headroom_units < d.safety_margin_units ? "var(--warning)" : "var(--rule-2)"}"></i>safety margin</span>
        <span><i style="background:var(--good)"></i>free to take offline</span>
        <span><i style="background:${d.can_convert_a_whole_datacentre ? "var(--good)" : "var(--critical)"}"></i>one datacentre</span>
      </div>
      <dl class="result" style="background:none;padding:0">
        <dt>Free to take offline</dt><dd>${Math.round(d.max_offline_units).toLocaleString()} units
          <span class="muted">(${Math.round(d.headroom_units).toLocaleString()} spare − ${Math.round(d.safety_margin_units).toLocaleString()} held back)</span></dd>
        <dt>One datacentre</dt><dd>${Math.round(perDc).toLocaleString()} units</dd>
        <dt>Hardware wait</dt><dd>${d.lead_time_days} days before the first pass</dd>
        <dt>To hold capacity flat</dt><dd>${Math.round(d.units_to_hold_capacity_flat).toLocaleString()} units
          <span style="color:${d.fits_in_footprint ? "var(--ink-3)" : "var(--critical)"}">
            (${d.footprint_multiple.toFixed(1)}× the space — ${d.fits_in_footprint ? "fits" : "does not fit"})</span></dd>
        <dt>Cost of converted racks</dt><dd>${d.cost_delta_pct >= 0 ? "+" : ""}${d.cost_delta_pct.toFixed(0)}%</dd>
      </dl>
      ${steps}${opts}
      <p class="note" style="margin:.8rem 0 0">The split into ${total} datacentres is an
        assumption — the source data gives a region total only. Every figure above scales with it.</p>
    </div>`;
  } catch (e) {
    out.innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
}

/* ------------------------------------------------------------- select */

async function select(region) {
  state.selected = state.selected === region ? null : region;
  state.customer = null;
  drawCustomers();
  drawTable();
  const r = state.selected;
  if (r) {
    await Promise.all([drawDetail(r), drawTrend(r), drawSpikes(r), drawFeatures(r),
                       runCalc(), runPlan()]);
  } else {
    $("detail-title").textContent = "Nothing selected";
    $("detail").innerHTML = '<p class="empty">Select a region or a customer to see the detail.</p>';
    $("calc-result").innerHTML = '<p class="empty">Select a region to model a hardware change.</p>';
    $("plan-result").innerHTML = '<p class="empty">Select a region to test a conversion.</p>';
    await Promise.all([drawTrend(null), drawSpikes(null), drawFeatures(null)]);
  }
}

/* ---------------------------------------------------------- threshold */

let thrTimer;
async function onThreshold() {
  const pct = +$("thr").value;
  $("thrv").textContent = pct + "%";
  clearTimeout(thrTimer);
  thrTimer = setTimeout(async () => {
    const d = await get(`/api/threshold?pct=${pct}`);
    const by = {};
    d.regions.forEach((f) => (by[f.region] = f));
    state.regions.forEach((r) => {
      const f = by[r.region];
      if (f) { r.status = f.status; r.daysUntilOrder = f.days_until_order; r.reason = f.reason; }
    });
    $("actionable").innerHTML =
      `<b style="color:var(--critical);font-family:var(--mono)">${d.actionable}</b>&nbsp;regions need a decision`;
    applyView();
    if (state.selected) drawDetail(state.selected);
  }, 140);
}

/* ---------------------------------------------------------------- init */

(async function init() {
  const d = await get("/api/overview");
  state.regions = d.regions;
  state.skus = d.skus;
  drawKpis(d.kpis, d.asOf);

  $("calc-to").innerHTML = d.skus.map((s) => `<option>${esc(s)}</option>`).join("");
  $("prov").innerHTML = `<table class="prov"><thead><tr><th>Entity</th><th class="n">Rows</th>
    <th>Source</th></tr></thead><tbody>${d.provenance.map((p) => `<tr>
      <td class="mono">${esc(p.Entity)}</td><td class="n">${p.Rows.toLocaleString()}</td>
      <td><span class="pill" style="color:${p.FullySynthetic ? "var(--ink-3)" : "var(--series)"}">
        ${p.FullySynthetic ? "generated" : "real + generated"}</span></td></tr>`).join("")}
    </tbody></table>`;

  $("q").oninput = applyView;
  $("thr").oninput = onThreshold;
  $("calc-to").onchange = () => { runCalc(); runPlan(); };
  $("calc-mode").onchange = runCalc;

  // The planner's two sliders are coupled: you cannot convert more datacentres
  // than the region has, so raising one may pull the other down.
  let planTimer;
  const onPlan = () => {
    const total = +$("plan-total").value;
    const dc = $("plan-dc");
    dc.max = total;
    if (+dc.value > total) dc.value = total;
    $("plan-dcv").textContent = dc.value;
    $("plan-totalv").textContent = total;
    clearTimeout(planTimer);
    planTimer = setTimeout(runPlan, 140);
  };
  $("plan-dc").oninput = onPlan;
  $("plan-total").oninput = onPlan;

  applyView();
  await Promise.all([drawTrend(null), drawSpikes(null), drawFeatures(null), drawCustomers(), initAssistant()]);
  $("actionable").innerHTML =
    `<b style="color:var(--critical);font-family:var(--mono)">${
      state.regions.filter((r) => ["breached","overdue","due_now"].includes(r.status)).length
    }</b>&nbsp;need action`;
})();


/* ------------------------------------------------------------- customers */

function ticketTable(rows) {
  if (!rows.length) return '<p class="empty">No requests.</p>';
  return `<table class="tickets"><thead><tr>
      <th>Incident</th><th>Customer</th><th>Region</th><th>What happened</th>
      <th class="n">Units asked</th><th class="n">Days short</th><th class="n">At risk</th>
    </tr></thead><tbody>${rows.map((r) => `
      <tr><td class="mono">${esc(r.incidentId)}</td>
          <td class="cust">${esc(r.customerShort)}</td>
          <td>${esc(r.region)}</td>
          <td>${esc(r.outcome)}</td>
          <td class="n">${Math.round(r.askedFor)}</td>
          <td class="n">${r.days.toFixed(0)}</td>
          <td class="n">${money(r.exposure)}</td></tr>
      <tr><td colspan="7" class="work">${esc(r.workingOut)}</td></tr>`).join("")}
    </tbody></table>`;
}

async function drawCustomers() {
  const d = await get("/api/customers");
  $("customers").innerHTML = `<table><thead><tr>
      <th>#</th><th>Subscription</th><th>Tier</th><th class="n">Revenue at risk</th>
      <th class="n">Failed</th><th>Regions</th></tr></thead>
    <tbody>${d.customers.map((c) => `
      <tr data-c="${esc(c.subscriptionId)}" class="${state.customer === c.subscriptionId ? "sel" : ""}">
        <td class="n">${c.rank}</td>
        <td class="cust">${esc(c.customerShort)}</td>
        <td>${esc(c.tier)}</td>
        <td class="n">${money(c.exposure)}</td>
        <td class="n">${c.failedRequests}/${c.totalRequests}</td>
        <td>${esc(c.regions.join(", "))}</td></tr>`).join("")}
    </tbody></table>`;
  $("customers").querySelectorAll("tr[data-c]").forEach((tr) =>
    (tr.onclick = () => selectCustomer(tr.dataset.c)));
}

async function selectCustomer(id) {
  state.customer = state.customer === id ? null : id;
  state.selected = null;
  drawTable();
  drawCustomers();
  if (!state.customer) {
    $("detail-title").textContent = "Nothing selected";
    $("detail").innerHTML = '<p class="empty">Click any region or customer.</p>';
    return;
  }
  const d = await get(`/api/customer/${encodeURIComponent(id)}`);
  $("detail-title").textContent = `Customer ${id.slice(0, 8)}`;
  $("detail").innerHTML = `
    <p class="scope">Subscription <b>${esc(id)}</b> · ${esc(d.tier)} tier ·
      annual revenue ${money(d.arr)}<br>
      ${d.failedCount} of ${d.totalCount} requests failed ·
      <b>${money(d.exposure)}</b> at risk · ${esc(d.regions.join(", "))}
      &nbsp;<button class="clear" id="clear-cust">clear</button></p>
    ${ticketTable(d.requests)}`;
  $("clear-cust").onclick = () => selectCustomer(id);
}

/* ------------------------------------------------------------- assistant */

const chat = { history: [], busy: false };

function addMessage(who, text, meta) {
  const el = document.createElement("div");
  el.className = `msg ${who}${meta && meta.source === "fallback" ? " fallback" : ""}`;
  const tag = meta && meta.source === "fallback"
    ? '<span class="tag">from the data — model answer rejected</span>' : "";
  el.innerHTML = `<span class="who">${who === "you" ? "You" : "Assistant"}</span>
    <div class="bubble">${esc(text)}${tag}</div>`;
  $("chat").appendChild(el);
  $("chat").scrollTop = $("chat").scrollHeight;
  return el;
}

async function askAssistant(question) {
  if (!question || chat.busy) return;
  chat.busy = true;
  $("ask-send").disabled = true;
  $("ask-input").value = "";
  addMessage("you", question);
  const thinking = addMessage("bot", "…");

  try {
    const r = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The selected region rides along so "why is this one urgent?" resolves.
      body: JSON.stringify({
        question: state.selected ? `${question} (currently viewing ${state.selected})` : question,
        history: chat.history.slice(-6),
      }),
    });
    const d = await r.json();
    thinking.remove();
    addMessage("bot", d.answer, d);
    chat.history.push({ role: "user", content: question },
                      { role: "assistant", content: d.answer });
    $("ask-state").textContent =
      d.source === "model" ? "answers only from these numbers" : "answered straight from the data";
  } catch (e) {
    thinking.remove();
    addMessage("bot", "The assistant is unavailable. Everything else on this page still works.");
  } finally {
    chat.busy = false;
    $("ask-send").disabled = false;
    $("ask-input").focus();
  }
}

async function initAssistant() {
  const { suggestions } = await get("/api/suggestions");
  $("chips").innerHTML = suggestions
    .map((s) => `<button class="chip" type="button">${esc(s)}</button>`).join("");
  $("chips").querySelectorAll(".chip").forEach((c) =>
    (c.onclick = () => askAssistant(c.textContent)));
  $("ask-form").onsubmit = (e) => { e.preventDefault(); askAssistant($("ask-input").value.trim()); };
}
