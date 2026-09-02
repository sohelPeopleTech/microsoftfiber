"""The stylesheet has to define what the pages ask for.

Every colour class in this project was used in tables, cards and captions from
the day the first one was written and defined nowhere except inside `.chart-tip`
-- so outside a tooltip they rendered in the inherited colour and did nothing at
all. Several rounds of "colour the dangerous rows" shipped with no colour on any
of them, and nothing caught it, because a missing CSS class is not an error
anywhere: the markup is valid and the page renders, it is just wrong.

These read the two static files as text. Crude, and the only thing standing
between a class name and silence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "webapp" / "static" / "base.css").read_text()
JS = (ROOT / "webapp" / "static" / "pages.js").read_text()

#: Classes carrying meaning rather than layout. If one of these is undefined
#: the page still renders, and silently stops saying what it means to say.
SEMANTIC = ["t-bad", "t-warn", "t-good", "t3"]


@pytest.mark.parametrize("name", SEMANTIC)
def test_semantic_colour_classes_are_defined_globally(name):
    """Defined outside `.chart-tip`, which is dark and scoped to itself."""
    unscoped = re.search(rf"(?m)^\.{re.escape(name)}\b[^{{]*{{", CSS)
    assert unscoped, (
        f".{name} is used in pages.js but has no global rule in base.css, so "
        f"every use outside .chart-tip inherits its colour and means nothing")


def test_wide_tables_can_scroll():
    """`.tablewrap` is on every table in the app and had no rule whatsoever, so
    the ten-column capacity tables pushed the page sideways instead of
    scrolling inside their panel."""
    assert re.search(r"(?m)^\.tablewrap\b[^{]*{[^}]*overflow-x", CSS), (
        ".tablewrap does not set overflow-x, so wide tables overflow the page")


def test_the_tooltip_keeps_its_own_lighter_variants():
    """The chart tooltip is dark; the global colours would be unreadable on it."""
    for name in ("t-bad", "t-warn", "t-good"):
        assert f".chart-tip .{name}" in CSS, (
            f"the dark tooltip lost its own .{name}, so it now paints a dark "
            f"colour on a dark background")


# --------------------------------------------------------------------------
# the vocabulary, in what a reader actually reads
# --------------------------------------------------------------------------

#: The Azure hardware model this product started as. Fabric is SaaS: there is no
#: server, no vendor, no core and nothing to order. `test_planning.py` already
#: guards the generated recommendation text, but every one of these survived
#: that test by living in a hand-written explainer instead -- including "the
#: hardware under it is failing more than the fleet average", which sat in the
#: Fleet map's own glossary while the page beside it talked about SKUs and
#: throttling.
BANNED = ["hardware class", "hardware classes", "hardware order", "hardware fault",
          "cores", "core-hours", "vendor", "provisioning", "lead time",
          "lead-time", "order due", "order overdue", "waiting to be bought",
          "replacement hardware", "poweredge", "proliant"]

#: Empty, and the point is that it is empty.
#:
#: This held /actions, /datacentre and /policy, which were still module 2's
#: migration model -- take a building offline, convert it between hardware
#: classes, wait out a provisioning lead time. They were exempted because the
#: words were accurate about what the pages did, and rewording them would have
#: described a Fabric model that was not there.
#:
#: So the model was built instead. /policy turned out never to have belonged
#: here at all: it had exactly one stray word. The other two now run on
#: planning.scale, which answers the question Fabric can actually be asked --
#: which capacity moves to which rung of the F-SKU ladder.
#:
#: Anything added back here is a page that has stopped speaking Fabric.
LEGACY_AZURE_PAGES: set[str] = set()

#: Sentences whose whole point is that the Azure model is gone. A note saying
#: "hardware and lead time were removed" has to be able to name what it removed.
#:
#: "procurement lead time" is a separate, deliberate exception. The ban on "lead
#: time" was about *hardware provisioning* -- Fabric is SaaS, nothing is racked,
#: and the old page printed a server delivery estimate beside a list of F SKUs.
#: Review then asked for a commercial procurement lead time, fixed at twelve
#: weeks, as the thing the buy decision is judged against. That is a real
#: business quantity rather than a leftover from the Azure model, so it is named
#: explicitly here: the phrase is allowed where it is qualified as procurement
#: or stated in weeks, and still banned everywhere else.
ALLOWED_CONTEXT = [
    "why there is no hardware", "were removed",
    "capacity takes to arrive once ordered", "12-week lead time",
]


#: Which page a line belongs to. Crude -- pages.js assigns each one as
#: `PAGES["/x"] = ...` in file order, so the most recent assignment above a line
#: is the page it renders.
_PAGE_ASSIGN = re.compile(r'^PAGES\["([^"]+)"\]')


def _reader_facing_lines():
    """Lines of pages.js, minus the ones only a developer sees.

    Field names are not user-visible and are allowed to say `cores_pending`
    where that is the column's name; a quoted sentence containing "cores" is a
    different thing entirely. Lines belonging to a page still on the Azure model
    are skipped, because there the vocabulary is telling the truth.
    """
    page = None
    for n, line in enumerate(JS.splitlines(), start=1):
        found = _PAGE_ASSIGN.match(line)
        if found:
            page = found.group(1)
        if page in LEGACY_AZURE_PAGES:
            continue
        if line.strip().startswith("//"):
            continue
        yield n, line


@pytest.mark.parametrize("phrase", BANNED)
def test_the_azure_vocabulary_is_absent_from_what_a_reader_reads(phrase):
    # Whole words. A substring check flagged `f.scores` for containing "cores"
    # forty times over, which is the same mistake test_planning.py made when it
    # flagged "Real-Time Intelligence" for containing "intel".
    word = re.compile(rf"\b{re.escape(phrase)}\b")
    hits = []
    for n, line in _reader_facing_lines():
        low = line.lower()
        if not word.search(low):
            continue
        if any(ok in low for ok in ALLOWED_CONTEXT):
            continue
        # `m.cores` and `cores:` are field names on the wire -- read as data,
        # never printed. The label rendered above them already says CU.
        if re.search(rf"\.{re.escape(phrase)}\b|\b{re.escape(phrase)}\s*[:_]", low):
            continue
        hits.append(f"pages.js:{n}: {line.strip()[:120]}")
    assert not hits, (
        f"{phrase!r} is Azure vocabulary and Fabric has no such thing:\n  "
        + "\n  ".join(hits))


def _innermost_interpolations(needle: str):
    """The tightest `${ ... }` wrapped around each mention of `needle`.

    Not "every interpolation that contains it": these are nested many levels
    deep, and the outermost wrapper on the Actions page is most of the page.
    Walking outward from the mention finds the one that actually renders it.
    """
    out = []
    at = JS.find(needle)
    while at >= 0:
        depth, i = 0, at
        start = None
        while i > 0:                      # outward to the opening ${
            if JS[i] == "}":
                depth += 1
            elif JS[i] == "{":
                if depth == 0:
                    if JS[i - 1] == "$":
                        start = i - 1
                    break
                depth -= 1
            i -= 1
        if start is not None:
            depth, j = 0, start + 1
            while j < len(JS):            # forward to its matching brace
                if JS[j] == "{":
                    depth += 1
                elif JS[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(JS[start + 2:j])
        at = JS.find(needle, at + 1)
    return out


def test_the_throttling_labels_are_read_as_text_not_as_objects():
    """PROBLEM maps a stage to {text, tone, why}, not to a string.

    Interpolating the object itself is valid JavaScript and renders the words
    "[object Object]" in a red pill where the throttling stage should be. It
    shipped that way on two tables of the data-centre page. No test could have
    caught it: both are built inside a template literal that nothing evaluates
    until a browser renders it, and it was found by looking at the page.
    """
    uses = [u for u in _innermost_interpolations("PROBLEM[")
            if "const PROBLEM" not in u]
    assert uses, "PROBLEM is no longer interpolated -- update this test"
    for expr in uses:
        assert re.search(r"\.(text|tone|why)\b", expr), (
            "a PROBLEM entry is interpolated whole, which renders as "
            "[object Object]:\n  " + " ".join(expr.split())[:140])


def test_the_pages_match_on_status_strings_the_engine_actually_emits():
    """`p.status === "due"` compared against a value nothing has ever sent.

    module1 emits "due_now". Three places in pages.js tested for "due", so the
    amber band on the fleet map was carried entirely by "overdue" -- a state
    that only existed because a hardware provisioning lead time outran the days
    left before a crossing. Remove the lead time and the map loses a colour,
    which is how this was found.
    """
    import re
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(ROOT / "src"))
    from module1 import threshold

    emitted = {v for k, v in vars(threshold).items()
               if k.startswith("STATUS_") and isinstance(v, str)}
    compared = set(re.findall(r'status\s*===\s*"([a-z_]+)"', JS))
    unknown = sorted(compared - emitted)
    assert not unknown, (
        f"pages.js compares status against {unknown}, which module1 never "
        f"emits. It emits {sorted(emitted)}")


def test_every_threshold_state_has_a_label_a_reader_can_act_on():
    """`due_now` rendered as "due now" against an act-by date three weeks out.

    The state widened when the hardware lead time became a decision window --
    it now means "falls inside this review cycle", up to thirty days -- and the
    word did not widen with it. Any state module 1 emits and the shell does not
    name falls back to the raw key with its underscore removed, which is how
    that happened.
    """
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(ROOT / "src"))
    from module1 import threshold

    shell = (ROOT / "webapp" / "static" / "shell.js").read_text()
    labelled = set(re.findall(r"^\s{2}([a-z_]+):\s*\"", 
                              shell[shell.index("const STATUS_LABEL"):
                                    shell.index("function statusPill")], re.M))
    emitted = {v for k, v in vars(threshold).items()
               if k.startswith("STATUS_") and isinstance(v, str)}
    missing = sorted(emitted - labelled)
    assert not missing, (
        f"module 1 emits {missing} and shell.js gives them no label, so they "
        f"reach the screen as the raw key")


def test_every_explanation_uses_the_one_tooltip_mechanism():
    """`title` alone is the browser's tooltip: about a second's delay, one
    OS-styled line, and poor with long text -- several of these explanations run
    past 380 characters, and a reviewer hovering one reported seeing nothing.

    shell.js draws them instead, from `data-info`. Anything carrying only a
    `title` is back on the mechanism that failed, and the reader gets two
    different hover behaviours in one product.
    """
    only_title = []
    for n, line in _reader_facing_lines():
        if 'title="${esc(' not in line:
            continue
        if "data-info=" in line:
            continue
        only_title.append(f"pages.js:{n}: {line.strip()[:110]}")
    assert not only_title, (
        "these explain themselves with `title` alone, which is the slow "
        "browser tooltip:\n  " + "\n  ".join(only_title))


def test_no_region_status_is_shown_to_a_reader_as_breached():
    """"Breached is a security term" -- review, bluntly, and it was the word on
    the pill for any region past its safety line.

    A region using the capacity it holds has not violated anything. The label is
    now "past its line". This checks the labels the shell defines rather than
    every string in the app, because "SLA breached" on the outcomes funnel is a
    different and correct use: a service level agreement genuinely is breached.
    """
    shell = (ROOT / "webapp" / "static" / "shell.js").read_text()
    block = shell[shell.index("const STATUS_LABEL"):shell.index("function statusPill")]
    labels = re.findall(r':\s*"([^"]+)"', block)
    assert labels, "STATUS_LABEL is empty -- update this test"
    offenders = [x for x in labels if "breach" in x.lower()]
    assert not offenders, f"a region status still reads as a breach: {offenders}"


def test_status_is_never_printed_raw():
    """Two places interpolated the status key straight into a pill, so the
    shell's labels were bypassed and the raw word reached the screen -- which is
    how "breached" survived being renamed everywhere else."""
    raw = re.findall(r"\$\{esc\((?:[a-z]\.)?status[^)]*\)\}", JS)
    assert not raw, (
        f"a status key is interpolated without going through statusPill(): {raw}")


def test_the_saturation_date_is_not_presented_as_a_fact():
    """Review: "let's not put that number ... it can happen in the next two
    hours". A single workload landing moves it, and for a region already past
    its line the date printed was in the past."""
    assert "Completely full" not in JS, (
        "the 100% saturation date is back on the region drill-down")


def test_the_regions_table_body_has_a_cell_for_every_heading():
    """Headings are generated from COLS; the body is hand-written.

    Adding a column means touching both, in the same place. A new cell was put
    before `cu_to_stay_under` while its COLS entry went after, so every row
    rendered the placeable figure under "To stay under" and the shortfall under
    "Placeable CU". Nothing errors -- the table is well-formed and wrong.
    """
    start = JS.index("const COLS = [")
    body = JS[start:JS.index("</tbody>", start)]
    # Headings come from the COLS array only. Scraping the whole block picked up
    # the filter dropdown's option labels once search was added above the table,
    # and counted them as columns -- so the test failed on a correct page.
    cols = body[:body.index("];")]
    headings = re.findall(r'label:\s*"([^"]+)"', cols)
    cells = body[body.index("<tbody>"):].count("<td")
    assert headings, "COLS no longer declares labels -- update this test"
    assert cells >= len(headings), (
        f"{len(headings)} headings but only {cells} cells in the row -- the "
        f"body and COLS have drifted apart")


def test_every_script_the_page_loads_exists_and_loads_in_order():
    """globe.js reads WORLD_PATH, so world.js has to come first.

    It was written, wired into pages.js, and never added to app.html -- so
    `globeMap` was undefined and the map page threw on render. Nothing in the
    suite noticed: the function existed, the caller existed, and the file was
    simply never delivered to the browser.
    """
    html = (ROOT / "webapp" / "static" / "app.html").read_text()
    scripts = re.findall(r'<script src="/static/([^"]+)"', html)
    assert scripts, "app.html loads no scripts -- update this test"

    for name in scripts:
        assert (ROOT / "webapp" / "static" / name).exists(), (
            f"app.html loads {name}, which is not in webapp/static")

    # Dependencies, as a pair of (needs, must come first).
    for needer, dependency in [("globe.js", "world.js"), ("pages.js", "shell.js")]:
        if needer in scripts and dependency in scripts:
            assert scripts.index(dependency) < scripts.index(needer), (
                f"{needer} runs before {dependency}, which it reads from")

    # And every file pages.js actually calls into has to be delivered. Checking
    # only the listed scripts cannot catch a missing one -- the list is simply
    # shorter and still internally consistent, which is why the first version
    # of this test passed against the very bug it was written for.
    defined: dict[str, str] = {}
    for f in sorted((ROOT / "webapp" / "static").glob("*.js")):
        for name in re.findall(r"(?m)^function\s+(\w+)\s*\(", f.read_text()):
            defined.setdefault(name, f.name)
    called = set(re.findall(r"\b(\w+)\s*\(", JS))
    missing = sorted({defined[n] for n in called
                      if n in defined and defined[n] not in scripts
                      and defined[n] != "pages.js"})
    assert not missing, (
        f"pages.js calls into {missing}, which app.html never loads -- the "
        f"function exists, the caller exists, and the browser never gets it")


def test_the_map_page_draws_the_globe():
    """Review asked for the map to be three-dimensional. If the flat renderer
    comes back the globe is gone, and the zoom-to-region behaviour with it."""
    block = JS[JS.index('PAGES["/map"]'):]
    block = block[:block.index('PAGES["/regions"]')]
    assert "globeMap(" in block, "the map page no longer renders the globe"
    assert "spinTo(" in block, (
        "the globe no longer turns to a region -- review asked for it to zoom "
        "in on click, not to cut to a new frame")


def _code_only(js: str) -> str:
    """The JavaScript with its comments removed.

    These checks assert that a name is absent from a block, and the comment
    explaining why it was removed contains that very name -- so both of the
    tests below passed against the bug and failed against the fix until the
    prose was taken out first.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", js)


