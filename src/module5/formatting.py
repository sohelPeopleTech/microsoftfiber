"""Number formatting shared by the recommendation text and the written finding.

One implementation so a figure reads identically in the posted card, the
markdown file, and the chat follow-up.
"""

from __future__ import annotations


def money(value: float) -> str:
    """$1.25M / $412K / $980 -- the register a leadership channel reads in."""
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.0f}K"
    return f"${value:,.0f}"


def units(value: float) -> str:
    return f"{value:,.0f}"


def pct(value: float) -> str:
    return f"{value:.0%}"
