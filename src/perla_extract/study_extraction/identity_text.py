"""Keep source designations and preparation prose separate from outcome assertions.

This is an identity projection, not a scientific paraphraser. It removes recognized
photovoltaic outcomes and explicit specimen counts. Numeric sample codes and
preparation amounts remain available to the identity matcher.
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
            "efficient",
            "η",
        )
    )
    + ")"
)
_NUMBER = r"[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?"
_APPROXIMATION = r"(?:(?:about|around|approximately|roughly|approx\.?)\s*|[≈~∼<>≤≥]\s*)?"
_VALUE = rf"{_APPROXIMATION}{_NUMBER}(?:\s*(?:±|\+/-|–|—|to)\s*{_NUMBER})?"
_UNIT = (
    r"(?:%|percent(?:age)?|dimensionless|fraction|m?V|"
    r"[kmunµμ]?A\s*(?:/|\s)\s*(?:cm|m)\s*(?:\^|\*\*)?\s*[−-]?[²2])"
)
_QUANTITY = rf"{_VALUE}\s*(?:{_UNIT})?"
_QUALIFIER = r"(?:(?:maximum|highest|best|mean|average|median|certified)[\s-]+)*"
_CONNECTOR = (
    r"\s*(?:(?:(?:(?:is|was)\s+)?measured\s+(?:as|at|to\s+be)|"
    r"of|is|was|at|=|:)\s*)?"
)
_OUTCOME = re.compile(
    # A unit-marked outcome can touch its subject after PDF whitespace loss:
    # cell20% PCE still contains the outcome 20%, not a specimen code cell20.
    # Require the unit on this branch so Device A20 PCE retains its A20 code.
    rf"(?<![\d.]){_VALUE}\s*{_UNIT}{_CONNECTOR}{_QUALIFIER}{_METRIC}(?!\w)|"
    rf"(?<!\w)(?:{_QUANTITY}{_CONNECTOR}{_QUALIFIER}{_METRIC}(?!\w)"
    rf"(?:{_CONNECTOR}{_QUANTITY})?|"
    rf"{_QUALIFIER}{_METRIC}(?!\w)(?:{_CONNECTOR}{_QUANTITY})?)",
    re.IGNORECASE,
)
_ATTACHED_OUTCOME = re.compile(
    rf"(?<!\w){_QUALIFIER}{_METRIC}{_VALUE}\s*(?:{_UNIT})?(?!\w)", re.IGNORECASE
)
_DESIGNATION_PREFIX = re.compile(r"\b(?:device|sample|cell|specimen|film)\s+$", re.I)
_CODE_BEFORE_OUTCOME = re.compile(
    rf"(?P<code>\d+){_CONNECTOR}{_QUALIFIER}{_METRIC}(?!\w)"
    rf"(?:{_CONNECTOR}{_QUANTITY})?",
    re.I,
)
_SPECIMEN_COUNT = re.compile(
    # Plural nouns distinguish a cohort count from a designation such as Device 20.
    r"(?<!\w)(?:\d+\s*(?:devices|cells|samples|specimens|films)\b|"
    r"(?:sample|cohort|population)\s+size\s*(?:(?:of|is|was|=|:)\s*)?\d+\b)",
    re.IGNORECASE,
)


def identity_text(value: str | None) -> str:
    """Return identity prose with recognized outcomes and specimen counts removed.

    The same projection precedes sample-code extraction, lexical features and
    literal descriptors. A removed assertion cannot choose between candidate
    owners through a different identity path. Unknown prose is kept literally.
    """

    if not value:
        return ""
    text = scientific_text(value)

    def remove_outcome(match: re.Match[str]) -> str:
        """Keep explicit unitless designations, never a unit-marked outcome.

        Device 1 PCE distribution names Device 1; cell 20% PCE reports an
        outcome. A named alphanumeric code such as Device PCE1 also survives.
        """

        if _DESIGNATION_PREFIX.search(text[: match.start()]):
            code = _CODE_BEFORE_OUTCOME.fullmatch(match[0])
            if code:
                return code["code"] + " "
            if re.fullmatch(r"(?=[A-Za-z0-9]*\d)[A-Za-z0-9]+", match[0]):
                return match[0]
        return " "

    text = _ATTACHED_OUTCOME.sub(remove_outcome, text)
    text = _OUTCOME.sub(remove_outcome, text)
    text = _SPECIMEN_COUNT.sub(" ", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", " ", text)
    return " ".join(text.split()).strip(" ,;:-")


def identity_tokens(value: str | None) -> set[str]:
    """Tokenize the shared projection while retaining meaningful code separators."""

    return set(re.findall(r"[^\W_]+(?:[-/][^\W_]+)*", identity_text(value).casefold()))