def test_the_globe_can_be_turned_and_zoomed():
    """Review asked for the globe to be draggable with zoom controls."""
    block = JS[JS.index('PAGES["/map"]'):]
    block = block[:block.index('PAGES["/regions"]')]
    for needed in ("zoom-in", "zoom-out", "pointerdown", "pointermove"):
        assert needed in block, f"the map lost its {needed} handling"
    assert "map-summary" not in block, (
        "the counts strip is back above the globe -- it restated the marker "
        "colours in words and pushed the map down the page")


def test_the_globe_does_not_capture_the_pointer_before_a_drag_starts():
    """Capturing on pointerdown silently kills every click on the map.

    setPointerCapture retargets the click that follows to the capturing
    element, so with the capture taken in pointerdown the region markers, the
    site markers and both zoom buttons rendered, hovered, and did nothing at
    all -- no error, no console warning, just a dead map. The capture has to
    wait until the pointer has moved far enough that it cannot be a click.
    """
    block = _code_only(JS[JS.index('PAGES["/map"]'):])
    block = block[:block.index('PAGES["/regions"]')]
    down = block[block.index('addEventListener("pointerdown"'):]
    down = down[:down.index('addEventListener("pointermove"')]
    assert "setPointerCapture" not in down, (
        "the pointer is captured on pointerdown again -- every marker and both "
        "zoom buttons will stop responding to clicks")
    move = block[block.index('addEventListener("pointermove"'):]
    assert "setPointerCapture" in move[:move.index("drawMap")], (
        "the drag never captures the pointer, so it stops the moment the "
        "cursor leaves the globe")


