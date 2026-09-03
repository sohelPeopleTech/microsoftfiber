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
   moved: it is now on each capacity pool, beside the thing being forecast. */
const NAV = [
  { path: "/",             icon: "◍", label: "Fleet map" },
  { path: "/overview",     icon: "▦", label: "Overview" },
  null,
  { path: "/regions",      icon: "◈", label: "Regions" },
  { path: "/datacentres",  icon: "▤", label: "Capacity pools" },
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

/* Capacity Units to one decimal place.

   Whole numbers hid the arithmetic on any grouped table: eastus2-dc02's three
   capacities use 1.3, 12.3 and 40.8 CU, which round to 1 + 12 + 41 = 54 under a
   parent showing 55. The rows were right and looked wrong. A tenth of a CU is
   below anything anyone acts on, so it costs nothing to show and lets a reader
   add the column up. */
const cu1 = (v) => (Number(v) || 0).toLocaleString(undefined, {
  minimumFractionDigits: 1, maximumFractionDigits: 1,
});

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
/* It sat open on every page, so every page opened on half a screen of
   instructions and the reader scrolled past them to reach the thing they came
   for. Collapsing it to a summary line helped; taking it off the page entirely
   is the honest version of the same fix. It is now a slide-over, opened from a
   help button beside the page heading, so it costs nothing until it is asked
   for and covers the page rather than pushing it down.

   What this returns is an inert carrier, not the panel. It renders wherever
   the page already put it -- above the heading, in most cases -- takes no
   layout, and is moved into the panel by `wireHowto()` after the view is
   built. That keeps this function's signature and every one of its fourteen
   call sites exactly as they were. */
function howto({ answers, steps, words = [], next, sources }) {
  return `<div class="howto-src" hidden>
    <section>
      <h3>What this page answers</h3>
      <p class="lede">${answers}</p>
    </section>

    ${/* One card per item, rather than one column of prose. The reader is
          looking at a block on the screen and wants the paragraph about that
          block; a bordered card is findable by eye, a run-on list is not.

          Still an <ol>: the steps are a walk down the page in order, and the
          numbering is the point of the section. The list markers are off and a
          counter draws them inside the card, so the order survives for a
          screen reader as well as on screen. */""}
    <section>
      <h3>Walking down the screen</h3>
      <ol class="howto-cards walk">${steps.map((s) =>
        `<li class="howto-card"><b>${s.what}</b><p>${s.is}</p></li>`).join("")}</ol>
    </section>

    ${words.length ? `<section>
      <h3>Definitions</h3>
      <ul class="howto-cards">${words.map((w) =>
        `<li class="howto-card"><b>${w.term}</b><p>${w.means}</p></li>`).join("")}</ul>
    </section>` : ""}

    <section>
      <h3>Recommended next steps</h3>
      <p>${next}</p>
    </section>

    <section>
      <h3>Data sources</h3>
      <p class="src">${sources}
        <a href="/methodology">See exactly how →</a></p>
    </section>
  </div>`;
}

function title(h1, sub) {
  return `<h1 class="page-title">${esc(h1)}</h1><p class="page-sub">${esc(sub)}</p>`;
}

/* ------------------------------------------------- the "how to read" panel */

/* One panel for the whole app, mounted on <body> and refilled per page.

   On <body> deliberately: `main > *` carries a transform animation, and an
   animating transform makes an ancestor the containing block for anything
   `position: fixed` inside it -- the panel would be trapped in the content
   column for the length of the view transition. The assistant panel sits on
   <body> for the same reason.

   Built once, listeners bound once; `wireHowto()` below is safe to call after
   every render. */
function howtoPanel() {
  let host = $("howto-panel");
  if (host) return host;

  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div class="howto-overlay" id="howto-overlay" hidden></div>
    <aside class="howto-panel" id="howto-panel" hidden
           role="dialog" aria-modal="true" aria-labelledby="howto-panel-title">
      <header>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10"/>
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
          <line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <div>
          <b id="howto-panel-title">How to read this page</b>
          <span id="howto-panel-sub"></span>
        </div>
        <button type="button" class="iconbtn howto-close" id="howto-close"
                aria-label="Close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </header>
      <div class="howto-body" id="howto-body" tabindex="-1"></div>
      <p class="howto-hint">Press <kbd>ESC</kbd> to close</p>
    </aside>`;
  while (wrap.firstElementChild) document.body.appendChild(wrap.firstElementChild);

  $("howto-close").addEventListener("click", () => closeHowto());
  $("howto-overlay").addEventListener("click", () => closeHowto());

  /* Escape closes, and Tab is held inside the panel while it is open -- an
     aria-modal dialog that lets focus wander back to the page behind it is
     lying about being modal. */
  document.addEventListener("keydown", (ev) => {
    if ($("howto-panel").hidden) return;
    if (ev.key === "Escape") { ev.preventDefault(); closeHowto(); return; }
    if (ev.key !== "Tab") return;
    const focusable = $("howto-panel").querySelectorAll(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])');
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault(); first.focus();
    }
  });
  return $("howto-panel");
}

//: The button that opened the panel, so focus can be handed back to it.
let howtoOpener = null;

function openHowto(trigger) {
  const panel = howtoPanel();
  howtoOpener = trigger;
  $("howto-overlay").hidden = false;
  panel.hidden = false;
  // Two frames: the element has to be laid out at its off-screen position
  // before the class that slides it in can animate from anywhere.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    $("howto-overlay").classList.add("on");
    panel.classList.add("on");
  }));
  document.body.classList.add("howto-open");   // the page behind must not scroll
  if (trigger) trigger.setAttribute("aria-expanded", "true");
  $("howto-body").focus();
}

function closeHowto() {
  const panel = $("howto-panel");
  if (!panel || panel.hidden) return;
  panel.classList.remove("on");
  $("howto-overlay").classList.remove("on");
  document.body.classList.remove("howto-open");
  if (howtoOpener) {
    howtoOpener.setAttribute("aria-expanded", "false");
    howtoOpener.focus();
    howtoOpener = null;
  }
  // Hidden only once it has slid out, or it vanishes instead of leaving.
  const done = () => { panel.hidden = true; $("howto-overlay").hidden = true; };
  const ms = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 220;
  setTimeout(done, ms);
}

/* Attaches this page's explainer to this page's heading.

   The heading is rendered by the page, the explainer by `howto()`, and the two
   are separate strings concatenated in fourteen places -- so they are joined
   here, after the view exists, rather than by touching any of them. The button
   goes *beside* the h1 rather than inside it: inside, its label joins the
   heading's accessible name and a screen reader announces "Capacity pools, How
   to read this page". */
function wireHowto() {
  closeHowto();
  const view = $("view");
  const h1 = view.querySelector("h1.page-title");

  // Every page gets the wrapper, so the heading sits in the same structure
  // whether or not it has an explainer to offer.
  if (h1 && !h1.parentElement.classList.contains("page-head")) {
    const head = document.createElement("div");
    head.className = "page-head";
    h1.replaceWith(head);
    head.appendChild(h1);
  }

  const src = view.querySelector(".howto-src");
  if (!src || !h1) return;

  /* Labelled, not a bare icon: a lone "?" beside a heading is a guess, and the
     one thing this control must not be is a puzzle.

     No `aria-label` -- the visible text is the accessible name. Setting one
     would replace "How to read" with something else in the accessibility tree,
     and anyone driving the page by voice would be saying a label the screen
     does not show. The fuller phrasing goes in `title`, which supplements
     rather than replaces. */
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "howto-trigger";
  trigger.id = "howto-trigger";
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.title = "How to read this page";
  trigger.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10"/>
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
      <line x1="12" y1="17" x2="12.01" y2="17"/></svg><span>How to read</span>`;
  h1.parentElement.appendChild(trigger);

  howtoPanel();
  $("howto-panel-sub").textContent = h1.textContent;
  $("howto-body").innerHTML = src.innerHTML;
  src.remove();                       // the carrier has done its job

  trigger.addEventListener("click", () => {
    if ($("howto-panel").hidden) openHowto(trigger); else closeHowto();
  });
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
function wireFilter(id, rowSelector, { total = null, noun = "rows", onApply = null } = {}) {
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
    // A page with nested detail rows (e.g. the per-SKU rows under a data
    // centre) keeps them in step with their parent here.
    if (onApply) onApply();
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

/* Several tables that answer related questions, in one panel instead of a
   column of panels.

   Three tables stacked down the Overview meant the third was a page-length
   scroll away from the first, and nothing on screen said they were three views
   of the same period rather than three unrelated blocks. As tabs they occupy
   one screen and the relationship is the layout.

   The tab strip *is* the panel header -- same surface, same height, same place
   the heading used to be -- so this reads as one card rather than a card with a
   toolbar bolted on. `items` is [{ label, count, hint, body }]; `count` and
   `hint` are optional, and `hint` replaces the header hint as tabs change. */
function tabPanel(id, items, { label = "" } = {}) {
  const tab = (t, i) => `<button type="button" class="tab" role="tab"
      id="${id}-tab-${i}" aria-controls="${id}-body-${i}"
      aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}"
      data-hint="${esc(t.hint || "")}">${esc(t.label)}${
      t.count == null ? "" : `<span class="tab-n">${num(t.count)}</span>`}</button>`;

  return `<section class="panel tabbed" id="${id}">
    <header role="tablist"${label ? ` aria-label="${esc(label)}"` : ""}>
      ${items.map(tab).join("")}
      <span class="hint" data-tab-hint>${esc(items[0]?.hint || "")}</span>
    </header>
    <div class="body flush">
      ${items.map((t, i) => `<div class="tab-body" role="tabpanel"
        id="${id}-body-${i}" aria-labelledby="${id}-tab-${i}"${i ? " hidden" : ""}
        >${t.body}</div>`).join("")}
    </div>
  </section>`;
}

//: How many rows a table shows before it scrolls inside itself rather than
//: pushing the page down. Twenty is about a screen, and a reader who wants the
//: twenty-first is looking for something specific and will scroll for it.
const TABLE_ROW_CAP = 20;

/* Caps a table at TABLE_ROW_CAP rows and lets it scroll on its own.
   Measured rather than assumed: rows here wrap to two and three lines
   depending on the prose in them, so a fixed pixel height would show fifteen
   rows on one table and twenty-eight on another. Runs when the table is
   visible -- a hidden tab measures zero. */
function capTableHeight(box) {
  if (!box || box.dataset.capped) return;
  const table = box.querySelector("table");
  // Visible rows only. The data-centre table carries its per-SKU rows collapsed
  // in the DOM, and a hidden row measures zero -- counting them capped the
  // table at the height of rows nobody can see.
  const rows = table && table.tBodies[0]
    ? [...table.tBodies[0].rows].filter((r) => !r.hidden) : [];
  // A table that fits is left alone entirely -- no cap, no inner scrollbar,
  // and no sticky header it does not need.
  if (rows.length <= TABLE_ROW_CAP) { box.dataset.capped = "1"; return; }
  const head = table.tHead ? table.tHead.getBoundingClientRect().height : 0;
  const top = rows[0].getBoundingClientRect().top;
  const cut = rows[TABLE_ROW_CAP].getBoundingClientRect().top;
  if (cut <= top) return;             // not laid out yet; measure again later
  box.style.maxHeight = `${Math.round(head + cut - top)}px`;
  box.classList.add("table-capped");  // pairs the scrolling with the height
  box.dataset.capped = "1";
}

function wireTabs(root) {
  root.querySelectorAll(".panel.tabbed").forEach((panel) => {
    const tabs = [...panel.querySelectorAll('[role="tab"]')];
    const hint = panel.querySelector("[data-tab-hint]");

    const show = (idx, focus = false) => {
      tabs.forEach((t, i) => {
        const on = i === idx;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (hint) hint.textContent = tabs[idx].dataset.hint || "";
      // Measured on reveal, for the same reason it is measured at all.
      const body = document.getElementById(tabs[idx].getAttribute("aria-controls"));
      body.querySelectorAll(".scroll-x, .tablewrap").forEach(capTableHeight);
      if (focus) tabs[idx].focus();
    };

    tabs.forEach((t, i) => t.addEventListener("click", () => show(i)));

    /* Arrow keys move between tabs, which is what a tablist is expected to do
       once it has taken the tab stop for itself. */
    panel.querySelector('[role="tablist"]').addEventListener("keydown", (ev) => {
      const at = tabs.indexOf(document.activeElement);
      if (at < 0) return;
      const to = { ArrowRight: at + 1, ArrowLeft: at - 1,
                   Home: 0, End: tabs.length - 1 }[ev.key];
      if (to === undefined) return;
      ev.preventDefault();
      show((to + tabs.length) % tabs.length, true);
    });

    show(tabs.findIndex((t) => t.getAttribute("aria-selected") === "true") || 0);
  });

  // Tables outside a tabbed panel get the same cap.
  root.querySelectorAll(".scroll-x, .tablewrap").forEach(capTableHeight);
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
  wireHowto();  // joins this page's explainer to this page's heading
  wireTabs($("view"));
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
