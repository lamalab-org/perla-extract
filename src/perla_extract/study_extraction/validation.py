"""Annotate grounding and link problems without deleting extracted records."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .identifiers import entity_id_lists, window_namespace
from .models import EvidenceBlock, StudyExtraction


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


def _normalized(value: object) -> str:
    """Normalize PDF typography for conservative source comparison."""

    return _normalized_with_offsets(value)[0]


def _contains(query: object, source: object) -> bool:
    """Match copied evidence after conservative whitespace and Unicode normalization."""

    raw_query = unicodedata.normalize("NFKC", str(query or ""))
    raw_source = unicodedata.normalize("NFKC", str(source or ""))
    if raw_query and raw_query in raw_source:
        return True
    needle = _normalized(query)
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


def _assembled_from_quotes(raw_value: object, references: list[dict]) -> bool:
    """Accept a value made only by joining two or more verified source quotes.

    Multi-part facts such as two tandem absorber formulas may live in separate
    blocks.  Joining exact quoted values with punctuation is grounded; adding,
    dropping, or rewriting any alphanumeric content is not.
    """

    parts = [_normalized(reference.get("quote")) for reference in references]
    return len(parts) > 1 and all(parts) and _normalized(raw_value) == "".join(parts)


def validate_study(
    extraction: StudyExtraction, blocks: list[EvidenceBlock]
) -> dict[str, object]:
    """Check evidence quotes, fact values, IDs, and links while preserving output."""

    block_by_id = {block.block_id: block for block in blocks}
    issues: list[dict[str, str]] = []
    total_facts = 0
    grounded_facts = 0
    assembled_facts = 0
    verified_facts: list[dict[str, object]] = []

    def issue(path: str, reason: str) -> None:
        issues.append({"path": path, "reason": reason})

    def evidence_supported(items: list[dict], path: str) -> bool:
        supported = bool(items)
        for index, reference in enumerate(items):
            block = block_by_id.get(reference.get("block_id"))
            reference_path = f"{path}.evidence[{index}]"
            if block is None:
                issue(reference_path, "unknown block_id")
                supported = False
            elif not _contains(reference.get("quote"), block.text):
                issue(reference_path, "quote not found in cited block")
                supported = False
        return supported

    def walk(value: object, path: str = "$") -> None:
        nonlocal total_facts, grounded_facts, assembled_facts
        if isinstance(value, dict):
            if {"name", "raw_value", "evidence"} <= value.keys():
                total_facts += 1
                evidence_ok = evidence_supported(value["evidence"], path)
                raw_direct = any(
                    (block := block_by_id.get(reference.get("block_id"))) is not None
                    and _contains(value["raw_value"], block.text)
                    for reference in value["evidence"]
                )
                raw_assembled = evidence_ok and _assembled_from_quotes(
                    value["raw_value"], value["evidence"]
                )
                raw_ok = raw_direct or raw_assembled
                if not raw_ok:
                    issue(path, "raw_value not found in cited evidence")
                if evidence_ok and raw_ok:
                    grounded_facts += 1
                    assembled_facts += int(raw_assembled and not raw_direct)
                    verified_facts.append({"path": path, **value})
            elif isinstance(value.get("evidence"), list):
                evidence_supported(value["evidence"], path)
            for key, item in value.items():
                if key != "evidence":
                    walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    data = extraction.model_dump(mode="json")
    walk(data)
    identifiers = entity_id_lists(extraction)
    family_ids = identifiers["device_family"]
    device_ids = identifiers["individual_device"]
    families, devices = set(family_ids), set(device_ids)
    collections = {
        "device_family": ("$.device_families", "family_id"),
        "individual_device": ("$.individual_devices", "device_id"),
        "performance_observation": (
            "$.performance_observations",
            "observation_id",
        ),
        "population_statistic": ("$.population_statistics", "population_id"),
        "stability_test": ("$.stability_tests", "test_id"),
    }
    for kind, ids in identifiers.items():
        if len(ids) != len(set(ids)):
            path, field = collections[kind]
            issue(path, f"duplicate {field}")
    for index, device in enumerate(extraction.individual_devices):
        if device.family_id and device.family_id not in families:
            issue(f"$.individual_devices[{index}].family_id", "unknown family_id")
    for index, observation in enumerate(extraction.performance_observations):
        if observation.device_id not in devices:
            issue(
                f"$.performance_observations[{index}].device_id",
                "unknown device_id",
            )
    for index, population in enumerate(extraction.population_statistics):
        if population.family_id and population.family_id not in families:
            issue(f"$.population_statistics[{index}].family_id", "unknown family_id")
    for index, test in enumerate(extraction.stability_tests):
        if test.family_id and test.family_id not in families:
            issue(f"$.stability_tests[{index}].family_id", "unknown family_id")
        if test.device_id and test.device_id not in devices:
            issue(f"$.stability_tests[{index}].device_id", "unknown device_id")

    entity_ids = {kind: set(ids) for kind, ids in identifiers.items()}
    equivalence_ids: set[str] = set()
    claimed: set[tuple[str, str]] = set()
    for index, group in enumerate(extraction.equivalence_groups):
        path = f"$.equivalence_groups[{index}]"
        if group.equivalence_id in equivalence_ids:
            issue(f"{path}.equivalence_id", "duplicate equivalence_id")
        equivalence_ids.add(group.equivalence_id)
        namespaces = {window_namespace(member) for member in group.member_ids}
        if None in namespaces or len(namespaces) < 2:
            issue(
                f"{path}.member_ids",
                "equivalence members must come from different windows",
            )
        for member_id in group.member_ids:
            member = (group.entity_kind, member_id)
            if member_id not in entity_ids[group.entity_kind]:
                issue(f"{path}.member_ids", "unknown equivalence member ID")
            if member in claimed:
                issue(f"{path}.member_ids", "equivalence member used more than once")
            claimed.add(member)

    return {
        "status": "verified" if not issues else "needs_review",
        "issues": issues,
        "counts": {
            "device_families": len(extraction.device_families),
            "individual_devices": len(extraction.individual_devices),
            "performance_observations": len(extraction.performance_observations),
            "population_statistics": len(extraction.population_statistics),
            "stability_tests": len(extraction.stability_tests),
            "equivalence_groups": len(extraction.equivalence_groups),
            "facts": total_facts,
            "source_verified_facts": grounded_facts,
            "source_assembled_facts": assembled_facts,
            "issues_by_reason": dict(Counter(item["reason"] for item in issues)),
        },
        "verified_facts": verified_facts,
    }