def test_the_map_side_card_does_not_repeat_the_panel_below_it():
    """Crosses, Fleet, Capacity, Workloads and the recommendation counts were
    removed from the card beside the globe: all of them are on the detail panel
    that opens underneath the moment a region is picked."""
    card = _code_only(JS[JS.index("function mapCard("):])
    card = card[:card.index("\n}\n")]
    for gone in ("Crosses", "Fleet", "Workloads", "to scale up",
                 "to rebalance", "licensing"):
        assert gone not in card, (
            f"{gone!r} is back on the map side card, where it duplicates the "
            f"panel below the map")
    assert "Current capacity usage" in card, (
        "the card lost the one figure the marker's colour is derived from")


def test_site_tie_lines_stop_short_of_the_region_marker():
    """Drawn centre to centre, ten spokes render as a black splat.

    Every site in a region is joined back to the region marker. Taking those
    lines all the way to the centre stacks ten strokes on one point, and the
    marker underneath disappears into a starburst -- it was on the first
    screenshot of the zoomed globe and read as a rendering fault. `spoke()`
    insets both ends, so the marker stays visible.
    """
    globe = (ROOT / "webapp" / "static" / "globe.js").read_text()
    assert "function spoke(" in globe, (
        "the region-to-site tie lines no longer inset their ends")
    line = globe[globe.index("<line "):]
    line = line[:line.index("/>")]
    assert "spoke(" in line, (
        "the tie line is being drawn from raw coordinates again, so every "
        "site's stroke runs into the region marker")
    assert "hit.x" not in line and "hit.y" not in line, (
        "the tie line starts at the region centre rather than outside its "
        "marker -- the marker will be buried under the spokes again")


