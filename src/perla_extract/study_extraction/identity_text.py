"""Keep source designations and preparation prose separate from outcome assertions.

This is an identity projection, not a scientific paraphraser. It removes only
recognized photovoltaic outcome names and their attached quantities. Numeric
sample codes and preparation amounts remain available to the identity matcher.
"""

from __future__ import annotations

import re

from .quantities import scientific_text
from .quantity_specs import QUANTITY_SPECS


def _name_pattern(name: str) -> str:
    if name.casefold() in {"voc", "jsc"}:
        return name[0] + r"[\s_-]*" + name[1:]
    return r"[\s_-]+".join(re.escape(word) for word in name.split())


_METRIC = (
    "(?:"
    + "|".join(
        _name_pattern(name)
        for name in (
            *(
                name
                for kind in ("pce", "ff", "voc", "jsc")
                for name in QUANTITY_SPECS[kind].names
            ),
            # These identify outcome prose here without asserting that generic
            # efficiency or eta denotes the photovoltaic PCE quantity.
            "efficiency",
            "η",
        )
    )
    + ")"
)
_NUMBER = r"[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?"
_VALUE = rf"(?:[≈~∼<>≤≥]\s*)?{_NUMBER}(?:\s*(?:±|\+/-|–|—|to)\s*{_NUMBER})?"
_UNIT = (
    r"(?:%|percent(?:age)?|dimensionless|fraction|m?V|"
    r"[kmunµμ]?A\s*(?:/|\s)\s*(?:cm|m)\s*(?:\^|\*\*)?\s*[−-]?[²2])"
)
_QUANTITY = rf"{_VALUE}\s*(?:{_UNIT})?"
_QUALIFIER = r"(?:(?:maximum|highest|best|mean|average|median|certified)[\s-]+)*"
_CONNECTOR = r"\s*(?:(?:of|is|was|at|=|:)\s*)?"
_OUTCOME = re.compile(
    rf"(?<!\w)(?:{_QUANTITY}{_CONNECTOR}{_QUALIFIER}{_METRIC}(?!\w)|"
    rf"{_QUALIFIER}{_METRIC}(?!\w)(?:{_CONNECTOR}{_QUANTITY})?)",
    re.IGNORECASE,
)
_ATTACHED_OUTCOME = re.compile(
    rf"(?<!\w){_QUALIFIER}{_METRIC}{_VALUE}\s*{_UNIT}(?!\w)", re.IGNORECASE
)


def identity_text(value: str | None) -> str:
    """Return identity prose with recognized outcome assertions removed.

    The same projection precedes sample-code extraction, lexical features and
    literal descriptors. A removed assertion cannot choose between candidate
    owners through a different identity path. Unknown prose is kept literally.
    """

    if not value:
        return ""
    text = _ATTACHED_OUTCOME.sub(" ", scientific_text(value))
    text = _OUTCOME.sub(" ", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", " ", text)
    return " ".join(text.split()).strip(" ,;:-")


def identity_tokens(value: str | None) -> set[str]:
    """Tokenize the shared projection while retaining meaningful code separators."""

    return set(re.findall(r"[^\W_]+(?:[-/][^\W_]+)*", identity_text(value).casefold()))
