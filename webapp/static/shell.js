/* Application shell -- chrome, routing, assistant, shared helpers.
 *
 * Routing is server-accepted and client-rendered: FastAPI serves app.html for
 * all six tab URLs, and this file dispatches on location.pathname. Deep links
 * and browser back work; no build step, no routing library.
 *
 * Every page renderer lives in pages.js and registers itself on PAGES.
 */

/* Rename in one place. The reference tool this was matched against names its
   assistant; a name makes it discoverable in a demo. */
const ASSISTANT_NAME = "Atlas";

const PAGES = {};                 // path -> async (view) => void, filled by pages.js
const $ = (id) => document.getElementById(id);

/* Grouped by what someone is looking for, not by module number: the fleet as a
   whole, then the same fleet at three levels of zoom, then how any of it was
   worked out.

   The map leads and sits at `/`, so it is what someone sees on signing in.
   Review's reasoning was that the first question is *where*, and a map answers
   that before a table does; Overview keeps its own URL directly beneath it.

   Six entries came off this list -- Reasons, Incidents, Forecast, Capacity
   policy, Recommendations and Actions. Only the list changed. Every one of
   those routes is still registered, still served and still backed by its
   endpoints, so a saved link or a demo bookmark still opens the page; it is
   simply no longer offered here. Forecasting in particular did not go away, it
   moved: it is now on each data centre, beside the thing being forecast. */
const NAV = [
  { path: "/",             icon: "◍", label: "Fleet map" },
  { path: "/overview",     icon: "▦", label: "Overview" },
  null,
  { path: "/regions",      icon: "◈", label: "Regions" },
  { path: "/datacentres",  icon: "▤", label: "Data centres" },
  { path: "/customers",    icon: "◉", label: "Customers" },
  null,
  { path: "/methodology",  icon: "ⓘ", label: "Methodology" },
];