# --------------------------------------------------------------------------
# search and region filtering
# --------------------------------------------------------------------------


def test_the_three_browsable_pages_all_carry_a_filter():
    """110 sites and 11 regions is past the point where scrolling finds one.

    Data centres, Regions and the Fleet map each get a search box and a
    dropdown, built from one shared component rather than three near-copies
    that drift apart.
    """
    shell = (ROOT / "webapp" / "static" / "shell.js").read_text()
    assert "function filterBar(" in shell, "the shared filter component is gone"
    assert "function wireFilter(" in shell, "the shared filter wiring is gone"

    for page_id in ("dc-filter", "rg-filter", "map-filter"):
        assert f'filterBar("{page_id}"' in JS, f"{page_id} no longer renders a filter"


def test_filterable_rows_carry_the_attributes_the_filter_reads():
    """`wireFilter` matches on data-search and data-filter. A row without them
    falls back to its rendered text, which silently matches on things like a
    revenue figure -- so the pages that filter must set them explicitly."""
    assert 'data-search="${esc(`${x.datacentre} ${x.region}' in JS, (
        "data centre rows no longer carry the text the search matches")
    assert 'data-filter="${esc(x.region)}"' in JS, (
        "data centre rows no longer carry the region the dropdown filters on")
    assert 'data-filter="${r.at_risk ? "risk" : "ok"}"' in JS, (
        "region rows no longer carry the threshold state the dropdown filters on")


