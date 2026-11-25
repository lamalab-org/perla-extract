"""Check correction integrity without treating a technical pass as scientific truth.

Source-grounded claims retain their measurement scope and multiplicity. Local IDs
anchor explicit edits; they never establish specimen identity across studies.
Ambiguous correspondence and unsupported semantic deletion keep the earlier draft.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .claims import ClaimLedger, audit_claim_coverage
from .models import (
    AbsorberComponent,
    EvidenceBlock,
    Layer,
    MaterialConstituent,
    PerformanceObservation,
    PopulationStatistic,
    ProcessingStep,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
    SubjectContext,
)
from .quantities import (
    CompoundClaim,
    LiteralClaim,
    ScalarClaim,
    interpret_quantity,
    scientific_text,
    scientific_value_text,
)
from .validation import scientific_record_fingerprint, validate_study

RECORD_COLLECTIONS = {
    "device_family": ("device_families", "family_id"),
    "individual_device": ("individual_devices", "device_id"),
    "performance_observation": ("performance_observations", "observation_id"),
    "population_statistic": ("population_statistics", "population_id"),
    "stability_test": ("stability_tests", "test_id"),
}


def record_for_path(study: StudyExtraction, path: str) -> tuple[str, object] | None:
    """Resolve a validation JSON path to its top-level scientific record."""

    match = re.match(r"^\$\.([a-z_]+)\[(\d+)\]", path)
    if not match or not hasattr(study, match.group(1)):
        return None
    collection = getattr(study, match.group(1))
    index = int(match.group(2))
    if not isinstance(collection, list) or index >= len(collection):
        return None
    kind = next(
        (
            candidate
            for candidate, (name, _) in RECORD_COLLECTIONS.items()
            if name == match.group(1)
        ),
        match.group(1),
    )
    return kind, collection[index]


def candidate_quality(
    study: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
    *,
    baseline: StudyExtraction | None = None,
) -> dict[str, int]:
    """Measure technical issues and source-value loss relative to a prior draft.

    Reader notes may be incomplete. Removing a record can reduce their mismatch
    count while deleting real data, so source-supported values are checked against
    the prior draft independently. Exact repetitions share one key; adding another
    value cannot compensate for losing a different one.
    """

    validation = validate_study(study, blocks)
    uncovered = 0
    missing_claims = 0
    if ledger is not None:
        coverage = audit_claim_coverage(ledger, study)
        uncovered = int(coverage["issue_count"])
        missing_claims = sum(
            int(coverage["counts"][key])
            for key in ("unmatched", "possible_match", "missing_shared_targets")
        )
    retention = (
        _retention_changes(baseline, study, blocks, validation)
        if baseline is not None
        else {
            "lost_verified_values": 0,
            "ownership_changes": 0,
            "ambiguous_ownership_edits": 0,
            "context_enrichments": 0,
        }
    )
    return {
        "validation_issues": len(validation["issues"]),
        "reported_values": int(validation["counts"]["reported_values"]),
        "source_verified_values": int(validation["counts"]["source_verified_values"]),
        "semantic_issues": uncovered,
        "missing_claims": missing_claims,
        **retention,
    }


def _claim_key(value: ReportedValue) -> tuple:
    """Retain complete source meaning independently of convenience-field errors.

    A parsed-number contradiction must be repairable without treating the invalid
    number as a scientific value. Its intact raw claim remains protected. Units,
    bounds, approximation, chemical case and charges are never flattened away.
    """

    interpretation = interpret_quantity(value)
    if interpretation.status == "contradiction":
        interpretation = interpret_quantity(
            value.model_copy(update={"value_number": None, "unit": None})
        )
    claim = interpretation.claim
    name = interpretation.kind or scientific_text(value.name)
    if isinstance(claim, ScalarClaim):
        magnitude, unit = (
            (claim.canonical.magnitude, claim.canonical.unit)
            if claim.canonical is not None
            else (claim.number, claim.unit)
        )
        # Decimal keys discard floating-point unit-conversion noise, not the
        # benchmark's 1% tolerance. Distinct reported numbers remain distinct.
        magnitude_key = format(magnitude, ".15g") if magnitude else "0"
        return name, "scalar", magnitude_key, unit, claim.qualifier
    if isinstance(claim, CompoundClaim):
        return (
            name,
            claim.kind,
            format(claim.first, ".15g") if claim.first else "0",
            format(claim.second, ".15g") if claim.second else "0",
            claim.unit,
        )
    assert isinstance(claim, LiteralClaim)
    return (
        name,
        "literal",
        scientific_value_text(value.raw_value),
        scientific_text(value.unit or ""),
    )


def _record_context(record: object) -> tuple:
    """Use reported measurement/statistical distinctions, not display labels."""

    fields = (
        ("measurement_type", "scan_direction")
        if isinstance(record, PerformanceObservation)
        else (
            "statistic_type",
            "sample_size",
            "error_bar_type",
            "measurement_type",
            "scan_direction",
        )
        if isinstance(record, PopulationStatistic)
        else ()
    )
    return tuple((name, getattr(record, name)) for name in fields)


def _nested_scope(study: StudyExtraction, path: str) -> tuple[tuple, tuple, tuple]:
    """Locate a value using schema fields; IDs only anchor an explicit edit scope."""

    node: object = study
    roles: list[str] = []
    owners: list[tuple[str, str]] = []
    context: list[tuple] = []
    for field, index in re.findall(r"\.([a-z_]+)|\[(\d+)\]", path):
        if field:
            node = getattr(node, field)
            roles.append(field)
        elif isinstance(node, list):
            node = node[int(index)]
        else:
            raise ValueError("a validation value path has an invalid list position")
        if isinstance(node, ProcessingStep):
            owners.append(("step_id", node.step_id))
            context.extend(
                (
                    ("operation", scientific_text(node.operation)),
                    ("sequence", node.sequence),
                )
            )
        elif isinstance(node, Layer):
            owners.append(("layer_id", node.layer_id))
            context.extend(
                (
                    ("layer_material", scientific_text(node.material)),
                    ("sequence", node.sequence),
                    ("role", node.role),
                )
            )
        elif isinstance(node, AbsorberComponent):
            owners.append(("absorber_id", node.absorber_id))
            if node.layer_id is not None:
                owners.append(("layer_id", node.layer_id))
        elif isinstance(node, StabilityCheckpoint):
            owners.append(("checkpoint_id", node.checkpoint_id))
            if node.time is not None:
                context.append(("checkpoint_time", _claim_key(node.time)))
        elif isinstance(node, SubjectContext):
            context.extend(
                (
                    ("treatment_stage", node.treatment_stage),
                    ("treated_material", scientific_text(node.treated_material or "")),
                )
            )
        elif isinstance(node, MaterialConstituent):
            context.append(("constituent", scientific_text(node.name), node.role))
    return tuple(roles[1:]), tuple(owners), tuple(context)


def _verified_claim_inventory(
    study: StudyExtraction, validation: dict[str, Any]
) -> dict[tuple, Counter[tuple]]:
    """Count source claims by meaning and explicit owner, retaining multiplicity.

    Identical entries within one scope count once. Distinct measurements and
    specimens count separately. IDs establish edit anchors only; moving one
    uniquely corresponding claim is reported separately from deleting it.
    """

    inventory: dict[tuple, Counter[tuple]] = defaultdict(Counter)
    seen: set[tuple] = set()
    duplicate_records: dict[tuple[str, str], str] = {}
    duplicate_checkpoints: dict[tuple[str, str], str] = {}
    for kind in ("performance_observation", "population_statistic", "stability_test"):
        collection, identifier = RECORD_COLLECTIONS[kind]
        canonical: dict[str, str] = {}
        checkpoint_anchors: dict[str, list[str]] = {}
        ignored = {identifier, "evidence"}
        if kind == "stability_test":
            ignored.add("checkpoint_id")
        for record in getattr(study, collection):
            signature = scientific_record_fingerprint(record, ignored)
            record_id = str(getattr(record, identifier))
            duplicate_records[(kind, record_id)] = canonical.setdefault(
                signature, record_id
            )
            if isinstance(record, StabilityTest):
                anchors = checkpoint_anchors.setdefault(
                    signature, [point.checkpoint_id for point in record.checkpoints]
                )
                duplicate_checkpoints.update(
                    ((record_id, point.checkpoint_id), anchor)
                    for point, anchor in zip(record.checkpoints, anchors)
                )
    for value in validation["verified_values"]:
        resolved = record_for_path(study, str(value["path"]))
        if resolved is None:
            continue
        kind, record = resolved
        _, identifier = RECORD_COLLECTIONS[kind]
        record_id = str(getattr(record, identifier))
        original_id = record_id
        record_id = duplicate_records.get((kind, record_id), record_id)
        roles, nested_owners, context = _nested_scope(study, str(value["path"]))
        nested_owners = tuple(
            (name, duplicate_checkpoints.get((original_id, anchor), anchor))
            if name == "checkpoint_id"
            else (name, anchor)
            for name, anchor in nested_owners
        )
        claim = ReportedValue.model_validate(
            {
                key: value[key]
                for key in ("name", "raw_value", "value_number", "unit", "evidence")
            }
        )
        key = kind, roles, _record_context(record), context, _claim_key(claim)
        owner = (
            record_id,
            getattr(record, "device_id", None),
            getattr(record, "family_id", None),
            nested_owners,
        )
        occurrence = key, owner
        if occurrence not in seen:
            inventory[key][owner] += 1
            seen.add(occurrence)
    return dict(inventory)


def _retention_changes(
    baseline: StudyExtraction,
    candidate: StudyExtraction,
    blocks: list[EvidenceBlock],
    validation: dict[str, Any],
) -> dict[str, int]:
    before = _verified_claim_inventory(baseline, validate_study(baseline, blocks))
    after = _verified_claim_inventory(candidate, validation)
    lost = moved = ambiguous = enriched = 0
    for key, owners in before.items():
        remaining = after.get(key, Counter()).copy()
        # A uniquely anchored claim can acquire previously missing context. An
        # explicit conflicting method/direction remains a different assertion.
        for owner, count in (owners - remaining).items():
            matches = [
                (other, target)
                for other, targets in after.items()
                if other != key
                and other[:2] == key[:2]
                and other[4] == key[4]
                and _context_extends(key[2], other[2])
                and _context_extends(key[3], other[3])
                for target in targets
                if targets[target] > 0
                and owner[0] == target[0]
                and owner[3] == target[3]
            ]
            if len(matches) == 1:
                other, target = matches[0]
                retained = min(count, after[other][target])
                remaining[target] += retained
                after[other][target] -= retained
                enriched += retained
        removed, added = owners - remaining, remaining - owners
        # Existing record IDs anchor an explicit relationship edit. They are not
        # evidence that two independently generated scientific subjects agree.
        for old in list(removed):
            matches = [new for new in added if old[0] == new[0] and old[3] == new[3]]
            if removed[old] and len(matches) == 1:
                new = matches[0]
                count = min(removed[old], added[new])
                removed[old] -= count
                added[new] -= count
                moved += count
        removed += Counter()
        added += Counter()
        old_count, new_count = sum(removed.values()), sum(added.values())
        lost += max(old_count - new_count, 0)
        if old_count and new_count:
            if old_count == new_count == 1:
                moved += 1
            else:
                ambiguous += min(old_count, new_count)
    return {
        "lost_verified_values": lost,
        "ownership_changes": moved,
        "ambiguous_ownership_edits": ambiguous,
        "context_enrichments": enriched,
    }


def _context_extends(before: tuple, after: tuple) -> bool:
    """Allow additional context while retaining every explicitly reported value."""

    current = {entry[0]: entry[1:] for entry in after}
    for entry in before:
        prior = entry[1:]
        values = current.get(entry[0])
        if values is None or len(prior) != len(values):
            return False
        if any(
            a not in (None, "", "not_reported") and a != b
            for a, b in zip(prior, values)
        ):
            return False
    return True


def is_monotonic_quality(before: dict[str, int], after: dict[str, int]) -> bool:
    """Return whether a candidate avoids trading away any grounded signal."""

    return (
        after["validation_issues"] <= before["validation_issues"]
        and after["semantic_issues"] <= before["semantic_issues"]
        and after["missing_claims"] <= before["missing_claims"]
        and after["lost_verified_values"] == 0
        and after.get("ambiguous_ownership_edits", 0) == 0
    )
