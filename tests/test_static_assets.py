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