def test_the_map_filter_survives_selecting_a_region():
    """Selecting a region rewrites the side panel. The filter sits in that panel,
    so it has to live outside the part that gets replaced or it vanishes the
    first time it is used."""
    assert 'const side = $("map-side-body");' in JS, (
        "the map writes selections over the whole aside again, which destroys "
        "the filter bar sitting in it")
    assert '<div id="map-side-body">' in JS


# --------------------------------------------------------------------------
# v4 review items
# --------------------------------------------------------------------------


def test_how_to_read_is_collapsed_on_arrival():
    """It sat open on every page, so every page opened on half a screen of
    instructions. Read once, it is not needed again."""
    shell = (ROOT / "webapp" / "static" / "shell.js").read_text()
    assert '<details class="howto">' in shell, (
        "the how-to banner is expanded by default again")
    assert '<details class="howto" open>' not in shell


def test_search_and_filter_are_one_control():
    """Two controls let a reader set contradictory criteria and gave no hint
    which one would find what they wanted. They are now a single combobox."""
    shell = (ROOT / "webapp" / "static" / "shell.js").read_text()
    assert 'role="combobox"' in shell, "the merged search control is gone"
    assert "<select" not in shell.split("function filterBar(")[1].split("function wireFilter(")[0], (
        "the filter dropdown is a separate <select> again")


