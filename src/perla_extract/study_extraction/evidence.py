"""Apply one conservative source-text policy wherever evidence is validated."""

from __future__ import annotations

import re
import unicodedata


def _normalized_with_offsets(value: object) -> tuple[str, list[int], str]:
    """Normalize OCR typography while retaining positions in the source string."""

    raw = unicodedata.normalize("NFKC", str(value or ""))
    raw = raw.replace("−", "-").replace("–", "-").replace("—", "-")
    characters: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(raw):
        if unicodedata.category(character) == "Pd":
            character = "-"
        for folded in character.casefold():
            if re.fullmatch(r"[\w.%<>~=+/\-]", folded):
                characters.append(folded)
                offsets.append(index)
    return "".join(characters), offsets, raw


def normalized_source_text(value: object) -> str:
    """Normalize PDF typography for conservative source comparison."""

    return _normalized_with_offsets(value)[0]


def source_contains_text(source: object, query: object) -> bool:
    """Match copied evidence without joining it to surrounding source words."""

    raw_query = unicodedata.normalize("NFKC", str(query or ""))
    raw_source = unicodedata.normalize("NFKC", str(source or ""))
    if raw_query and raw_query in raw_source:
        return True
    needle = normalized_source_text(query)
    haystack, offsets, boundary_source = _normalized_with_offsets(source)
    if not needle:
        return False
    for match in re.finditer(re.escape(needle), haystack):
        source_start = offsets[match.start()]
        source_end = offsets[match.end() - 1]
        before = boundary_source[source_start - 1] if source_start else ""
        after = (
            boundary_source[source_end + 1]
            if source_end + 1 < len(boundary_source)
            else ""
        )
        if needle[0].isalnum() and before.isalnum():
            continue
        if needle[-1].isalnum() and after.isalnum():
            continue
        return True
    return False


def assembled_from_quotes(raw_value: object, references: list[dict]) -> bool:
    """Accept a value made only by joining two or more verified source quotes."""

    parts = [normalized_source_text(reference.get("quote")) for reference in references]
    return (
        len(parts) > 1
        and all(parts)
        and normalized_source_text(raw_value) == "".join(parts)
    )