/* ------------------------------------------------------------- formatting */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const money = (v) => {
  const n = Number(v) || 0;
  const a = Math.abs(n);
  if (a >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${Math.round(n / 1e3)}K`;
  return `$${Math.round(n).toLocaleString()}`;
};

const pct = (v, digits = 0) => `${(Number(v) || 0).toFixed(digits)}%`;
const num = (v) => (Number(v) || 0).toLocaleString();

/* Titlecase a snake_case status without pretending it is prose. */
const words = (s) => String(s ?? "").replace(/_/g, " ");

/* --------------------------------------------------------------- fetching */

async function get(url) {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) { location.href = "/login"; throw new Error("unauthenticated"); }
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.status === 401) { location.href = "/login"; throw new Error("unauthenticated"); }
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

/* ------------------------------------------------------- shared fragments */

/* The explainer every page opens with.
 *
 * It began as the reference tool's five prose sections and was hard to follow:
 * it *described* a layout to someone who had not looked at it yet, so the
 * reader had to hold a mental model of the page while reading about it.
 *
 * Now it walks down the screen instead. Each numbered step names the thing you
 * are looking at, in the order it appears, so you can read one line and then
 * glance at the block it refers to. Definitions come after the walkthrough --
 * a word means more once you have seen where it is used.
 */
/* Collapsed by default.

   It sat open on every page, so every page opened on half a screen of
   instructions and the reader scrolled past them to reach the thing they came
   for. Read once, it is not needed again; left open, it pushes the table below
   the fold on arrival. The summary stays visible so it is one click away. */
function howto({ answers, steps, words = [], next, sources }) {
  return `<details class="howto">
    <summary>How to read this page</summary>
    <div class="body">
      <p class="lede">${answers}</p>

      <ol class="walk">${steps.map((s) =>
        `<li><b>${s.what}</b> — ${s.is}</li>`).join("")}</ol>

      ${words.length ? `<h4>Definitions</h4>
        <ul class="terms">${words.map((w) =>
          `<li><b>${w.term}</b> — ${w.means}</li>`).join("")}</ul>` : ""}

      <h4>Recommended next steps</h4>
      <p>${next}</p>

      <p class="src">Data sources: ${sources}
        <a href="/methodology">See exactly how →</a></p>
    </div>
  </details>`;
}

function title(h1, sub) {
  return `<h1 class="page-title">${esc(h1)}</h1><p class="page-sub">${esc(sub)}</p>`;
}

/* `help` renders an info marker carrying the explanation. Review asked for one
   on every metric -- these are terms nobody outside capacity operations knows,
   and a definition that only exists in the page explainer is a definition the
   reader has already scrolled past. */
function kpi(label, value, sub, tone = "", help = "") {
  return `<div class="kpi">
    <span class="label">${esc(label)}${help ? info(help) : ""}</span>
    <span class="value ${tone}">${value}</span>
    <span class="sub">${esc(sub || "")}</span>
  </div>`;
}

/* The little "i" beside a figure, and the explanation behind it.

   These carried only a `title` attribute, which is the browser's own tooltip:
   it waits about a second before appearing, renders as a single OS-styled
   line, and handles three hundred characters of prose badly. Several of these
   explanations run past three hundred and eighty. A reviewer hovering one
   reported seeing nothing at all, which is the honest outcome -- the
   explanation was there and unreadable, which is the same as absent.

   The text now lives in a data attribute and is drawn by wireInfo() below.
   `title` is kept as well: if the JavaScript has not run yet, the browser
   tooltip is still better than silence. */
function info(text) {
  return ` <span class="info" tabindex="0" role="button" aria-label="${esc(text)}"
    data-info="${esc(text)}" title="${esc(text)}">i</span>`;
}

/* One tooltip element, and one set of listeners on the document.

   Positioned against the viewport rather than a parent, because these markers
   sit inside panels that scroll and table cells that clip -- a bubble
   positioned relative to either gets cut off. Flipped below the marker when
   there is no room above, and clamped to the window on both sides so an
   explanation on the last column is not half off the screen.

   Delegated rather than bound per element. The first version walked the view
   after each route and attached listeners, which missed everything drawn
   afterwards: the fleet map fetches its markers once the page has already
   rendered, so twelve of its explanations were wired to nothing while the
   forecast's forty-four worked. Anything carrying data-info now works whenever
   it appears, including markup that has not been written yet. */
function wireInfo() {
  if (document.__infoWired) return;
  document.__infoWired = true;

  const tip = document.createElement("div");
  tip.id = "info-tip";
  tip.className = "info-tip";
  tip.hidden = true;
  document.body.appendChild(tip);

  const hide = () => { tip.hidden = true; };
  const show = (el) => {
    const text = el.dataset.info;
    if (!text) return;
    tip.textContent = text;
    tip.hidden = false;
    const m = el.getBoundingClientRect();
    const t = tip.getBoundingClientRect();
    const pad = 8;
    let left = m.left + m.width / 2 - t.width / 2;
    left = Math.max(pad, Math.min(left, window.innerWidth - t.width - pad));
    let top = m.top - t.height - 10;
    if (top < pad) top = m.bottom + 10;          // no room above: sit below
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
  };

  const find = (ev) => ev.target instanceof Element
    ? ev.target.closest("[data-info]") : null;

  document.addEventListener("mouseover", (ev) => {
    const el = find(ev);
    if (el) show(el); else hide();
  });
  document.addEventListener("focusin", (ev) => {
    const el = find(ev);
    if (el) show(el);
  });
  document.addEventListener("focusout", hide);
  window.addEventListener("scroll", hide, { passive: true });
}

/* Column heading with its own explanation. */
function th(label, help, cls = "") {
  return `<th${cls ? ` class="${cls}"` : ""}>${esc(label)}${help ? info(help) : ""}</th>`;
}

/* A search box and one dropdown, above a table.

   Three pages grew the same need at once -- find one row among a hundred and
   ten, or cut the list to a single region -- so it is one component rather than
   three near-copies that drift. Filtering is done in the DOM against rows the
   page has already rendered: no refetch, no server round-trip, and no second
   code path that could disagree with the table it is filtering.

   `rows` must carry `data-search` (the text the box matches, lowercased) and,
   where a dropdown is used, `data-filter`. */
function filterBar(id, { placeholder = "Search…", options = [],
                         allLabel = "All", label = "" } = {}) {
  /* One control, not two.

     This was a search box and a separate dropdown sitting beside it, which
     asked the reader to work out which of the two would find what they wanted
     -- and let them set both to contradictory things. It is now a single
     combobox: type to narrow, or open the list and pick. The suggestions are
     the same values the dropdown held, filtered live by what has been typed,
     so one control does both jobs and cannot disagree with itself. */
  return `<div class="filter-bar" id="${id}">
    <div class="combo" role="combobox" aria-expanded="false" aria-haspopup="listbox">
      <svg class="combo-icon" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6"/>
        <path d="M10.4 10.4 14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
      </svg>
      <input type="text" id="${id}-q" autocomplete="off" spellcheck="false"
        placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"
        aria-controls="${id}-list">
      <button type="button" class="combo-clear" id="${id}-clear"
        aria-label="Clear" hidden>&times;</button>
      <button type="button" class="combo-toggle" id="${id}-toggle"
        aria-label="Show ${esc(label || "all options")}">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4"
          fill="none" stroke="currentColor" stroke-width="1.7"
          stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
      <ul class="combo-list" id="${id}-list" role="listbox" hidden>
        <li role="option" data-value="">${esc(allLabel)}</li>
        ${options.map((o) => `<li role="option" data-value="${esc(o.value ?? o)}"
          >${esc(o.label ?? o)}</li>`).join("")}
      </ul>
    </div>
    <span class="filter-count" id="${id}-count"></span>
  </div>`;
}

/* Wires a `filterBar` to the rows beneath it. Returns the apply function so a
   page can re-run it after redrawing its own table. */
function wireFilter(id, rowSelector, { total = null, noun = "rows" } = {}) {
  const box = $(`${id}-q`), list = $(`${id}-list`), count = $(`${id}-count`);
  const toggle = $(`${id}-toggle`), clear = $(`${id}-clear`);
  if (!box) return () => {};
  const combo = box.closest(".combo");
  const items = [...list.querySelectorAll("li")];

  // Set when a value was chosen from the list rather than typed. A pick is an
  // exact filter on one value; typed text is a substring match across the row.
  let picked = "";

  function openList(show) {
    list.hidden = !show;
    combo.setAttribute("aria-expanded", String(!!show));
    if (show) {
      // Only offer options still reachable from what has been typed.
      const term = picked ? "" : box.value.trim().toLowerCase();
      items.forEach((li) => {
        li.hidden = !!term && li.dataset.value !== ""
          && !li.textContent.toLowerCase().includes(term);
      });
    }
  }

  function apply() {
    const term = picked ? "" : box.value.trim().toLowerCase();
    const rows = [...document.querySelectorAll(rowSelector)];
    let shown = 0;
    rows.forEach((tr) => {
      const hay = (tr.dataset.search || tr.textContent || "").toLowerCase();
      const ok = (!term || hay.includes(term))
              && (!picked || (tr.dataset.filter || "") === picked);
      tr.hidden = !ok;
      if (ok) shown++;
    });
    const all = total ?? rows.length;
    clear.hidden = !(box.value || picked);
    // Silent when nothing is filtered: a count that never moves is noise.
    count.textContent = (term || picked)
      ? (shown ? `${shown} of ${all} ${noun}` : `No ${noun} match`)
      : "";
    count.classList.toggle("empty-result", !!(term || picked) && !shown);
  }

  box.addEventListener("input", () => { picked = ""; openList(true); apply(); });
  box.addEventListener("focus", () => openList(true));
  toggle.addEventListener("click", () => {
    if (list.hidden) { box.focus(); openList(true); } else openList(false);
  });
  clear.addEventListener("click", () => {
    box.value = ""; picked = ""; openList(false); apply(); box.focus();
  });

  list.addEventListener("click", (ev) => {
    const li = ev.target.closest("li[data-value]");
    if (!li) return;
    picked = li.dataset.value;
    box.value = picked ? li.textContent.trim() : "";
    openList(false);
    apply();
  });

  // Escape closes; clicking away closes. Bound on the document but removed with
  // it -- route() replaces the view, and a listener left on document would
  // outlive the combobox it was closing.
  box.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { openList(false); box.blur(); }
  });
  document.addEventListener("click", (ev) => {
    if (!combo.contains(ev.target)) openList(false);
  });

  apply();
  return apply;
}

function panel(heading, inner, { hint = "", flush = false } = {}) {
  return `<section class="panel">
    <header><h3>${esc(heading)}</h3>${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</header>
    <div class="body${flush ? " flush" : ""}">${inner}</div>
  </section>`;
}

/* Status -> pill class. Shared because Overview, Regions and Actions all show
   the same Module 1 status and must not disagree about what red means. */
/* What each threshold state is called on screen.
   `due_now` needed one: the state means "the decision falls inside this review
   cycle", which is up to thirty days out, and the label read "due now" against
   a region whose act-by date was three weeks away. The state widened when the
   hardware lead time was replaced by a decision window; the word did not go
   with it. The key stays `due_now` because the data and the tests are built on
   it -- only what a reader sees changes. */
const STATUS_LABEL = {
  breached: "past its line",
  overdue: "overdue",
  due_now: "decide this cycle",
  due_soon: "decide this cycle",
  approaching: "approaching",
  stable: "stable",
};

function statusPill(status) {
  const tone = { breached: "bad", overdue: "bad", due_now: "warn", due_soon: "warn" }[status] || "good";
  return `<span class="pill ${tone}">${esc(STATUS_LABEL[status] || words(status))}</span>`;
}

/* Review rejected "breached": a breach reads as a fault, and a region using the
   capacity it holds has not done anything wrong. The status a reader needs is
   binary and plain -- is this region in risk or not -- with the amount of the
   threshold consumed stated beside it rather than dressed up as a violation. */
/* In risk, or not. Nothing else.
   This carried "threshold utilised by 14.2%" underneath, and a count of
   capacities refusing work was later stacked under that, which left one cell
   holding three separate facts and rows four lines tall. The column answers one
   question and now says only its answer; the other two facts have columns of
   their own. */
function thresholdPill(r) {
  return r.at_risk
    ? `<span class="pill bad">In risk</span>`
    : `<span class="pill good">Not in risk</span>`;
}

function riskPill(band) {
  const tone = { high: "bad", medium: "warn", low: "good" }[band] || "mute";
  return `<span class="pill ${tone}">${esc(band)}</span>`;
}

/* --------------------------------------------------------------- chrome */

function renderNav(path) {
  // `/map` is the map's old address and still resolves. Someone arriving on it
  // should see the map entry lit rather than nothing lit at all.
  const here = path === "/map" ? "/" : path;
  $("nav").innerHTML = NAV.map((item) => {
    if (!item) return "<hr>";
    const on = item.path === here ? " on" : "";
    return `<a class="nav-link${on}" href="${item.path}">
      <span class="ico">${item.icon}</span>${esc(item.label)}</a>`;
  }).join("");
}

async function renderIdentity() {
  const me = await get("/api/me");
  $("who-name").textContent = me.name;
  $("who-scope").textContent = me.scope;
  $("who-initials").textContent = me.initials;
  $("me-initials").textContent = me.initials;
  $("me-initials").title = me.name;
}

/* ------------------------------------------------------------- assistant */

const chat = { history: [], busy: false, loaded: false };

function addMessage(who, text) {
  const el = document.createElement("div");
  el.className = `msg ${who}`;
  el.innerHTML = `<span class="who">${who === "you" ? "You" : esc(ASSISTANT_NAME)}</span>
                  <span class="what"></span>`;
  el.querySelector(".what").textContent = text;   // never innerHTML for model output
  $("chat-log").appendChild(el);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

async function askAssistant(question) {
  if (!question || chat.busy) return;
  chat.busy = true;
  $("chat-input").value = "";
  // Starter chips have done their job once a conversation exists, and they
  // take a third of the panel. Clear them so the answers get the room.
  $("chat-chips").innerHTML = "";
  addMessage("you", question);
  addMessage("bot", "…");
  const pending = $("chat-log").lastChild.querySelector(".what");

  try {
    const d = await post("/api/ask", { question, history: chat.history.slice(-6) });
    pending.textContent = d.answer;
    chat.history.push({ role: "user", content: question },
                      { role: "assistant", content: d.answer });
  } catch {
    pending.textContent =
      "The assistant is unavailable. Everything else on this page still works.";
  } finally {
    chat.busy = false;
  }
}

async function openChat() {
  $("chat-panel").classList.add("open");
  $("chat-input").focus();
  if (chat.loaded) return;
  chat.loaded = true;
  addMessage("bot",
    `Ask me anything about the capacity data on screen. I only answer from the ` +
    `current snapshot — if a figure is not in it, I will say so rather than guess.`);
  try {
    const d = await get("/api/suggestions");
    $("chat-chips").innerHTML = d.suggestions
      .map((s) => `<button type="button">${esc(s)}</button>`).join("");
    $("chat-chips").querySelectorAll("button").forEach((b) =>
      (b.onclick = () => askAssistant(b.textContent)));
  } catch { /* chips are a convenience; the input still works without them */ }
}

function wireChat() {
  $("chat-name").textContent = ASSISTANT_NAME;
  $("chat-title").textContent = ASSISTANT_NAME;
  $("chat-open").onclick = openChat;
  $("chat-close").onclick = () => $("chat-panel").classList.remove("open");
  $("chat-form").onsubmit = (e) => {
    e.preventDefault();
    askAssistant($("chat-input").value.trim());
  };
}

/* ---------------------------------------------------------------- router */

/* Intercept same-origin nav clicks so tab switches do not reload the shell.
   Anything else -- external links, /logout -- falls through to the browser. */
function wireLinks() {
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a || a.target || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;
    if (resolve(url.pathname).path === "/" && url.pathname !== "/") return;
    e.preventDefault();
    // Carry the query string. Filters on the recommendations page live in it,
    // and dropping it here silently ignored every filter link.
    navigate(url.pathname + url.search);
  });
  addEventListener("popstate", () => route());
}

function navigate(path) {
  // Compare the query too. Two links to /recommendations differing only by
  // ?kind= are different destinations, and comparing pathname alone made
  // switching filters a no-op that looked like a dead link.
  if (path === location.pathname + location.search) return;
  history.pushState({}, "", path);
  route();
}

/* Deep pages carry an id in the path: /datacentre/westeurope-dc01. Resolved to
   its renderer here so the URL stays linkable rather than being a panel state
   that vanishes on refresh. */
function resolve(pathname) {
  if (pathname in PAGES) return { path: pathname, arg: null };
  const cut = pathname.lastIndexOf("/");
  const base = pathname.slice(0, cut);
  if (cut > 0 && base in PAGES) {
    return { path: base, arg: decodeURIComponent(pathname.slice(cut + 1)) };
  }
  return { path: "/", arg: null };
}

async function route() {
  const { path, arg } = resolve(location.pathname);
  renderNav(path);
  $("sidebar").classList.remove("open");
  $("view").innerHTML = `<p class="loading">Loading…</p>`;
  scrollTo(0, 0);
  try {
    await PAGES[path]($("view"), arg, location.search);
  } catch (err) {
    $("view").innerHTML =
      `<p class="error">Could not load this page: ${esc(err.message)}</p>`;
  }
  wireInfo();   // idempotent; listens on the document, so late markup is covered
}

/* Header badge: how many regions need an order placed. Shown app-wide because
   it is the number that should pull someone back to the tool. */
async function renderAlerts() {
  try {
    const d = await get("/api/overview");

    /* The as-of date is the last date in the extract, not today -- that is
       deliberate, so repeated runs over a fixed file give identical numbers.
       Unexplained it just looks like stale data, which is the first thing a
       reviewer would challenge. So say which it is, and how old. */
    const asOf = String(d.asOf).slice(0, 10);
    const ageDays = Math.round((Date.now() - Date.parse(asOf)) / 86400000);
    $("asof").textContent = `Data to ${asOf}`;
    $("asof").title =
      `The sample extract ends on ${asOf} (${ageDays} days ago). Figures are ` +
      `calculated as at that date, not today, so the same file always produces ` +
      `the same numbers.`;
    if (ageDays > 45) $("asof").style.color = "var(--warn)";

    const due = d.regions.filter((r) =>
      ["breached", "overdue", "due_now"].includes(r.status)).length;
    const badge = $("alert-count");
    badge.textContent = due;
    badge.hidden = due === 0;
    $("alerts").onclick = () => navigate("/regions");
  } catch { /* the badge is decoration; never block the app on it */ }
}

function start() {
  $("menu").onclick = () => $("sidebar").classList.toggle("open");
  wireChat();
  wireLinks();
  renderIdentity();
  renderAlerts();
  route();
}
