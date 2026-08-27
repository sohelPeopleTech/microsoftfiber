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
ALLOWED_CONTEXT = ["why there is no hardware", "were removed"]


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
