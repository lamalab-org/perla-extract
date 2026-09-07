"""Recognize explicit subfigure labels without interpreting scientific text."""

from __future__ import annotations

import re
from typing import Literal

PANEL_MARKER = re.compile(
    r"\(([a-z])\)|(?<!\w)([a-z])\)(?=\s|[,:;.])", re.IGNORECASE
)

FigureClassName = Literal[
    "jv",
    "eqe",
    "population_statistics",
    "stability",
    "characterization",
    "device_structure",
    "other",
]
DataPresentation = Literal[
    "no_numeric_data",
    "explicit_numeric_labels",
    "inset_table",
    "plotted_values_only",
    "mixed",
    "uncertain",
]


def conservative_schema_relevance(
    figure_class: FigureClassName, data_presentation: DataPresentation
) -> bool:
    """Decide whether a panel should enter the figure-loss count by default.

    Reviewers are estimating facts lost by a text-only extraction, not the broader
    scientific value of figures. Device diagrams can directly report composition;
    result plots count only when they print discrete values or contain a table.
    Characterization and other panels remain opt-in because deciding whether they
    populate a supported property requires scientific context. Keeping this boundary
    deterministic prevents a model's generous topic judgment from inflating the
    default count while leaving reviewers one checkbox to override it.
    """

    if figure_class == "device_structure":
        return True
    return figure_class in {"jv", "eqe", "population_statistics", "stability"} and (
        data_presentation in {"explicit_numeric_labels", "inset_table", "mixed"}
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
