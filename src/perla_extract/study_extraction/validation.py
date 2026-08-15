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

    Multi-part values such as two tandem absorber formulas may live in separate
    blocks.  Joining exact quoted values with punctuation is grounded; adding,
    dropping, or rewriting any alphanumeric content is not.
    """

    parts = [_normalized(reference.get("quote")) for reference in references]
    return len(parts) > 1 and all(parts) and _normalized(raw_value) == "".join(parts)


def validate_study(
    extraction: StudyExtraction, blocks: list[EvidenceBlock]
) -> dict[str, object]:
    """Annotate textual grounding and relationship failures without deleting output.

    These deterministic checks prove that quotes and raw reported values occur in supplied
    blocks and that identifiers resolve. They do not prove semantic correctness or
    recall, so the complete extraction remains available for human review.
    """

    block_by_id = {block.block_id: block for block in blocks}
    issues: list[dict[str, str]] = []
    total_reported_values = 0
    source_verified_values = 0
    source_assembled_values = 0
    verified_values: list[dict[str, object]] = []

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
        nonlocal total_reported_values, source_verified_values, source_assembled_values
        if isinstance(value, dict):
            if {"name", "raw_value", "evidence"} <= value.keys():
                total_reported_values += 1
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
                    source_verified_values += 1
                    source_assembled_values += int(raw_assembled and not raw_direct)
                    verified_values.append({"path": path, **value})
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
    link_ids: set[str] = set()
    claimed: set[tuple[str, str]] = set()
    for index, link in enumerate(extraction.identity_links):
        path = f"$.identity_links[{index}]"
        if link.link_id in link_ids:
            issue(f"{path}.link_id", "duplicate link_id")
        link_ids.add(link.link_id)
        namespaces = {
            window_namespace(candidate_id) for candidate_id in link.candidate_ids
        }
        if None in namespaces or len(namespaces) < 2:
            issue(
                f"{path}.candidate_ids",
                "linked candidates must come from different windows",
            )
        for candidate_id in link.candidate_ids:
            candidate = (link.entity_kind, candidate_id)
            if candidate_id not in entity_ids[link.entity_kind]:
                issue(f"{path}.candidate_ids", "unknown linked candidate ID")
            if candidate in claimed:
                issue(f"{path}.candidate_ids", "candidate used in more than one link")
            claimed.add(candidate)

    return {
        "status": "verified" if not issues else "needs_review",
        "issues": issues,
        "counts": {
            "device_families": len(extraction.device_families),
            "individual_devices": len(extraction.individual_devices),
            "performance_observations": len(extraction.performance_observations),
            "population_statistics": len(extraction.population_statistics),
            "stability_tests": len(extraction.stability_tests),
            "identity_links": len(extraction.identity_links),
            "reported_values": total_reported_values,
            "source_verified_values": source_verified_values,
            "source_assembled_values": source_assembled_values,
            "issues_by_reason": dict(Counter(item["reason"] for item in issues)),
        },
        "verified_values": verified_values,
    }