def test_site_markers_are_scattered_not_ringed():
    """Ten dots equidistant from a hub is a diagram of nothing, and the
    regularity implied a spatial relationship that does not exist. The layout
    must still be deterministic, or markers jump about mid-drag."""
    globe = (ROOT / "webapp" / "static" / "globe.js").read_text()
    assert "function jitter(" in globe, "site scatter is gone"
    assert "GOLDEN" in globe
    assert "const ringDeg = 4.2;" not in globe, (
        "sites are back on a single fixed-radius ring")


def test_a_selected_region_is_fenced_and_filled_the_view():
    """With sites scattered, nothing said where the region ended -- and at the
    old zoom, selecting a region looked much like not selecting one."""
    globe = (ROOT / "webapp" / "static" / "globe.js").read_text()
    assert "region-fence" in globe and "stroke-dasharray" in globe, (
        "the dotted region boundary is gone")
    assert "zoom: 6.8" in JS, "the region zoom no longer fills the view"


def test_map_markers_have_a_hover_readout_with_coordinates():
    """Review asked the hover to show latitude and longitude alongside what it
    already carried. The markers now build a cursor-following tooltip rather
    than leaning on the browser's slow one-line <title>."""
    block = JS[JS.index('PAGES["/map"]'):]
    block = block[:block.index('PAGES["/regions"]')]
    assert "function coordLine(" in block and "toFixed(3)" in block, (
        "the marker hover no longer prints coordinates")
    assert 'className = "chart-tip map-tip"' in block, (
        "the map lost its cursor-following hover tooltip")
    globe = (ROOT / "webapp" / "static" / "globe.js").read_text()
    assert "<title>" not in globe, (
        "a marker is back to the browser's <title> tooltip, which now double-"
        "renders behind the hover readout")


def test_the_planning_columns_are_on_the_data_centre_table():
    """Review asked for the buy decision to be visible in the list: how full,
    against a fixed line, at what growth, with what runway."""
    for label in ("Total CU", "Utilised CU", "Utilisation", "Threshold",
                  "Growth", "Lead time", "Weeks to decide", "Status"):
        assert label in JS, f"the {label!r} column is missing"


def test_the_region_table_says_what_its_average_is_an_average_of():
    assert 'label: "Data centres"' in JS, "the site count column is missing"
    assert 'label: "Utilised CU"' in JS, "the utilised CU column is missing"
