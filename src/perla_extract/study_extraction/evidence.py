"""Apply one conservative source-text policy wherever evidence is validated."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher

from .models import EvidenceBlock, StudyExtraction

MAX_CITATION_LENGTH = 1600


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


def repair_noncontiguous_citation_quotes(
    extraction: StudyExtraction, blocks: list[EvidenceBlock]
) -> tuple[StudyExtraction, dict[str, object]]:
    """Replace a stitched excerpt only when it is an ordered subset of one block.

    Structured-output models sometimes copy several real passages from one block but
    omit the prose between them. Such a string is not a valid verbatim citation. If
    the normalized quote is exactly two long source spans joined together, those exact
    spans are a conservative replacement. No scientific value or source pointer
    changes.
    """

    data = deepcopy(extraction.model_dump(mode="json"))
    block_by_id = {block.block_id: block for block in blocks}
    repairs: list[dict[str, object]] = []

    def ordered_subset(query: str, source: str) -> bool:
        if len(query) < 80:
            return False
        position = 0
        for character in query:
            position = source.find(character, position)
            if position < 0:
                return False
            position += 1
        return True

    def replacement(
        reference: dict, path: str, *, allow_split: bool
    ) -> list[dict]:
        block = block_by_id.get(str(reference["block_id"]))
        quote = str(reference["quote"])
        if block is None or source_contains_text(block.text, quote):
            return [reference]
        query = normalized_source_text(quote)
        source, offsets, raw_source = _normalized_with_offsets(block.text)
        if len(block.text) <= MAX_CITATION_LENGTH and ordered_subset(query, source):
            repairs.append(
                {
                    "path": path,
                    "block_id": block.block_id,
                    "old_quote": quote,
                    "new_quotes": [block.text],
                    "rule": "restore_complete_short_block",
                }
            )
            return [{"block_id": block.block_id, "quote": block.text}]
        if len(query) < 80:
            return [reference]
        matches = [
            match
            for match in SequenceMatcher(
                None, query, source, autojunk=False
            ).get_matching_blocks()
            if match.size
        ]
        if (
            len(matches) != 2
            or min(match.size for match in matches) < 40
            or sum(match.size for match in matches) != len(query)
        ):
            return [reference]
        source_quotes = [
            raw_source[
                offsets[match.b] : offsets[match.b + match.size - 1] + 1
            ].strip()
            for match in matches
        ]
        if any(
            not source_quote or len(source_quote) > MAX_CITATION_LENGTH
            for source_quote in source_quotes
        ):
            return [reference]
        if allow_split:
            new_quotes = source_quotes
            rule = "split_two_exact_source_spans"
        else:
            # Some parent records already use the smallest evidence-list limit. In
            # that case, retain the longer exact source span instead of leaving one
            # invalid stitched quotation or exceeding the public schema's limit.
            new_quotes = [max(source_quotes, key=len)]
            rule = "retain_longest_exact_source_span"
        repairs.append(
            {
                "path": path,
                "block_id": block.block_id,
                "old_quote": quote,
                "new_quotes": new_quotes,
                "rule": rule,
            }
        )
        return [{"block_id": block.block_id, "quote": item} for item in new_quotes]

    def walk(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence" and isinstance(item, list):
                    value[key] = [
                        replacement(
                            reference,
                            f"{path}.evidence[{index}]",
                            allow_split=len(item) < 8,
                        )
                        for index, reference in enumerate(item)
                    ]
                    value[key] = [
                        reference for group in value[key] for reference in group
                    ]
                else:
                    walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(data)
    return StudyExtraction.model_validate(data), {
        "repair_count": len(repairs),
        "repairs": repairs,
    }


def repair_unique_citation_pointers(
    extraction: StudyExtraction, blocks: list[EvidenceBlock]
) -> tuple[StudyExtraction, dict[str, object]]:
    """Relink a citation only when its unchanged quote has one source match.

    A repair corrects transport metadata, not scientific content. Zero or multiple
    matches remain unresolved, and searching by a bare reported value is deliberately
    forbidden because common numbers and material names are ambiguous.
    """

    data = deepcopy(extraction.model_dump(mode="json"))
    block_by_id = {block.block_id: block for block in blocks}
    repairs: list[dict[str, str]] = []
    unresolved: list[dict[str, object]] = []

    def has_context(quote: str) -> bool:
        """Require enough prose to distinguish a citation from a bare value."""

        return (
            len(normalized_source_text(quote)) >= 12
            and len(re.findall(r"\w+", quote)) >= 2
        )

    def walk(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            if {"block_id", "quote"} <= value.keys():
                old_id = str(value["block_id"])
                quote = str(value["quote"])
                current = block_by_id.get(old_id)
                if current is not None and source_contains_text(current.text, quote):
                    return
                if not has_context(quote):
                    unresolved.append(
                        {
                            "path": path,
                            "block_id": old_id,
                            "quote": quote,
                            "matching_block_ids": [],
                            "reason": "quote_too_short_for_safe_repair",
                        }
                    )
                    return
                matches = [
                    block.block_id
                    for block in blocks
                    if source_contains_text(block.text, quote)
                ]
                if len(matches) == 1:
                    value["block_id"] = matches[0]
                    repairs.append(
                        {
                            "path": path,
                            "old_block_id": old_id,
                            "new_block_id": matches[0],
                            "quote": quote,
                            "rule": "unique_normalized_quote_match",
                        }
                    )
                else:
                    unresolved.append(
                        {
                            "path": path,
                            "block_id": old_id,
                            "quote": quote,
                            "matching_block_ids": matches,
                        }
                    )
                return
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(data)
    return StudyExtraction.model_validate(data), {
        "repair_count": len(repairs),
        "unresolved_count": len(unresolved),
        "repairs": repairs,
        "unresolved": unresolved,
    }
