"""Recognize explicit subfigure labels without interpreting scientific text."""

from __future__ import annotations

import re

PANEL_MARKER = re.compile(
    r"\(([a-z])\)|(?<!\w)([a-z])\)(?=\s|[,:;.])", re.IGNORECASE
)


def caption_panel_labels(caption: str) -> set[str]:
    """Return panel labels written as unambiguous markers such as ``(a)`` or ``b)``.

    Restricting this check to typography catches silent panel omissions without
    guessing that ordinary scientific letters, such as A-site or B-site, are panels.
    """

    return {
        (match.group(1) or match.group(2)).casefold()
        for match in PANEL_MARKER.finditer(caption)
    }
