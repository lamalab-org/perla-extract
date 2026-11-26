"""Check reported values against their citations, as saving does, and explain failures.

The verdict comes from the same function the save validation uses
(`reported_value_support`), so a value reported as grounded here is accepted on save.
The explanation only helps the reviewer correct the input; it never changes the verdict.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from perla_extract.study_extraction.evidence import (
    source_contains_scientific_text,
    source_contains_text,
)
from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.validation import reported_value_support

NUMBER = re.compile(r"[+\-−]?\d+(?:[.,]\d+)*")


def _compact(text: object) -> str:
    raw = unicodedata.normalize("NFC", str(text or "")).replace("−", "-")
    return "".join(character for character in raw if not character.isspace())


def _numbers(quote: str) -> list[str]:
    seen: list[str] = []
    for match in NUMBER.finditer(quote):
        number = match.group().replace("−", "-")
        if number not in seen:
            seen.append(number)
    return seen


def _value_hint(raw_value: str, quote: str) -> str:
    """Why a value is not found in a quote that is itself correct."""

    value = _compact(raw_value)
    compact = _compact(quote)
    numbers = _numbers(quote)
    if value and value.lower() in compact.lower() and value not in compact:
        start = compact.lower().index(value.lower())
        return (
            f'The quote writes it as "{compact[start : start + len(value)]}"; '
            "case and notation must match the paper exactly."
        )
    digits = value.replace(",", "")
    printed = [number for number in numbers if number.replace(",", "") == digits]
    if printed and printed[0] != value:
        return (
            f'The paper prints it as "{printed[0]}"; type the value exactly as printed.'
        )
    containing = [number for number in numbers if value in number and number != value]
    if containing:
        listed = ", ".join(f'"{number}"' for number in containing[:4])
        return (
            f'"{value}" occurs only inside {listed}, not as a number of its own. '
            "Check the value, or choose the sentence where it is printed."
        )
    in_value = NUMBER.search(value)
    if re.search(r"[a-zA-Zµ°%]", value) and in_value:
        number = in_value.group()
        if number in numbers:
            return (
                f'The number {number} is in the quote but not "{raw_value.strip()}" as written. '
                "Put only the number in the value field and the unit in the unit field."
            )
    if numbers:
        return "Numbers in this quote: " + ", ".join(numbers[:12]) + "."
    return "The quote contains no numbers."


def check_values(
    values: list[dict[str, Any]], blocks: list[EvidenceBlock]
) -> list[dict[str, Any]]:
    """One result per value: ok, and a reason and hint for the reviewer when not ok.

    A value with ``quote_only`` checks only that its quotes occur in their blocks.
    """

    block_by_id = {block.block_id: block for block in blocks}
    results = []
    for value in values:
        raw_value = str(value.get("raw_value") or "")
        references = [
            reference
            for reference in value.get("evidence") or []
            if isinstance(reference, dict)
        ]
        candidate = {
            "raw_value": raw_value,
            "unit": value.get("unit"),
            "evidence": references,
        }
        quote_only = bool(value.get("quote_only"))
        if not raw_value.strip() and not quote_only:
            results.append({"ok": False, "reason": "The value is empty.", "hint": ""})
            continue
        if not references:
            results.append(
                {"ok": False, "reason": "This value has no evidence yet.", "hint": ""}
            )
            continue
        problem = None
        for reference in references:
            block = block_by_id.get(str(reference.get("block_id")))
            if block is None:
                problem = (
                    f"The evidence block {reference.get('block_id')} is not in this paper.",
                    "Select the sentence again in the paper.",
                )
                break
            if not source_contains_text(block.text, reference.get("quote")):
                problem = (
                    "The quote is not in its evidence block.",
                    "Select the sentence again; the quote must be copied from the paper text.",
                )
                break
        if problem is None and quote_only:
            results.append({"ok": True, "reason": "", "hint": ""})
            continue
        if problem is None:
            direct, assembled = reported_value_support(candidate, block_by_id, True)
            if direct or assembled:
                results.append({"ok": True, "reason": "", "hint": ""})
                continue
            if not all(
                source_contains_scientific_text(
                    block_by_id[str(reference.get("block_id"))].text,
                    reference.get("quote"),
                )
                for reference in references
            ):
                problem = (
                    "The quote differs from the paper text in case or special characters "
                    "(ligatures, sub- or superscripts).",
                    "Select the sentence again from the text view so the quote is copied exactly.",
                )
            else:
                quotes = " | ".join(
                    str(reference.get("quote")) for reference in references
                )
                problem = (
                    f'"{raw_value}" is not in the quote.',
                    _value_hint(raw_value, quotes),
                )
        reason, hint = problem
        results.append({"ok": False, "reason": reason, "hint": hint})
    return results
